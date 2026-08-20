"""Il riassunto quotidiano su Telegram: come stanno andando i canali.

Perché esiste: un sistema che devi controllare a mano non è automatico. Fino a
oggi per sapere se qualcosa era uscito bisognava aprire GitHub, YouTube Studio,
l'app di TikTok e il pannello di Cloudflare — quattro posti, ogni giorno, per
scoprire quasi sempre che era tutto a posto. Il costo di quel controllo si paga
anche nei giorni in cui non serve, ed è il motivo per cui si smette di farlo
proprio prima del giorno in cui servirebbe.

Il messaggio è scritto per essere letto sul telefono in dieci secondi: prima
cosa NON va, poi cosa è uscito, poi i numeri. In quest'ordine perché è l'ordine
in cui servono — se qualcosa è rotto il resto può aspettare.

Le fonti sono quelle che abbiamo davvero, e il bollettino lo dice quando una
manca invece di lasciare uno zero ambiguo:
  · YouTube    API Analytics — indietro di 2-3 giorni, non è un guasto
  · Instagram  solo cosa è uscito: le insights vogliono un permesso che non c'è
  · TikTok     solo le bozze spedite: le viste vogliono lo scope video.list
  · Bluesky    like e repost, leggibili senza autenticazione
  · sito       Cloudflare, finestra di 7 giorni
"""
from __future__ import annotations

import datetime as _dt
import sqlite3
import time
from typing import List

import httpx

from .config import cfg, env


def _ieri() -> tuple:
    """Inizio e fine di ieri, in epoch. La finestra del bollettino."""
    oggi = _dt.datetime.now(_dt.timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0)
    return (oggi - _dt.timedelta(days=1)).timestamp(), oggi.timestamp()


def _uscite(conn: sqlite3.Connection) -> List[str]:
    """Cosa è uscito nelle ultime 24 ore, canale per canale."""
    da = time.time() - 86400
    righe = []

    n = conn.execute("SELECT COUNT(*) FROM reels WHERE status='published' "
                     "AND published_at > ?", (da,)).fetchone()[0]
    righe.append(f"reel Instagram    {n}")

    n = conn.execute("SELECT COUNT(DISTINCT ref) FROM fact_uses "
                     "WHERE channel='youtube' AND ref LIKE 'yt-%' AND used_at > ?",
                     (da,)).fetchone()[0]
    righe.append(f"Short YouTube     {n}")

    n = conn.execute("SELECT COUNT(DISTINCT ref) FROM fact_uses "
                     "WHERE channel='tiktok' AND used_at > ?", (da,)).fetchone()[0]
    righe.append(f"bozze TikTok      {n}")

    n = conn.execute("SELECT COUNT(*) FROM fact_uses "
                     "WHERE channel='bluesky' AND used_at > ?", (da,)).fetchone()[0]
    righe.append(f"post Bluesky      {n}")

    # I caroselli si nominano solo se sono accesi: in pausa uno zero direbbe
    # una cosa che sappiamo già, e sposterebbe l'attenzione dalle righe vere.
    if cfg.get("publish.instagram.enabled", True):
        n = conn.execute("SELECT COUNT(*) FROM posts WHERE status='published' "
                         "AND published_at > ?", (da,)).fetchone()[0]
        righe.append(f"caroselli IG      {n}")
    return righe


def _bluesky() -> str:
    """Like e repost degli ultimi post. Leggibili senza autenticazione."""
    handle = (env("BLUESKY_HANDLE") or "").strip()
    if not handle:
        return ""
    try:
        r = httpx.get("https://public.api.bsky.app/xrpc/app.bsky.feed.getAuthorFeed",
                      params={"actor": handle, "limit": 10}, timeout=20)
        feed = r.json().get("feed", [])
    except Exception:
        return ""
    da = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=1)
    recenti = [i["post"] for i in feed
               if i["post"]["record"]["createdAt"] >= da.strftime("%Y-%m-%dT%H:%M:%S")]
    if not recenti:
        return ""
    like = sum(p.get("likeCount", 0) for p in recenti)
    rep = sum(p.get("repostCount", 0) for p in recenti)
    return f"{len(recenti)} post · {like} like · {rep} repost"


def _youtube(conn: sqlite3.Connection) -> List[str]:
    """Le viste degli ultimi giorni. L'API è indietro: si dice, non si nasconde."""
    try:
        from .publish import youtube as yt
        token = yt.access_token()
    except Exception:
        return ["YouTube: credenziali non disponibili"]
    oggi = _dt.date.today()
    try:
        r = httpx.get("https://youtubeanalytics.googleapis.com/v2/reports",
                      headers={"Authorization": f"Bearer {token}"},
                      params={"ids": "channel==MINE",
                              "startDate": str(oggi - _dt.timedelta(days=6)),
                              "endDate": str(oggi),
                              "metrics": "views,subscribersGained",
                              "dimensions": "day", "sort": "day"}, timeout=30)
        righe = r.json().get("rows", [])
    except Exception as exc:
        return [f"YouTube: lettura fallita ({str(exc)[:40]})"]
    if not righe:
        return ["YouTube: nessun dato ancora (l'API è indietro di 2-3 giorni)"]
    viste = sum(x[1] for x in righe)
    iscritti = sum(x[2] for x in righe)
    ultimo = righe[-1]
    return [f"7 giorni: {viste} viste · +{iscritti} iscritti",
            f"ultimo dato ({ultimo[0][5:]}): {ultimo[1]} viste"]


def componi(conn: sqlite3.Connection) -> str:
    """Il messaggio, in HTML per Telegram."""
    from . import allarme, sito_metriche

    parti: List[str] = []

    # 1. Cosa non va. Sta in cima perché se qualcosa è rotto il resto può
    #    aspettare, e perché un messaggio che comincia coi numeri buoni si
    #    smette di leggere prima di arrivare al problema.
    allarme.azzera()
    allarme.silenzio(conn)
    allarme.cadenza(conn)
    problemi = allarme.elenco() if hasattr(allarme, "elenco") else []
    if problemi:
        parti.append("⚠️ <b>DA GUARDARE</b>")
        parti += [f"· {p}" for p in problemi]
    else:
        parti.append("✅ <b>Tutti i canali nei tempi previsti.</b>")

    # 2. Cosa è uscito.
    parti.append("\n<b>Ultime 24 ore</b>\n<code>" + "\n".join(_uscite(conn)) + "</code>")

    # 3. I numeri, per le fonti che li danno davvero.
    parti.append("\n<b>YouTube</b>\n" + "\n".join(_youtube(conn)))

    bs = _bluesky()
    if bs:
        parti.append(f"\n<b>Bluesky</b>\n{bs}")

    try:
        dati = sito_metriche.riassunto()
        tot = sum(n for _, n in dati["pagine"])
        riga = f"{tot} visite in 7 giorni"
        if dati["provenienza"]:
            da = ", ".join(f"{k} {v}" for k, v in dati["provenienza"][:3])
            riga += f"\narrivano da: {da}"
        parti.append(f"\n<b>Sito</b>\n{riga}")
    except Exception:
        pass

    return "\n".join(parti)


def manda(conn: sqlite3.Connection) -> bool:
    """Compone e invia. Ritorna False se Telegram non è configurato."""
    from . import review

    if not review.enabled():
        print("· Telegram non configurato: bollettino non inviato")
        return False
    review.notify(componi(conn))
    return True
