import hashlib
import json
import os
import sqlite3
import tempfile
from pathlib import Path
from urllib.parse import quote

import pandas as pd
import streamlit as st
from dotenv import load_dotenv
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_groq import ChatGroq

load_dotenv()

APP_NAME = "DataGenie"
DEFAULT_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
MAX_ROWS = 100
PREVIEW_ROWS = 8


@st.cache_resource(show_spinner=False)
def get_llm(api_key: str) -> ChatGroq:
    if not api_key:
        raise ValueError("Add a Groq API key in the sidebar or set GROQ_API_KEY in your environment.")
    return ChatGroq(model=DEFAULT_MODEL, temperature=0, streaming=False, api_key=api_key)


@st.cache_data(show_spinner=False)
def persist_uploaded_db(file_bytes: bytes, file_name: str) -> str:
    """Persist one uploaded SQLite database to a stable temporary path."""
    digest = hashlib.sha256(file_bytes).hexdigest()[:12]
    safe_name = Path(file_name).stem.replace(" ", "_") or "uploaded"
    path = Path(tempfile.gettempdir()) / f"datagenie_{safe_name}_{digest}.db"
    if not path.exists():
        path.write_bytes(file_bytes)
    return str(path)


def get_db_path(uploaded_file) -> str | None:
    if uploaded_file is not None:
        return persist_uploaded_db(uploaded_file.getvalue(), uploaded_file.name)

    sample_db = Path(__file__).parent / "student.db"
    return str(sample_db) if sample_db.exists() else None


def connect_read_only(db_path: str) -> sqlite3.Connection:
    """Open SQLite in read-only mode and deny mutating operations at the engine level."""
    uri = f"file:{quote(str(Path(db_path).resolve()))}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, check_same_thread=False)
    conn.row_factory = sqlite3.Row

    denied_actions = {
        sqlite3.SQLITE_INSERT,
        sqlite3.SQLITE_UPDATE,
        sqlite3.SQLITE_DELETE,
        sqlite3.SQLITE_CREATE_INDEX,
        sqlite3.SQLITE_CREATE_TABLE,
        sqlite3.SQLITE_CREATE_TEMP_INDEX,
        sqlite3.SQLITE_CREATE_TEMP_TABLE,
        sqlite3.SQLITE_CREATE_TEMP_TRIGGER,
        sqlite3.SQLITE_CREATE_TEMP_VIEW,
        sqlite3.SQLITE_CREATE_TRIGGER,
        sqlite3.SQLITE_CREATE_VIEW,
        sqlite3.SQLITE_DROP_INDEX,
        sqlite3.SQLITE_DROP_TABLE,
        sqlite3.SQLITE_DROP_TEMP_INDEX,
        sqlite3.SQLITE_DROP_TEMP_TABLE,
        sqlite3.SQLITE_DROP_TEMP_TRIGGER,
        sqlite3.SQLITE_DROP_TEMP_VIEW,
        sqlite3.SQLITE_DROP_TRIGGER,
        sqlite3.SQLITE_DROP_VIEW,
        sqlite3.SQLITE_ALTER_TABLE,
        sqlite3.SQLITE_ATTACH,
        sqlite3.SQLITE_DETACH,
        sqlite3.SQLITE_PRAGMA,
        sqlite3.SQLITE_TRANSACTION,
    }

    def authorizer(action, _arg1, _arg2, _db_name, _trigger):
        return sqlite3.SQLITE_DENY if action in denied_actions else sqlite3.SQLITE_OK

    conn.set_authorizer(authorizer)
    return conn


def quote_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def get_schema(conn: sqlite3.Connection) -> dict[str, list[str]]:
    schema: dict[str, list[str]] = {}
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()

    for row in rows:
        table_name = row[0]
        columns = conn.execute(f"PRAGMA table_info({quote_identifier(table_name)})").fetchall()
        schema[table_name] = [column[1] for column in columns]
    return schema


def schema_to_text(schema: dict[str, list[str]]) -> str:
    return "\n".join(f"- {table}({', '.join(columns)})" for table, columns in schema.items())


def extract_json_object(text: str) -> dict:
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("No JSON object found")
    return json.loads(text[start : end + 1])


def ask_llm_for_sql(llm: ChatGroq, question: str, schema_text: str) -> dict:
    """Generate one read-only SQLite query plus a concise user-facing note."""
    system = SystemMessage(
        content=(
            "You are DataGenie, a SQLite query assistant.\n"
            "Use only the tables and columns in the supplied schema.\n"
            "Generate exactly one read-only query. SELECT and WITH...SELECT are allowed.\n"
            "Never generate INSERT, UPDATE, DELETE, CREATE, DROP, ALTER, ATTACH, DETACH, PRAGMA, "
            "VACUUM, transactions, or multiple statements.\n"
            f"Keep result sets small. Add LIMIT {MAX_ROWS} when the user did not request a smaller limit.\n"
            "If a reasonable assumption is required, state it briefly in note. Do not reveal hidden reasoning.\n"
            "Return strict JSON only with keys: sql, note, followups.\n"
            "followups must contain exactly two short related questions.\n\n"
            f"SCHEMA:\n{schema_text}"
        )
    )
    user = HumanMessage(content=f"Question: {question}")
    response = llm.invoke([system, user])

    try:
        data = extract_json_object(response.content.strip())
        return {
            "sql": str(data.get("sql", "")).strip(),
            "note": str(data.get("note", "")).strip(),
            "followups": [str(item) for item in data.get("followups", [])][:2],
        }
    except Exception:
        return {
            "sql": "",
            "note": "The model did not return a valid structured query. Please rephrase the question.",
            "followups": ["What tables are available?", "Show me a simple summary of the data."],
        }


def validate_read_only_sql(sql: str) -> tuple[bool, str]:
    cleaned = sql.strip().rstrip(";").strip()
    if not cleaned:
        return False, "No SQL was generated."

    if ";" in cleaned:
        return False, "Blocked: multiple SQL statements are not allowed."

    first_word = cleaned.split(None, 1)[0].lower()
    if first_word not in {"select", "with"}:
        return False, "Blocked: only read-only SELECT queries are allowed."

    forbidden = {
        "insert", "update", "delete", "create", "drop", "alter", "attach", "detach",
        "pragma", "vacuum", "replace", "reindex", "begin", "commit", "rollback",
    }
    tokens = {token.strip("(),\n\t\r").lower() for token in cleaned.replace("=", " ").split()}
    if tokens & forbidden:
        return False, "Blocked: the generated SQL contains a prohibited operation."

    return True, cleaned


def run_sql(conn: sqlite3.Connection, sql: str) -> pd.DataFrame | str:
    valid, result = validate_read_only_sql(sql)
    if not valid:
        return result

    sql_to_run = result
    if "limit" not in sql_to_run.lower():
        sql_to_run = f"{sql_to_run} LIMIT {MAX_ROWS}"

    try:
        return pd.read_sql_query(sql_to_run, conn)
    except Exception as exc:
        return f"SQL error: {exc}"


def build_final_answer(llm: ChatGroq, question: str, sql: str, result: pd.DataFrame | str) -> str:
    if isinstance(result, pd.DataFrame):
        if result.empty:
            result_text = "The query returned 0 rows."
        else:
            result_text = result.head(PREVIEW_ROWS).to_csv(index=False)
    else:
        result_text = str(result)

    system = SystemMessage(
        content=(
            "You are DataGenie. Explain database query results in concise, plain language. "
            "Base the answer only on the supplied result. Do not invent values. "
            "If there is an error, explain it briefly and suggest how the user could rephrase the question."
        )
    )
    user = HumanMessage(
        content=f"Question: {question}\n\nSQL:\n{sql}\n\nResult preview:\n{result_text}"
    )
    return llm.invoke([system, user]).content.strip()


def main() -> None:
    st.set_page_config(page_title=APP_NAME, page_icon="🧞", layout="wide")
    st.title("🧞 DataGenie — Natural-Language SQL Assistant")
    st.caption("Ask questions about a SQLite database in plain English or Roman Urdu.")

    with st.sidebar:
        st.header("Database")
        uploaded = st.file_uploader("Upload a SQLite database", type=["db", "sqlite", "sqlite3"])
        st.caption("If no file is uploaded, the included student.db sample is used.")

        st.header("Groq API key")
        key_input = st.text_input("GROQ_API_KEY", type="password")
        api_key = key_input or os.getenv("GROQ_API_KEY", "")
        st.caption("The app needs a Groq API key to generate SQL and explanations.")

    db_path = get_db_path(uploaded)
    if not db_path:
        st.warning("No database found. Upload a SQLite database or generate the sample database.")
        return

    try:
        conn = connect_read_only(db_path)
        schema = get_schema(conn)
    except Exception as exc:
        st.error(f"Could not open the database: {exc}")
        return

    if not schema:
        st.error("No user tables were found in this database.")
        return

    with st.expander("Detected database schema", expanded=False):
        st.code(schema_to_text(schema), language="text")

    try:
        llm = get_llm(api_key)
    except Exception as exc:
        st.info(str(exc))
        return

    if "history" not in st.session_state:
        st.session_state.history = []

    for turn in st.session_state.history:
        with st.chat_message(turn["role"]):
            st.markdown(turn["content"])

    question = st.chat_input("Ask a question about your data…")
    if not question:
        return

    with st.chat_message("user"):
        st.markdown(question)
    st.session_state.history.append({"role": "user", "content": question})

    with st.chat_message("assistant"):
        with st.spinner("Generating a safe SQL query…"):
            plan = ask_llm_for_sql(llm, question, schema_to_text(schema))
            sql = plan["sql"]
            note = plan["note"]
            followups = plan["followups"]

            if note:
                st.caption(note)

            if not sql:
                st.warning("No valid SQL was generated. Try rephrasing the question.")
                final_answer = note
            else:
                st.markdown("**Generated SQL**")
                st.code(sql, language="sql")
                result = run_sql(conn, sql)

                if isinstance(result, pd.DataFrame):
                    if result.empty:
                        st.info("The query ran successfully but returned no rows.")
                    else:
                        st.dataframe(result, use_container_width=True)
                else:
                    st.error(result)

                with st.spinner("Explaining the result…"):
                    final_answer = build_final_answer(llm, question, sql, result)
                st.markdown(final_answer)

            if followups:
                st.markdown("**Suggested follow-up questions**")
                for item in followups:
                    st.write(f"- {item}")

    st.session_state.history.append({"role": "assistant", "content": final_answer})
    conn.close()


if __name__ == "__main__":
    main()
