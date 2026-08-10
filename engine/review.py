"""Coda di approvazione umana via Telegram.

Perché esiste: sia Instagram che TikTok penalizzano i contenuti percepiti come
non originali o prodotti in serie. Un occhio umano per due secondi al giorno è
la differenza fra una pagina che cresce e una che viene silenziosamente
declassata — e nelle prime settimane è anche il modo più economico di scoprire
cosa sbaglia il generatore.

Niente webhook: si usa getUpdates. Approvi rispondendo in chat:
    /ok 12      → pubblica il post 12
    /no 12      → scarta il post 12
"""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import List, Tuple

import httpx

from .config import DATA_DIR, env
from .db import set_post_status

OFFSET_FILE = DATA_DIR / "telegram_offset.txt"


def _api(method: str) -> str:
    return f"https://api.telegram.org/bot{env('TELEGRAM_BOT_TOKEN')}/{method}"


def enabled() -> bool:
    return bool(env("TELEGRAM_BOT_TOKEN") and env("TELEGRAM_CHAT_ID"))


def notify(text: str) -> None:
    if not enabled():
        return
    try:
        with httpx.Client(timeout=30) as client:
            client.post(
                _api("sendMessage"),
                data={
                    "chat_id": env("TELEGRAM_CHAT_ID"),
                    "text": text,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": "true",
                },
            )
    except Exception as exc:
        print(f"[telegram] invio fallito: {exc}")


def send_for_veto(post_id: int, image_paths: List[Path], caption: str, minutes: int) -> None:
    """Mostra il post e annuncia che uscirà da solo salvo veto.

    Differenza sostanziale da `send_for_review`: qui il silenzio significa sì.
    L'utente non deve fare nulla perché il post esca — deve agire solo per
    fermarlo. È il modello giusto per chi vuole automazione ma non vuole
    scoprire i propri post dopo che sono usciti.
    """
    if not enabled():
        return
    chat_id = env("TELEGRAM_CHAT_ID")

    with httpx.Client(timeout=120) as client:
        media = [{"type": "photo", "media": f"attach://f{i}"} for i in range(len(image_paths))]
        files = {
            f"f{i}": (p.name, p.read_bytes(), "image/png")
            for i, p in enumerate(image_paths)
        }
        client.post(
            _api("sendMediaGroup"),
            data={"chat_id": chat_id, "media": json.dumps(media)},
            files=files,
        )
        preview = caption if len(caption) <= 2800 else caption[:2800] + "…"
        client.post(
            _api("sendMessage"),
            data={
                "chat_id": chat_id,
                "text": (
                    f"<b>Post #{post_id}</b> — esce fra {minutes} minuti.\n\n"
                    f"{preview}\n\n"
                    f"Non devi fare niente. Per bloccarlo: <code>/no {post_id}</code>"
                ),
                "parse_mode": "HTML",
                "disable_web_page_preview": "true",
            },
        )


def vetoed(conn: sqlite3.Connection, post_id: int) -> bool:
    """True se nel frattempo è arrivato un /no per questo post."""
    poll_decisions(conn)
    row = conn.execute("SELECT status FROM posts WHERE id=?", (post_id,)).fetchone()
    return bool(row) and row["status"] == "failed"


def send_for_review(post_id: int, image_paths: List[Path], caption: str) -> None:
    """Manda le slide + la caption e chiede una decisione."""
    if not enabled():
        return
    chat_id = env("TELEGRAM_CHAT_ID")

    with httpx.Client(timeout=120) as client:
        media = [
            {"type": "photo", "media": f"attach://f{i}"} for i in range(len(image_paths))
        ]
        files = {
            f"f{i}": (p.name, p.read_bytes(), "image/png")
            for i, p in enumerate(image_paths)
        }
        client.post(
            _api("sendMediaGroup"),
            data={"chat_id": chat_id, "media": json.dumps(media)},
            files=files,
        )

        preview = caption if len(caption) <= 3000 else caption[:3000] + "…"
        client.post(
            _api("sendMessage"),
            data={
                "chat_id": chat_id,
                "text": (
                    f"<b>Post #{post_id}</b>\n\n{preview}\n\n"
                    f"<code>/ok {post_id}</code> per pubblicare · "
                    f"<code>/no {post_id}</code> per scartare"
                ),
                "parse_mode": "HTML",
                "disable_web_page_preview": "true",
            },
        )


def poll_decisions(conn: sqlite3.Connection) -> List[Tuple[int, str]]:
    """Legge i messaggi nuovi e applica le decisioni. Restituisce (post_id, esito)."""
    if not enabled():
        return []

    offset = 0
    if OFFSET_FILE.exists():
        try:
            offset = int(OFFSET_FILE.read_text().strip())
        except ValueError:
            offset = 0

    with httpx.Client(timeout=40) as client:
        resp = client.get(_api("getUpdates"), params={"offset": offset + 1, "timeout": 0})
    resp.raise_for_status()
    updates = resp.json().get("result", [])

    decisions: List[Tuple[int, str]] = []
    last_id = offset
    for update in updates:
        last_id = max(last_id, update["update_id"])
        text = (update.get("message") or {}).get("text", "").strip()
        # Approvazione dei post: /ok 12  ·  /no 12
        match = re.match(r"^/(ok|no)\s+(\d+)$", text, re.I)
        if match:
            verb, post_id = match.group(1).lower(), int(match.group(2))
            status = "approved" if verb == "ok" else "failed"
            set_post_status(conn, post_id, status)
            decisions.append((post_id, status))
            continue

        # Risposte ai commenti: /reply <id>  ·  /skip <id>
        # Gli id dei commenti Instagram non sono numerici, quindi il pattern è
        # più largo di quello dei post.
        match = re.match(r"^/(reply|skip)\s+(\S+)$", text, re.I)
        if match:
            verb, comment_id = match.group(1).lower(), match.group(2)
            try:
                from . import comments as cm

                if verb == "skip":
                    cm.mark(conn, comment_id, "skipped")
                    decisions.append((comment_id, "skipped"))
                else:
                    row = conn.execute(
                        "SELECT draft FROM comments WHERE id=?", (comment_id,)
                    ).fetchone()
                    if row:
                        cm.post_reply(comment_id, row["draft"])
                        cm.mark(conn, comment_id, "replied")
                        decisions.append((comment_id, "replied"))
            except Exception as exc:
                notify(f"⚠️ Risposta a {comment_id} fallita:\n<code>{exc}</code>")

    if last_id > offset:
        OFFSET_FILE.write_text(str(last_id))

    return decisions
