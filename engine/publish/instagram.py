"""Pubblicazione su Instagram via Graph API.

Flusso per un'immagine singola:
    POST /{ig-user-id}/media          → creation_id
    POST /{ig-user-id}/media_publish  → media_id

Flusso per un carosello (2-10 slide):
    POST /{ig-user-id}/media  con is_carousel_item=true   × N  → child ids
    POST /{ig-user-id}/media  con media_type=CAROUSEL     → parent id
    POST /{ig-user-id}/media_publish                      → media_id

Vincoli reali:
  - le immagini devono stare su URL pubbliche SENZA query string
  - il crop del carosello segue la PRIMA immagine: se le slide non hanno tutte
    lo stesso aspect ratio, IG taglia le successive. Noi le generiamo tutte
    identiche, quindi non è un problema — ma non mescolare formati.
  - massimo 10 slide
  - limite di post pubblicati per account: usa 25/24h come soglia prudente
  - 200 chiamate API/ora per account
"""
from __future__ import annotations

import time
from typing import Dict, List, Optional

import httpx

from ..config import env, require_env

GRAPH = "https://graph.facebook.com/v21.0"


class InstagramError(RuntimeError):
    pass


def _post(client: httpx.Client, path: str, data: Dict[str, str]) -> Dict:
    resp = client.post(f"{GRAPH}/{path}", data=data)
    if resp.status_code >= 400:
        raise InstagramError(f"{path} → {resp.status_code} {resp.text}")
    return resp.json()


def _get(client: httpx.Client, path: str, params: Dict[str, str]) -> Dict:
    resp = client.get(f"{GRAPH}/{path}", params=params)
    if resp.status_code >= 400:
        raise InstagramError(f"{path} → {resp.status_code} {resp.text}")
    return resp.json()


def _wait_ready(client: httpx.Client, container_id: str, token: str, timeout: int = 180) -> None:
    """Il container va in FINISHED in modo asincrono: pubblicare prima
    restituisce un errore generico e poco chiaro. Meglio aspettare."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        status = _get(
            client, container_id, {"fields": "status_code,status", "access_token": token}
        )
        code = status.get("status_code")
        if code == "FINISHED":
            return
        if code == "ERROR":
            raise InstagramError(f"container {container_id} in errore: {status}")
        time.sleep(4)
    raise InstagramError(f"timeout in attesa del container {container_id}")


def publish(image_urls: List[str], caption: str, alt_text: str = "") -> str:
    """Pubblica e restituisce l'ID del media. Solleva InstagramError su fallimento."""
    # Validazione degli input prima di leggere la configurazione: se le
    # immagini sono sbagliate deve dirlo, non lamentarsi di una env var.
    if not image_urls:
        raise InstagramError("nessuna immagine da pubblicare")
    if len(image_urls) > 10:
        raise InstagramError(f"{len(image_urls)} slide: il massimo è 10")
    for url in image_urls:
        if "?" in url:
            raise InstagramError(
                f"URL con query string non supportata da Instagram: {url}"
            )

    user_id = require_env("IG_USER_ID")
    token = require_env("IG_ACCESS_TOKEN")

    with httpx.Client(timeout=120) as client:
        if len(image_urls) == 1:
            data = {"image_url": image_urls[0], "caption": caption, "access_token": token}
            if alt_text:
                data["alt_text"] = alt_text
            container = _post(client, f"{user_id}/media", data)["id"]
            _wait_ready(client, container, token)
        else:
            children: List[str] = []
            for url in image_urls:
                child = _post(
                    client,
                    f"{user_id}/media",
                    {
                        "image_url": url,
                        "is_carousel_item": "true",
                        "access_token": token,
                    },
                )["id"]
                children.append(child)
            for child in children:
                _wait_ready(client, child, token)

            container = _post(
                client,
                f"{user_id}/media",
                {
                    "media_type": "CAROUSEL",
                    "children": ",".join(children),
                    "caption": caption,
                    "access_token": token,
                },
            )["id"]
            _wait_ready(client, container, token)

        published = _post(
            client,
            f"{user_id}/media_publish",
            {"creation_id": container, "access_token": token},
        )
        return published["id"]


def publish_reel(video_url: str, caption: str, cover_url: str = "") -> str:
    """Pubblica un Reel. Restituisce l'ID del media.

    Differenze rispetto alle immagini, tutte fonti di errori se ignorate:
      - `media_type=REELS`, e il file va passato come `video_url`
      - la lavorazione del video è molto più lenta: un container immagine è
        pronto in pochi secondi, uno video può richiederne sessanta o più
      - il video deve essere verticale, h264/aac, e durare fra 3 e 900 secondi
      - come per le immagini, l'URL non può contenere query string
    """
    if "?" in video_url:
        raise InstagramError(
            f"URL con query string non supportata da Instagram: {video_url}"
        )

    user_id = require_env("IG_USER_ID")
    token = require_env("IG_ACCESS_TOKEN")

    with httpx.Client(timeout=180) as client:
        data = {
            "media_type": "REELS",
            "video_url": video_url,
            "caption": caption,
            "access_token": token,
        }
        if cover_url and "?" not in cover_url:
            data["cover_url"] = cover_url

        container = _post(client, f"{user_id}/media", data)["id"]
        # I video richiedono un'attesa molto più lunga delle immagini: con il
        # timeout predefinito la pubblicazione fallirebbe su un container che
        # era solo lento, non rotto.
        _wait_ready(client, container, token, timeout=600)

        published = _post(
            client,
            f"{user_id}/media_publish",
            {"creation_id": container, "access_token": token},
        )
        return published["id"]


def quota_used() -> Optional[int]:
    """Post pubblicati via API nelle ultime 24h. None se l'endpoint non risponde."""
    user_id = env("IG_USER_ID")
    token = env("IG_ACCESS_TOKEN")
    if not (user_id and token):
        return None
    try:
        with httpx.Client(timeout=30) as client:
            data = _get(
                client,
                f"{user_id}/content_publishing_limit",
                {"fields": "quota_usage,config", "access_token": token},
            )
        return int(data["data"][0]["quota_usage"])
    except Exception:
        return None


def token_days_left() -> Optional[int]:
    """Giorni prima che Meta revochi l'accesso ai dati.

    Il token in sé non scade (`expires_at: 0`), ma Meta applica una scadenza
    separata all'accesso ai dati — 90 giorni dall'autorizzazione. Superata
    quella, ogni chiamata fallisce e la pipeline si ferma senza che nulla lo
    annunci. È il modo tipico in cui questi sistemi muoiono.

    None se non determinabile.
    """
    import time as _time

    token = env("IG_ACCESS_TOKEN")
    if not token:
        return None
    try:
        with httpx.Client(timeout=30) as client:
            resp = client.get(
                f"{GRAPH}/debug_token",
                params={"input_token": token, "access_token": token},
            )
        data = resp.json().get("data", {})
        if not data.get("is_valid"):
            return 0
        # Si prende la scadenza più vicina fra le due che Meta espone.
        deadlines = [
            d for d in (data.get("expires_at"), data.get("data_access_expires_at")) if d
        ]
        if not deadlines:
            return None
        return int((min(deadlines) - _time.time()) // 86400)
    except Exception:
        return None


def insights(media_id: str) -> Dict[str, int]:
    """Metriche di un post. I nomi delle metriche cambiano tra versioni della
    Graph API: quelle non riconosciute vengono semplicemente ignorate."""
    token = require_env("IG_ACCESS_TOKEN")
    wanted = "reach,likes,comments,saved,shares"
    try:
        with httpx.Client(timeout=30) as client:
            data = _get(
                client, f"{media_id}/insights", {"metric": wanted, "access_token": token}
            )
    except InstagramError:
        return {}

    out: Dict[str, int] = {}
    for item in data.get("data", []):
        name = item.get("name")
        values = item.get("values") or [{}]
        value = int(values[0].get("value", 0) or 0)
        out["saves" if name == "saved" else name] = value
    return out
