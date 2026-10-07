import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "student.db"

ROWS = [
    ("Malik", "Data Science", "A", 70),
    ("Ali", "Development", "B", 90),
    ("Khalid", "Mathematics", "A", 10),
    ("Maham", "Data Science", "B", 20),
    ("Kanza", "Development", "A", 60),
    ("Qasim", "Data Science", "C", 99),
    ("Haider", "Development", "A", 40),
    ("John", "Mathematics", "D", 20),
    ("Harris", "Development", "A", 33),
    ("Aalina", "Mathematics", "C", 66),
    ("Jacob", "Development", "B", 100),
]

with sqlite3.connect(DB_PATH) as connection:
    connection.execute("DROP TABLE IF EXISTS student")
    connection.execute(
        """
        CREATE TABLE student (
            name TEXT NOT NULL,
            course TEXT NOT NULL,
            section TEXT NOT NULL,
            marks INTEGER NOT NULL
        )
        """
    )
    connection.executemany("INSERT INTO student VALUES (?, ?, ?, ?)", ROWS)

print(f"Created sample database: {DB_PATH}")
