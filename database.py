"""SQLite persistence layer for conversation history.

Stores conversations and messages per client (identified by a
browser-generated UUID).  Uses Python's built-in sqlite3 module
with WAL mode for concurrent read performance.

Database file: ./chat_history.db
"""

import json
import sqlite3
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path

# -- Config ------------------------------------------------------------------

DB_PATH = Path(__file__).parent / "chat_history.db"
_TW_TZ = timezone(timedelta(hours=8))


# -- Connection helper -------------------------------------------------------

def _connect(db_path: Path = DB_PATH) -> sqlite3.Connection:
    """Open a connection with row-factory, WAL mode, and FK enforcement."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


# -- Schema ------------------------------------------------------------------

def init_db(db_path: Path = DB_PATH) -> None:
    """Create tables and run lightweight migrations if needed."""
    conn = _connect(db_path)
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS conversations (
                id          TEXT PRIMARY KEY,
                client_id   TEXT NOT NULL,
                title       TEXT NOT NULL DEFAULT 'New Chat',
                created_at  TEXT NOT NULL,
                updated_at  TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_conv_client
                ON conversations(client_id);

            CREATE TABLE IF NOT EXISTS messages (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id   TEXT    NOT NULL,
                role              TEXT    NOT NULL,
                content           TEXT    NOT NULL,
                model             TEXT,
                elapsed_seconds   REAL,
                tools_used        TEXT,
                use_rag           INTEGER,
                created_at        TEXT    NOT NULL,
                FOREIGN KEY (conversation_id)
                    REFERENCES conversations(id) ON DELETE CASCADE
            );
            """
        )

        # Migration: add client_id column if upgrading from v1 schema.
        _migrate_add_client_id(conn)

        conn.commit()
    finally:
        conn.close()


def _migrate_add_client_id(conn: sqlite3.Connection) -> None:
    """Backfill client_id for databases created before v2."""
    cursor = conn.execute("PRAGMA table_info(conversations)")
    columns = {row["name"] for row in cursor.fetchall()}
    if "client_id" not in columns:
        conn.execute("ALTER TABLE conversations ADD COLUMN client_id TEXT NOT NULL DEFAULT 'legacy'")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_conv_client ON conversations(client_id)")


# -- Timestamp helper --------------------------------------------------------

def _now() -> str:
    """Current time in ISO-8601 format (UTC+8)."""
    return datetime.now(_TW_TZ).isoformat()


# -- Conversation CRUD -------------------------------------------------------

def create_conversation(client_id: str, title: str = "New Chat") -> dict:
    """Insert a new conversation belonging to client_id."""
    conn = _connect()
    try:
        cid = str(uuid.uuid4())
        now = _now()
        conn.execute(
            "INSERT INTO conversations (id, client_id, title, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (cid, client_id, title, now, now),
        )
        conn.commit()
        return {"id": cid, "title": title, "created_at": now, "updated_at": now}
    finally:
        conn.close()


def list_conversations(client_id: str) -> list[dict]:
    """Return all conversations for a given client, newest first."""
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT id, title, created_at, updated_at "
            "FROM conversations WHERE client_id = ? ORDER BY updated_at DESC",
            (client_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_conversation(conversation_id: str, client_id: str) -> dict | None:
    """Return a conversation with messages, or None if not found / not owned."""
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT id, title, created_at, updated_at "
            "FROM conversations WHERE id = ? AND client_id = ?",
            (conversation_id, client_id),
        ).fetchone()
        if row is None:
            return None

        messages = conn.execute(
            "SELECT id, role, content, model, elapsed_seconds, "
            "tools_used, use_rag, created_at "
            "FROM messages WHERE conversation_id = ? ORDER BY id ASC",
            (conversation_id,),
        ).fetchall()

        conv = dict(row)
        conv["messages"] = [_serialise_message(m) for m in messages]
        return conv
    finally:
        conn.close()


def delete_conversation(conversation_id: str, client_id: str) -> bool:
    """Delete a conversation only if it belongs to client_id."""
    conn = _connect()
    try:
        cursor = conn.execute(
            "DELETE FROM conversations WHERE id = ? AND client_id = ?",
            (conversation_id, client_id),
        )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def update_conversation_title(conversation_id: str, title: str) -> None:
    """Set a new title and bump updated_at."""
    conn = _connect()
    try:
        conn.execute(
            "UPDATE conversations SET title = ?, updated_at = ? WHERE id = ?",
            (title, _now(), conversation_id),
        )
        conn.commit()
    finally:
        conn.close()


# -- Message CRUD ------------------------------------------------------------

def add_message(
    conversation_id: str,
    role: str,
    content: str,
    *,
    model: str | None = None,
    elapsed_seconds: float | None = None,
    tools_used: list[str] | None = None,
    use_rag: bool | None = None,
) -> dict:
    """Append a message and bump the parent conversation's updated_at."""
    conn = _connect()
    try:
        now = _now()
        tools_json = json.dumps(tools_used) if tools_used else None

        conn.execute(
            "INSERT INTO messages "
            "(conversation_id, role, content, model, elapsed_seconds, "
            "tools_used, use_rag, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                conversation_id, role, content, model,
                elapsed_seconds, tools_json,
                int(use_rag) if use_rag is not None else None, now,
            ),
        )
        conn.execute(
            "UPDATE conversations SET updated_at = ? WHERE id = ?",
            (now, conversation_id),
        )
        conn.commit()

        return {
            "role": role, "content": content, "model": model,
            "elapsed_seconds": elapsed_seconds,
            "tools_used": tools_used or [],
            "use_rag": use_rag, "created_at": now,
        }
    finally:
        conn.close()


# -- Serialisation -----------------------------------------------------------

def _serialise_message(row: sqlite3.Row) -> dict:
    """Convert a DB row to a JSON-safe dict."""
    d = dict(row)
    raw = d.get("tools_used")
    d["tools_used"] = json.loads(raw) if raw else []
    d["use_rag"] = bool(d["use_rag"]) if d["use_rag"] is not None else None
    return d
