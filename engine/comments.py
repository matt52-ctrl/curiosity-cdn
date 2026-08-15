"""Gestione dei commenti: lettura, classificazione, risposte.

Perché conta: rispondere nella prima ora dopo la pubblicazione è uno dei
segnali più forti che Instagram usa per decidere se continuare a distribuire
un post. Ogni risposta genera anche una notifica, che riporta la persona sul
post e allunga la sua vita.

⚠️ Scelta di progetto: **le risposte non vengono pubblicate da sole.**
Vengono redatte e mandate su Telegram per approvazione. Il motivo non è
prudenza generica: una risposta automatica sbagliata è pubblica, sta sotto al
tuo post per sempre, e su una pagina che si presenta come "fatti verificati"
una sola risposta a vanvera costa più di dieci risposte mancate.
Il caso peggiore è la correzione: se qualcuno segnala un errore reale e il bot
risponde difendendo il post, il danno è serio.

Chi vuole il pieno automatico può mettere `comments.require_approval: false`,
ma è sconsigliato finché non hai letto un centinaio di commenti veri.
"""
from __future__ import annotations

import json
import sqlite3
import time
from typing import Any, Dict, List, Optional

import httpx

from .config import cfg, require_env
from .llm import ask_json

GRAPH = "https://graph.facebook.com/v21.0"

REPLY_SCHEMA = {
    "type": "object",
    "properties": {
        "category": {
            "type": "string",
            "enum": ["question", "correction", "praise", "hostile", "spam", "other"],
        },
        "should_reply": {"type": "boolean"},
        "reply": {"type": "string"},
        "needs_human": {"type": "boolean"},
        "reason": {"type": "string"},
    },
    "required": ["category", "should_reply", "reply", "needs_human", "reason"],
    "additionalProperties": False,
}


def _system() -> str:
    return f"""You handle comments for {cfg.get('brand.name')} ({cfg.get('brand.handle')}),
an account that posts one carefully-checked psychology fact at a time.

VOICE
{cfg.get('voice.guide')}

SOUNDING HUMAN — this matters more than any individual reply
A single reply is never what exposes an automated account. The pattern is:
every reply the same length, the same opening move, the same unbroken
politeness, an answer for everyone, and total confidence every time.
So:
  - Vary the shape. Some replies are one clause. Some are two sentences.
    Never open two consecutive replies the same way, and never start with
    "Great question", "Thanks for asking", "Absolutely", or "Indeed".
  - Match the energy you are given. A three-word comment gets a short reply.
    A thoughtful paragraph earns a real answer. Replying at length to
    "interesting!" reads as a machine.
  - Be willing to say "I don't know", "that's outside what the study showed",
    or "fair point, the post oversimplified". Uncertainty is the single most
    human thing you can express, and this account can afford it.
  - Do not answer everything. Leaving a comment alone is normal.
  - Never mirror the commenter's exact words back at them. It is a tell.

Classify the comment, then decide whether to reply.

CATEGORIES
  question    asks something answerable — the highest-value reply
  correction  claims the post is wrong, misattributed, or oversimplified
  praise      positive but with no content to answer
  hostile     insult, bait, or bad-faith provocation
  spam        promotion, follow-for-follow, unrelated links
  other       anything else

WHEN TO REPLY (should_reply = true)
  - questions: nearly always. This is where replies earn their keep.
  - corrections: always, and set needs_human = true. Never defend the post
    reflexively. If the objection has merit, say so plainly.
  - praise: only if you can add one real detail. A reply that says "thank
    you!" is noise and reads as a bot.
  - hostile and spam: never. Do not engage.

REPLY RULES
  - Under 220 characters. Instagram replies are read on a phone.
  - Answer the actual question. Do not restate the post.
  - If you are not sure of the answer, say you are not sure. Never invent a
    study, a number, or a name to fill a reply — this account's entire value
    is that it does not do that.
  - CITATIONS COME FROM THE SOURCE ON RECORD, SHOWN ABOVE, AND NOWHERE ELSE.
    Not from what you remember. If someone asks for the source, you may name
    only what is on record, exactly as written there. If nothing is on
    record, say the source is in the archive rather than guessing at it, and
    do not produce an author, a year, or a journal from memory. A wrong year
    stated confidently in public is worse than no answer: it is the one
    mistake this account cannot survive making twice.
  - No emoji unless the comment used one first, then at most one.
  - Never ask them to follow, never link out, never pitch anything.

needs_human = true whenever the reply involves a factual correction, a
sensitive personal disclosure, anything medical, or anything you are not
confident about. Those must be read by a person before going out."""


# Chiedere al modello di "variare" non funziona: converge comunque sulla
# stessa forma — due frasi dichiarative di lunghezza simile. La variazione va
# imposta meccanicamente, estraendo una forma a sorte e vincolandolo a quella.
REPLY_SHAPES = [
    ("terse", "One short sentence. Under 12 words. No preamble."),
    ("detail", "Lead with a concrete specific from the study — a number, a name, "
               "a detail — then one sentence of context. Two sentences maximum."),
    ("hedge", "Answer, but name the limit of what the study actually shows. "
              "Two sentences. It is fine to sound less than certain."),
    ("question_back", "Answer in one clause, then ask them one genuine short "
                      "question back. Not a rhetorical one."),
    ("plain", "One sentence that answers the thing asked, and nothing else. "
              "No context, no elaboration."),
    ("aside", "Answer briefly, then add one adjacent detail from the research "
              "they did not ask for but would enjoy. Two sentences."),
]

# Deliberatamente fuori dal sorteggio: concedere il punto ha senso solo quando
# c'è un punto da concedere. Assegnata a caso su una domanda innocua produce
# risposte difensive ("è giusto chiedere il contesto…") su un commento che non
# contestava nulla. Sulle correzioni il modello sceglie da sé.
CORRECTION_SHAPE = (
    "correction_soft",
    "Concede what is fair in their point first, then add the one thing that "
    "still stands. Two sentences.",
)


def pick_shape(recent_shapes: Optional[List[str]] = None) -> tuple:
    """Estrae una forma evitando le ultime usate, così due risposte vicine non
    hanno mai lo stesso scheletro."""
    import random

    avoid = set(recent_shapes or [])
    pool = [s for s in REPLY_SHAPES if s[0] not in avoid] or REPLY_SHAPES
    return random.choice(pool)


def draft_reply(
    comment_text: str,
    post_hook: str,
    post_fact: str,
    fonte: str = "",
    recent_replies: Optional[List[str]] = None,
    commenter_history: Optional[List[str]] = None,
    recent_shapes: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Redige una risposta.

    `recent_replies` è ciò che impedisce l'effetto bot: senza vedere cosa ha
    già scritto, il modello converge sempre sulla stessa forma di frase.
    `commenter_history` evita di trattare come sconosciuto qualcuno che ha già
    commentato — rispondere due volte allo stesso modo alla stessa persona è
    il modo più veloce per farsi smascherare.
    """
    # La fonte va messa davanti al modello, non lasciata alla sua memoria.
    # Senza, alla domanda «source?» rispondeva citando uno studio con l'anno
    # sbagliato, pur avendo il divieto di inventare scritto nel prompt.
    riga_fonte = (
        f"\nTHE SOURCE ON RECORD FOR THIS POST: {fonte}" if fonte else
        "\nTHERE IS NO SOURCE ON RECORD FOR THIS POST."
    )
    parts = [
        f"A comment arrived on this post.\n\nPOST HOOK: {post_hook}\n"
        f"POST CLAIM: {post_fact}{riga_fonte}\n\nCOMMENT: {comment_text}"
    ]

    if recent_replies:
        shown = "\n".join(f'  - "{r}"' for r in recent_replies[-12:])
        parts.append(
            "Your last replies, most recent first. Do NOT reuse their opening "
            "words, their sentence shape, or their length. If they are all "
            "roughly the same size, break the pattern deliberately:\n" + shown
        )

    if commenter_history:
        prev = "\n".join(f'  - "{c}"' for c in commenter_history[-4:])
        parts.append(
            "This person has commented before. Do not greet them as a stranger "
            "or repeat what you already told them:\n" + prev
        )

    shape_name, shape_rule = pick_shape(recent_shapes)
    parts.append(
        f"REQUIRED SHAPE for this reply — follow it even if another shape would "
        f"feel more natural, unless the comment is a correction (then use your "
        f"judgement):\n  {shape_rule}"
    )
    parts.append("Classify it and draft a reply if one is warranted.")

    result = ask_json(
        _system(), "\n\n".join(parts), REPLY_SCHEMA, effort="medium", max_tokens=4000
    )
    result["shape"] = shape_name
    return result


def recent_replies(conn: sqlite3.Connection, limit: int = 12) -> List[str]:
    rows = conn.execute(
        """SELECT draft FROM comments WHERE status='replied' AND draft != ''
           ORDER BY seen_at DESC LIMIT ?""",
        (limit,),
    ).fetchall()
    return [r["draft"] for r in rows]


def commenter_history(
    conn: sqlite3.Connection, username: str, limit: int = 4
) -> List[str]:
    if not username:
        return []
    rows = conn.execute(
        """SELECT text FROM comments WHERE username=? ORDER BY seen_at DESC LIMIT ?""",
        (username, limit),
    ).fetchall()
    return [r["text"] for r in rows]


# ─── API Instagram ────────────────────────────────────────────────────────────

def fetch_comments(media_id: str, limit: int = 25) -> List[Dict[str, Any]]:
    token = require_env("IG_ACCESS_TOKEN")
    try:
        with httpx.Client(timeout=40) as client:
            resp = client.get(
                f"{GRAPH}/{media_id}/comments",
                params={
                    "fields": "id,text,username,timestamp,replies{id}",
                    "limit": limit,
                    "access_token": token,
                },
            )
            resp.raise_for_status()
            return resp.json().get("data", [])
    except Exception as exc:
        print(f"    lettura commenti fallita: {exc}")
        return []


def post_reply(comment_id: str, message: str) -> Optional[str]:
    token = require_env("IG_ACCESS_TOKEN")
    with httpx.Client(timeout=40) as client:
        resp = client.post(
            f"{GRAPH}/{comment_id}/replies",
            data={"message": message[:280], "access_token": token},
        )
    if resp.status_code >= 400:
        raise RuntimeError(f"risposta rifiutata: {resp.status_code} {resp.text[:200]}")
    return resp.json().get("id")


# ─── Persistenza ──────────────────────────────────────────────────────────────

SCHEMA = """
CREATE TABLE IF NOT EXISTS comments (
    id            TEXT PRIMARY KEY,      -- id del commento sulla piattaforma
    post_id       INTEGER,
    username      TEXT,
    text          TEXT,
    seen_at       REAL,
    category      TEXT,
    draft         TEXT,
    needs_human   INTEGER DEFAULT 1,
    status        TEXT DEFAULT 'new'     -- new | pending | replied | skipped
);
"""

# La tabella nasce quando c'era solo Instagram e solo i caroselli. Le colonne
# aggiunte dicono da dove arriva il commento: senza, una risposta destinata a
# YouTube verrebbe mandata all'API di Instagram, che restituirebbe un id
# sconosciuto e la risposta andrebbe persa.
MIGRAZIONI = [
    "ALTER TABLE comments ADD COLUMN platform TEXT NOT NULL DEFAULT 'instagram'",
    "ALTER TABLE comments ADD COLUMN source TEXT NOT NULL DEFAULT 'post'",
]


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    for m in MIGRAZIONI:
        try:
            conn.execute(m)
        except sqlite3.OperationalError:
            pass          # già applicata
    conn.commit()


def record(conn: sqlite3.Connection, comment: Dict, post_id: int, verdict: Dict,
           platform: str = "instagram", source: str = "post") -> None:
    conn.execute(
        """INSERT OR IGNORE INTO comments
           (id, post_id, username, text, seen_at, category, draft, needs_human,
            status, platform, source)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (
            comment["id"],
            post_id,
            comment.get("username", ""),
            comment.get("text", ""),
            time.time(),
            verdict["category"],
            verdict["reply"],
            1 if verdict["needs_human"] else 0,
            "pending" if verdict["should_reply"] else "skipped",
            platform,
            source,
        ),
    )
    conn.commit()


def invia_risposta(riga, testo: str) -> Optional[str]:
    """Manda la risposta all'API giusta in base a dove sta il commento."""
    if riga["platform"] == "youtube":
        from .publish.youtube import rispondi_commento
        return rispondi_commento(riga["id"], testo)
    return post_reply(riga["id"], testo)


def already_seen(conn: sqlite3.Connection, comment_id: str) -> bool:
    return (
        conn.execute("SELECT 1 FROM comments WHERE id=?", (comment_id,)).fetchone()
        is not None
    )


def pending(conn: sqlite3.Connection) -> List[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM comments WHERE status='pending' ORDER BY seen_at ASC"
    ).fetchall()


def mark(conn: sqlite3.Connection, comment_id: str, status: str) -> None:
    conn.execute("UPDATE comments SET status=? WHERE id=?", (status, comment_id))
    conn.commit()
