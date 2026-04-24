import sqlite3
from pathlib import Path

from flask import current_app, g


SCHEMA = """
CREATE TABLE IF NOT EXISTS todos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    is_complete INTEGER NOT NULL DEFAULT 0,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at DATETIME
);

CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    transcript TEXT NOT NULL,
    summary TEXT,
    audio_path TEXT,
    source TEXT NOT NULL DEFAULT 'device',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS interactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    transcript TEXT,
    assistant_response TEXT,
    status TEXT NOT NULL DEFAULT 'received',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


def get_db():
    db = g.get("_database")
    if db is None:
        db_path = current_app.config["DATABASE_PATH"]
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(db_path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA busy_timeout = 10000")
        g._database = db
    return db


def close_db(_exception=None):
    db = g.pop("_database", None)
    if db is not None:
        db.close()


def init_db():
    db = get_db()
    db.executescript(SCHEMA)
    db.commit()


def init_app(app):
    app.teardown_appcontext(close_db)
    with app.app_context():
        init_db()


def rows_to_dicts(rows):
    return [dict(row) for row in rows]


def fetch_todos(limit=None, include_completed=True):
    db = get_db()
    query = """
        SELECT id, title, is_complete, created_at, completed_at
        FROM todos
    """
    params = []
    clauses = []

    if not include_completed:
        clauses.append("is_complete = 0")

    if clauses:
        query += " WHERE " + " AND ".join(clauses)

    query += " ORDER BY is_complete ASC, created_at DESC"

    if limit is not None:
        query += " LIMIT ?"
        params.append(limit)

    return rows_to_dicts(db.execute(query, params).fetchall())


def insert_todo(title):
    db = get_db()
    cursor = db.execute(
        "INSERT INTO todos (title) VALUES (?)",
        (title,),
    )
    db.commit()
    return fetch_todo(cursor.lastrowid)


def fetch_todo(todo_id):
    db = get_db()
    row = db.execute(
        """
        SELECT id, title, is_complete, created_at, completed_at
        FROM todos
        WHERE id = ?
        """,
        (todo_id,),
    ).fetchone()
    return dict(row) if row else None


def update_todo_title(todo_id, title):
    db = get_db()
    db.execute(
        """
        UPDATE todos
        SET title = ?
        WHERE id = ?
        """,
        (title, todo_id),
    )
    db.commit()
    return fetch_todo(todo_id)


def mark_todo_complete(todo_id):
    db = get_db()
    db.execute(
        """
        UPDATE todos
        SET is_complete = 1, completed_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (todo_id,),
    )
    db.commit()
    return fetch_todo(todo_id)


def delete_todo(todo_id):
    db = get_db()
    existing = fetch_todo(todo_id)
    if existing is None:
        return None
    db.execute("DELETE FROM todos WHERE id = ?", (todo_id,))
    db.commit()
    return existing


def clear_todos():
    db = get_db()
    db.execute("DELETE FROM todos")
    db.commit()


def fetch_notes(limit=None):
    db = get_db()
    query = """
        SELECT id, transcript, summary, audio_path, source, created_at
        FROM notes
        ORDER BY created_at DESC
    """
    params = []
    if limit is not None:
        query += " LIMIT ?"
        params.append(limit)
    return rows_to_dicts(db.execute(query, params).fetchall())


def fetch_note(note_id):
    db = get_db()
    row = db.execute(
        """
        SELECT id, transcript, summary, audio_path, source, created_at
        FROM notes
        WHERE id = ?
        """,
        (note_id,),
    ).fetchone()
    return dict(row) if row else None


def delete_note(note_id):
    db = get_db()
    existing = fetch_note(note_id)
    if existing is None:
        return None
    db.execute("DELETE FROM notes WHERE id = ?", (note_id,))
    db.commit()
    return existing


def clear_notes():
    db = get_db()
    db.execute("DELETE FROM notes")
    db.commit()


def insert_note(transcript, summary=None, audio_path=None, source="device"):
    db = get_db()
    cursor = db.execute(
        """
        INSERT INTO notes (transcript, summary, audio_path, source)
        VALUES (?, ?, ?, ?)
        """,
        (transcript, summary, audio_path, source),
    )
    db.commit()
    row = db.execute(
        """
        SELECT id, transcript, summary, audio_path, source, created_at
        FROM notes
        WHERE id = ?
        """,
        (cursor.lastrowid,),
    ).fetchone()
    return dict(row)


def fetch_interactions(limit=None):
    db = get_db()
    query = """
        SELECT id, transcript, assistant_response, status, created_at
        FROM interactions
        ORDER BY created_at DESC
    """
    params = []
    if limit is not None:
        query += " LIMIT ?"
        params.append(limit)
    return rows_to_dicts(db.execute(query, params).fetchall())


def fetch_interaction(interaction_id):
    db = get_db()
    row = db.execute(
        """
        SELECT id, transcript, assistant_response, status, created_at
        FROM interactions
        WHERE id = ?
        """,
        (interaction_id,),
    ).fetchone()
    return dict(row) if row else None


def insert_interaction(transcript=None, assistant_response=None, status="received"):
    db = get_db()
    cursor = db.execute(
        """
        INSERT INTO interactions (transcript, assistant_response, status)
        VALUES (?, ?, ?)
        """,
        (transcript, assistant_response, status),
    )
    db.commit()
    row = db.execute(
        """
        SELECT id, transcript, assistant_response, status, created_at
        FROM interactions
        WHERE id = ?
        """,
        (cursor.lastrowid,),
    ).fetchone()
    return dict(row)


def update_interaction(interaction_id, transcript=None, assistant_response=None, status=None):
    db = get_db()
    current = db.execute(
        """
        SELECT id, transcript, assistant_response, status, created_at
        FROM interactions
        WHERE id = ?
        """,
        (interaction_id,),
    ).fetchone()

    if current is None:
        return None

    transcript = current["transcript"] if transcript is None else transcript
    assistant_response = (
        current["assistant_response"] if assistant_response is None else assistant_response
    )
    status = current["status"] if status is None else status

    db.execute(
        """
        UPDATE interactions
        SET transcript = ?, assistant_response = ?, status = ?
        WHERE id = ?
        """,
        (transcript, assistant_response, status, interaction_id),
    )
    db.commit()

    row = db.execute(
        """
        SELECT id, transcript, assistant_response, status, created_at
        FROM interactions
        WHERE id = ?
        """,
        (interaction_id,),
    ).fetchone()
    return dict(row)


def delete_interaction(interaction_id):
    db = get_db()
    existing = fetch_interaction(interaction_id)
    if existing is None:
        return None
    db.execute("DELETE FROM interactions WHERE id = ?", (interaction_id,))
    db.commit()
    return existing


def clear_interactions():
    db = get_db()
    db.execute("DELETE FROM interactions")
    db.commit()
