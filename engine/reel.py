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

def _gradazione() -> str:
    """Filtro colore applicato a OGNI filmato di sfondo, prima del testo.

    Le clip di stock arrivano da fonti diverse e non hanno niente in comune:
    una notturna desaturata e una al neon rosa acceso finivano nello stesso
    video una dopo l'altra, e la seconda non sembrava della stessa pagina.
    Su un profilo il colore e' meta' del riconoscimento.

    Perche' una curva e non `brightness`: abbassare la luminosita' di una
    quantita' fissa spegne le clip gia' scure — misurato, una notturna passava
    da 18 a 5 di luminosita' media, cioe' nero. La curva lascia i neri dove
    sono e comprime solo le luci alte, che sono il vero problema: quelle
    coprono il testo bianco e sfondano la palette.
    """
    sat = float(cfg.get("reel.grade_saturation", 0.45))
    if sat >= 1.0 and not cfg.get("reel.grade_highlights", True):
        return ""
    pezzi = [f"eq=saturation={sat}"]
    if cfg.get("reel.grade_highlights", True):
        pezzi.append("curves=all='0/0 0.5/0.44 1/0.78'")
    return ",".join(pezzi) + ","



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
    montate: List[Dict] = []

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
            f"crop={w}:{h},{_gradazione()}format=yuv420p[bg]"
        )
    else:
        n = int(dur * 30)
        args += ["-loop", "1", "-t", f"{dur}", "-i", str(background)]
        sfondo = (
            f"[0:v]scale={w*2}:{h*2},zoompan=z='min(zoom+0.0008,1.12)':d={n}"
            f":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={w}x{h}:fps=30,"
            f"{_gradazione()}format=yuv420p[bg]"
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
            f"[1:v]format=rgba,"
            f"fade=t=out:st={stacco:.2f}:d=0.4:alpha=1[a];"
            f"[2:v]format=rgba,fade=t=in:st={stacco + 0.3:.2f}:d=0.45:alpha=1,"
            f"fade=t=out:st={dur - coda:.2f}:d=0.45:alpha=1[b];"
            f"[bg][a]overlay=0:0:format=auto[p];"
            f"[p][b]overlay=0:0:format=auto[v]"
        )
    else:
        filtro = (
            f"{sfondo};"
            f"[1:v]format=rgba,"
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

    # Copertina esplicita. Senza, Instagram sceglie da sé un fotogramma e
    # spesso pesca prima che il testo compaia: nel feed e nella griglia il
    # reel appare come una clip di stock qualunque, senza motivo per fermarsi.
    # Il secondo scelto è dopo la dissolvenza d'ingresso e prima della
    # rivelazione, quindi mostra l'aggancio per intero.
    copertina = out.parent / "cover.jpg"
    try:
        _run([
            ff, "-y", "-ss", "1.2", "-i", str(out),
            "-frames:v", "1", "-q:v", "3", str(copertina),
        ])
    except Exception as exc:
        print(f"    copertina non estratta: {exc}")

    return out


def _ingresso(sfondo: Path, e_immagine: bool, durata: float) -> List[str]:
    """Come si apre lo sfondo in ffmpeg. Un filmato si ricicla in loop fino a
    coprire la durata; un'immagine ferma va aperta con `-loop 1`, perché
    `-stream_loop` su un file a fotogramma singolo non produce niente e il
    segmento esce vuoto senza che ffmpeg lo dica."""
    modo = ["-loop", "1"] if e_immagine else ["-stream_loop", "-1"]
    return modo + ["-t", f"{durata}", "-i", str(sfondo)]


def _coda_cta(ff: str, sfondo: Path, e_immagine: bool, nome: str, tmp: Path,
              indice: int, canale: str = "",
              domanda: str = "") -> Optional[Path]:
    """Il fotogramma finale: cosa chiediamo a chi è arrivato in fondo.

    Perché esiste: fino al 20 agosto 2026 i video finivano e basta. L'ultima
    rivelazione sfumava, il file era finito, e a chi aveva guardato tutto non
    veniva chiesto niente — né di iscriversi né di rispondere. Non era una CTA
    debole, non c'era proprio.

    Perché in coda e non sopra l'ultima rivelazione: la rivelazione è la parte
    che la gente ferma e rimanda a qualcuno, e dividere quel fotogramma fra la
    risposta e una richiesta le indebolisce entrambe. Il prezzo di metterla in
    coda è che il video si allunga, ed è un prezzo che si paga in un modo
    subdolo — vedi la nota su `cta.secondi` in config.yaml: la percentuale di
    visione scende anche se non guarda meno nessuno.

    Lo sfondo è quello dell'ultima curiosità, già scaricato: cercarne uno nuovo
    costerebbe una chiamata a Pexels per due secondi di video.

    `domanda` arriva da fuori perché non è sempre la stessa: «Which one was new
    to you?» presuppone che ce ne fosse più di una, e in un video a curiosità
    singola è una domanda senza senso. Vuota = si usa `cta.domanda`.

    ⚠️ I parametri di codifica devono restare IDENTICI a quelli dei segmenti
    del ciclo qui sopra. La concatenazione usa `-c:v copy` e non ricodifica:
    con un fps o un profilo diverso il video esce corrotto e ffmpeg non dice
    niente. Se si tocca la riga di codifica di sopra, va toccata anche questa.
    """
    from . import render

    domanda = (domanda or cfg.get("cta.domanda", "") or "").strip()
    if not domanda:
        return None
    azione = (cfg.get(f"cta.azione.{canale}", "")
              or cfg.get("cta.azione.default", "") or "").strip()
    dur = float(cfg.get("cta.secondi", 2.4))

    # La chiocciola non si scrive: `brand.watermark` è già stampato in fondo a
    # ogni fotogramma, questo compreso.
    overlay = render.render_slides(
        [{"kicker": "", "headline": domanda, "body": azione}],
        f"{nome}-cta", "line", size=REEL_SIZE, transparent=True,
    )[0]

    w, h = REEL_SIZE
    seg = tmp / f"{indice:02d}.mp4"
    filtro = (
        f"{_fondo(sfondo, e_immagine, dur, w, h, indice)};"
        # Entra subito ma non di colpo: 0,35 di dissolvenza. Su due secondi e
        # mezzo un ingresso più lento mangerebbe il tempo di lettura.
        f"[1:v]format=rgba,fade=t=in:st=0:d=0.35:alpha=1,"
        f"fade=t=out:st={dur - 0.45:.2f}:d=0.4:alpha=1[t];"
        f"[bg][t]overlay=0:0:format=auto[v]"
    )
    _run([
        ff, "-y",
        *_ingresso(sfondo, e_immagine, dur),
        "-loop", "1", "-t", f"{dur}", "-i", str(overlay),
        "-filter_complex", filtro, "-map", "[v]",
        "-t", f"{dur}",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
        "-maxrate", "6M", "-bufsize", "12M",
        "-pix_fmt", "yuv420p", "-r", "30",
        str(seg),
    ])
    return seg


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


def _sfondo_a_tema(voce: Dict) -> Optional[Path]:
    """Un filmato d'archivio che parla DI QUESTA curiosita'. None se non c'e'.

    Perche' viene prima dell'immagine generata, ribaltando l'ordine del 4
    settembre. Quel giorno la scelta era fra due cose imperfette: un filmato
    che si muove ma parla d'altro (`per_frase` sceglie per umore, non per
    argomento) e un'immagine ferma ma in tema. Si era scelta la seconda,
    pagando il movimento — misurato: 1,13 contro 3,63 su 255.

    Il terzo caso non lo avevamo perche' non avevamo la domanda giusta da
    fare all'archivio. Ora ce l'abbiamo: `image_query` descrive una scena
    filmabile, ed e' esattamente cio' che si digita in una libreria di
    filmati. Un filmato in tema vince su TUTTE le misure prese — si muove
    come l'archivio e parla dell'argomento come l'immagine — quindi non e'
    un compromesso diverso, e' la casella che mancava.

    `esigente=True` non e' un dettaglio: senza, Pexels restituisce il primo
    risultato qualunque sia, e "in tema" tornerebbe a essere una parola. Con
    il filtro acceso si accetta di tornare a mani vuote, e in quel caso si
    scende all'immagine generata.
    """
    if not cfg.get("reel.sfondo_a_tema", True):
        return None
    query = (voce.get("image_query") or "").strip()
    if not query:
        return None
    # L'import sta qui dentro come in tutto il resto del file: `footage` tira
    # dietro httpx e ffmpeg, e chi importa `reel` per una funzione di montaggio
    # non deve pagarli.
    from . import footage
    try:
        clip = footage.cerca(query, orientamento="portrait", esigente=True)
        if not clip:
            return None
        path = footage.scarica(clip)
    except Exception as exc:
        print(f"    filmato in tema non cercato ({str(exc)[:60]})")
        return None
    if not path:
        return None
    # Lo stesso controllo di leggibilita' che fa `per_frase`: sopra a un fondo
    # troppo chiaro o troppo affollato il testo non si legge, e un video
    # illeggibile e' peggio di uno sfondo fuori tema.
    if not footage.si_vede(path):
        print("    filmato in tema illeggibile: provo l'immagine generata")
        return None
    print(f"    sfondo in tema: {query[:60]}")
    return path


def _sfondo_generato(voce: Dict, seme: str) -> Optional[Path]:
    """L'immagine generata per questa curiosita', o None per usare l'archivio.

    PERCHE' UN'IMMAGINE E NON UN FILMATO. Non e' un ripiego estetico: e' la
    prima volta che lo sfondo parla della curiosita'. `footage.per_frase` della
    frase usa SOLO il seme casuale, e sceglie il filmato guardando l'umore —
    quindi il video sul "rifiuti le idee che non sono tue" e quello sul
    "confondi la paura con l'attrazione" pescano dallo stesso mucchio. Con
    `image_query` scritto dallo scrittore, invece, dietro alla frase finisce
    una scena che la riguarda.

    Perche' non i video generativi, che erano la richiesta iniziale: al 3
    settembre 2026 non esiste nessuna API di generazione video con un piano
    gratuito utilizzabile a questi volumi. Verificato provider per provider.
    Le immagini generate sono cio' che resta, e costano 163 neuroni l'una.

    Ritorna None senza lamentarsi in tutti i casi dubbi. Uno sfondo mancante
    non deve MAI costare l'uscita di una fascia oraria: si torna al filmato
    d'archivio, che e' il comportamento di sempre.
    """
    if not cfg.get("reel.sfondo_generato", False):
        return None
    query = (voce.get("image_query") or "").strip()
    if not query:
        return None

    from . import neuroni, visuals

    try:
        # Sempre il modello economico, mai la rotazione del giorno. Quattro
        # sfondi al giorno su un modello Leonardo sarebbero undicimila
        # neuroni — l'intera quota — spesi dietro a del testo, sotto una
        # gradazione che li scurisce. La rotazione resta nei caroselli, dove
        # l'immagine e' il contenuto e non lo sfondo.
        #
        # `generate` tiene gia' una cache su disco per prompt e modello:
        # rimontare lo stesso video non ricompra l'immagine, e i neuroni non
        # si spendono due volte per la stessa scena.
        immagine = visuals.generate(query, modello=neuroni.ECONOMICO)
    except Exception as exc:
        print(f"    immagine non generata ({str(exc)[:60]}): uso l'archivio")
        return None
    if not immagine or not immagine.path or not immagine.path.exists():
        return None
    return immagine.path


def _fondo(sfondo: Path, e_immagine: bool, durata: float, w: int, h: int,
           indice: int = 0) -> str:
    """La catena di filtri che porta lo sfondo a [bg], filmato o immagine.

    ⚠️ LA CARRELLATA C'E' SOLO SULLE IMMAGINI, e la differenza e' misurata.
    Sui FILMATI era stata provata e bocciata: sullo stesso filmato, con e
    senza, riduceva il movimento invece di aumentarlo (3,63 → 2,59 su 255).
    Lo scala-e-riscala sfoca il dettaglio fine e lo zoom e' troppo lento per
    compensare — su un filmato che gia' si muove da solo si perde e basta.

    Su un'immagine ferma il conto e' opposto: senza carrellata il movimento e'
    esattamente zero, e undici secondi di fotografia immobile si leggono come
    una diapositiva. Qui lo zoom non compete con nessun movimento: e' l'unico
    che c'e'. E' la stessa ragione per cui `compose` ce l'ha da sempre.

    PERCHE' LA CARRELLATA E' PIU' AMPIA CHE IN `compose` (1,40 invece di 1,14,
    e con una deriva diagonale invece che centrata). Misurato il 3 settembre
    2026 sulla stessa immagine, stesso metro dell'agosto scorso — differenza
    media fra fotogrammi consecutivi, su 255:

        zoom 1,14 centrato          0,60      <- quello di `compose`
        zoom 1,25 centrato          0,69
        zoom 1,25 + deriva          1,48
        zoom 1,40 + deriva          1,91
        filmato d'archivio          3,63

    La deriva rende piu' dello zoom: 1,25 con la deriva batte 1,40 senza. E
    costa quasi niente in nitidezza — misurata sull'energia dei bordi a zoom
    massimo, 0,53 a 1,14 contro 0,51 a 1,40. Il dettaglio fine se n'e' gia'
    andato prima, nell'ingrandimento del quadrato 1024 al 9:16: una volta
    pagato quello, allargare la carrellata e' gratis.

    Resta il fatto che 1,91 e' meta' di un filmato vero. E' il prezzo delle
    immagini generate, e va saputo: si guadagna uno sfondo che parla della
    curiosita' e si perde movimento. Da qui si spegne con
    `reel.sfondo_generato: false`.
    """
    if not e_immagine:
        return (f"[0:v]scale={w}:{h}:force_original_aspect_ratio=increase,"
                f"crop={w}:{h},{_gradazione()}format=yuv420p[bg]")

    n = max(1, int(durata * 30))
    zoom = float(cfg.get("reel.carrellata_zoom", 1.40))
    passo = max(0.0001, (zoom - 1.0) / n)

    # Direzione alternata, come lo zoom in/out di `compose`: tre segmenti che
    # derivano tutti verso lo stesso angolo si leggono come un unico movimento
    # lungo, e il taglio fra l'uno e l'altro sparisce.
    verso = indice % 4
    dx = f"(iw-iw/zoom)*on/{n}" if verso in (0, 3) else f"(iw-iw/zoom)*(1-on/{n})"
    dy = f"(ih-ih/zoom)*on/{n}" if verso in (0, 1) else f"(ih-ih/zoom)*(1-on/{n})"

    return (
        f"[0:v]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},"
        f"zoompan=z='min(zoom+{passo:.6f},{zoom})':d={n}:x='{dx}':y='{dy}'"
        f":s={w}x{h}:fps=30,{_gradazione()}format=yuv420p[bg]"
    )


def build_multi(voci: List[Dict], name: str,
                totale: Optional[float] = None,
                massimo: Optional[float] = None,
                canale: str = "",
                domanda_cta: str = "") -> tuple:
    """Ritorna (percorso, voci_effettivamente_montate).

    Le due cose vanno insieme: se un filmato non si trova quel segmento salta,
    e chi scrive la descrizione deve sapere quali curiosita' sono finite
    davvero nel video. Annunciarne una che non c'e' e' peggio che ometterla.
    """
    """Versione lunga per YouTube: piu' curiosita' concatenate in un video solo.

    Perche' esiste: Instagram premia i reel da 7-15 secondi, YouTube gli Short
    da 30-45. Con un formato solo si finisce per andare bene su una piattaforma
    e male sull'altra. Questa funzione costruisce la variante YouTube tenendo
    identici stile, tipografia e registro musicale — cambia solo il montaggio.

    Ogni curiosita' ha il proprio filmato: cambiare scena ogni dieci secondi
    e' anche cio' che tiene lo spettatore oltre la meta', dove un fondo unico
    per trenta secondi lo perderebbe.
    """
    from . import footage, render

    if not voci:
        return None, []

    ff = _ffmpeg()
    w, h = REEL_SIZE

    # La durata per curiosita' si ricava dal totale, non e' fissa: con tre
    # curiosita' a undici secondi il video sta nella finestra premiata, con
    # due sole cadrebbe a ventidue e ne uscirebbe. Meglio dare piu' respiro
    # a ciascuna che consegnare un video troppo corto.
    #
    # Il limite superiore serve al caso opposto: con una curiosita' sola la
    # divisione darebbe trentatre' secondi di frase ferma, che e' peggio di
    # un video breve.
    # La durata complessiva arriva da fuori perche' ogni piattaforma ha la
    # sua finestra: YouTube premia i 30-45 secondi, TikTok i 42-54 sui
    # contenuti che spiegano qualcosa. Un valore fisso andrebbe bene su una e
    # male sull'altra — lo stesso errore che avevamo fatto con i 9 secondi di
    # Instagram mandati su YouTube.
    totale = float(totale if totale is not None
                   else cfg.get("reel.long_target_seconds", 33.0))
    massimo = float(massimo if massimo is not None
                    else cfg.get("reel.long_max_seconds_per_fact", 16.0))
    per_voce = min(massimo, totale / max(1, len(voci)))
    # Quando l'aggancio lascia il posto alla rivelazione. Era il 40% della
    # durata — 4,4 secondi su 11 — ma un aggancio di sette parole si legge in
    # due. Restavano oltre due secondi in cui lo spettatore aveva gia' letto
    # tutto e non succedeva piu' niente: e' li' che il pollice scorre, ed e'
    # la spiegazione piu' semplice del 61% che se ne andava.
    #
    # Ora si calcola sul testo: tempo di lettura piu' un respiro. Un aggancio
    # corto cede prima, uno lungo ha il tempo che gli serve.
    parole = len((voci[0].get("hook") or "").split()) if voci else 7
    lettura = parole / float(cfg.get("reel.parole_al_secondo", 3.5))
    respiro = float(cfg.get("reel.respiro", 0.7))
    stacco = min(per_voce * 0.55, max(1.6, lettura + respiro))
    print(f"    {len(voci)} curiosita' x {per_voce:.1f}s = {per_voce*len(voci):.0f}s")

    out_dir = OUTPUT_DIR / f"yt-{name}"
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp = out_dir / "_segmenti"
    tmp.mkdir(exist_ok=True)

    segmenti: List[Path] = []
    montate: List[Dict] = []
    # (percorso, è_un_immagine): la coda con la CTA riusa lo sfondo dell'ultima
    # curiosità e deve sapere quale dei due tipi ha in mano, perché un'immagine
    # si apre con `-loop 1` e un filmato con `-stream_loop`.
    ultimo_sfondo: Optional[tuple] = None
    for i, v in enumerate(voci):
        hook = v.get("hook") or v.get("line", "")
        reveal = v.get("reveal", "")

        # Tre tentativi, dal migliore al peggiore.
        #
        #   1. un filmato d'archivio CHE PARLA di questa curiosita': si muove
        #      ed e' in tema, quindi vince su entrambe le misure;
        #   2. l'immagine generata: in tema ma ferma, con la carrellata a
        #      simulare il movimento;
        #   3. il filmato scelto per umore: si muove ma parla d'altro.
        #
        # Il terzo e' il comportamento storico e resta come rete: uno sfondo
        # mancante non deve mai costare l'uscita di una fascia oraria.
        sfondo = _sfondo_a_tema(v)
        e_immagine = False
        if sfondo is None:
            sfondo = _sfondo_generato(v, hook + str(i))
            e_immagine = sfondo is not None
        if sfondo is None:
            sfondo = footage.per_frase(v.get("mood", "reflective"), hook + str(i))
        if not sfondo:
            print(f"    nessuno sfondo per la curiosita' {i+1}: la salto")
            continue
        montate.append(v)
        ultimo_sfondo = (sfondo, e_immagine)

        overlays = render.render_slides(
            [
                {"kicker": "", "headline": hook, "body": ""},
                {"kicker": "", "headline": reveal or hook, "body": ""},
            ],
            f"yt-{name}-{i}", "line", size=REEL_SIZE, transparent=True,
        )

        seg = tmp / f"{i:02d}.mp4"
        filtro = (
            # La carrellata sta dentro `_fondo`, e c'e' SOLO sulle immagini:
            # sui filmati era stata provata e bocciata a misura. La nota lunga
            # con i numeri e' li'.
            f"{_fondo(sfondo, e_immagine, per_voce, w, h, i)};"
            f"[1:v]format=rgba,fade=t=out:st={stacco:.2f}:d=0.4:alpha=1[a];"
            f"[2:v]format=rgba,fade=t=in:st={stacco + 0.3:.2f}:d=0.45:alpha=1,"
            f"fade=t=out:st={per_voce - 0.5:.2f}:d=0.4:alpha=1[b];"
            f"[bg][a]overlay=0:0:format=auto[p];"
            f"[p][b]overlay=0:0:format=auto[v]"
        )
        _run([
            ff, "-y",
            *_ingresso(sfondo, e_immagine, per_voce),
            "-loop", "1", "-t", f"{per_voce}", "-i", str(overlays[0]),
            "-loop", "1", "-t", f"{per_voce}", "-i", str(overlays[1]),
            "-filter_complex", filtro, "-map", "[v]",
            "-t", f"{per_voce}",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
            "-maxrate", "6M", "-bufsize", "12M",
            "-pix_fmt", "yuv420p", "-r", "30",
            str(seg),
        ])
        segmenti.append(seg)

    if not segmenti:
        return None, []

    durata = per_voce * len(segmenti)

    # La richiesta finale. Si aggiunge in coda, quindi non toglie tempo a
    # nessuna curiosita': il video si allunga di due secondi e mezzo.
    #
    # Se fallisce il video esce lo stesso, senza. Un fotogramma di CTA non
    # vale la perdita dell'intero montaggio — e la fascia oraria di uno Short
    # non si recupera.
    if ultimo_sfondo is not None:
        try:
            coda = _coda_cta(ff, ultimo_sfondo[0], ultimo_sfondo[1], name, tmp,
                             len(segmenti), canale, domanda_cta)
            if coda:
                segmenti.append(coda)
                durata += float(cfg.get("cta.secondi", 2.4))
        except Exception as exc:
            print(f"    fotogramma finale non montato: {str(exc)[:80]}")

    # Concatenazione + una sola traccia musicale sopra l'intero video: cambiare
    # brano a ogni curiosita' spezzerebbe il video in tre cose separate.
    elenco = tmp / "elenco.txt"
    elenco.write_text("".join(f"file '{s.name}'\n" for s in segmenti))
    musica = _traccia_a_caso(name, voci[0].get("mood", "reflective"))
    out = out_dir / "short.mp4"

    args = [ff, "-y", "-f", "concat", "-safe", "0", "-i", str(elenco)]
    if musica:
        args += ["-stream_loop", "-1", "-i", str(musica)]
    args += ["-c:v", "copy"]
    if musica:
        args += [
            "-map", "0:v", "-map", "1:a",
            "-af", f"volume=0.3,afade=t=out:st={durata - 1.5:.2f}:d=1.5",
            "-c:a", "aac", "-b:a", "128k", "-shortest",
        ]
    args += ["-movflags", "+faststart", str(out)]
    _run(args)

    for s in segmenti:
        s.unlink(missing_ok=True)
    elenco.unlink(missing_ok=True)
    tmp.rmdir()

    # Copertina: il primo aggancio in campo, come per i reel brevi.
    try:
        _run([ff, "-y", "-ss", "1.5", "-i", str(out), "-frames:v", "1",
              "-q:v", "3", str(out.parent / "cover.jpg")])
    except Exception:
        pass

    return out, montate
