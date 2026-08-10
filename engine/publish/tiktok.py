"""Pubblicazione su TikTok (photo post) via Content Posting API.

⚠️  Leggi questo prima di attivarlo.

TikTok non è simmetrico a Instagram. Due muri reali:

1. **Audit dell'app.** Finché il tuo client non passa l'audit TikTok, TUTTO
   ciò che pubblichi è forzato a visibilità privata (`SELF_ONLY`) e ricevi
   l'errore `unaudited_client_can_only_post_to_private_accounts` se provi a
   fare altrimenti. L'audit richiede 2-4 settimane e più giri di feedback.

2. **Verifica del dominio.** Le foto si caricano solo via `PULL_FROM_URL`, e
   TikTok pretende che tu abbia verificato la proprietà del dominio da cui
   servi le immagini. `raw.githubusercontent.com` non è tuo → non lo puoi
   verificare. Per TikTok serve un dominio tuo (backend Cloudinary con
   dominio custom, o un tuo bucket con CNAME).

Perciò il default in config.yaml è `mode: inbox`: le foto arrivano nella tua
inbox TikTok come bozza e sei tu a premere "Post" dall'app. È l'unico modo
onesto di partire senza audit. Un giro di 10 secondi al giorno sul telefono.

Limiti: max 35 foto per post, 6 richieste/minuto per access token,
5 upload pendenti per 24h.
"""
from __future__ import annotations

from typing import Dict, List

import httpx

from ..config import cfg, require_env

API = "https://open.tiktokapis.com/v2"


class TikTokError(RuntimeError):
    pass


def publish_photos(image_urls: List[str], title: str, description: str) -> str:
    """Restituisce il publish_id. In modalità `inbox` il post resta bozza."""
    token = require_env("TIKTOK_ACCESS_TOKEN")
    mode = cfg.get("publish.tiktok.mode", "inbox")

    if len(image_urls) > 35:
        raise TikTokError(f"{len(image_urls)} foto: il massimo è 35")

    post_mode = "DIRECT_POST" if mode == "direct" else "MEDIA_UPLOAD"

    payload: Dict = {
        "media_type": "PHOTO",
        "post_mode": post_mode,
        "source_info": {
            "source": "PULL_FROM_URL",
            "photo_cover_index": 0,
            "photo_images": image_urls,
        },
        "post_info": {
            "title": title[:90],          # limite: 90 rune UTF-16
            "description": description[:4000],
        },
    }

    with httpx.Client(timeout=120) as client:
        resp = client.post(
            f"{API}/post/publish/content/init/",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json; charset=UTF-8",
            },
            json=payload,
        )

    if resp.status_code >= 400:
        body = resp.text
        if "url_ownership_unverified" in body:
            raise TikTokError(
                "TikTok non riconosce il dominio delle immagini. Devi verificare "
                "la proprietà del prefisso URL nel developer portal. "
                "raw.githubusercontent.com non è verificabile — serve un dominio tuo."
            )
        if "unaudited_client" in body:
            raise TikTokError(
                "Il client non ha passato l'audit: puoi pubblicare solo in privato. "
                "Usa mode: inbox in config.yaml finché l'audit non è approvato."
            )
        raise TikTokError(f"{resp.status_code} {body}")

    data = resp.json()
    if data.get("error", {}).get("code") not in (None, "ok"):
        raise TikTokError(str(data["error"]))
    return data["data"]["publish_id"]


def status(publish_id: str) -> Dict:
    token = require_env("TIKTOK_ACCESS_TOKEN")
    with httpx.Client(timeout=30) as client:
        resp = client.post(
            f"{API}/post/publish/status/fetch/",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json; charset=UTF-8",
            },
            json={"publish_id": publish_id},
        )
    resp.raise_for_status()
    return resp.json()
