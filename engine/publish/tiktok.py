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


# ─── Preparazione dei lotti ───────────────────────────────────────────────────
# Non si pubblica via API, e non è una rinuncia: l'API può solo depositare una
# bozza nell'inbox dell'app, mentre il programmatore di TikTok vive in TikTok
# Studio, sul desktop. Una bozza nell'inbox non si può passare a Studio — sono
# due percorsi che non si toccano. Passando direttamente dai file si evitano
# l'app sviluppatore, l'audit di 2-4 settimane e la verifica del dominio, e si
# ottiene in cambio l'unica cosa che serve davvero: la programmazione.

def componi_didascalia(frasi: list) -> Dict[str, str]:
    """Didascalia, hashtag e commento da fissare, per un video del lotto.

    La didascalia resta corta di proposito. Su TikTok viene troncata dopo poche
    righe e il resto sta sotto un "altro" che quasi nessuno apre: metterci il
    contesto significa scriverlo per nessuno. L'aggancio della prima curiosità
    fa da titolo, il resto lo dice il video.
    """
    apertura = frasi[0]["hook"].rstrip(".").strip()

    # Gli hashtag stanno in fondo e sono pochi. Venti hashtag generici erano
    # una tattica di anni fa: oggi diluiscono il segnale su cui TikTok decide
    # a chi mostrarti, ed è meglio essere classificati con precisione su tre
    # temi che vagamente su venti.
    tag = ["psychology", "humanbehavior", "psychologyfacts", "learnontiktok"]

    def _chiave(h: str) -> str:
        """Forma normalizzata per riconoscere lo stesso hashtag scritto in due modi.

        Il caso vero, non teorico: le curiosita' sono scritte in inglese
        britannico e gli hashtag fissi in americano, quindi #humanbehaviour e
        #humanbehavior finivano entrambi nella stessa lista bruciando uno dei
        cinque posti per dire due volte la stessa cosa.
        """
        return h.lower().replace("our", "or").rstrip("s")

    propri = []
    visti = {_chiave(x) for x in tag}
    for f in frasi:
        for h in (f.get("hashtags") or [])[:2]:
            h = h.lstrip("#")
            if _chiave(h) not in visti:
                visti.add(_chiave(h))
                propri.append(h)
    scelti = (propri[:2] + tag)[:5]

    return {
        "didascalia": f"{apertura} — and three more things your mind does without asking.\n\n"
                      + " ".join("#" + x for x in scelti),
        # Il commento fissato è il ponte verso le altre due piattaforme. Sta
        # nei commenti e non nella didascalia per due motivi: la didascalia
        # viene troncata, e TikTok tratta con più sospetto ciò che spinge
        # fuori dalla piattaforma quando sta nel corpo del post.
        "commento": _ponte(),
    }


def _ponte() -> str:
    """Il commento da fissare sotto al video: dove trovare il resto."""
    ig = (cfg.get("brand.handle", "") or "").lstrip("@")
    yt = (cfg.get("brand.youtube", "") or "").lstrip("@")
    pezzi = ["One checked fact a day."]
    if ig:
        pezzi.append(f"Longer versions on Instagram: @{ig}")
    if yt:
        pezzi.append(f"Full ones on YouTube: @{yt}")
    return "\n".join(pezzi)
