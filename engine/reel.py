"""Composizione dei Reel: fotogrammi statici → video verticale.

Perché i reel: i caroselli vengono mostrati soprattutto a chi già segue la
pagina, mentre i reel sono l'unico formato che Instagram distribuisce
sistematicamente agli sconosciuti. Su un account nuovo è la differenza fra
crescere e restare fermi.

Come funziona: si riusano le stesse battute del carosello, renderizzate a
1080×1920 dal template `reel`, e ogni fotogramma diventa un segmento video
con una lenta carrellata (effetto Ken Burns) e una dissolvenza verso il
successivo. Niente voce — scelta esplicita — quindi il ritmo del testo è
l'unica cosa che tiene lo spettatore.

⚠️ Un reel senza audio viene penalizzato da Instagram e agli utenti sembra
rotto. `music_path` in config permette di aggiungere una base royalty-free;
senza, il video esce muto.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Dict, List, Optional

from .config import OUTPUT_DIR, ROOT, cfg

REEL_SIZE = (1080, 1920)


def _ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if not exe:
        raise RuntimeError(
            "ffmpeg non trovato. macOS: brew install ffmpeg — "
            "GitHub Actions: sudo apt-get install -y ffmpeg"
        )
    return exe


def _run(args: List[str]) -> None:
    r = subprocess.run(args, capture_output=True, text=True)
    if r.returncode != 0:
        # ffmpeg scrive tutto su stderr, comprese le informazioni: si prendono
        # solo le ultime righe, dove sta l'errore vero.
        coda = "\n".join(r.stderr.strip().splitlines()[-6:])
        raise RuntimeError(f"ffmpeg fallito:\n{coda}")


def _durate(n: int) -> List[float]:
    """Quanto resta in campo ogni battuta.

    La prima dura di più perché deve essere letta da chi è appena arrivato e
    non sa ancora di cosa si parla; l'ultima meno, perché a quel punto o hai
    convinto o la persona è già andata. Totale intorno ai 14 secondi: i reel
    brevi hanno un completion rate molto più alto, e il completion rate è ciò
    che decide la distribuzione.
    """
    base = float(cfg.get("reel.seconds_per_beat", 2.8))
    d = [base] * n
    if n:
        d[0] = base + 1.0
        d[-1] = max(1.8, base - 0.4)
    return d


def compose(frames: List[Path], out: Path, music: Optional[Path] = None) -> Path:
    """Monta i fotogrammi in un mp4 verticale pronto per la pubblicazione."""
    ff = _ffmpeg()
    w, h = REEL_SIZE
    durate = _durate(len(frames))
    fade = float(cfg.get("reel.crossfade", 0.4))
    fps = 30

    tmp = out.parent / "_segmenti"
    tmp.mkdir(parents=True, exist_ok=True)
    segmenti: List[Path] = []

    for i, (frame, dur) in enumerate(zip(frames, durate)):
        seg = tmp / f"{i:02d}.mp4"
        # zoompan: carrellata lentissima. Senza, un'immagine ferma per tre
        # secondi legge come una slide e non come un video.
        n_frames = int(dur * fps)
        zoom_in = i % 2 == 0
        z = "min(zoom+0.0009,1.14)" if zoom_in else "max(1.14-on*0.0009,1.0)"
        _run([
            ff, "-y", "-loop", "1", "-i", str(frame),
            "-vf",
            f"scale={w*2}:{h*2},"
            f"zoompan=z='{z}':d={n_frames}:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
            f":s={w}x{h}:fps={fps},format=yuv420p",
            "-t", f"{dur}", "-r", str(fps),
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            str(seg),
        ])
        segmenti.append(seg)

    # Concatenazione con dissolvenza incrociata fra un segmento e il successivo.
    inputs: List[str] = []
    for s in segmenti:
        inputs += ["-i", str(s)]

    if len(segmenti) == 1:
        filtro, ultima = "[0:v]null[v]", "[v]"
    else:
        parti, offset, prec = [], 0.0, "[0:v]"
        for i in range(1, len(segmenti)):
            offset += durate[i - 1] - fade
            etichetta = f"[x{i}]"
            parti.append(
                f"{prec}[{i}:v]xfade=transition=fade:duration={fade}"
                f":offset={offset:.3f}{etichetta}"
            )
            prec = etichetta
        filtro, ultima = ";".join(parti), prec

    args = [ff, "-y"] + inputs
    if music and music.exists():
        args += ["-stream_loop", "-1", "-i", str(music)]

    args += ["-filter_complex", filtro, "-map", ultima]

    if music and music.exists():
        idx = len(segmenti)
        durata_totale = sum(durate) - fade * (len(segmenti) - 1)
        args += [
            "-map", f"{idx}:a",
            "-af", f"afade=t=out:st={max(0, durata_totale-1.2):.2f}:d=1.2,volume=0.35",
            "-shortest",
            "-c:a", "aac", "-b:a", "128k",
        ]

    args += [
        "-c:v", "libx264", "-preset", "medium", "-crf", "21",
        "-pix_fmt", "yuv420p", "-r", str(fps),
        "-movflags", "+faststart",
        str(out),
    ]
    _run(args)

    for s in segmenti:
        s.unlink(missing_ok=True)
    tmp.rmdir()
    return out


def _traccia_a_caso(seed: str, mood: str = "reflective") -> Optional[Path]:
    """Sceglie una traccia coerente col tono della frase.

    Il registro conta più della varietà: un brano allegro sotto una frase
    amara rovina il reel più del silenzio. La libreria è divisa in cartelle
    per stato d'animo (vedi setup_music.py) e qui si pesca solo dalla cartella
    giusta, ripiegando su `reflective` se quel registro è vuoto.

    La scelta è stabile per seme, non casuale a ogni chiamata: rigenerare lo
    stesso reel non cambia la musica, ma reel diversi non escono tutti uguali.
    """
    import hashlib
    import random as _random

    from .config import ASSETS_DIR

    base = ASSETS_DIR / "music"

    def _tracce(cartella: Path) -> list:
        if not cartella.is_dir():
            return []
        return sorted(list(cartella.glob("*.ogg")) + list(cartella.glob("*.mp3")))

    tracce = _tracce(base / mood) or _tracce(base / "reflective")
    # Compatibilità con la libreria piatta della prima versione.
    if not tracce:
        tracce = sorted(list(base.glob("*.ogg")) + list(base.glob("*.mp3")))
    if not tracce:
        return None

    rnd = _random.Random(hashlib.sha1(f"{mood}:{seed}".encode()).hexdigest())
    return rnd.choice(tracce)


def build_line(
    line: str,
    background: Path,
    name: str,
    seconds: Optional[float] = None,
    mood: str = "reflective",
    reveal: str = "",
) -> Path:
    """Un reel breve: una frase sola sopra un filmato, con musica.

    `background` può essere un video o un'immagine: nel secondo caso viene
    animato con una carrellata, per non sembrare una diapositiva.
    """
    from . import render

    ff = _ffmpeg()
    w, h = REEL_SIZE
    dur = float(seconds or cfg.get("reel.line_seconds", 6.5))

    # Due tempi: prima l'aggancio da solo, poi la rivelazione sotto. Il
    # secondo fotogramma contiene entrambi, così la rivelazione si aggiunge
    # invece di sostituire — chi legge lentamente non perde l'inizio.
    #
    # `reveal` vuoto ricade sul comportamento a un tempo solo: serve ai reel
    # vecchi, salvati prima che la struttura esistesse.
    if reveal:
        # La rivelazione SOSTITUISCE l'aggancio, non gli si aggiunge sotto.
        # Tenendo entrambi in campo la risposta finisce in corpo piccolo, che
        # e' esattamente il contrario di cio' che serve: la risposta e' la
        # parte che la gente ferma e manda a qualcuno.
        overlays = render.render_slides(
            [
                {"kicker": "", "headline": line, "body": ""},
                {"kicker": "", "headline": reveal, "body": ""},
            ],
            f"reel-{name}", "line", size=REEL_SIZE, transparent=True,
        )
    else:
        overlays = render.render_slides(
            [{"kicker": "", "headline": line, "body": ""}],
            f"reel-{name}", "line", size=REEL_SIZE, transparent=True,
        )
    overlay = overlays[0]
    out = overlay.parent / "reel.mp4"

    musica = _traccia_a_caso(line, mood)
    e_video = background.suffix.lower() in {".mp4", ".mov", ".webm", ".m4v"}

    args = [ff, "-y"]
    if e_video:
        # Il filmato viene tagliato alla durata della frase: le clip di stock
        # durano decine di secondi e a noi ne servono sei.
        args += ["-stream_loop", "-1", "-t", f"{dur}", "-i", str(background)]
        sfondo = (
            f"[0:v]scale={w}:{h}:force_original_aspect_ratio=increase,"
            f"crop={w}:{h},format=yuv420p[bg]"
        )
    else:
        n = int(dur * 30)
        args += ["-loop", "1", "-t", f"{dur}", "-i", str(background)]
        sfondo = (
            f"[0:v]scale={w*2}:{h*2},zoompan=z='min(zoom+0.0008,1.12)':d={n}"
            f":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={w}x{h}:fps=30,"
            f"format=yuv420p[bg]"
        )

    # `-loop 1` è indispensabile: senza, l'immagine del testo fornisce un solo
    # fotogramma a t=0 e il resto del video resta senza scritta. Il difetto è
    # invisibile in fase di montaggio — ffmpeg non segnala nulla — e si scopre
    # solo guardando il video oltre il primo istante.
    args += ["-loop", "1", "-t", f"{dur}", "-i", str(overlay)]
    if reveal:
        args += ["-loop", "1", "-t", f"{dur}", "-i", str(overlays[1])]
    if musica:
        args += ["-stream_loop", "-1", "-i", str(musica)]

    # Il testo entra in dissolvenza: comparire di colpo sul primo fotogramma
    # sembra un errore di codifica.
    # L'ultimo mezzo secondo resta senza testo, come il primo: cosi' quando
    # Instagram ripete il video l'anello non ha uno scatto visibile e chi
    # guarda lo rivede senza accorgersene. Le repliche sono uno dei segnali
    # piu' forti per la distribuzione.
    coda = 0.55

    if reveal:
        # L'aggancio resta in campo fino a `stacco`, poi lascia il posto alla
        # risposta. Il ritardo e' voluto: se la risposta arriva subito non c'e'
        # nessuna attesa da premiare, e il tempo di visione non cresce.
        stacco = float(cfg.get("reel.reveal_at", 3.4))
        filtro = (
            f"{sfondo};"
            f"[1:v]format=rgba,fade=t=in:st=0.2:d=0.45:alpha=1,"
            f"fade=t=out:st={stacco:.2f}:d=0.4:alpha=1[a];"
            f"[2:v]format=rgba,fade=t=in:st={stacco + 0.3:.2f}:d=0.45:alpha=1,"
            f"fade=t=out:st={dur - coda:.2f}:d=0.45:alpha=1[b];"
            f"[bg][a]overlay=0:0:format=auto[p];"
            f"[p][b]overlay=0:0:format=auto[v]"
        )
    else:
        filtro = (
            f"{sfondo};"
            f"[1:v]format=rgba,fade=t=in:st=0.2:d=0.5:alpha=1,"
            f"fade=t=out:st={dur - coda:.2f}:d=0.45:alpha=1[txt];"
            f"[bg][txt]overlay=0:0:format=auto[v]"
        )

    args += ["-filter_complex", filtro, "-map", "[v]"]
    if musica:
        # L'indice dell'audio dipende da quanti fotogrammi di testo ci sono.
        idx_audio = 3 if reveal else 2
        args += [
            "-map", f"{idx_audio}:a",
            "-af", f"volume=0.3,afade=t=out:st={max(0, dur-1.0):.2f}:d=1.0",
            "-c:a", "aac", "-b:a", "128k",
        ]
    args += [
        "-t", f"{dur}",
        "-c:v", "libx264", "-preset", "medium", "-crf", "23",
        # Tetto al bitrate: senza, una ripresa molto mossa (fumo, pioggia,
        # folla) produce file da decine di megabyte per pochi secondi. Pesano
        # sul repo, rallentano il caricamento e Instagram li ricomprime
        # comunque: la qualita' in piu' non arriva mai a chi guarda.
        "-maxrate", "6M", "-bufsize", "12M",
        "-pix_fmt", "yuv420p", "-r", "30",
        "-movflags", "+faststart",
        str(out),
    ]
    _run(args)
    return out


def build(slides: List[Dict[str, str]], name: str) -> Path:
    """Dalle battute al file mp4."""
    from . import render

    frames = render.render_slides(slides, f"reel-{name}", "reel", size=REEL_SIZE)
    out = frames[0].parent / "reel.mp4"

    music_cfg = cfg.get("reel.music_path", "")
    music = (ROOT / music_cfg) if music_cfg else None
    if music and not music.exists():
        print(f"    ⚠ base musicale non trovata ({music}): il reel esce muto")
        music = None

    return compose(frames, out, music)
