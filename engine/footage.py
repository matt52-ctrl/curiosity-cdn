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


def _ffmpeg() -> str:
    import shutil
    return shutil.which("ffmpeg") or "ffmpeg"


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


# Parole che compaiono in mezzo catalogo e non dicono di cosa sia la clip. Se
# restassero, «close up of hands» combacerebbe con qualunque cosa.
_GENERICHE = {
    "close", "closeup", "shot", "shots", "view", "with", "over", "into",
    "from", "that", "this", "some", "very", "slow", "motion", "footage",
    "video", "clip", "background", "scene", "person", "people", "someone",
    "hand", "hands", "man", "woman", "adult", "young", "beautiful",
    # Parole corte di servizio. La soglia sta a tre lettere e non a quattro
    # perche' scartando le corte si perdevano proprio i soggetti piu' secchi
    # — «eye», «sky», «car» — e «close up human eye» restava con la sola
    # parola «human», che in un archivio non la scrive nessuno.
    "the", "and", "for", "two", "one", "its", "his", "her", "out", "off",
    "are", "was", "not", "but", "who", "how", "why", "all", "you", "our",
}


def _parole_chiave(query: str) -> List[str]:
    """Le parole della query che dicono davvero cosa si vuole vedere."""
    fuori = []
    for p in re.split(r"[^a-z]+", query.lower()):
        if len(p) >= 3 and p not in _GENERICHE:
            # Radice grezza: basta a far combaciare "papers" con "paperwork" e
            # "walking" con "walk". Non e' morfologia, e' un troncamento, e
            # per confrontare due etichette di stock e' abbastanza.
            fuori.append(p[:5])
    return fuori


def _pertinente(query: str, descrizione: str) -> bool:
    """La clip parla di cio' che si e' chiesto, o e' finita qui per riempire?

    Serve perche' gli archivi non dicono mai di no. Chiedendo «hands holding
    research papers» le prime posizioni di Pexels sono esattamente quello —
    misurate: «a person working with paperworks», «person holding documents»,
    «close up of hands flipping through old documents» — ma la lista non si
    ferma quando i risultati buoni finiscono, continua a scorrere il catalogo
    con criteri sempre piu' larghi. Piu' in basso, per quella stessa query,
    c'e' un bambino che soffia una lingua di Menelik.

    In cima non ci si arriva sempre, ed e' il punto: le clip gia' usate si
    scartano, sono duecento e passa e crescono a ogni video, quindi la
    posizione media di cio' che si prende scende di mese in mese. Il filtro
    non serve oggi, serve fra sei mesi, e allora sara' tardi per accorgersene.

    Non e' un giudizio di qualita': e' un controllo che almeno una parola
    della richiesta compaia in cio' con cui l'archivio ha etichettato la clip.
    """
    chiavi = _parole_chiave(query)
    if not chiavi:
        # Query tutta di parole generiche: non c'e' niente da verificare, e
        # rifiutare tutto sarebbe peggio che accettare.
        return True
    testo = re.sub(r"[^a-z]+", " ", descrizione.lower())
    return any(k in testo for k in chiavi)


def _cerca_pixabay(query: str, usate: Dict,
                   esigente: bool = False) -> Optional[Dict]:
    """Seconda fonte. Serve alla varietà: due archivi diversi hanno cataloghi
    diversi, e alternarli allontana il momento in cui una clip si ripete."""
    key = env("PIXABAY_API_KEY")
    if not key:
        return None
    try:
        r = httpx.get(
            "https://pixabay.com/api/videos/",
            params={"key": key, "q": query, "per_page": 20, "safesearch": "true"},
            timeout=45,
        )
        r.raise_for_status()
        hits = r.json().get("hits", [])
    except Exception:
        return None

    for h in hits:
        vid = f"px{h['id']}"
        if vid in usate or h.get("duration", 0) < 5:
            continue
        if esigente and not _pertinente(query, h.get("tags", "")):
            continue
        # Pixabay espone più tagli: si prende il verticale se c'è, altrimenti
        # il più piccolo in HD — il ritaglio a 1080×1920 avviene comunque dopo.
        files = h.get("videos", {})
        scelto = files.get("large") or files.get("medium")
        if not scelto or not scelto.get("url"):
            continue
        if scelto.get("height", 0) < 1000:
            continue
        return {"id": vid, "url": scelto["url"], "durata": h["duration"]}
    return None


def cerca(query: str, evita_usate: bool = True,
          orientamento: str = "portrait",
          esigente: bool = False) -> Optional[Dict]:
    """Cerca una clip verticale. None se nessuna fonte ha nulla di nuovo.

    Con `esigente` si scartano le clip che non hanno niente a che vedere con
    la query, e si accetta di tornare a mani vuote. Lo usa il formato lungo,
    dove la clip accompagna una frase precisa: li' una clip sbagliata e' un
    non sequitur che dura mezzo minuto. Nei reel resta spento, perche' li' la
    clip e' uno sfondo per sei secondi e restare senza costa di piu'.
    """
    usate = _usate() if evita_usate else {}

    key = env("PEXELS_API_KEY")
    if not key:
        # Senza chiave Pexels si passa direttamente all'altro archivio.
        # Prima si usciva qui restituendo None, rendendo irraggiungibile il
        # ripiego proprio nel caso in cui serve di piu'.
        return _cerca_pixabay(query, usate, esigente)
    try:
        r = httpx.get(
            "https://api.pexels.com/videos/search",
            headers={"Authorization": key},
            params={
                "query": query,
                "orientation": orientamento,
                "per_page": 15,
                "size": "medium",
            },
            timeout=45,
        )
        r.raise_for_status()
        video = r.json().get("videos", [])
    except Exception as exc:
        print(f"    Pexels non risponde: {exc}")
        video = []

    for v in video:
        if str(v["id"]) in usate:
            continue
        # Si preferisce una clip che copra il reel senza ripetersi in loop.
        if v.get("duration", 0) < 5:
            continue
        # Pexels non espone i tag, ma l'indirizzo della pagina contiene la
        # descrizione della clip: .../video/child-using-a-party-blower-7331246/
        if esigente and not _pertinente(query, v.get("url", "")):
            continue
        file_hd = [
            f for f in v.get("video_files", [])
            # Il lato lungo deve arrivare a 1080: in verticale e' l'altezza,
            # in orizzontale la larghezza. Chiedere 1080 di altezza a un
            # filmato panoramico scartava quasi tutto il catalogo.
            if (f.get("height", 0) >= 1080 and f.get("width", 0) >= 600
                    if orientamento == "portrait"
                    else f.get("width", 0) >= 1920 and f.get("height", 0) >= 720)
        ]
        if not file_hd:
            continue
        # Il file più piccolo fra quelli in HD: scaricare 4K per un reel da
        # 6 secondi è tempo e banda buttati.
        file_hd.sort(key=lambda f: f.get("height", 0))
        return {"id": v["id"], "url": file_hd[0]["link"], "durata": v["duration"]}

    # Pexels non ha nulla di nuovo per questa scena: si prova l'altro archivio
    # prima di rinunciare e cambiare scena.
    return _cerca_pixabay(query, usate, esigente)


def scarica(clip: Dict) -> Optional[Path]:
    target = CACHE / f"{clip['id']}.mp4"
    if target.exists() and target.stat().st_size > 100_000:
        _segna(clip["id"], "riuso")
        return target
    # Due tentativi, non uno. Il timeout in mezzo a uno scaricamento non e' un
    # "questa clip non c'e'", e' la rete che ha singhiozzato — misurato sul
    # primo capitolo: due timeout di fila su un blocco, che e' finito con una
    # ripresa di ripiego mentre la clip giusta esisteva. Un secondo tentativo
    # su una connessione nuova costa qualche secondo e recupera il caso.
    for tentativo in (1, 2):
        try:
            with httpx.Client(timeout=180, follow_redirects=True) as client:
                with client.stream("GET", clip["url"]) as r:
                    r.raise_for_status()
                    with open(target, "wb") as f:
                        for blocco in r.iter_bytes(65536):
                            f.write(blocco)
            break
        except Exception as exc:
            target.unlink(missing_ok=True)
            if tentativo == 1:
                print(f"    scaricamento interrotto ({str(exc)[:60]}), riprovo")
                continue
            print(f"    scaricamento clip fallito: {exc}")
            return None

    _segna(clip["id"], "nuova")
    return target


def si_vede(video: Path) -> bool:
    """Dice se nella clip si vede qualcosa, guardando tre fotogrammi.

    Serve perché le scene notturne ("distant thunder sky", "moon night
    clouds") a volte restituiscono riprese che sono nero pieno con due lucine.
    Il testo sopra resta leggibile, quindi nessun controllo se ne accorge, ma
    il risultato sono nove secondi di schermo nero: chi scorre lo legge come un
    caricamento fallito e passa oltre. È già finito in produzione una volta.

    Anche il caso opposto va escluso — un cielo bianco slavato — perché lì il
    testo bianco sparisce del tutto.
    """
    import subprocess

    livelli = []
    for istante in ("1", "4", "7"):
        try:
            r = subprocess.run(
                [_ffmpeg(), "-v", "error", "-ss", istante, "-i", str(video),
                 "-frames:v", "1", "-vf", "scale=64:64,format=gray",
                 "-f", "rawvideo", "-"],
                capture_output=True, timeout=60,
            )
            if r.stdout:
                livelli.append(sum(r.stdout) / len(r.stdout))
        except Exception:
            return True          # nel dubbio si tiene: meglio un fondo brutto che nessuno

    if not livelli:
        return True
    medio = sum(livelli) / len(livelli)
    # 0 = nero pieno, 255 = bianco pieno. Sotto 16 la ripresa non si distingue
    # dal fondo nero; sopra 235 il testo bianco non si stacca più.
    if medio < 16:
        print(f"    scartata: quasi nera (luminosità {medio:.0f}/255)")
        return False
    if medio > 235:
        print(f"    scartata: slavata (luminosità {medio:.0f}/255)")
        return False
    return True


def per_frase(mood: str, frase: str, orientamento: str = "portrait") -> Optional[Path]:
    """Dal tono della frase al file video, provando più scene.

    Se una scena non dà risultati nuovi si passa alla successiva invece di
    arrendersi: con dodici opzioni per registro, restare senza sfondo è
    praticamente impossibile.
    """
    opzioni = list(SCENE.get(mood) or SCENE["reflective"])
    rnd = random.Random(hashlib.sha1(frase.encode()).hexdigest())
    rnd.shuffle(opzioni)

    ripiego = None
    for scena in opzioni[:5]:
        clip = cerca(scena, orientamento=orientamento)
        if not clip:
            continue
        path = scarica(clip)
        if not path:
            continue
        if si_vede(path):
            print(f"    sfondo: {scena}")
            return path
        # Si tiene da parte: se nessuna delle cinque scene passa il controllo,
        # un fondo brutto vale comunque più di un reel che non esce.
        ripiego = ripiego or path

    if ripiego:
        print("    nessuna scena leggibile: uso la prima trovata")
    return ripiego
