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
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS conversations (
                id          TEXT PRIMARY KEY,
                client_id   TEXT NOT NULL,
                user_id     INTEGER DEFAULT NULL,
                title       TEXT NOT NULL DEFAULT 'New Chat',
                created_at  TEXT NOT NULL,
                updated_at  TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );

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
        _migrate_add_users_table(conn)

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

def _migrate_add_users_table(conn: sqlite3.Connection) -> None:
    """Migration: add user_id column if upgrading to authentication schema."""
    cursor = conn.execute("PRAGMA table_info(conversations)")
    columns = {row["name"] for row in cursor.fetchall()}
    if "user_id" not in columns:
        conn.execute("ALTER TABLE conversations ADD COLUMN user_id INTEGER DEFAULT NULL REFERENCES users(id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_conv_user ON conversations(user_id)")
    
    # Add index for efficient garbage collection queries O(log N)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_conv_updated_at ON conversations(updated_at)")

# -- Timestamp helper --------------------------------------------------------

def _now() -> str:
    """Current time in ISO-8601 format (UTC+8)."""
    return datetime.now(_TW_TZ).isoformat()


# -- User CRUD ---------------------------------------------------------------

def create_user(username: str, password_hash: str) -> dict:
    """Insert a new user and return their profile."""
    conn = _connect()
    try:
        now = _now()
        cursor = conn.execute(
            "INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)",
            (username, password_hash, now)
        )
        conn.commit()
        return {"id": cursor.lastrowid, "username": username, "created_at": now}
    finally:
        conn.close()

def get_user_by_username(username: str) -> dict | None:
    """Retrieve a user by username, including their password hash."""
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT id, username, password_hash, created_at FROM users WHERE username = ?",
            (username,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()

def bind_guest_conversations(client_id: str, user_id: int) -> int:
    """Merge anonymous conversations into the authenticated user's account."""
    conn = _connect()
    try:
        cursor = conn.execute(
            "UPDATE conversations SET user_id = ? WHERE client_id = ? AND user_id IS NULL",
            (user_id, client_id)
        )
        conn.commit()
        return cursor.rowcount
    finally:
        conn.close()

# -- Conversation CRUD -------------------------------------------------------

def create_conversation(client_id: str, title: str = "New Chat", user_id: int | None = None) -> dict:
    """Insert a new conversation belonging to client_id."""
    conn = _connect()
    try:
        if user_id is None:
            # Enforce single conversation for guests: delete any existing local sessions
            conn.execute("DELETE FROM conversations WHERE client_id = ? AND user_id IS NULL", (client_id,))
            
        cid = str(uuid.uuid4())
        now = _now()
        conn.execute(
            "INSERT INTO conversations (id, client_id, user_id, title, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (cid, client_id, user_id, title, now, now),
        )
        conn.commit()
        return {"id": cid, "title": title, "created_at": now, "updated_at": now}
    finally:
        conn.close()

def gc_guest_conversations() -> None:
    """Garbage Collection: Delete orphaned guest conversations older than 24 hours."""
    conn = _connect()
    try:
        cutoff = (datetime.now(_TW_TZ) - timedelta(hours=24)).isoformat()
        conn.execute(
            "DELETE FROM conversations WHERE user_id IS NULL AND updated_at < ?",
            (cutoff,)
        )
        conn.commit()
    except Exception as e:
        print(f"Background GC error: {e}")
    finally:
        conn.close()

def list_conversations(client_id: str, user_id: int | None = None) -> list[dict]:
    """Return all conversations for a given client, newest first."""
    conn = _connect()
    try:
        if user_id is not None:
            query = "SELECT id, title, created_at, updated_at FROM conversations WHERE user_id = ? ORDER BY updated_at DESC"
            params = (user_id,)
        else:
            query = "SELECT id, title, created_at, updated_at FROM conversations WHERE client_id = ? AND user_id IS NULL ORDER BY updated_at DESC"
            params = (client_id,)
            
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_conversation(conversation_id: str, client_id: str, user_id: int | None = None) -> dict | None:
    """Return a conversation with messages, or None if not found / not owned."""
    conn = _connect()
    try:
        if user_id is not None:
            query = "SELECT id, title, created_at, updated_at FROM conversations WHERE id = ? AND user_id = ?"
            params = (conversation_id, user_id)
        else:
            query = "SELECT id, title, created_at, updated_at FROM conversations WHERE id = ? AND client_id = ? AND user_id IS NULL"
            params = (conversation_id, client_id)
            
        row = conn.execute(query, params).fetchone()
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


def delete_conversation(conversation_id: str, client_id: str, user_id: int | None = None) -> bool:
    """Delete a conversation only if it belongs to client_id."""
    conn = _connect()
    try:
        if user_id is not None:
            query = "DELETE FROM conversations WHERE id = ? AND user_id = ?"
            params = (conversation_id, user_id)
        else:
            query = "DELETE FROM conversations WHERE id = ? AND client_id = ? AND user_id IS NULL"
            params = (conversation_id, client_id)
            
        cursor = conn.execute(query, params)
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

        cursor = conn.execute(
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
        msg_id = cursor.lastrowid
        conn.execute(
            "UPDATE conversations SET updated_at = ? WHERE id = ?",
            (now, conversation_id),
        )
        conn.commit()

        return {
            "id": msg_id, "role": role, "content": content, "model": model,
            "elapsed_seconds": elapsed_seconds,
            "tools_used": tools_used or [],
            "use_rag": use_rag, "created_at": now,
        }
    finally:
        conn.close()

def edit_message_and_truncate(conversation_id: str, message_id: int, new_content: str) -> None:
    """Update a user message and delete all subsequent messages in the conversation."""
    conn = _connect()
    try:
        conn.execute(
            "UPDATE messages SET content = ? WHERE id = ? AND conversation_id = ? AND role = 'user'",
            (new_content, message_id, conversation_id),
        )
        conn.execute(
            "DELETE FROM messages WHERE conversation_id = ? AND id > ?",
            (conversation_id, message_id),
        )
        conn.execute(
            "UPDATE conversations SET updated_at = ? WHERE id = ?",
            (_now(), conversation_id),
        )
        conn.commit()
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
