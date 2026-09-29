"""Il fenomeno di ogni curiosita': cio' che riconosce i doppioni di significato.

Perche' esiste. Il 29 settembre 2026, rileggendo i titoli di YouTube, lo stesso
fenomeno era uscito piu' volte con parole diverse: i numeri scelti a caso il
12/8, il 12/9, il 19/9 e il 27/9; "tutti ti guardano" il 15/8, il 21/9 e il
26/9; "guidi meglio della media" il 14/8 e di nuovo il 29/9. Per il registro dei
consumi erano curiosita' diverse — righe diverse di `facts` — e per la
deduplica lessicale di `ideas.similarity` anche, perche' non condividono le
parole. Per chi guarda sono la stessa cosa.

Due cause, entrambe chiuse qui:
  1. Il generatore vedeva solo le ultime 60 curiosita' (circa cinque giorni di
     produzione): passato quel margine, i fenomeni famosi tornavano. Ora vede
     l'elenco di tutti i fenomeni gia' coperti.
  2. La selezione escludeva la stessa RIGA, mai lo stesso fenomeno. Ora
     `ammessi` toglie le curiosita' il cui fenomeno e' uscito sullo stesso
     canale da meno di `pipeline.riposo_fenomeno` giorni.

L'etichetta la da' un modello, riusando un nome gia' in archivio quando e' la
stessa cosa. Una curiosita' senza etichetta — modello giu', quota finita — passa
la selezione come prima: meglio un doppione raro che un canale fermo.
"""
from __future__ import annotations

import sqlite3
import time
from typing import Dict, Iterable, List, Sequence

from .config import cfg

USABILI = "('approved','rendered','published')"

SCHEMA = {
    "type": "object",
    "properties": {
        "labels": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "phenomenon": {"type": "string"},
                },
                "required": ["id", "phenomenon"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["labels"],
    "additionalProperties": False,
}

SYSTEM = """You label short findings from psychology and behavioural science with
the phenomenon each one is about. The labels are used to stop a social account
from posting the same insight twice in different words.

What counts as the same phenomenon is decided by the VIEWER, not by the
literature. Two findings share a label when someone who saw the first would
feel, on seeing the second, "I already know this one". Examples:
  - "You think everyone noticed your stain" and "You think your nerves are
    obvious to everyone" → both "spotlight effect" (the illusion of
    transparency is the same insight to a viewer).
  - "You pick numbers based on nothing" and "A random number in your head
    changes your estimate" → both "anchoring".
  - "One insult outweighs five compliments" and "You track threats and ignore
    wins" → both "negativity bias".
  - "You think you drive better than most" and "Most people rate themselves
    above average" → both "better-than-average effect".
But keep genuinely different insights apart: "hindsight bias" (I knew it
would happen) is not "overconfidence" (I am sure I am right now).

Rules:
  - 1 to 4 words, lowercase, the best-known name of the effect when one
    exists ("anchoring", "zeigarnik effect", "mere exposure effect").
  - When a finding is the same phenomenon as a label in EXISTING LABELS,
    return that label VERBATIM. Inventing a synonym for an existing label is
    the one mistake that defeats the whole purpose.
  - Only create a new label when none of the existing ones fits.
  - Return one entry for every id you are given, and only those ids."""


# Coppie che il modello tiene separate nonostante l'istruzione, perche' in
# letteratura sono due voci distinte. Per chi guarda sono la stessa: "tutti
# notano la tua ansia" e "tutti notano la macchia" sono uscite a cinque giorni
# di distanza a settembre. Trovate rileggendo le 161 etichette del 29/9/2026.
SINONIMI = {
    "illusion of transparency": "spotlight effect",
    "focalism": "focusing illusion",
}


def normalizza(etichetta: str) -> str:
    e = " ".join((etichetta or "").lower().strip().strip(".").split())
    if e.startswith("the "):
        e = e[4:]
    return SINONIMI.get(e, e)


def noti(conn: sqlite3.Connection) -> List[str]:
    """Tutti i fenomeni gia' in archivio, fra le curiosita' pubblicabili."""
    return [r[0] for r in conn.execute(
        f"""SELECT DISTINCT fenomeno FROM facts
            WHERE fenomeno <> '' AND status IN {USABILI}
            ORDER BY fenomeno""").fetchall()]


def etichetta(conn: sqlite3.Connection, righe: Sequence, lotto: int = 100) -> int:
    """Scrive `facts.fenomeno` per le righe date. Ritorna quante ne ha scritte.

    A lotti, e ogni lotto riceve l'elenco aggiornato dai precedenti: e' l'elenco
    a tenere coerenti i nomi fra una chiamata e l'altra. Chiedere tutto in una
    volta rischia l'uscita troncata; chiedere a lotti senza elenco produce
    "anchoring" in un lotto e "anchoring effect" nel successivo.
    """
    from .llm import ask_json

    elenco = set(noti(conn))
    scritte = 0
    for i in range(0, len(righe), lotto):
        pezzo = righe[i:i + lotto]
        ids = {int(r["id"]) for r in pezzo}
        materiale = "\n".join(f"[{r['id']}] {r['hook']} — {r['fact']}" for r in pezzo)
        user = (
            "EXISTING LABELS (reuse verbatim when it is the same phenomenon):\n"
            + ("\n".join(f"  - {e}" for e in sorted(elenco)) or "  (none yet)")
            + f"\n\nFINDINGS TO LABEL:\n{materiale}\n\nReturn JSON matching the schema."
        )
        dati = ask_json(SYSTEM, user, SCHEMA, effort="medium", max_tokens=12000)
        for x in dati.get("labels", []):
            try:
                fid = int(x["id"])
            except (KeyError, TypeError, ValueError):
                continue
            e = normalizza(x.get("phenomenon", ""))
            # Un id che non era nel lotto e' un'allucinazione del modello:
            # scriverlo sovrascriverebbe l'etichetta di un'altra curiosita'.
            if fid not in ids or not e:
                continue
            conn.execute("UPDATE facts SET fenomeno=? WHERE id=?", (e, fid))
            elenco.add(e)
            scritte += 1
        conn.commit()
    return scritte


def etichetta_mancanti(conn: sqlite3.Connection, limite: int = 300) -> int:
    """Ripara le curiosita' pubblicabili rimaste senza etichetta.

    Gira a ogni generazione: se un giro precedente ha perso l'etichettatura per
    una quota finita, qui si recupera senza che nessuno se ne accorga.
    """
    righe = conn.execute(
        f"""SELECT id, hook, fact FROM facts
            WHERE fenomeno = '' AND status IN {USABILI}
            ORDER BY id LIMIT ?""", (limite,)).fetchall()
    return etichetta(conn, righe) if righe else 0


def riposo(canale: str) -> float:
    """Giorni prima che un fenomeno possa tornare su `canale`."""
    per_canale = cfg.get("pipeline.riposo_fenomeno.per_canale", {}) or {}
    return float(per_canale.get(canale, cfg.get("pipeline.riposo_fenomeno.giorni", 30)))


def usati_di_recente(conn: sqlite3.Connection, canale: str) -> set:
    giorni = riposo(canale)
    return {r[0] for r in conn.execute(
        """SELECT DISTINCT f.fenomeno FROM fact_uses u JOIN facts f ON f.id = u.fact_id
           WHERE u.channel = ? AND u.used_at > ? AND f.fenomeno <> ''""",
        (canale, time.time() - giorni * 86400)).fetchall()}


def ammessi(conn: sqlite3.Connection, righe: Iterable, canale: str) -> List:
    """Le righe di `facts` pubblicabili su `canale` senza ripetere un fenomeno.

    Toglie quelle il cui fenomeno e' uscito li' di recente, e dentro l'elenco
    tiene solo la prima per fenomeno: due curiosita' sullo stesso effetto nello
    stesso lotto uscirebbero a poche ore di distanza, o nello stesso video.
    L'ordine delle righe e' rispettato, quindi chi chiama decide la priorita'.
    """
    visti = set(usati_di_recente(conn, canale))
    fuori = []
    for r in righe:
        e = r["fenomeno"] if "fenomeno" in r.keys() else ""
        if e:
            if e in visti:
                continue
            visti.add(e)
        fuori.append(r)
    return fuori


def conta(righe: Iterable) -> Dict[str, int]:
    """Quante curiosita' per fenomeno: serve solo alla diagnosi."""
    n: Dict[str, int] = {}
    for r in righe:
        e = r["fenomeno"] or "(senza)"
        n[e] = n.get(e, 0) + 1
    return n
