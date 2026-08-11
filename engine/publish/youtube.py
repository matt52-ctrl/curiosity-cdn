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


def componi_metadati(hook: str, reveal: str, caption: str, tags: list) -> Dict:
    """Titolo e descrizione pensati per YouTube, non riciclati da Instagram.

    Su YouTube gli Short compaiono anche nella ricerca, con un carosello
    dedicato: il titolo e' quindi un campo di ricerca, non solo un'etichetta.
    Su Instagram non esiste nulla di equivalente, quindi riusare la stessa
    stringa sprecherebbe l'unico vantaggio che YouTube offre.

    Gli hashtag scendono a tre: e' l'intervallo che YouTube tratta come
    segnale, e oltre quello vengono ignorati.
    """
    # Il titolo unisce i due tempi: chi lo legge in ricerca vede la domanda e
    # la risposta insieme, che e' cio' che fa cliccare.
    titolo = hook.rstrip(".") 
    if reveal and len(titolo) + len(reveal) < 90:
        titolo = f"{titolo} — {reveal.rstrip('.')}"
    titolo = titolo[:95]

    tre = [t for t in tags[:3]]
    descrizione = caption.strip()
    if tre:
        descrizione += "\n\n" + " ".join("#" + t for t in tre)

    return {"title": titolo, "description": descrizione, "tags": tags[:15]}


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
