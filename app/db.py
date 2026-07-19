import os
import sqlite3
import time
from contextlib import contextmanager

DB_PATH = os.environ.get("DB_PATH", "/data/db/app.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS clusters (
    name TEXT PRIMARY KEY,
    kind TEXT NOT NULL,            -- 'incluster' | 'token' | 'kubeconfig'
    api_server TEXT,
    token TEXT,
    verify_ssl INTEGER DEFAULT 1,
    kubeconfig TEXT,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS companion_questions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cluster TEXT NOT NULL,
    namespace TEXT,
    kind TEXT NOT NULL,
    name TEXT NOT NULL,
    question TEXT NOT NULL,
    answer TEXT,
    created_at REAL NOT NULL,
    answered_at REAL
);
"""


@contextmanager
def get_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with get_db() as conn:
        conn.executescript(SCHEMA)
        conn.execute(
            "INSERT OR IGNORE INTO clusters (name, kind, created_at) VALUES (?, 'incluster', ?)",
            ("pxinf (in-cluster)", time.time()),
        )


def list_clusters():
    with get_db() as conn:
        return [dict(r) for r in conn.execute("SELECT name, kind, api_server, created_at FROM clusters ORDER BY created_at")]


def get_cluster(name: str):
    with get_db() as conn:
        row = conn.execute("SELECT * FROM clusters WHERE name = ?", (name,)).fetchone()
        return dict(row) if row else None


def add_cluster(name: str, kind: str, api_server: str | None, token: str | None, verify_ssl: bool, kubeconfig: str | None):
    with get_db() as conn:
        conn.execute(
            "INSERT INTO clusters (name, kind, api_server, token, verify_ssl, kubeconfig, created_at) VALUES (?,?,?,?,?,?,?)",
            (name, kind, api_server, token, int(verify_ssl), kubeconfig, time.time()),
        )


def submit_question(cluster: str, namespace: str | None, kind: str, name: str, question: str) -> int:
    with get_db() as conn:
        cur = conn.execute(
            "INSERT INTO companion_questions (cluster, namespace, kind, name, question, created_at) VALUES (?,?,?,?,?,?)",
            (cluster, namespace, kind, name, question, time.time()),
        )
        return cur.lastrowid


def get_question(qid: int):
    with get_db() as conn:
        row = conn.execute("SELECT * FROM companion_questions WHERE id = ?", (qid,)).fetchone()
        return dict(row) if row else None


def list_pending_questions():
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM companion_questions WHERE answer IS NULL ORDER BY created_at").fetchall()
        return [dict(r) for r in rows]


def list_recent_questions(cluster: str, namespace: str, kind: str, name: str, limit: int = 10):
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM companion_questions WHERE cluster=? AND kind=? AND name=? AND (namespace=? OR namespace IS NULL) "
            "ORDER BY created_at DESC LIMIT ?",
            (cluster, kind, name, namespace, limit),
        ).fetchall()
        return [dict(r) for r in rows]


def answer_question(qid: int, answer: str):
    with get_db() as conn:
        conn.execute(
            "UPDATE companion_questions SET answer = ?, answered_at = ? WHERE id = ?",
            (answer, time.time(), qid),
        )
