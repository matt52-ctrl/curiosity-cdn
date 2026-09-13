"""I numeri di Instagram e TikTok, letti dove si riesce davvero a leggerli.

Nasce il 13 settembre 2026, e corregge una cosa che il progetto dava per vera
da agosto: che Instagram e TikTok fossero canali CIECHI. Non lo sono — o meglio,
non del tutto, e la differenza vale un capitolo intero del bollettino.

  INSTAGRAM. Quello che non si puo' leggere sono le INSIGHTS (reach, plays,
  saved, shares): l'edge `/insights` risponde 400 «(#10) Application does not
  have permission». Ma i campi base del media — `like_count`, `comments_count`
  — e il `followers_count` del profilo arrivano con 200, con il token che
  abbiamo gia' in mano. Verificato il 13 settembre 2026 sull'account vero.
  Quindi: like, commenti e follower si', visualizzazioni no.

  TIKTOK. L'API ufficiale RISPONDE, e questa e' la correzione piu' grossa.
  Il progetto dava per vero da agosto che `video/list` rispondesse 401
  `scope_not_authorized`: riprovato il 13 settembre 2026 con lo stesso token
  che usiamo per pubblicare, risponde 200. Lo scope c'e'. Quindi arrivano
  VISUALIZZAZIONI, like, commenti e condivisioni VIDEO PER VIDEO, piu'
  follower e like totali da `user/info`.
  Non serve nessun browser, nessun CAPTCHA, nessun passaggio a mano — ed e'
  l'unico canale dei tre che ci da' le visualizzazioni, che su Instagram
  restano invisibili.

Regola della casa, ereditata dal bollettino: quando una fonte non risponde si
dice che non ha risposto. Uno zero ambiguo e' peggio di una riga mancante.
"""
from __future__ import annotations

import datetime as _dt
from typing import Dict, List, Optional

import httpx

from .config import cfg, env

GRAPH = "https://graph.facebook.com/v21.0"
TIKTOK = "https://open.tiktokapis.com/v2"


def _quando(s: str) -> _dt.datetime:
    """La data di un media Instagram. Arriva come `+0000`, che `fromisoformat`
    su Python 3.9 non digerisce: va letta a mano."""
    return _dt.datetime.strptime(s, "%Y-%m-%dT%H:%M:%S%z")


def instagram(giorni: int = 7) -> Optional[Dict]:
    """Post, like, commenti e follower. `None` se il token non risponde.

    Divide per TIPO — reel contro carosello — perche' e' l'unica cosa che
    Instagram ci lascia misurare sul formato, ed e' una domanda vera: i due
    formati costano tempi diversi e finora nessuno sapeva quale dei due rende.
    """
    uid, tok = env("IG_USER_ID"), env("IG_ACCESS_TOKEN")
    if not uid or not tok:
        return None
    try:
        with httpx.Client(timeout=30) as c:
            prof = c.get(f"{GRAPH}/{uid}", params={
                "fields": "followers_count,media_count",
                "access_token": tok}).json()
            media = c.get(f"{GRAPH}/{uid}/media", params={
                "fields": "timestamp,media_type,like_count,comments_count",
                "limit": "100", "access_token": tok}).json().get("data", [])
    except Exception:
        return None
    if "followers_count" not in prof:
        return None

    ora = _dt.datetime.now(_dt.timezone.utc)
    dentro = []
    for m in media:
        try:
            if (ora - _quando(m["timestamp"])).days < giorni:
                dentro.append(m)
        except Exception:
            continue

    def conta(righe: List[Dict]) -> Dict:
        return {"post": len(righe),
                "like": sum(x.get("like_count", 0) for x in righe),
                "commenti": sum(x.get("comments_count", 0) for x in righe)}

    return {
        "follower": prof.get("followers_count", 0),
        "post_totali": prof.get("media_count", 0),
        "periodo": conta(dentro),
        "reel": conta([x for x in dentro if x.get("media_type") == "VIDEO"]),
        "caroselli": conta([x for x in dentro
                            if x.get("media_type") == "CAROUSEL_ALBUM"]),
    }


def tiktok(giorni: int = 7) -> Optional[Dict]:
    """Profilo e video dall'API ufficiale. `None` se il token non risponde.

    Due chiamate: `user/info` per i totali del profilo, `video/list` per i
    video. La seconda e' paginata — si continua finche' `has_more` e' vero e i
    video sono ancora dentro la finestra, poi ci si ferma: chiedere tutto lo
    storico ogni lunedi' costerebbe quota per dati che non guardiamo.

    ⚠️ `follower` e `like_totali` sono CUMULATIVI dal primo video, mentre i
    numeri sotto `periodo` sono della finestra. Il riepilogo li stampa in due
    righe diverse apposta: sommarli o confrontarli sarebbe leggere due cose
    diverse come una sola.
    """
    try:
        from .publish import tiktok as api
        tok = api._token_accesso()
    except Exception:
        return None
    if not tok:
        return None

    testa = {"Authorization": f"Bearer {tok}"}
    limite = _dt.datetime.now(_dt.timezone.utc).timestamp() - giorni * 86_400
    try:
        with httpx.Client(timeout=30) as c:
            u = c.get(f"{TIKTOK}/user/info/", headers=testa, params={
                "fields": "follower_count,likes_count,video_count"})
            prof = u.json().get("data", {}).get("user", {})
            if not prof:
                return None

            video: List[Dict] = []
            cursore = None
            for _ in range(10):          # tetto duro: 200 video, poi basta
                corpo = {"max_count": 20}
                if cursore:
                    corpo["cursor"] = cursore
                r = c.post(f"{TIKTOK}/video/list/", headers={
                    **testa, "Content-Type": "application/json"}, params={
                    "fields": "id,create_time,view_count,like_count,"
                              "comment_count,share_count"}, json=corpo)
                d = r.json().get("data", {})
                lotto = d.get("videos", []) or []
                video += lotto
                # Si esce appena il lotto sfora all'indietro la finestra: i
                # video tornano dal piu' recente, quindi da li' in poi sono
                # tutti vecchi.
                if (not d.get("has_more") or not lotto
                        or min(v.get("create_time", 0) for v in lotto) < limite):
                    break
                cursore = d.get("cursor")
    except Exception:
        return None

    dentro = [v for v in video if v.get("create_time", 0) >= limite]
    somma = lambda k: sum(v.get(k, 0) for v in dentro)
    return {
        "follower": prof.get("follower_count", 0),
        "like_totali": prof.get("likes_count", 0),
        "video_totali": prof.get("video_count", 0),
        "periodo": {"video": len(dentro), "viste": somma("view_count"),
                    "like": somma("like_count"),
                    "commenti": somma("comment_count"),
                    "condivisioni": somma("share_count")},
    }
