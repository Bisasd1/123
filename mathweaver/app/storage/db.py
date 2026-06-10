"""存储:sqlite3(每次操作独立连接,线程安全)+ 文件产物目录。"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

from app.core.config import VAR_DIR
from app.ir import new_id

DB_PATH = VAR_DIR / "mathweaver.db"
ARTIFACTS_DIR = VAR_DIR / "artifacts"

SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
  id TEXT PRIMARY KEY, title TEXT, created_at REAL, updated_at REAL
);
CREATE TABLE IF NOT EXISTS messages (
  id TEXT PRIMARY KEY, conv_id TEXT, role TEXT, content TEXT,
  meta TEXT, created_at REAL
);
CREATE TABLE IF NOT EXISTS graphs (
  id TEXT PRIMARY KEY, conv_id TEXT, problem_id TEXT, version INTEGER,
  parent_version_id TEXT, data TEXT, created_at REAL
);
CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conv_id, created_at);
CREATE INDEX IF NOT EXISTS idx_graphs_conv ON graphs(conv_id, created_at);
"""


def _conn() -> sqlite3.Connection:
    VAR_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _conn() as c:
        c.executescript(SCHEMA)
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)


# ----------------------------------------------------------------- 会话/消息

def create_conversation(title: str = "新会话") -> dict:
    cid, now = new_id("conv"), time.time()
    with _conn() as c:
        c.execute("INSERT INTO conversations VALUES (?,?,?,?)", (cid, title, now, now))
    return {"id": cid, "title": title, "created_at": now, "updated_at": now}


def list_conversations() -> list[dict]:
    with _conn() as c:
        rows = c.execute("SELECT * FROM conversations ORDER BY updated_at DESC").fetchall()
    return [dict(r) for r in rows]


def touch_conversation(conv_id: str, title: str | None = None) -> None:
    with _conn() as c:
        if title:
            c.execute("UPDATE conversations SET updated_at=?, title=? WHERE id=?",
                      (time.time(), title[:60], conv_id))
        else:
            c.execute("UPDATE conversations SET updated_at=? WHERE id=?",
                      (time.time(), conv_id))


def delete_conversation(conv_id: str) -> None:
    with _conn() as c:
        for table in ("messages", "graphs"):
            c.execute(f"DELETE FROM {table} WHERE conv_id=?", (conv_id,))
        c.execute("DELETE FROM conversations WHERE id=?", (conv_id,))


def add_message(conv_id: str, role: str, content: str, meta: dict | None = None) -> dict:
    mid, now = new_id("msg"), time.time()
    with _conn() as c:
        c.execute("INSERT INTO messages VALUES (?,?,?,?,?,?)",
                  (mid, conv_id, role, content,
                   json.dumps(meta or {}, ensure_ascii=False), now))
    touch_conversation(conv_id)
    return {"id": mid, "conv_id": conv_id, "role": role, "content": content,
            "meta": meta or {}, "created_at": now}


def list_messages(conv_id: str) -> list[dict]:
    with _conn() as c:
        rows = c.execute("SELECT * FROM messages WHERE conv_id=? ORDER BY created_at",
                         (conv_id,)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["meta"] = json.loads(d.get("meta") or "{}")
        out.append(d)
    return out


# ----------------------------------------------------------------- 图谱版本

def save_graph(conv_id: str, graph_dict: dict) -> None:
    with _conn() as c:
        c.execute("INSERT OR REPLACE INTO graphs VALUES (?,?,?,?,?,?,?)",
                  (graph_dict["id"], conv_id, graph_dict.get("problem_id", ""),
                   graph_dict.get("version", 1), graph_dict.get("parent_version_id"),
                   json.dumps(graph_dict, ensure_ascii=False), time.time()))
    touch_conversation(conv_id)


def get_graph(graph_id: str) -> dict | None:
    with _conn() as c:
        row = c.execute("SELECT data FROM graphs WHERE id=?", (graph_id,)).fetchone()
    return json.loads(row["data"]) if row else None


def graph_conv(graph_id: str) -> str | None:
    with _conn() as c:
        row = c.execute("SELECT conv_id FROM graphs WHERE id=?", (graph_id,)).fetchone()
    return row["conv_id"] if row else None


def list_graphs(conv_id: str) -> list[dict]:
    with _conn() as c:
        rows = c.execute(
            "SELECT id, version, parent_version_id, created_at FROM graphs "
            "WHERE conv_id=? ORDER BY created_at", (conv_id,)).fetchall()
    return [dict(r) for r in rows]
