"""Raccolta metriche e loop di apprendimento.

Il segnale che conta su Instagram non sono i like: sono i **salvataggi** e le
**condivisioni**, perché sono ciò che l'algoritmo legge come "questo contenuto
vale la pena distribuirlo". Un post con 300 like e 4 salvataggi è morto; uno
con 80 like e 60 salvataggi viene spinto.

`learning_brief()` estrae i pattern dai post migliori e li rimanda dentro la
generazione. Va nel messaggio utente, non nel system prompt, così la cache del
prompt resta valida.
"""
from __future__ import annotations

import json
import sqlite3
from typing import List

from .db import insert_metrics, published_posts, top_performers
from .publish import instagram


def collect(conn: sqlite3.Connection) -> int:
    """Aggiorna le metriche di tutti i post pubblicati. Ritorna quanti aggiornati."""
    updated = 0
    for post in published_posts(conn):
        metrics = instagram.insights(post["ig_media_id"])
        if metrics:
            insert_metrics(conn, post["id"], metrics)
            updated += 1
    return updated


def report(conn: sqlite3.Connection, limit: int = 10) -> str:
    rows = top_performers(conn, limit)
    if not rows:
        return "Nessuna metrica ancora. Le insights compaiono qualche ora dopo il post."

    lines = ["", "  save-rate   reach   saves   hook", "  " + "─" * 66]
    for r in rows:
        rate = (r["save_rate"] or 0) * 100
        lines.append(
            f"  {rate:8.2f}%  {r['reach']:6d}  {r['saves']:6d}   {r['hook'][:44]}"
        )
    return "\n".join(lines)


def learning_brief(conn: sqlite3.Connection, limit: int = 8) -> str:
    """Testo da iniettare nella generazione: cosa ha funzionato finora."""
    rows = top_performers(conn, limit)
    if len(rows) < 3:
        return ""

    lines = []
    for r in rows:
        try:
            kw = ", ".join(json.loads(r["keywords"])[:4])
        except (json.JSONDecodeError, TypeError):
            kw = ""
        rate = (r["save_rate"] or 0) * 100
        lines.append(f'  - "{r["hook"]}" [{kw}] — save rate {rate:.1f}%')

    return (
        "These posts performed best with this audience, measured by save rate.\n"
        "Note what they have in common — subject matter, sentence shape, how\n"
        "specific the hook is — and lean that way without repeating them:\n"
        + "\n".join(lines)
    )
