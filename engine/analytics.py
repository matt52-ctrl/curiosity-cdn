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

from .db import (
    insert_metrics,
    published_posts,
    reel_migliori,
    salva_metriche_reel,
    top_performers,
)
from .publish import instagram


def collect(conn: sqlite3.Connection) -> int:
    """Aggiorna le metriche di tutti i post pubblicati. Ritorna quanti aggiornati.

    Se non aggiorna nulla lo dice, e dice perche'. Prima falliva in silenzio a
    ogni ciclo: nei log comparivano zero metriche senza alcuna indicazione che
    la causa fosse un permesso mancante e non l'assenza di dati.
    """
    pubblicati = published_posts(conn)
    updated = 0
    for post in pubblicati:
        metrics = instagram.insights(post["ig_media_id"])
        if metrics:
            insert_metrics(conn, post["id"], metrics)
            updated += 1

    if pubblicati and updated == 0:
        print(
            f"    nessuna metrica su {len(pubblicati)} post pubblicati: "
            f"al token manca instagram_manage_insights. Copertura, "
            f"salvataggi e condivisioni restano invisibili, e il ciclo di "
            f"apprendimento non ha dati su cui lavorare."
        )
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
    """Testo da iniettare nella generazione: cosa ha funzionato finora.

    Mette insieme le due piattaforme. Oggi Instagram non restituisce nulla —
    manca `instagram_manage_insights` — quindi in pratica parla YouTube; il
    giorno che il permesso arriva, le due parti si sommano senza toccare altro.
    """
    pezzi = []

    yt = brief_youtube(conn)
    if yt:
        pezzi.append(yt)

    rows = top_performers(conn, limit)
    if len(rows) < 3:
        return "\n\n".join(pezzi)

    lines = []
    for r in rows:
        try:
            kw = ", ".join(json.loads(r["keywords"])[:4])
        except (json.JSONDecodeError, TypeError):
            kw = ""
        rate = (r["save_rate"] or 0) * 100
        lines.append(f'  - "{r["hook"]}" [{kw}] — save rate {rate:.1f}%')

    pezzi.append(
        "These posts performed best with this audience, measured by save rate.\n"
        "Note what they have in common — subject matter, sentence shape, how\n"
        "specific the hook is — and lean that way without repeating them:\n"
        + "\n".join(lines)
    )
    return "\n\n".join(pezzi)


# ─── YouTube ──────────────────────────────────────────────────────────────────
# Esiste perché su Instagram le metriche non arrivano: al token manca
# `instagram_manage_insights` e copertura, salvataggi e condivisioni restano
# invisibili. Su YouTube invece si leggono, quindi il ciclo di apprendimento
# può partire da lì — su una piattaforma sola, ma con dati veri.


def raccogli_youtube(conn: sqlite3.Connection) -> int:
    """Aggiorna le metriche dei reel usciti su YouTube. Ritorna quanti aggiornati."""
    from .publish import youtube as yt

    from .db import video_youtube

    # L'elenco arriva dal registro dei consumi, non da `reels.youtube_id`:
    # da quando YouTube e' scollegato da Instagram i suoi video non nascono
    # piu' da un reel, e quella colonna resta vuota. Leggendo solo li' la
    # raccolta trovava i due video dell'epoca in cui i canali erano legati e
    # ignorava tutti gli altri — silenziosamente.
    elenco = video_youtube(conn)
    if not elenco:
        return 0

    # Per i video vecchi si conserva il collegamento al reel, cosi' le
    # rilevazioni gia' salvate restano coerenti.
    per_reel = {
        r["youtube_id"]: r["id"]
        for r in conn.execute(
            "SELECT id, youtube_id FROM reels "
            "WHERE youtube_id IS NOT NULL AND youtube_id != ''"
        ).fetchall()
    }

    # Due fonti, unite: le statistiche pubbliche danno like e commenti, che
    # l'API delle analytics non espone; le analytics danno la percentuale di
    # visione, che è l'unica a spiegare *perché* un video è andato bene.
    dati: dict = {}
    try:
        for vid, m in yt.statistiche(elenco).items():
            dati.setdefault(vid, {}).update(m)
    except yt.PermessoMancante as exc:
        print(f"    statistiche YouTube non leggibili: {exc}")
    except Exception as exc:
        print(f"    statistiche YouTube fallite: {exc}")

    try:
        for vid, m in yt.ritenzione().items():
            dati.setdefault(vid, {}).update(m)
    except yt.PermessoMancante as exc:
        print(f"    percentuale di visione non leggibile: {exc}")
    except Exception as exc:
        print(f"    analytics YouTube fallita: {exc}")

    n = 0
    for vid, m in dati.items():
        salva_metriche_reel(conn, per_reel.get(vid), "youtube", m, video_id=vid)
        n += 1
    if n:
        print(f"    {n} video aggiornati da YouTube")
    return n


def rapporto_youtube(conn: sqlite3.Connection, limit: int = 12) -> str:
    from .db import testo_video_youtube

    # Si raggruppa per video, non per reel: dopo lo scollegamento i video
    # YouTube non hanno un reel, e agganciarsi a quello ne mostrava due su
    # cinque senza dire che ne mancavano tre.
    righe = conn.execute(
        """SELECT COALESCE(m.video_id, CAST(m.reel_id AS TEXT)) k,
                  MAX(m.views) v, MAX(m.avg_view_pct) p,
                  MAX(m.avg_view_sec) s, MAX(m.likes) l
           FROM reel_metrics m WHERE m.platform='youtube'
           GROUP BY k ORDER BY v DESC LIMIT ?""",
        (limit,),
    ).fetchall()
    if not righe:
        return ("Nessuna statistica YouTube. Se il canale ha già dei video, "
                "manca il permesso: python3 setup_youtube.py")

    out = ["", "  viste   visione   sec   like   aggancio", "  " + "─" * 68]
    for r in righe:
        testo = testo_video_youtube(conn, r["k"])
        out.append(f"  {r['v']:5d}   {r['p']:6.1f}%  {r['s']:4.0f}  {r['l']:5d}   {testo[:38]}")
    return "\n".join(out)


def brief_youtube(conn: sqlite3.Connection, limit: int = 6) -> str:
    """Cosa insegnare al generatore, dai dati YouTube.

    Si guarda la percentuale di visione e non le visualizzazioni: le
    visualizzazioni sono l'effetto, la percentuale è la causa. Imparare
    dall'effetto vuol dire copiare i video che YouTube ha spinto per motivi
    che non dipendono dal testo.

    Si mostrano le migliori **e** le peggiori. Il solo elenco dei vincitori dice
    al modello "scrivi così" senza dirgli rispetto a cosa: con due estremi
    davanti può vedere cosa cambia fra una frase che trattiene e una che perde
    lo spettatore, che è l'unica cosa che gli serve sapere.
    """
    righe = reel_migliori(conn, limit * 3)
    if len(righe) < 3:
        return ""

    from .db import testo_video_youtube

    def elenca(gruppo):
        return "\n".join(
            f'  - "{testo_video_youtube(conn, r["chiave"])[:110]}" '
            f'— {r["pct"]:.0f}% ({r["views"]} views)'
            for r in gruppo
        )

    migliori = righe[:limit // 2 or 1]
    # Le peggiori solo se c'è abbastanza materiale da rendere il confronto
    # sensato: con quattro video in tutto, "la peggiore" è semplicemente la
    # quarta e non insegna niente.
    peggiori = righe[-(limit // 2):] if len(righe) >= 6 else []

    parti = [
        "On YouTube Shorts the number that decides distribution is how much of\n"
        "the video the average viewer actually watched — not likes, not views.\n"
        "\nHIGHEST RETENTION so far:\n" + elenca(migliori)
    ]
    if peggiori:
        parti.append(
            "LOWEST RETENTION so far — people left early:\n" + elenca(peggiori)
        )
    parti.append(
        "Work out what separates the two groups: how fast the hook lands, how\n"
        "concrete it is, whether the payoff justifies the wait. Write like the\n"
        "first group without reusing their subjects."
    )
    return "\n\n".join(parti)
