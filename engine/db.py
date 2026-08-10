"""Stato persistente: fatti generati, post pubblicati, metriche.

SQLite perché il dato è piccolo, relazionale e deve sopravvivere ai riavvii.
La tabella `facts` è anche la memoria anti-duplicato: senza questa la pipeline
ripubblica le stesse tre curiosità in loop.
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import DATA_DIR

DB_PATH = DATA_DIR / "engine.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS facts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at    REAL NOT NULL,
    niche         TEXT NOT NULL,
    hook          TEXT NOT NULL,
    fact          TEXT NOT NULL,
    detail        TEXT NOT NULL,
    kicker        TEXT NOT NULL,
    source_hint   TEXT NOT NULL DEFAULT '',
    keywords      TEXT NOT NULL DEFAULT '[]',
    verdict       TEXT NOT NULL DEFAULT 'unverified',
    confidence    REAL NOT NULL DEFAULT 0.0,
    verify_note   TEXT NOT NULL DEFAULT '',
    status        TEXT NOT NULL DEFAULT 'new'
        -- new | rejected | approved | rendered | published | failed
);

CREATE TABLE IF NOT EXISTS posts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    fact_id       INTEGER NOT NULL REFERENCES facts(id),
    created_at    REAL NOT NULL,
    format        TEXT NOT NULL,          -- carousel | single
    caption       TEXT NOT NULL,
    hashtags      TEXT NOT NULL DEFAULT '[]',
    image_paths   TEXT NOT NULL DEFAULT '[]',
    image_urls    TEXT NOT NULL DEFAULT '[]',
    ig_media_id   TEXT,
    tiktok_id     TEXT,
    published_at  REAL,
    status        TEXT NOT NULL DEFAULT 'draft'
        -- draft | pending_review | approved | published | failed
);

CREATE TABLE IF NOT EXISTS metrics (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id       INTEGER NOT NULL REFERENCES posts(id),
    collected_at  REAL NOT NULL,
    reach         INTEGER DEFAULT 0,
    likes         INTEGER DEFAULT 0,
    comments      INTEGER DEFAULT 0,
    saves         INTEGER DEFAULT 0,
    shares        INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_facts_status ON facts(status);
CREATE INDEX IF NOT EXISTS idx_posts_status ON posts(status);
"""

# Aggiunte dopo la prima versione dello schema. SQLite non ha
# "ADD COLUMN IF NOT EXISTS", quindi si tenta e si ignora l'errore.
MIGRATIONS = [
    # Il testo delle slide: senza, un ritocco al CSS obbliga a rigenerare
    # tutto via API. Con questo si ri-renderizza a costo zero.
    "ALTER TABLE posts ADD COLUMN slides TEXT NOT NULL DEFAULT '[]'",
]


def connect(path: Optional[Path] = None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path or DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    for statement in MIGRATIONS:
        try:
            conn.execute(statement)
        except sqlite3.OperationalError:
            pass          # colonna già presente
    conn.commit()
    return conn


# ─── facts ────────────────────────────────────────────────────────────────────

def insert_fact(conn: sqlite3.Connection, niche: str, f: Dict[str, Any]) -> int:
    cur = conn.execute(
        """INSERT INTO facts
           (created_at, niche, hook, fact, detail, kicker, source_hint, keywords)
           VALUES (?,?,?,?,?,?,?,?)""",
        (
            time.time(),
            niche,
            f["hook"],
            f["fact"],
            f["detail"],
            f["kicker"],
            f.get("source_hint", ""),
            json.dumps(f.get("keywords", [])),
        ),
    )
    conn.commit()
    return int(cur.lastrowid)


def set_verification(
    conn: sqlite3.Connection, fact_id: int, verdict: str, confidence: float, note: str
) -> None:
    status = "approved" if verdict == "supported" else "rejected"
    conn.execute(
        "UPDATE facts SET verdict=?, confidence=?, verify_note=?, status=? WHERE id=?",
        (verdict, confidence, note, status, fact_id),
    )
    conn.commit()


def set_fact_status(conn: sqlite3.Connection, fact_id: int, status: str) -> None:
    conn.execute("UPDATE facts SET status=? WHERE id=?", (status, fact_id))
    conn.commit()


def next_approved_fact(conn: sqlite3.Connection) -> Optional[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM facts WHERE status='approved' ORDER BY confidence DESC, id ASC LIMIT 1"
    ).fetchone()


def all_published_texts(conn: sqlite3.Connection) -> List[str]:
    """Corpus anti-duplicato: tutto ciò che è già uscito o è in coda."""
    rows = conn.execute(
        "SELECT fact FROM facts WHERE status IN ('approved','rendered','published')"
    ).fetchall()
    return [r["fact"] for r in rows]


# ─── posts ────────────────────────────────────────────────────────────────────

def insert_post(
    conn: sqlite3.Connection,
    fact_id: int,
    fmt: str,
    caption: str,
    hashtags: List[str],
    image_paths: List[str],
    status: str = "draft",
    slides: Optional[List[Dict[str, Any]]] = None,
) -> int:
    cur = conn.execute(
        """INSERT INTO posts
           (fact_id, created_at, format, caption, hashtags, image_paths, status, slides)
           VALUES (?,?,?,?,?,?,?,?)""",
        (
            fact_id,
            time.time(),
            fmt,
            caption,
            json.dumps(hashtags),
            json.dumps(image_paths),
            status,
            json.dumps(slides or []),
        ),
    )
    conn.commit()
    return int(cur.lastrowid)


def set_post_urls(conn: sqlite3.Connection, post_id: int, urls: List[str]) -> None:
    conn.execute(
        "UPDATE posts SET image_urls=? WHERE id=?", (json.dumps(urls), post_id)
    )
    conn.commit()


def set_post_status(conn: sqlite3.Connection, post_id: int, status: str) -> None:
    conn.execute("UPDATE posts SET status=? WHERE id=?", (status, post_id))
    conn.commit()


def mark_published(
    conn: sqlite3.Connection,
    post_id: int,
    ig_media_id: Optional[str] = None,
    tiktok_id: Optional[str] = None,
) -> None:
    conn.execute(
        """UPDATE posts SET status='published', published_at=?,
           ig_media_id=COALESCE(?, ig_media_id),
           tiktok_id=COALESCE(?, tiktok_id) WHERE id=?""",
        (time.time(), ig_media_id, tiktok_id, post_id),
    )
    conn.commit()


def get_post(conn: sqlite3.Connection, post_id: int) -> Optional[sqlite3.Row]:
    return conn.execute("SELECT * FROM posts WHERE id=?", (post_id,)).fetchone()


def posts_by_status(conn: sqlite3.Connection, status: str) -> List[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM posts WHERE status=? ORDER BY id ASC", (status,)
    ).fetchall()


def published_posts(conn: sqlite3.Connection) -> List[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM posts WHERE status='published' AND ig_media_id IS NOT NULL"
    ).fetchall()


# ─── metrics ──────────────────────────────────────────────────────────────────

def insert_metrics(conn: sqlite3.Connection, post_id: int, m: Dict[str, int]) -> None:
    conn.execute(
        """INSERT INTO metrics (post_id, collected_at, reach, likes, comments, saves, shares)
           VALUES (?,?,?,?,?,?,?)""",
        (
            post_id,
            time.time(),
            m.get("reach", 0),
            m.get("likes", 0),
            m.get("comments", 0),
            m.get("saves", 0),
            m.get("shares", 0),
        ),
    )
    conn.commit()


def top_performers(conn: sqlite3.Connection, limit: int = 10) -> List[sqlite3.Row]:
    """I post con più salvataggi per reach — il segnale che conta davvero su IG."""
    return conn.execute(
        """
        SELECT f.hook, f.fact, f.keywords,
               MAX(m.reach) AS reach, MAX(m.saves) AS saves,
               CAST(MAX(m.saves) AS REAL) / NULLIF(MAX(m.reach), 0) AS save_rate
        FROM metrics m
        JOIN posts p ON p.id = m.post_id
        JOIN facts f ON f.id = p.fact_id
        GROUP BY p.id
        HAVING reach > 0
        ORDER BY save_rate DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
