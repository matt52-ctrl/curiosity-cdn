"""Episodio lungo per YouTube: dieci curiosità a tema, con voce.

Perché esiste, e perché non è "gli Short ma più lunghi".

Il problema che risolve è di conversione, non di copertura. Gli Short portano
visualizzazioni e non portano iscritti — 853 viste e 3 iscritti, al terzo
giorno — perché chi scorre non si affeziona a un video che dura nove secondi.
Chi guarda otto minuti invece ha già investito, e si iscrive. Sono due
algoritmi separati con due funzioni diverse: gli Short fanno trovare la pagina,
il formato lungo la fa seguire.

⚠️ Serve una voce, e non è un vezzo. Il formato attuale — testo sovrimpresso su
filmato — regge nove secondi e regge quarantacinque. A otto minuti nessuno
legge: guarderebbe due schermate e uscirebbe. Senza narrazione questo formato
non esiste proprio.

La voce è Edge TTS: gratuita, senza chiave, neurale. Non è ElevenLabs, ma la
differenza fra "sintetica ma pulita" e "nessun video" non si discute.

Il montaggio segue la voce, non il contrario: ogni segmento dura quanto dura la
sua narrazione. Fissare le durate a priori avrebbe significato o tagliare la
voce a metà frase o lasciare silenzi.
"""
from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .config import OUTPUT_DIR, cfg
from .reel import _ffmpeg, _traccia_a_caso

WIDE = (1920, 1080)


def _voce() -> str:
    return cfg.get("lungo.voce", "en-US-AndrewNeural")


def narra(testo: str, dest: Path) -> Optional[float]:
    """Sintetizza la narrazione. Ritorna la durata in secondi.

    La velocità è rallentata di proposito: la cadenza predefinita di questi
    motori è da notiziario, e su una pagina che si presenta come pacata suona
    sbagliata prima ancora che si capisca cosa dice.
    """
    import edge_tts

    async def _fai():
        com = edge_tts.Communicate(
            testo, _voce(),
            rate=cfg.get("lungo.velocita", "-8%"),
        )
        await com.save(str(dest))

    try:
        asyncio.run(_fai())
    except Exception as exc:
        print(f"    narrazione fallita: {str(exc)[:110]}")
        return None
    if not dest.exists() or dest.stat().st_size < 1000:
        return None
    return _durata(dest)


def _durata(f: Path) -> Optional[float]:
    """Durata in secondi di un file audio o video."""
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(f)],
        capture_output=True, text=True,
    )
    try:
        return float(r.stdout.strip())
    except ValueError:
        return None


def testo_parlato(f: Dict) -> str:
    """Cosa dice la voce per una curiosità.

    Non è il testo del sito: lì si legge, qui si ascolta. All'orecchio le
    parentesi e le sigle non esistono, e una citazione letta per esteso
    ("Journal of Personality and Social Psychology") spezza il ritmo senza
    aggiungere nulla — a schermo si vede comunque.
    """
    import re

    pezzi = [f["hook"].rstrip(".") + "."]
    if f.get("fact"):
        pezzi.append(f["fact"])
    if f.get("detail"):
        pezzi.append(f["detail"])
    testo = " ".join(pezzi)

    # Ripuliture per l'orecchio: le abbreviazioni lette a voce suonano male.
    testo = re.sub(r"\be\.g\.\s*", "for example, ", testo)
    testo = re.sub(r"\bi\.e\.\s*", "that is, ", testo)
    testo = re.sub(r"\bvs\.?\s", "versus ", testo)
    testo = re.sub(r"\s*\([^)]{0,80}\)", "", testo)      # incisi fra parentesi
    testo = re.sub(r"\s{2,}", " ", testo)
    return testo.strip()


SCENE_SCHEMA = {
    "type": "object",
    "properties": {
        "scene": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "hook": {"type": "string"},
                    "scena": {"type": "string"},
                },
                "required": ["hook", "scena"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["scene"],
    "additionalProperties": False,
}


def scene(fatti: List[Dict]) -> Dict[str, str]:
    """Una scena da disegnare per ogni curiosita'. Ritorna {hook: scena}.

    PERCHE' ESISTE. Fino al 12 settembre 2026 l'episodio lungo era l'unico
    formato del canale senza identita' visiva: `_segmento` chiamava
    `footage.per_frase`, che sceglie un filmato d'archivio guardando SOLO
    l'umore. Dietro dieci capitoli diversi finivano dieci filmati che non
    parlavano di nessuno di loro, e soprattutto non erano incisioni — il video
    piu' lungo e piu' visibile del canale era l'unico che non si riconosceva.

    Perche' una chiamata sola e non una per capitolo: le scene devono anche
    essere diverse fra loro, e un modello che le scrive tutte insieme lo vede.
    Dieci chiamate separate producevano dieci stanze vuote.

    Perche' non si riusa `image_query` degli Short: la tabella `facts` non ce
    l'ha. Quel campo lo scrive `lines.generate` al momento di montare un reel
    e vive nella frase, non nella curiosita' — qui le curiosita' arrivano dal
    database, mesi dopo.

    Se la chiamata fallisce si torna a `{}` e ogni capitolo ripiega
    sull'archivio, che e' il comportamento di prima: un episodio meno
    riconoscibile esce comunque, un episodio che non esce non lo recupera
    nessuno.
    """
    from .llm import ask_json

    elenco = "\n".join(f"- {f['hook'].rstrip('.')}" for f in fatti)
    sistema = """You describe the scene to be engraved behind each chapter of a
video essay about how the human mind works.

Each scene is rendered as an antique copperplate engraving. You do not write
the style — that is added automatically. You write only WHAT IS IN THE FRAME.

RULES
  · One concrete, physical, observable scene. A thing in a place. Never an
    abstraction, never a metaphor that needs explaining, never a diagram.
  · No text anywhere in the scene, and nothing that invites a label: no jars,
    no packaging, no books seen front-on, no shop fronts, no signage, no
    screens with anything on them. Measured on 10/9/2026: a kitchen counter
    produces labelled jars and a fake signature, an empty corridor produces a
    clean plate. It is the scene that decides, not the style.
  · When a person is in the scene, write "a solitary adult in a long plain
    overcoat, seen from behind". Always that figure, never a face.
  · The ten scenes must not repeat each other. Vary the place, the distance
    and what is in it — a room, a landscape, an object alone on a surface, a
    figure at a distance.
  · Under 25 words each."""

    try:
        d = ask_json(
            sistema,
            f"Write one scene for each of these findings:\n{elenco}",
            SCENE_SCHEMA, effort="medium", max_tokens=2000,
        )
    except Exception as exc:
        print(f"    scene non scritte ({str(exc)[:60]}): sfondi d'archivio")
        return {}

    return {v["hook"].rstrip("."): v["scena"].strip()
            for v in d.get("scene", []) if v.get("scena")}


def _sfondo(f: Dict, scena: str) -> Tuple[Optional[Path], bool]:
    """Lo sfondo del capitolo: (percorso, e_un_immagine).

    L'incisione generata viene prima, il filmato d'archivio resta la rete.
    Come negli Short (`engine/reel.py:_sfondo_generato`) e per la stessa
    ragione: uno sfondo mancante non deve mai costare l'uscita di un video.

    Dieci incisioni a 163 neuroni l'una sono 1.630 neuroni una volta a
    settimana. Ci stanno dentro la dotazione giornaliera solo da quando i due
    Leonardo sono usciti dalla rotazione — vedi `cloudflare_modelli` in
    config.yaml. Con quelli accesi, questa funzione avrebbe trovato 429.
    """
    from . import footage, neuroni, visuals

    if scena and cfg.get("lungo.sfondo_generato", True):
        try:
            immagine = visuals.generate(scena, modello=neuroni.ECONOMICO)
        except Exception as exc:
            print(f"    incisione non generata ({str(exc)[:60]})")
            immagine = None
        if immagine and immagine.path and immagine.path.exists():
            return immagine.path, True

    clip = footage.per_frase(f.get("mood", "reflective"), f["hook"],
                            orientamento="landscape")
    return clip, False


def _catena_sfondo(e_immagine: bool, durata: float, w: int, h: int) -> str:
    """I filtri che portano lo sfondo a [bg].

    SULL'INCISIONE SI RIEMPIE, NON SI RITAGLIA. Flux rende un quadrato da
    1024; portarlo a coprire un 16:9 con `increase`+`crop` butterebbe il 44%
    dell'altezza, cioe' proprio il tratteggio che rende riconoscibile lo
    stile. E' lo stesso conto che il 10 settembre ha fatto passare reel.css da
    `cover` a `contain`, su un fotogramma di forma diversa. Qui la tavola si
    posa intera al centro e ai lati resta il nero — che non e' una banda nera
    di ripiego: il fondo dell'incisione e' gia' un nero quasi pieno, quindi il
    bordo non si vede e la tavola sembra incorniciata invece che tagliata.

    La carrellata c'e' solo sull'immagine, per la ragione misurata in
    `engine/reel.py:_fondo`: su un filmato che si muove da solo lo
    scala-e-riscala toglie movimento invece di aggiungerne, su una tavola
    ferma e' l'unico movimento che esiste.
    """
    if not e_immagine:
        return (f"[0:v]scale={w}:{h}:force_original_aspect_ratio=increase,"
                f"crop={w}:{h},setsar=1[bg]")

    n = max(1, int(durata * 30))
    zoom = float(cfg.get("lungo.carrellata_zoom", 1.12))
    passo = max(0.0001, (zoom - 1.0) / n)
    lato = min(w, h)
    return (
        f"[0:v]scale={lato}:{lato}:force_original_aspect_ratio=decrease,"
        f"zoompan=z='min(zoom+{passo:.6f},{zoom})':d={n}"
        f":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={lato}x{lato}:fps=30,"
        f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1[bg]"
    )


def _segmento(f: Dict, indice: int, out: Path, scena: str = "") -> Optional[Tuple[Path, float]]:
    """Un blocco: narrazione + sfondo + testo. Ritorna (video, durata)."""
    from . import render

    ff = _ffmpeg()
    w, h = WIDE

    voce_mp3 = out / f"voce-{indice:02d}.mp3"
    durata = narra(testo_parlato(f), voce_mp3)
    if not durata:
        return None

    clip, e_immagine = _sfondo(f, scena)
    if not clip:
        print(f"    nessuno sfondo per «{f['hook'][:40]}», salto")
        return None

    # A schermo solo l'ancora e la fonte: il contenuto lo porta la voce.
    # Scriverlo tutto significherebbe far leggere invece che ascoltare, e a
    # otto minuti la lettura non la sostiene nessuno.
    slide = {"kicker": "", "headline": f["hook"],
             "body": f.get("source_hint", "") or "", "image_query": "",
             "image_kind": "concept"}
    png = render.render_slides([slide], f"lungo-{indice:02d}", "wide",
                               size=WIDE, transparent=True)[0]

    seg = out / f"seg-{indice:02d}.mp4"
    # -loop 1 sul PNG: senza, la sovrimpressione dura un fotogramma solo e il
    # resto del segmento resta muto di testo. È già successo sui reel.
    # `-loop 1` sull'incisione, `-stream_loop -1` sul filmato: un'immagine
    # ferma fornisce un fotogramma solo e senza il loop il segmento durerebbe
    # 1/30 di secondo. Vale per lo sfondo esattamente come per il PNG del
    # testo qui sotto, dove lo stesso errore era gia' costato i reel.
    subprocess.run([
        ff, "-y",
        *(["-loop", "1"] if e_immagine else ["-stream_loop", "-1"]),
        "-i", str(clip),
        "-loop", "1", "-i", str(png),
        "-i", str(voce_mp3),
        "-filter_complex",
        f"{_catena_sfondo(e_immagine, durata, w, h)};"
        f"[bg][1:v]overlay=0:0:format=auto[v]",
        "-map", "[v]", "-map", "2:a",
        "-t", f"{durata:.3f}",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "21",
        "-pix_fmt", "yuv420p", "-r", "30",
        "-c:a", "aac", "-b:a", "160k",
        str(seg),
    ], check=True, capture_output=True)
    return seg, durata


def costruisci(fatti: List[Dict], titolo_tema: str,
               nome: str) -> Optional[Tuple[Path, List[Dict]]]:
    """Monta l'episodio. Ritorna (video, capitoli).

    I capitoli non sono un ornamento: YouTube li trasforma in segmenti
    navigabili, e uno spettatore che salta a ciò che gli interessa resta,
    mentre uno che non trova quello che cercava esce. Si ricavano dalle durate
    reali dei segmenti, quindi sono esatti per costruzione.
    """
    if not fatti:
        return None

    ff = _ffmpeg()
    out = OUTPUT_DIR / f"lungo-{nome}"
    out.mkdir(parents=True, exist_ok=True)

    # Le scene si chiedono tutte insieme, PRIMA del giro: devono essere
    # diverse fra loro, e questo lo vede solo chi le scrive tutte in una volta.
    # Se la chiamata fallisce il dizionario resta vuoto e ogni capitolo
    # ripiega sull'archivio, un capitolo per volta.
    disegni = scene(fatti)
    if disegni:
        print(f"  {len(disegni)} scene da incidere\n")

    segmenti: List[Path] = []
    capitoli: List[Dict] = []
    t = 0.0
    for i, f in enumerate(fatti):
        print(f"  [{i + 1}/{len(fatti)}] {f['hook'][:56]}")
        r = _segmento(f, i, out, disegni.get(f["hook"].rstrip("."), ""))
        if not r:
            continue
        seg, dur = r
        segmenti.append(seg)
        capitoli.append({"secondi": t, "titolo": f["hook"].rstrip("."),
                         "hook": f["hook"]})
        t += dur

    if len(segmenti) < 2:
        print("    meno di due segmenti montati: episodio annullato")
        return None

    elenco = out / "lista.txt"
    elenco.write_text("".join(f"file '{s.resolve()}'\n" for s in segmenti))
    grezzo = out / "grezzo.mp4"
    subprocess.run([ff, "-y", "-f", "concat", "-safe", "0", "-i", str(elenco),
                    "-c", "copy", str(grezzo)], check=True, capture_output=True)

    finale = out / "episodio.mp4"
    musica = _traccia_a_caso(nome, fatti[0].get("mood", "reflective"))
    if musica:
        # La musica sta molto sotto la voce e non si ferma fra un segmento e
        # l'altro: se ripartisse a ogni blocco si sentirebbero le giunture, ed
        # è esattamente ciò che fa capire che un video è assemblato.
        vol = float(cfg.get("lungo.volume_musica", 0.10))
        subprocess.run([
            ff, "-y", "-i", str(grezzo), "-stream_loop", "-1", "-i", str(musica),
            "-filter_complex",
            f"[1:a]volume={vol},afade=t=out:st={max(0, t - 4):.2f}:d=4[m];"
            f"[0:a][m]amix=inputs=2:duration=first:dropout_transition=0[a]",
            "-map", "0:v", "-map", "[a]", "-c:v", "copy",
            "-c:a", "aac", "-b:a", "192k", "-t", f"{t:.3f}", str(finale),
        ], check=True, capture_output=True)
    else:
        grezzo.replace(finale)

    # Copertina: un fotogramma della prima curiosità, che è anche l'apertura
    # fredda. Senza, YouTube ne sceglie uno a caso e spesso è una dissolvenza.
    subprocess.run([ff, "-y", "-ss", "2", "-i", str(finale), "-frames:v", "1",
                    "-q:v", "2", str(out / "cover.jpg")],
                   check=False, capture_output=True)

    print(f"  → {finale}  ({t/60:.1f} minuti, {len(segmenti)} curiosità)")
    return finale, capitoli


def descrizione(tema: str, capitoli: List[Dict], fatti: List[Dict]) -> str:
    """Descrizione con i capitoli. I timestamp li legge YouTube da soli.

    Regola di YouTube: il primo capitolo deve stare a 0:00 e ne servono almeno
    tre, altrimenti li ignora in silenzio e restano righe di testo inutile.
    """
    from .config import cfg as _c

    ig = (_c.get("brand.handle", "") or "").lstrip("@")
    sito = (_c.get("sito.url", "") or "").rstrip("/")

    # Le prime due o tre righe sono le uniche che YouTube mostra prima di
    # «Altro»: se i collegamenti stanno solo in fondo li vede chi ha gia'
    # deciso di cercarli. Vanno in alto, e ripetuti in fondo per chi scorre
    # fino alla fine — sono due comportamenti diversi, non lo stesso due volte.
    #
    # Qui gli indirizzi sono per esteso, non chiocciole: sui video lunghi
    # YouTube rende i link cliccabili (a differenza degli Short, dove non lo
    # fa mai). Serve pero' il canale verificato, altrimenti restano testo.
    # Il numero si conta, non si scrive a mano: se un segmento salta per
    # mancanza di filmato l'episodio ne ha nove, e una descrizione che ne
    # promette dieci si smentisce da sola nel primo minuto.
    NUMERI = {2: "two", 3: "three", 4: "four", 5: "five", 6: "six",
              7: "seven", 8: "eight", 9: "nine", 10: "ten"}
    quanti = NUMERI.get(len(fatti), str(len(fatti)))

    # La richiesta sta in seconda riga, dentro le due che YouTube mostra prima
    # di «Altro». Prima non c'era affatto: la descrizione elencava il sito e
    # Instagram — cioè mandava altrove il pubblico del canale che stiamo
    # cercando di far crescere — e non chiedeva mai l'iscrizione, lo stesso
    # difetto che avevano gli Short.
    richiesta = (_c.get("cta.testo.youtube", "") or "").strip()

    righe = [
        f"{tema} — {quanti} things your mind does without asking you.",
    ]
    if richiesta:
        righe.append(richiesta)
    else:
        righe.append("Every claim here names the study behind it.")
    righe.append("")
    if sito:
        righe.append(f"Full archive and sources: {sito}")
    if ig:
        righe.append(f"One of these every day: https://instagram.com/{ig}")
    righe.append("")

    if len(capitoli) >= 3:
        righe.append("Chapters")
        for c in capitoli:
            m, s = divmod(int(c["secondi"]), 60)
            righe.append(f"{m}:{s:02d} {c['titolo'][:70]}")
        righe.append("")

    fonti = [f.get("source_hint") for f in fatti if f.get("source_hint")]
    if fonti:
        righe.append("Studies referenced")
        righe += [f"· {x}" for x in fonti]
        righe.append("")

    # Ripetuta in fondo, e non è la stessa cosa detta due volte: chi legge le
    # prime due righe e chi scorre fino alle fonti sono due persone diverse,
    # e la seconda ha appena finito di verificare che la promessa è vera.
    if richiesta:
        righe.append(richiesta)
        righe.append("")

    righe.append("─────")
    if sito:
        righe.append(f"Website  {sito}")
    if ig:
        righe.append(f"Instagram  https://instagram.com/{ig}")
    yt = (_c.get("brand.youtube", "") or "").lstrip("@")
    if yt:
        righe.append(f"Shorts  https://youtube.com/@{yt}/shorts")

    return "\n".join(righe)


# ─── Titolo e miniatura ───────────────────────────────────────────────────────
#
# Si generano nella STESSA chiamata, e non è un risparmio: la regola che conta
# è che miniatura e titolo funzionino come un'unità sola — la miniatura apre
# una domanda, il titolo dà il contesto, e non devono mai ripetersi. Generati
# separatamente direbbero due volte la stessa cosa, che è lo spreco più comune
# su YouTube.
#
# ⚠️ Non abbiamo un volto, e i volti prendono il 20-30% di clic in più. Non è
# aggirabile senza cambiare natura al canale: si compete sulla tipografia.

COPERTINA_SCHEMA = {
    "type": "object",
    "properties": {
        "titolo": {"type": "string"},
        "miniatura": {"type": "string"},
        "occhiello": {"type": "string"},
    },
    "required": ["titolo", "miniatura", "occhiello"],
    "additionalProperties": False,
}


def titolo_e_miniatura(tema: str, fatti: List[Dict]) -> Dict[str, str]:
    """Titolo del video e testo della miniatura, progettati insieme."""
    from .llm import ask_json

    elenco = "\n".join(f"- {f['hook']}" for f in fatti[:10])
    sistema = f"""You write the title and thumbnail for a YouTube episode of
{cfg.get('brand.name')}, a channel about how the human mind actually works.

VOICE
{cfg.get('voice.guide')}

The title and the thumbnail are ONE unit. The thumbnail opens a gap; the title
tells the viewer what they are getting. They must never say the same thing —
that wastes the only two pieces of real estate you have.

THUMBNAIL TEXT
  Three words. Not four. At the size this is actually seen — 210 pixels wide,
  on a phone, between twenty other thumbnails — a fourth word becomes a smudge.
  Blunt and a little accusatory. It should feel like an accusation the viewer
  privately suspects is true.
  Good: "YOU CHOSE WRONG" · "IT WASN'T LUCK" · "YOU REMEMBER WRONG"
  Bad: "PSYCHOLOGY FACTS" (no gap) · "10 AMAZING FACTS" (hype, and it is what
  the title already says)

TITLE
  Under 60 characters. Say concretely what is inside — the thumbnail already
  did the provoking. A number helps because it promises an end.
  Never: "you won't believe", "shocking", "this will blow your mind".

EYEBROW
  Two or three words, the topic. Appears small above the thumbnail text."""

    try:
        d = ask_json(
            sistema,
            f"Topic: {tema}\n\nThe episode covers these findings:\n{elenco}\n\n"
            f"Write the title, the thumbnail text, and the eyebrow.",
            COPERTINA_SCHEMA, effort="medium", max_tokens=1200,
        )
    except Exception as exc:
        print(f"    titolo generato in automatico ({str(exc)[:60]})")
        return {"titolo": f"{len(fatti)} things your {tema} does without asking",
                "miniatura": "YOU DECIDE WRONG", "occhiello": tema}

    d["miniatura"] = " ".join((d.get("miniatura") or "").split()[:3]).upper()
    return d


def miniatura(testo: str, occhiello: str, out: Path) -> Optional[Path]:
    """Disegna la miniatura 1280×720: fondo a gradiente e testo, niente foto.

    Il punto di fuoco è l'occhiello in oro sopra al testo: il renderer scherma
    l'HTML, quindi colorare una singola parola avrebbe stampato il tag.

    Non prende un fotogramma del filmato, e la scelta è misurata — il perché sta
    in `templates/thumb.css`, sopra `.canvas`.
    """
    from . import render

    slide = {"kicker": occhiello, "headline": testo, "body": "",
             "image_query": "", "image_kind": "concept"}
    try:
        png = render.render_slides([slide], f"thumb-{out.stem}", "thumb",
                                   size=(1280, 720))[0]
    except Exception as exc:
        print(f"    miniatura non disegnata: {str(exc)[:80]}")
        return None
    dest = out.parent / "thumb.jpg"
    subprocess.run(["ffmpeg", "-y", "-i", str(png), "-q:v", "2", str(dest)],
                   check=False, capture_output=True)
    return dest if dest.exists() else png
