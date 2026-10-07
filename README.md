# DataGenie — Natural-Language SQL Assistant

DataGenie is a Streamlit application that lets users explore a SQLite database with plain-language questions. It uses a Groq-hosted LLM to translate a question into SQL, shows the generated query, executes it against the database in read-only mode, and explains the result in simple language.

## What it demonstrates

- Natural-language-to-SQL generation
- SQLite schema discovery
- Read-only query execution with multiple safety checks
- LLM-generated explanations and follow-up suggestions
- Streamlit chat interface
- Support for English and Roman Urdu questions

## How it works

1. Upload a SQLite database, or use the included sample database.
2. DataGenie detects tables and columns.
3. Ask a question in natural language.
4. The LLM generates a single read-only `SELECT` query.
5. The app validates and executes the SQL.
6. Results are displayed and summarized in plain language.

## Safety design

The project is intentionally read-only:

- SQLite connections are opened in `mode=ro`.
- An SQLite authorizer denies write/schema-changing operations.
- The app accepts only `SELECT` or `WITH ... SELECT` queries.
- Multiple statements and common mutating SQL keywords are blocked.
- Result sets are capped when no explicit limit is supplied.

These controls reduce risk, but generated SQL should still be reviewed before using the project with sensitive or production data.

## Tech stack

- Python
- Streamlit
- SQLite
- Pandas
- LangChain Core
- LangChain Groq
- Groq-hosted LLMs

## Setup

```bash
python -m venv .venv
```

Activate the environment, then install dependencies:

```bash
pip install -r requirements.txt
```

Copy the environment template and add your Groq API key:

```bash
cp .env.example .env
```

If needed, recreate the sample database:

```bash
python create_sample_db.py
```

Run the app:

```bash
streamlit run app.py
```

## Example questions

- Which student has the highest marks?
- Show students in the Development course.
- What is the average score by course?
- Data Science ke students ke marks dikhao.

## Project structure

```text
.
├── app.py
├── create_sample_db.py
├── student.db
├── requirements.txt
├── .env.example
├── .gitignore
└── README.md
```

## Notes

- A Groq API key is required for query generation and explanations.
- Uploaded databases are copied to a temporary local file for the session.
- Do not upload databases containing confidential or regulated data to an environment you do not control.
- This is a portfolio/demo project, not a production database administration tool.
