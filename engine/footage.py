"""Filmati di sfondo per i reel, da Pexels Video.

Due requisiti che guidano tutto il modulo:

1. **Varietà.** Un profilo dove ogni reel ha lo stesso mare al tramonto smette
   di essere guardato dopo il terzo. Le clip già usate vengono ricordate e
   scartate, e la scelta della scena parte dal tono della frase invece che da
   una lista fissa.

2. **Durata.** Le clip di stock durano 10-45 secondi, i reel 6. Il taglio
   avviene in fase di montaggio, ma qui si preferiscono clip abbastanza lunghe
   da non dover essere ripetute in loop dentro un video così breve.
"""
from __future__ import annotations

import hashlib
import json
import random
import re
from pathlib import Path
from typing import Dict, List, Optional

import httpx

from .config import DATA_DIR, env

CACHE = DATA_DIR / "footage"
CACHE.mkdir(parents=True, exist_ok=True)
USATE = DATA_DIR / "footage_usate.json"

# Scene per registro emotivo. Servono a due cose: dare coerenza fra musica,
# frase e immagine, e garantire varietà — da ogni registro si pesca a caso,
# quindi due reel dello stesso tono non escono quasi mai uguali.
SCENE = {
    "reflective": [
        "calm ocean horizon", "fog over still lake", "empty road at dusk",
        "clouds time lapse sky", "rain on window", "snow falling forest",
        "candle flame dark room", "wheat field wind", "mountain mist morning",
        "slow river stones", "empty beach grey sky", "moon night clouds",
    ],
    "unsettling": [
        "dark stormy sea", "abandoned corridor", "flickering neon night",
        "deep forest darkness", "smoke black background", "cave water drip",
        "night highway headlights", "wind bare branches", "storm clouds moving",
        "underwater dark blue", "empty stairwell", "distant thunder sky",
    ],
    "warm": [
        "golden hour field", "sunlight through leaves", "warm kitchen light",
        "hands pottery clay", "campfire close up", "autumn leaves falling",
        "sunset over hills", "coffee steam morning light", "dog running grass",
        "string lights evening", "sun through curtains", "wool blanket texture",
    ],
    "bright": [
        "waves crashing sunlight", "city street timelapse", "confetti falling",
        "tropical water aerial", "flowers blooming macro", "kids running park",
        "balloons sky", "bike wheels spinning", "waterfall sunlight",
        "market colours", "swimming pool from above", "sparklers night",
    ],
}


def _usate() -> Dict[str, str]:
    if USATE.exists():
        try:
            return json.loads(USATE.read_text())
        except json.JSONDecodeError:
            return {}
    return {}


def _segna(video_id: str, quando: str) -> None:
    d = _usate()
    d[str(video_id)] = quando
    # Oltre 400 voci la memoria non serve più: le clip vecchie possono
    # tornare senza che nessuno se ne accorga.
    if len(d) > 400:
        d = dict(list(d.items())[-400:])
    USATE.write_text(json.dumps(d))


def scena_per(mood: str, seme: str = "") -> str:
    """Sceglie una scena coerente col tono, in modo stabile per seme."""
    opzioni = SCENE.get(mood) or SCENE["reflective"]
    rnd = random.Random(hashlib.sha1((seme or mood).encode()).hexdigest())
    return rnd.choice(opzioni)


def cerca(query: str, evita_usate: bool = True) -> Optional[Dict]:
    """Cerca una clip verticale. None se Pexels non risponde o non ha nulla."""
    key = env("PEXELS_API_KEY")
    if not key:
        return None
    try:
        r = httpx.get(
            "https://api.pexels.com/videos/search",
            headers={"Authorization": key},
            params={
                "query": query,
                "orientation": "portrait",
                "per_page": 15,
                "size": "medium",
            },
            timeout=45,
        )
        r.raise_for_status()
        video = r.json().get("videos", [])
    except Exception as exc:
        print(f"    Pexels non risponde: {exc}")
        return None

    usate = _usate() if evita_usate else {}
    for v in video:
        if str(v["id"]) in usate:
            continue
        # Si preferisce una clip che copra il reel senza ripetersi in loop.
        if v.get("duration", 0) < 5:
            continue
        file_hd = [
            f for f in v.get("video_files", [])
            if f.get("height", 0) >= 1080 and f.get("width", 0) >= 600
        ]
        if not file_hd:
            continue
        # Il file più piccolo fra quelli in HD: scaricare 4K per un reel da
        # 6 secondi è tempo e banda buttati.
        file_hd.sort(key=lambda f: f.get("height", 0))
        return {"id": v["id"], "url": file_hd[0]["link"], "durata": v["duration"]}

    return None


def scarica(clip: Dict) -> Optional[Path]:
    target = CACHE / f"{clip['id']}.mp4"
    if target.exists() and target.stat().st_size > 100_000:
        _segna(clip["id"], "riuso")
        return target
    try:
        with httpx.Client(timeout=180, follow_redirects=True) as client:
            with client.stream("GET", clip["url"]) as r:
                r.raise_for_status()
                with open(target, "wb") as f:
                    for blocco in r.iter_bytes(65536):
                        f.write(blocco)
    except Exception as exc:
        print(f"    scaricamento clip fallito: {exc}")
        target.unlink(missing_ok=True)
        return None

    _segna(clip["id"], "nuova")
    return target


def per_frase(mood: str, frase: str) -> Optional[Path]:
    """Dal tono della frase al file video, provando più scene.

    Se una scena non dà risultati nuovi si passa alla successiva invece di
    arrendersi: con dodici opzioni per registro, restare senza sfondo è
    praticamente impossibile.
    """
    opzioni = list(SCENE.get(mood) or SCENE["reflective"])
    rnd = random.Random(hashlib.sha1(frase.encode()).hexdigest())
    rnd.shuffle(opzioni)

    for scena in opzioni[:4]:
        clip = cerca(scena)
        if not clip:
            continue
        path = scarica(clip)
        if path:
            print(f"    sfondo: {scena}")
            return path
    return None
