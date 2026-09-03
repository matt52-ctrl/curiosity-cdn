"""Reperimento immagini di sfondo pertinenti al fatto.

Due fonti, in cascata:

  pexels     — chiave gratuita, 200 richieste/ora, licenza che consente uso
               commerciale senza attribuzione obbligatoria. Resa migliore per
               soggetti generici ed emotivi. È il primario se la chiave c'è.
  wikimedia  — nessuna chiave, limiti generosi, insuperabile su soggetti
               concreti, storici e scientifici. Richiede attribuzione, che
               viene stampata sulla slide.

Openverse è deliberatamente assente: anonimo consente 5 richieste/ora, che in
una pipeline si esaurisce al primo carosello.

⚠️ Licenze. Questa è una pagina pubblica, quindi le immagini vengono filtrate:
si accettano solo pubblico dominio, CC0, CC BY e CC BY-SA. Tutto ciò che è
NonCommercial o NoDerivatives viene scartato, perché su un account che può
monetizzare non è utilizzabile.
"""
from __future__ import annotations

import base64
import hashlib
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

from .config import DATA_DIR, cfg, env

CACHE_DIR = DATA_DIR / "imgcache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _user_agent() -> str:
    """Wikimedia richiede uno User-Agent identificabile con un contatto, e
    throttla in modo aggressivo (429) chi non lo fornisce. Il contatto si
    imposta in config.yaml → brand.contact."""
    contact = cfg.get("brand.contact", "") or "https://github.com/"
    return f"CuriosityEngine/1.0 ({contact})"


# Wikimedia serve le immagini da upload.wikimedia.org, che rate-limita le
# richieste ravvicinate. Un intervallo minimo fra i download evita i 429
# molto meglio di qualunque logica di retry.
_MIN_INTERVAL = 1.2
_last_request = 0.0


def _throttle() -> None:
    global _last_request
    wait = _MIN_INTERVAL - (time.time() - _last_request)
    if wait > 0:
        time.sleep(wait)
    _last_request = time.time()

# Licenze ammesse su Wikimedia. Il confronto è su LicenseShortName normalizzato.
_ALLOWED_LICENCE = re.compile(
    r"^(cc0|public domain|pd|cc by(-sa)?( \d(\.\d)?)?)$", re.I
)
_FORBIDDEN = re.compile(r"(nc|nd|non[- ]commercial|no ?derivat)", re.I)


class Image:
    """Un'immagine reperita, pronta per essere incorporata in una slide."""

    def __init__(self, url: str, credit: str = "", source: str = ""):
        self.url = url
        self.credit = credit
        self.source = source
        self.path: Optional[Path] = None

    def __repr__(self) -> str:
        return f"<Image {self.source} {self.url[:60]}>"


# ─── Pexels ───────────────────────────────────────────────────────────────────

def _search_pexels(query: str, orientation: str = "portrait") -> List[Image]:
    key = env("PEXELS_API_KEY")
    if not key:
        return []
    try:
        with httpx.Client(timeout=30) as client:
            resp = client.get(
                "https://api.pexels.com/v1/search",
                headers={"Authorization": key},
                params={
                    "query": query,
                    "orientation": orientation,
                    "per_page": 5,
                    "size": "large",
                },
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        print(f"    pexels fallito ({exc}) — passo a wikimedia")
        return []

    out = []
    for photo in data.get("photos", []):
        src = photo.get("src", {})
        url = src.get("portrait") or src.get("large2x") or src.get("original")
        if url:
            # Pexels non obbliga all'attribuzione, ma accreditare il fotografo
            # è corretto e non costa nulla in composizione.
            out.append(Image(url, f"Photo: {photo.get('photographer', '')}", "pexels"))
    return out


# ─── Wikimedia Commons ────────────────────────────────────────────────────────

def _clean_html(raw: str) -> str:
    text = re.sub(r"<[^>]+>", "", raw or "")
    return re.sub(r"\s+", " ", text).strip()


def _search_wikimedia(query: str, width: int = 1400) -> List[Image]:
    try:
        with httpx.Client(timeout=30, follow_redirects=True) as client:
            resp = client.get(
                "https://commons.wikimedia.org/w/api.php",
                params={
                    "action": "query",
                    "generator": "search",
                    "gsrsearch": f"filetype:bitmap {query}",
                    "gsrlimit": 8,
                    "gsrnamespace": 6,
                    "prop": "imageinfo",
                    "iiprop": "url|extmetadata",
                    "iiurlwidth": width,
                    "format": "json",
                },
                headers={"User-Agent": _user_agent()},
            )
            resp.raise_for_status()
            pages = resp.json().get("query", {}).get("pages", {})
    except Exception as exc:
        print(f"    wikimedia fallito: {exc}")
        return []

    out: List[Image] = []
    for page in pages.values():
        info = (page.get("imageinfo") or [{}])[0]
        meta = info.get("extmetadata", {})
        licence = _clean_html(meta.get("LicenseShortName", {}).get("value", ""))

        if _FORBIDDEN.search(licence) or not _ALLOWED_LICENCE.match(licence.strip()):
            continue

        url = info.get("thumburl") or info.get("url")
        if not url:
            continue
        # I thumburl arrivano con parametri di tracciamento: vanno tolti, e
        # comunque non devono finire in un URL destinato a Instagram.
        url = url.split("?")[0]

        artist = _clean_html(meta.get("Artist", {}).get("value", ""))[:48]
        credit = f"{artist} · {licence}" if artist else licence
        out.append(Image(url, credit, "wikimedia"))

    return out


# ─── Generazione AI ───────────────────────────────────────────────────────────
#
# Claude non genera immagini: serve un provider dedicato. Due supportati,
# scelti in config.yaml → visuals.ai_provider.
#
# Quando conviene generare invece di cercare:
#   - la ricerca stock fallisce sui concetti astratti ("attenzione",
#     "distorsione della memoria"): trova solo cervelli luminosi di repertorio
#   - la coerenza visiva è perfetta, perché lo stile è fissato dal prompt
#   - nessun problema di licenza o attribuzione
#
# Quando NON conviene, ed è il motivo per cui esiste l'instradamento:
#   su una pagina di FATTI, generare l'immagine finta di una cosa reale e
#   specifica (una missione Apollo, un reperto, una persona storica) significa
#   fabbricare prove sulla pagina il cui unico valore è l'accuratezza.
#   Per quei soggetti si usa una fotografia vera.

_AI_MODELS = {
    # gemini-3.1-flash-lite-image è il più economico dei modelli immagine
    # ancora attivi. Verificato l'8 agosto 2026 interrogando /v1beta/models.
    "gemini": "gemini-3.1-flash-lite-image",
    # ⚠️ Tutti gli imagen-4.* rispondono 404 "no longer available to new
    # users": restano solo per progetti Google che li usavano già.
    "imagen": "imagen-4.0-fast-generate-001",
    "openai": "gpt-image-2",
    # Gratuiti, nessuna carta richiesta.
    "pollinations": "flux",
    "cloudflare": "@cf/black-forest-labs/flux-1-schnell",
}


def _style_suffix() -> str:
    """Lo stile fissato in config: è ciò che rende venti immagini generate in
    momenti diversi riconoscibili come la stessa pagina."""
    return cfg.get("visuals.ai_style", "").strip()


def _gemini_interactions(key: str, prompt: str, model: str) -> Optional[bytes]:
    """Interactions API — il formato attuale."""
    with httpx.Client(timeout=180) as client:
        resp = client.post(
            "https://generativelanguage.googleapis.com/v1beta/interactions",
            headers={"x-goog-api-key": key, "Content-Type": "application/json"},
            json={
                "model": model,
                "input": [{"type": "text", "text": prompt}],
                "response_format": {
                    "type": "image",
                    "mime_type": "image/jpeg",
                    # 4:5 è esattamente il formato del post (1080×1350): evita
                    # che il cover-crop butti via metà della composizione.
                    "aspect_ratio": "4:5",
                    "image_size": "1K",
                },
            },
        )
        resp.raise_for_status()
        data = resp.json()

    # La posizione del payload è cambiata fra le revisioni: si cercano le
    # varianti note invece di fissarne una sola.
    for path in (
        ("output_image", "data"),
        ("outputImage", "data"),
        ("interaction", "output_image", "data"),
    ):
        node: object = data
        for step in path:
            node = node.get(step) if isinstance(node, dict) else None
            if node is None:
                break
        if isinstance(node, str) and len(node) > 100:
            return base64.b64decode(node)
    return None


def _gemini_generate_content(key: str, prompt: str, model: str) -> Optional[bytes]:
    """generateContent — formato legacy, ancora attivo su diversi modelli."""
    with httpx.Client(timeout=180) as client:
        resp = client.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
            headers={"x-goog-api-key": key, "Content-Type": "application/json"},
            json={
                "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "generationConfig": {
                    # Alcuni modelli rifiutano IMAGE da solo: TEXT+IMAGE è la
                    # combinazione che risulta accettata ovunque.
                    "responseModalities": ["TEXT", "IMAGE"],
                    "imageConfig": {"aspectRatio": "4:5"},
                },
            },
        )
        resp.raise_for_status()
        data = resp.json()

    for part in data["candidates"][0]["content"]["parts"]:
        inline = part.get("inlineData") or part.get("inline_data")
        if inline and inline.get("data"):
            return base64.b64decode(inline["data"])
    return None


def _generate_gemini(prompt: str, model: str) -> Optional[bytes]:
    """Google espone due formati e quello storico è marcato legacy, quindi si
    prova prima il nuovo e si ricade sul vecchio. Quale dei due sia attivo
    dipende dal modello e dal momento."""
    key = env("GEMINI_API_KEY") or env("GOOGLE_API_KEY")
    if not key:
        return None

    errors = []
    for name, call in (
        ("interactions", _gemini_interactions),
        ("generateContent", _gemini_generate_content),
    ):
        try:
            raw = call(key, prompt, model)
            if raw:
                return raw
            errors.append(f"{name}: nessuna immagine nella risposta")
        except Exception as exc:
            detail = str(exc)
            if isinstance(exc, httpx.HTTPStatusError):
                detail = f"{exc.response.status_code} {exc.response.text[:220]}"
            errors.append(f"{name}: {detail}")

    print("    generazione gemini fallita:")
    for err in errors:
        print(f"      · {err}")
    return None


def _generate_imagen(prompt: str, model: str) -> Optional[bytes]:
    key = env("GEMINI_API_KEY") or env("GOOGLE_API_KEY")
    if not key:
        return None
    try:
        with httpx.Client(timeout=180) as client:
            resp = client.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{model}:predict",
                headers={"x-goog-api-key": key, "Content-Type": "application/json"},
                json={
                    "instances": [{"prompt": prompt}],
                    # Imagen accetta solo 1:1, 9:16, 16:9, 4:3, 3:4 — il 4:5
                    # del formato post lo rifiuta con INVALID_ARGUMENT. 3:4 è
                    # il verticale più vicino; il cover-crop copre la differenza.
                    "parameters": {"sampleCount": 1, "aspectRatio": "3:4"},
                },
            )
            resp.raise_for_status()
            preds = resp.json().get("predictions", [])
        if preds and preds[0].get("bytesBase64Encoded"):
            return base64.b64decode(preds[0]["bytesBase64Encoded"])
    except Exception as exc:
        print(f"    generazione imagen fallita: {exc}")
    return None


def _generate_pollinations(prompt: str, model: str) -> Optional[bytes]:
    """Pollinations.ai — nessuna chiave, nessun account, gratis.

    Il compromesso: il tier anonimo restituisce 686×858 qualunque dimensione
    tu chieda, quindi su una slide 1080×1350 c'è un ingrandimento di ~1.6×.
    Su uno sfondo desaturato e velato si nota poco; nel template `photo` a
    tutto campo si nota di più. È un servizio pubblico gratuito: va trattato
    come "spesso disponibile", non come garantito.
    """
    from urllib.parse import quote

    width = int(cfg.get("format.width", 1080))
    height = int(cfg.get("format.height", 1350))
    url = (
        f"https://image.pollinations.ai/prompt/{quote(prompt[:1200])}"
        f"?width={width}&height={height}&nologo=true"
    )
    if model:
        url += f"&model={model}"

    try:
        with httpx.Client(timeout=180, follow_redirects=True) as client:
            resp = client.get(url)
            resp.raise_for_status()
        if resp.content[:2] != b"\xff\xd8" and resp.content[:4] != b"\x89PNG":
            print("    pollinations: risposta non riconosciuta come immagine")
            return None
        return resp.content
    except Exception as exc:
        print(f"    generazione pollinations fallita: {exc}")
        return None


def _modello_cloudflare_di_oggi() -> str:
    """Quale dei modelli in rotazione tocca oggi.

    Perché a rotazione e non uno fisso: i tre danno immagini ugualmente buone
    ma diverse, e alternarli rompe la monotonia della griglia senza costare
    niente — girano tutti sullo stesso free tier di Workers AI.

    Perché la rotazione è PER GIORNO e non per slide. Un carosello con cinque
    slide generate da tre modelli diversi non sembra vario, sembra sbagliato:
    lo stile cambia dentro lo stesso post, che è l'unico posto in cui la
    coerenza serve davvero. Ruotando sul giorno, ogni post resta uniforme al
    suo interno e a variare è il profilo visto dall'alto — che è quello che
    Mattia guarda quando dice che la pagina è monotona.

    Deterministica e senza stato salvato, come `scegli_lunghezza`: dallo
    stesso giorno esce sempre lo stesso modello, quindi si può ricostruire a
    posteriori con quale è stata fatta un'immagine.

    ⚠️ Non del tutto deterministica, da oggi: se i neuroni rimasti non bastano
    per il modello di turno si ripiega sul più economico. È la differenza fra
    un'immagine peggiore e nessuna immagine — i due Leonardo costano più di
    duemila neuroni l'uno e ne bastano tre a finire la giornata. Vedi
    `engine/neuroni.py` per le misure.
    """
    import datetime as _dt

    from . import neuroni

    modelli = cfg.get("visuals.cloudflare_modelli", []) or [
        "@cf/black-forest-labs/flux-1-schnell"]
    if not isinstance(modelli, list) or not modelli:
        return "@cf/black-forest-labs/flux-1-schnell"
    di_turno = modelli[_dt.date.today().toordinal() % len(modelli)]
    return neuroni.modello_possibile(di_turno)


def _generate_cloudflare(prompt: str, model: str) -> Optional[bytes]:
    """Cloudflare Workers AI sul free tier: tre modelli a rotazione.

    10.000 neuroni al giorno gratis, che a questi volumi non si esauriscono
    mai. Serve un account Cloudflare gratuito: da lì Account ID e un API token
    con permesso Workers AI.

    ⚠️ I modelli NON accettano lo stesso corpo, e sbagliarlo costa l'immagine
    intera — Cloudflare risponde 400 e la catena ripiega in silenzio.
    Verificato il 3 settembre 2026 sulla chiave di Mattia:

        flux-1-schnell   width/height RIFIUTATI, vuole `steps` (max 8)
                         → esce 1024x1024, il ritaglio a 4:5 toglie un quinto
        lucid-origin     width/height accettati → 1080x1350 nativi
        phoenix-1.0      width/height accettati → 1080x1350 nativi

    Il verticale nativo non è un dettaglio estetico: sono 262 px di larghezza
    che con flux-1-schnell si buttavano nel ritaglio.
    """
    account = env("CLOUDFLARE_ACCOUNT_ID")
    token = env("CLOUDFLARE_API_TOKEN")
    if not (account and token):
        return None

    model = model or _modello_cloudflare_di_oggi()

    corpo: Dict[str, Any] = {"prompt": prompt[:2000]}

    if "flux-1-schnell" in model:
        # Niente width/height per questo modello. Lo schema non li prevede più
        # e li rifiuta con un 400: "Additional or unevaluated properties
        # '/width, /height' at '/' not allowed". Il cambio è stato distribuito
        # a scaglioni, e questo lo rendeva quasi invisibile — su sei richieste
        # identiche cinque venivano respinte e una passava. Non si vedeva come
        # un guasto ma come immagini che ogni tanto uscivano peggiori:
        # Cloudflare falliva, la catena ripiegava in silenzio su pollinations,
        # e nessuno lo diceva.
        #
        # Cloudflare rifiuta con 400 qualunque `steps` sopra 8, e il rifiuto
        # fa fallire l'immagine intera: va limitato qui invece di fidarsi
        # della configurazione.
        corpo["steps"] = max(1, min(8, int(cfg.get("visuals.steps", 8))))
    else:
        # I modelli Leonardo (lucid-origin, phoenix-1.0) accettano le
        # dimensioni e danno il verticale nativo. `steps` NON si passa: non
        # sta nel loro schema e ricadremmo nello stesso 400 di sopra.
        corpo["width"] = int(cfg.get("visuals.larghezza", 1080))
        corpo["height"] = int(cfg.get("visuals.altezza", 1350))

    try:
        with httpx.Client(timeout=180) as client:
            resp = client.post(
                f"https://api.cloudflare.com/client/v4/accounts/{account}/ai/run/{model}",
                headers={"Authorization": f"Bearer {token}"},
                json=corpo,
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        detail = exc
        if isinstance(exc, httpx.HTTPStatusError):
            detail = f"{exc.response.status_code} {exc.response.text[:200]}"
        print(f"    generazione cloudflare fallita: {detail}")
        return None

    b64 = (data.get("result") or {}).get("image")
    if not b64:
        print(f"    cloudflare: nessuna immagine nella risposta ({str(data)[:160]})")
        return None

    # Segnata subito, non a fine giro: il conteggio di Cloudflare arriva con
    # qualche minuto di ritardo e senza questa riga le immagini di uno stesso
    # carosello si crederebbero tutte le prime della giornata.
    from . import neuroni
    neuroni.registra(model)

    return base64.b64decode(b64)


def _generate_openai(prompt: str, model: str) -> Optional[bytes]:
    key = env("OPENAI_API_KEY")
    if not key:
        return None
    try:
        with httpx.Client(timeout=180) as client:
            resp = client.post(
                "https://api.openai.com/v1/images/generations",
                headers={"Authorization": f"Bearer {key}"},
                json={
                    "model": model,
                    "prompt": prompt,
                    "size": "1024x1536",
                    "quality": cfg.get("visuals.ai_quality", "low"),
                    "n": 1,
                },
            )
            resp.raise_for_status()
            data = resp.json()
        b64 = data["data"][0].get("b64_json")
        return base64.b64decode(b64) if b64 else None
    except Exception as exc:
        print(f"    generazione openai fallita: {exc}")
    return None


def generate(subject: str, modello: str = "") -> Optional[Image]:
    """Genera un'immagine per il soggetto. None se il provider non è
    configurato o fallisce — il chiamante ripiega sulla ricerca.

    `modello` scavalca sia `visuals.ai_model` sia la rotazione del giorno, e
    serve a un caso solo: gli sfondi dei video, che sono quattro al giorno e
    devono costare poco. Nei giorni in cui la rotazione tocca un modello
    Leonardo, quattro sfondi sarebbero undicimila neuroni — l'intera quota
    giornaliera, spesa dietro a del testo scurito dalla gradazione. La
    rotazione resta dov'è utile: nei caroselli, dove l'immagine è il contenuto.
    """
    provider = cfg.get("visuals.ai_provider", "none")
    if provider in ("none", "", None):
        return None

    prompt = f"{subject}. {_style_suffix()}".strip()

    generatori = {
        "gemini": _generate_gemini,
        "imagen": _generate_imagen,
        "openai": _generate_openai,
        "pollinations": _generate_pollinations,
        "cloudflare": _generate_cloudflare,
    }

    # Catena di ripiego invece di un provider solo. Il margine su Cloudflare e'
    # sottile: 10.000 neuroni al giorno coprono giusto le dieci immagini dei
    # due caroselli, e qualunque consumo in piu' — una prova, un giro
    # ripetuto — esaurisce la quota. Quando succede, senza catena si
    # ripiegherebbe sulla ricerca stock, che su questa nicchia restituisce
    # repertorio fuori tema: e' proprio il motivo per cui `prefer_generated`
    # e' acceso. Meglio un'immagine generata da un secondo provider gratuito
    # che una foto d'archivio scelta male.
    catena = [provider]
    ripiego = cfg.get("visuals.ai_fallback", "") or ""
    if ripiego and ripiego not in ("none", provider):
        catena.append(ripiego)

    # `provider` viene riscritto più sotto col nome di chi ha davvero prodotto
    # l'immagine, per il credito. Il valore di partenza serve prima, e va
    # tenuto da parte adesso.
    provider_iniziale = provider

    raw = None
    for i, nome in enumerate(catena):
        # Il modello imposto vale solo per il provider a cui appartiene: se la
        # catena ripiega su pollinations, passargli un nome di modello
        # Cloudflare non avrebbe senso.
        if modello and nome == provider_iniziale:
            model = modello
        else:
            model = cfg.get("visuals.ai_model", "") or _AI_MODELS.get(nome, "")
        raw = generatori.get(nome, lambda *_: None)(prompt, model)
        if raw:
            if i:
                print(f"    ripiegato su {nome}")
            provider = nome        # il credito deve dire chi l'ha davvero fatta
            break

    if not raw:
        return None

    # Il modello entra nella chiave, non solo il prompt: da quando la
    # rotazione esiste, lo stesso soggetto reso da flux e da lucid-origin è
    # due immagini diverse, e con la chiave sul solo prompt la seconda
    # ritroverebbe in cache la prima.
    digest = hashlib.sha1(f"{prompt}|{modello}".encode()).hexdigest()[:16]
    target = CACHE_DIR / f"ai-{digest}.png"
    target.write_bytes(raw)

    image = Image(target.as_uri(), "Illustration: AI-generated", f"ai:{provider}")
    image.path = target
    return image


# ─── API pubblica ─────────────────────────────────────────────────────────────

def find(query: str) -> Optional[Image]:
    """Cerca un'immagine per la query. None se nessuna fonte dà risultati."""
    for search in (_search_pexels, _search_wikimedia):
        results = search(query)
        if results:
            return results[0]
    return None


def acquire(query: str, kind: str = "concept") -> Optional[Image]:
    """Ottiene un'immagine instradando fra generazione e ricerca.

    kind="real_subject" → si cerca una fotografia vera, e non si genera mai:
      il soggetto esiste davvero e inventarne una versione plausibile su una
      pagina di fatti è disinformazione, per quanto bella venga.
    kind="concept" → si genera (se un provider è configurato), perché è il
      caso in cui la ricerca stock restituisce repertorio inutile; se la
      generazione non è disponibile si ripiega sulla ricerca.
    """
    if kind == "real_subject":
        return find(query)

    generated = generate(query)
    if generated:
        return generated
    return find(query)


def fetch(image: Image) -> Optional[Path]:
    """Scarica (con cache su disco) e restituisce il path locale."""
    digest = hashlib.sha1(image.url.encode()).hexdigest()[:16]
    suffix = Path(image.url.split("?")[0]).suffix.lower()
    if suffix not in (".jpg", ".jpeg", ".png", ".webp"):
        suffix = ".jpg"
    target = CACHE_DIR / f"{digest}{suffix}"

    if target.exists() and target.stat().st_size > 2000:
        image.path = target
        return target

    headers = {"User-Agent": _user_agent()}
    for attempt in range(4):
        try:
            _throttle()
            with httpx.Client(timeout=60, follow_redirects=True) as client:
                resp = client.get(image.url, headers=headers)

            if resp.status_code == 429:
                # Rispetta Retry-After se c'è, altrimenti backoff esponenziale.
                delay = float(resp.headers.get("Retry-After", 0)) or 2 ** (attempt + 1)
                print(f"    rate limit, riprovo fra {delay:.0f}s")
                time.sleep(min(delay, 30))
                continue

            resp.raise_for_status()
            target.write_bytes(resp.content)
            image.path = target
            return target

        except Exception as exc:
            if attempt == 3:
                print(f"    download fallito dopo 4 tentativi: {exc}")
                return None
            time.sleep(2 ** attempt)

    return None


def as_data_uri(path: Path) -> str:
    """Incorpora l'immagine nell'HTML: nessuna dipendenza di rete nel momento
    del rendering, e il file di preview resta ispezionabile da solo."""
    mime = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
    }.get(path.suffix.lower(), "image/jpeg")
    b64 = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{b64}"


def attach_images(slides: List[Dict[str, str]]) -> int:
    """Per ogni slide con `image_query`, cerca e scarica l'immagine.

    Modifica le slide sul posto aggiungendo `image` (data URI) e `credit`.
    Restituisce quante slide hanno ottenuto un'immagine. Le slide senza
    immagine restano valide: i template fotografici degradano sul fondo
    tinta unita invece di rompersi.
    """
    # Il tetto vale SOLO per le immagini generate, perché sono le uniche che
    # costano: 5 slide × 2 post al giorno fanno 300 immagini al mese. Quelle
    # cercate su Wikimedia o Pexels sono gratuite e non vengono contate — con
    # la generazione disattivata il tetto non limita nulla.
    ai_budget = int(cfg.get("visuals.max_ai_images_per_post", 0))
    if ai_budget <= 0:
        ai_budget = len(slides)

    found = 0
    generated = 0
    used_urls: set = set()

    for slide in slides:
        query = (slide.get("image_query") or "").strip()
        if not query:
            continue

        kind = slide.get("image_kind", "concept")

        # Con `prefer_generated` anche i soggetti reali vengono generati. Su
        # nicchie di comportamento quotidiano l'archivio restituisce spesso
        # materiale storico fuori tema (una cartolina d'epoca per un fatto
        # sull'imbarazzo sociale): meglio un'immagine costruita sul concetto.
        # Lasciarlo a false ha senso su nicchie storiche o scientifiche, dove
        # la foto autentica del soggetto vale più di qualunque generazione.
        if cfg.get("visuals.prefer_generated", False) and generated < ai_budget:
            kind = "concept"

        # Esaurito il budget di generazione, i concetti ripiegano sulla
        # ricerca invece di restare vuoti.
        if kind == "concept" and generated >= ai_budget:
            kind = "real_subject"

        print(f"    {'genero' if kind == 'concept' else 'cerco'}: {query}")
        image = acquire(query, kind)

        # Se la ricerca non trova nulla si genera comunque: una slide vuota in
        # mezzo a un carosello si legge come un errore di caricamento, ed è
        # peggio di un'illustrazione approssimativa.
        if not image and kind != "concept" and generated < ai_budget:
            print("      nessun risultato in archivio — genero")
            image = generate(query)

        if not image:
            print("      nessun risultato")
            continue

        # Rete di sicurezza contro il carosello con cinque volte la stessa
        # foto: se la ricerca restituisce un'immagine già usata nel post, si
        # preferisce lasciare la slide su fondo pieno.
        if image.url in used_urls:
            print("      già usata in questa slide precedente — salto")
            continue
        used_urls.add(image.url)

        path = image.path or fetch(image)
        if not path:
            continue

        slide["image"] = as_data_uri(path)
        # Da dove viene la foto. L'immagine incorporata serve solo a rendere
        # le slide adesso e non viene salvata (peserebbe un megabyte a slide
        # in un database che sta nel repo); questi due campi bastano a
        # ritrovarla se un giorno si vuole ri-renderizzare il post.
        slide["image_file"] = str(path)
        slide["image_src"] = image.url or ""
        slide["credit"] = image.credit
        found += 1
        if image.source.startswith("ai:"):
            generated += 1
        print(f"      ✓ {image.source}: {image.credit[:52]}")

    return found
