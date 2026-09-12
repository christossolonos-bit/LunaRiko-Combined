"""SQLite store for Luna live memory — turns + working/durable/profiles.

Per character under MEMORY_DIR/live_memory.db. No OpenAI; no FAISS here.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Optional

from server.config import MEMORY_DIR

_DB_NAME = "live_memory.db"
_lock = threading.Lock()
_conn: Optional[sqlite3.Connection] = None


def _db_path() -> Path:
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    return MEMORY_DIR / _DB_NAME


def _connect() -> sqlite3.Connection:
    global _conn
    if _conn is not None:
        return _conn
    path = _db_path()
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS turns (
            seq INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL NOT NULL,
            source TEXT NOT NULL,
            user_id TEXT,
            display TEXT,
            role TEXT,
            speaker TEXT NOT NULL,
            body TEXT NOT NULL,
            consolidated INTEGER NOT NULL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_turns_consolidated ON turns(consolidated, seq);
        CREATE INDEX IF NOT EXISTS idx_turns_user ON turns(user_id, seq);

        CREATE TABLE IF NOT EXISTS docs (
            key TEXT PRIMARY KEY,
            content TEXT NOT NULL,
            updated_at REAL NOT NULL
        );
        """
    )
    conn.commit()
    _conn = conn
    return conn


def append_turn(
    *,
    source: str,
    body: str,
    speaker: str,
    user_id: Optional[str] = None,
    display: Optional[str] = None,
    role: Optional[str] = None,
    ts: Optional[float] = None,
) -> int:
    """Persist one turn. Returns seq."""
    text = (body or "").strip()
    if not text:
        return 0
    with _lock:
        conn = _connect()
        cur = conn.execute(
            """
            INSERT INTO turns (ts, source, user_id, display, role, speaker, body, consolidated)
            VALUES (?, ?, ?, ?, ?, ?, ?, 0)
            """,
            (
                float(ts if ts is not None else time.time()),
                (source or "unknown")[:32],
                (user_id or None),
                (display or None),
                (role or None),
                (speaker or display or user_id or "someone")[:128],
                text[:2000],
            ),
        )
        conn.commit()
        return int(cur.lastrowid or 0)


def unconsolidated_turns(limit: int = 80) -> list[dict[str, Any]]:
    with _lock:
        conn = _connect()
        rows = conn.execute(
            """
            SELECT seq, ts, source, user_id, display, role, speaker, body
            FROM turns
            WHERE consolidated = 0
            ORDER BY seq ASC
            LIMIT ?
            """,
            (max(1, min(int(limit), 200)),),
        ).fetchall()
    return [dict(r) for r in rows]


def mark_consolidated(up_to_seq: int) -> None:
    if up_to_seq <= 0:
        return
    with _lock:
        conn = _connect()
        conn.execute(
            "UPDATE turns SET consolidated = 1 WHERE consolidated = 0 AND seq <= ?",
            (int(up_to_seq),),
        )
        conn.commit()


def recent_turns(limit: int = 30, before_seq: Optional[int] = None) -> list[dict[str, Any]]:
    with _lock:
        conn = _connect()
        if before_seq is not None:
            rows = conn.execute(
                """
                SELECT seq, ts, source, user_id, display, role, speaker, body
                FROM turns
                WHERE seq < ?
                ORDER BY seq DESC
                LIMIT ?
                """,
                (int(before_seq), max(1, min(int(limit), 100))),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT seq, ts, source, user_id, display, role, speaker, body
                FROM turns
                ORDER BY seq DESC
                LIMIT ?
                """,
                (max(1, min(int(limit), 100)),),
            ).fetchall()
    out = [dict(r) for r in rows]
    out.reverse()
    return out


def search_turns(query: str, *, user_id: Optional[str] = None, limit: int = 40) -> list[dict[str, Any]]:
    q = (query or "").strip()
    if not q:
        return recent_turns(limit=limit)
    tokens = [t for t in q.split() if len(t) > 2][:6] or [q]
    clauses = " OR ".join(["body LIKE ?"] * len(tokens))
    likes = [f"%{t}%" for t in tokens]
    with _lock:
        conn = _connect()
        if user_id:
            rows = conn.execute(
                f"""
                SELECT seq, ts, source, user_id, display, role, speaker, body
                FROM turns
                WHERE ({clauses}) AND user_id = ?
                ORDER BY seq DESC
                LIMIT ?
                """,
                (*likes, str(user_id), max(1, min(int(limit), 100))),
            ).fetchall()
        else:
            rows = conn.execute(
                f"""
                SELECT seq, ts, source, user_id, display, role, speaker, body
                FROM turns
                WHERE ({clauses})
                ORDER BY seq DESC
                LIMIT ?
                """,
                (*likes, max(1, min(int(limit), 100))),
            ).fetchall()
    out = [dict(r) for r in rows]
    out.reverse()
    return out


def get_doc(key: str) -> str:
    with _lock:
        conn = _connect()
        row = conn.execute(
            "SELECT content FROM docs WHERE key = ?", (key,)
        ).fetchone()
    return (row["content"] if row else "") or ""


def set_doc(key: str, content: str) -> None:
    with _lock:
        conn = _connect()
        conn.execute(
            """
            INSERT INTO docs (key, content, updated_at) VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
              content = excluded.content,
              updated_at = excluded.updated_at
            """,
            (key, content or "", time.time()),
        )
        conn.commit()


def get_profile(user_id: str) -> Optional[dict[str, Any]]:
    raw = get_doc(f"profile:{user_id}")
    if not raw:
        return None
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        return None


def save_profile(user_id: str, fields: dict[str, Any]) -> dict[str, Any]:
    existing = get_profile(user_id) or {}
    merged = {**existing}
    for k, v in (fields or {}).items():
        if v is None:
            continue
        s = str(v).strip()
        if s:
            merged[k] = s
    set_doc(f"profile:{user_id}", json.dumps(merged, ensure_ascii=False))
    return merged


def replace_profile(user_id: str, fields: dict[str, Any]) -> dict[str, Any]:
    """Overwrite a profile card (no merge)."""
    cleaned = {
        k: str(v).strip()
        for k, v in (fields or {}).items()
        if v is not None and str(v).strip()
    }
    set_doc(f"profile:{user_id}", json.dumps(cleaned, ensure_ascii=False))
    return cleaned


def delete_doc(key: str) -> None:
    with _lock:
        conn = _connect()
        conn.execute("DELETE FROM docs WHERE key = ?", (key,))
        conn.commit()


def delete_profile(user_id: str) -> None:
    delete_doc(f"profile:{user_id}")


def delete_turns_for_user_ids(user_ids: list[str]) -> int:
    ids = [str(u) for u in user_ids if u]
    if not ids:
        return 0
    with _lock:
        conn = _connect()
        placeholders = ",".join("?" * len(ids))
        cur = conn.execute(
            f"DELETE FROM turns WHERE user_id IN ({placeholders})",
            ids,
        )
        conn.commit()
        return int(cur.rowcount or 0)


def turn_count() -> int:
    with _lock:
        conn = _connect()
        row = conn.execute("SELECT COUNT(*) AS n FROM turns").fetchone()
    return int(row["n"] if row else 0)
