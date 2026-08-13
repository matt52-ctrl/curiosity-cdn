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


def _segmento(f: Dict, indice: int, out: Path) -> Optional[Tuple[Path, float]]:
    """Un blocco: narrazione + filmato + testo. Ritorna (video, durata)."""
    from . import footage, render

    ff = _ffmpeg()
    w, h = WIDE

    voce_mp3 = out / f"voce-{indice:02d}.mp3"
    durata = narra(testo_parlato(f), voce_mp3)
    if not durata:
        return None

    clip = footage.per_frase(f.get("mood", "reflective"), f["hook"],
                            orientamento="landscape")
    if not clip:
        print(f"    nessun filmato per «{f['hook'][:40]}», salto")
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
    subprocess.run([
        ff, "-y",
        "-stream_loop", "-1", "-i", str(clip),
        "-loop", "1", "-i", str(png),
        "-i", str(voce_mp3),
        "-filter_complex",
        f"[0:v]scale={w}:{h}:force_original_aspect_ratio=increase,"
        f"crop={w}:{h},setsar=1[v0];"
        f"[v0][1:v]overlay=0:0:format=auto[v]",
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

    segmenti: List[Path] = []
    capitoli: List[Dict] = []
    t = 0.0
    for i, f in enumerate(fatti):
        print(f"  [{i + 1}/{len(fatti)}] {f['hook'][:56]}")
        r = _segmento(f, i, out)
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
    righe = [
        f"{tema} — ten things your mind does without asking you.",
        "Every claim here names the study behind it.",
        "",
    ]
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

    righe.append("─────")
    if sito:
        righe.append(f"Website  {sito}")
    if ig:
        righe.append(f"Instagram  https://instagram.com/{ig}")
    yt = (_c.get("brand.youtube", "") or "").lstrip("@")
    if yt:
        righe.append(f"Shorts  https://youtube.com/@{yt}/shorts")

    return "\n".join(righe)
