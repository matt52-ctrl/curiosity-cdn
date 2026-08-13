"""Pubblicazione su YouTube Shorts.

Perché YouTube e non TikTok: TikTok pretende che l'app superi un audit prima
di poter pubblicare in pubblico — senza, ogni contenuto resta privato o in
bozza, e l'attesa è di settimane. YouTube pubblica subito sul proprio canale.

Un video diventa uno Short automaticamente se è verticale e dura meno di tre
minuti: i nostri sono 1080×1920 da nove secondi, quindi non serve dichiarare
nulla — basta caricarlo.

⚠️ Quota: l'API concede 10.000 unità al giorno e un caricamento ne costa
1.600, cioè **sei video al giorno**. Ai tre reel attuali sta larga, ma non si
può salire molto senza chiedere un aumento a Google.

⚠️ Il refresh token: se la schermata di consenso OAuth resta in stato
"Testing", Google fa scadere il refresh token dopo **sette giorni** e la
pubblicazione si ferma senza preavviso. La schermata va portata in
"Production" — resta senza verifica, mostra un avviso a chi accede, ma il
token non scade più. È il singolo errore che fa fallire questa integrazione.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Optional

import httpx

from ..config import DATA_DIR, env

TOKEN_URL = "https://oauth2.googleapis.com/token"
UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"

# Il refresh token vive qui: è l'unica credenziale che non si può rigenerare
# senza rifare l'accesso dal browser.
TOKEN_FILE = DATA_DIR / "youtube_token.json"


class YouTubeError(RuntimeError):
    pass


def _refresh_token() -> str:
    """Legge il refresh token dall'ambiente o dal file salvato."""
    dall_ambiente = env("YOUTUBE_REFRESH_TOKEN")
    if dall_ambiente:
        return dall_ambiente
    if TOKEN_FILE.exists():
        try:
            return json.loads(TOKEN_FILE.read_text()).get("refresh_token", "")
        except json.JSONDecodeError:
            return ""
    return ""


def access_token() -> str:
    """Scambia il refresh token con un token d'accesso valido un'ora."""
    refresh = _refresh_token()
    client_id = env("YOUTUBE_CLIENT_ID")
    client_secret = env("YOUTUBE_CLIENT_SECRET")

    if not (refresh and client_id and client_secret):
        raise YouTubeError(
            "Credenziali YouTube mancanti: servono YOUTUBE_CLIENT_ID, "
            "YOUTUBE_CLIENT_SECRET e YOUTUBE_REFRESH_TOKEN. "
            "Vedi SETUP.md → YouTube."
        )

    r = httpx.post(
        TOKEN_URL,
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh,
            "grant_type": "refresh_token",
        },
        timeout=60,
    )
    if r.status_code >= 400:
        detail = r.json().get("error_description") or r.text[:200]
        if "invalid_grant" in r.text:
            raise YouTubeError(
                f"Refresh token non più valido ({detail}). Succede quando la "
                f"schermata di consenso OAuth è rimasta in stato 'Testing': "
                f"Google li fa scadere dopo 7 giorni. Portala in 'Production' "
                f"e rifai l'autorizzazione una volta sola."
            )
        raise YouTubeError(f"rinnovo token fallito: {detail}")
    return r.json()["access_token"]


def componi_metadati(hook: str, reveal: str, caption: str, tags: list,
                     altre: Optional[list] = None) -> Dict:
    """Titolo e descrizione pensati per YouTube, non riciclati da Instagram.

    Tre regole, tutte verificate contro le pratiche 2026 e tutte diverse da
    quello che serve su Instagram:

    1. **Titolo sotto i 40 caratteri.** Su mobile viene troncato fra i 50 e i
       60, e i video che rendono di piu' stanno intorno alle otto parole.
       Quindi si usa il solo aggancio, non aggancio + rivelazione: la seconda
       parte e' gia' nel video, ripeterla nel titolo lo taglia a meta'.

    2. **Hashtag nella descrizione, mai nel titolo.** I primi tre compaiono
       come link cliccabili sopra il titolo, e un titolo pulito lascia le
       parole chiave in evidenza.

    3. **#Shorts serve ancora.** E' il segnale con cui YouTube classifica il
       formato. Sopra i 15 hashtag totali YouTube li ignora tutti.
    """
    # Solo l'aggancio, tagliato su parola intera.
    titolo = hook.rstrip(".").strip()
    if len(titolo) > 45:
        titolo = titolo[:45].rsplit(" ", 1)[0].rstrip(" ,;:—-")

    # Tre tag tematici piu' #Shorts: quattro in tutto, dentro l'intervallo
    # che YouTube considera.
    scelti = [x for x in tags[:3] if x.lower() != "shorts"] + ["Shorts"]

    # Ponte fra le piattaforme. Chi arriva da YouTube deve poter trovare
    # l'account Instagram senza cercarlo, e viceversa: il filigrana
    # @oddlywireddaily e' gia' impresso nel video, quindi chi guarda su
    # YouTube vede l'handle anche senza leggere la descrizione. Qui si
    # aggiunge il collegamento diretto, per chi invece la apre.
    from ..config import cfg

    # Su YouTube i link nella descrizione SONO cliccabili, al contrario di
    # Instagram: qui conviene l'URL completo, non il solo nome.
    handle = (cfg.get("brand.handle", "") or "").lstrip("@")
    # Chiocciola e non indirizzo: nelle descrizioni degli Short YouTube NON
    # rende cliccabili i link, per scelta anti-spam. Un URL che non si clicca
    # sembra rotto; una chiocciola si legge come un nome e si cerca — che è
    # l'unica azione davvero possibile lì.
    ponte = (f"\n\nOne of these every day on Instagram too: @{handle}"
             if handle else "")

    descrizione = caption.strip()

    # Il video lungo contiene piu' curiosita', ma la didascalia racconta solo
    # la prima. Elencare anche le altre non e' un vezzo: YouTube indicizza la
    # descrizione, e senza questo il video risulta a tema solo dell'apertura
    # mentre due terzi del parlato riguardano altro.
    if altre:
        righe = "\n".join(f"· {x.rstrip('.')}." for x in altre if x)
        if righe:
            descrizione += "\n\nAlso in this video:\n" + righe

    descrizione += ponte
    descrizione += "\n\n" + " ".join("#" + x for x in scelti)

    return {
        "title": titolo,
        "description": descrizione,
        "tags": tags[:15],
    }


def publish(
    video: Path,
    title: str,
    description: str,
    tags: Optional[list] = None,
    privacy: str = "public",
) -> str:
    """Carica un video come Short pubblico. Restituisce l'id del video."""
    if not video.exists():
        raise YouTubeError(f"file non trovato: {video}")

    token = access_token()

    metadata: Dict = {
        "snippet": {
            # 100 caratteri è il limite di YouTube; oltre, l'API rifiuta.
            "title": title[:100],
            "description": description[:4900],
            "tags": (tags or [])[:15],
            # 22 = People & Blogs. Le categorie sono obbligatorie e questa è
            # quella che YouTube stesso consiglia per i contenuti divulgativi
            # brevi non riconducibili a Education formale.
            "categoryId": "22",
        },
        "status": {
            # `private` serve alle prove: permette di verificare che l'intera
            # catena funzioni senza mettere nulla davanti al pubblico.
            "privacyStatus": privacy,
            "selfDeclaredMadeForKids": False,
        },
    }

    # Caricamento in due passi: prima i metadati, poi il file. L'endpoint
    # "resumable" è l'unico che regge file grandi senza timeout.
    inizio = httpx.post(
        UPLOAD_URL,
        params={"uploadType": "resumable", "part": "snippet,status"},
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "X-Upload-Content-Type": "video/mp4",
            "X-Upload-Content-Length": str(video.stat().st_size),
        },
        json=metadata,
        timeout=90,
    )
    if inizio.status_code >= 400:
        raise YouTubeError(f"apertura caricamento fallita: {inizio.text[:250]}")

    sessione = inizio.headers.get("Location")
    if not sessione:
        raise YouTubeError("YouTube non ha restituito l'URL di caricamento")

    with open(video, "rb") as f:
        r = httpx.put(
            sessione,
            content=f.read(),
            headers={"Content-Type": "video/mp4"},
            timeout=600,
        )
    if r.status_code >= 400:
        if "quotaExceeded" in r.text:
            raise YouTubeError(
                "Quota YouTube esaurita: 10.000 unità al giorno, e un "
                "caricamento ne costa 1.600 (sei video al giorno). "
                "Si azzera a mezzanotte ora del Pacifico."
            )
        raise YouTubeError(f"caricamento fallito: {r.text[:250]}")

    return r.json()["id"]


def quota_note() -> str:
    return "6 caricamenti al giorno (10.000 unità, 1.600 a video)"


# ─── Commenti ─────────────────────────────────────────────────────────────────
# Servono il permesso `youtube.force-ssl`, che è più ampio del solo
# caricamento. Chi ha autorizzato prima che questa parte esistesse ha un token
# senza quel permesso: le chiamate qui sotto falliscono con 403 e il ciclo
# tira dritto. Si sistema rilanciando una volta `python3 setup_youtube.py`.

API = "https://www.googleapis.com/youtube/v3"


class PermessoMancante(YouTubeError):
    """Il token c'è ma non copre i commenti — non è un guasto, è un setup vecchio."""


def _intestazioni() -> Dict:
    return {"Authorization": f"Bearer {access_token()}"}


def leggi_commenti(video_id: str, limite: int = 25) -> list:
    """Commenti di primo livello sotto un video, nella forma usata da engine.comments.

    Si normalizzano i campi ai nomi di Instagram (`id`, `text`, `username`)
    perché la parte che redige le risposte non deve sapere da quale
    piattaforma arriva il commento: la voce e le regole sono le stesse.
    """
    try:
        r = httpx.get(
            f"{API}/commentThreads",
            params={"part": "snippet", "videoId": video_id,
                    "maxResults": min(limite, 100), "order": "time",
                    "textFormat": "plainText"},
            headers=_intestazioni(), timeout=40,
        )
    except YouTubeError:
        raise
    except Exception as exc:
        print(f"    lettura commenti YouTube fallita: {exc}")
        return []

    if r.status_code == 403:
        testo = r.text[:200]
        if "insufficient" in testo.lower() or "Scope" in testo:
            raise PermessoMancante(
                "il token YouTube non copre i commenti (manca youtube.force-ssl). "
                "Rilancia una volta:  python3 setup_youtube.py"
            )
        # I commenti disattivati sul video non sono un errore da segnalare.
        return []
    if r.status_code >= 400:
        print(f"    lettura commenti YouTube: {r.status_code} {r.text[:120]}")
        return []

    fuori = []
    for t in r.json().get("items", []):
        c = t["snippet"]["topLevelComment"]
        s = c["snippet"]
        fuori.append({
            "id": c["id"],
            "text": s.get("textOriginal", ""),
            "username": s.get("authorDisplayName", ""),
            "timestamp": s.get("publishedAt", ""),
            # Serve a non rispondere due volte: YouTube dice già quante
            # risposte ha il thread, Instagram va interrogato a parte.
            "reply_count": t["snippet"].get("totalReplyCount", 0),
        })
    return fuori


def rispondi_commento(comment_id: str, testo: str) -> Optional[str]:
    r = httpx.post(
        f"{API}/comments",
        params={"part": "snippet"},
        json={"snippet": {"parentId": comment_id, "textOriginal": testo[:9000]}},
        headers=_intestazioni(), timeout=40,
    )
    if r.status_code == 403:
        raise PermessoMancante(
            "il token YouTube non permette di rispondere (manca youtube.force-ssl). "
            "Rilancia una volta:  python3 setup_youtube.py"
        )
    if r.status_code >= 400:
        raise YouTubeError(f"risposta rifiutata: {r.status_code} {r.text[:200]}")
    return r.json().get("id")


# ─── Statistiche ──────────────────────────────────────────────────────────────

ANALYTICS = "https://youtubeanalytics.googleapis.com/v2/reports"


def statistiche(video_ids: list) -> Dict[str, Dict]:
    """Visualizzazioni, like e commenti per un gruppo di video.

    Una sola chiamata per un massimo di 50 video: interrogarli uno alla volta
    costerebbe una unità di quota ciascuno e, con tre pubblicazioni al giorno,
    l'elenco cresce senza smettere.
    """
    if not video_ids:
        return {}
    r = httpx.get(
        f"{API}/videos",
        params={"part": "statistics", "id": ",".join(video_ids[:50])},
        headers=_intestazioni(), timeout=40,
    )
    if r.status_code == 403:
        raise PermessoMancante(
            "il token YouTube non permette di leggere le statistiche. "
            "Rilancia una volta:  python3 setup_youtube.py"
        )
    if r.status_code >= 400:
        raise YouTubeError(f"statistiche rifiutate: {r.status_code} {r.text[:160]}")

    fuori = {}
    for v in r.json().get("items", []):
        s = v.get("statistics", {})
        fuori[v["id"]] = {
            "views": int(s.get("viewCount", 0) or 0),
            "likes": int(s.get("likeCount", 0) or 0),
            "comments": int(s.get("commentCount", 0) or 0),
        }
    return fuori


def ritenzione(giorni: int = 30) -> Dict[str, Dict]:
    """Percentuale media di video guardata, per video.

    È il numero che conta davvero. YouTube decide se continuare a mostrare uno
    Short in base a quanto della clip viene guardato: un video con poche
    visualizzazioni ma alta percentuale viene rilanciato, uno con tante
    visualizzazioni e bassa percentuale muore lì. I like non entrano nel
    calcolo, ed è per questo che imparare dai like porta fuori strada.

    Richiede il permesso `yt-analytics.readonly`, che è separato da quello di
    caricamento: senza, si solleva PermessoMancante e il resto prosegue.
    """
    import datetime as dt

    fine = dt.date.today()
    inizio = fine - dt.timedelta(days=giorni)
    r = httpx.get(
        ANALYTICS,
        params={
            "ids": "channel==MINE",
            "startDate": inizio.isoformat(),
            "endDate": fine.isoformat(),
            "metrics": "views,averageViewPercentage,averageViewDuration",
            "dimensions": "video",
            "sort": "-views",
            "maxResults": 200,
        },
        headers=_intestazioni(), timeout=60,
    )
    if r.status_code in (401, 403):
        raise PermessoMancante(
            "il token YouTube non copre le statistiche di visione "
            "(manca yt-analytics.readonly). Rilancia una volta:  "
            "python3 setup_youtube.py"
        )
    if r.status_code >= 400:
        raise YouTubeError(f"analytics rifiutata: {r.status_code} {r.text[:160]}")

    d = r.json()
    # Le colonne arrivano descritte a parte: leggerle per posizione fissa
    # significherebbe rompersi in silenzio il giorno che Google ne aggiunge una.
    colonne = [c["name"] for c in d.get("columnHeaders", [])]
    fuori = {}
    for riga in d.get("rows", []):
        v = dict(zip(colonne, riga))
        fuori[v["video"]] = {
            "views": int(v.get("views", 0) or 0),
            "avg_view_pct": float(v.get("averageViewPercentage", 0) or 0),
            "avg_view_sec": float(v.get("averageViewDuration", 0) or 0),
        }
    return fuori
