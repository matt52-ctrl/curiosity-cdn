"""Rendering slide: HTML/CSS → PNG via Chromium headless.

Perché HTML e non Pillow: la tipografia è il 90% di un post-immagine, e
ottenere kerning, interlinea, bilanciamento e auto-fit decenti in Pillow è
lavoro manuale infinito. Qui il layout sta in un template che puoi modificare
senza toccare Python, e il risultato è identico su 500 post.
"""
from __future__ import annotations

import base64
import html
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from playwright.sync_api import sync_playwright

from .config import ASSETS_DIR, OUTPUT_DIR, TEMPLATES_DIR, cfg

FONT_FILES = {
    "Display": "InstrumentSerif-Regular.ttf",
    "DisplayItalic": "InstrumentSerif-Italic.ttf",
    "Heavy": "ArchivoBlack-Regular.ttf",
    "Body": "Inter-Regular.ttf",
    "BodyMedium": "Inter-Medium.ttf",
    "Mono": "JetBrainsMono-Regular.ttf",
}


def _font_face_css() -> str:
    """Incorpora i font come data URI: nessun problema di path relativi
    dentro Chromium, e il file HTML resta autonomo se lo vuoi ispezionare."""
    blocks = []
    for family, filename in FONT_FILES.items():
        path = ASSETS_DIR / "fonts" / filename
        if not path.exists():
            continue
        b64 = base64.b64encode(path.read_bytes()).decode("ascii")
        blocks.append(
            f"@font-face{{font-family:'{family}';"
            f"src:url(data:font/ttf;base64,{b64}) format('truetype');"
            f"font-display:block;}}"
        )
    return "\n".join(blocks)


def _fit_class(text: str, limits=(28, 42, 56)) -> str:
    """Scala la headline in tre gradini invece di scriverla in un font size
    fisso: una headline di 18 caratteri e una di 58 non possono avere lo
    stesso corpo senza sembrare due pagine diverse."""
    n = len(text)
    if n <= limits[0]:
        return "fit-xl"
    if n <= limits[1]:
        return "fit-lg"
    if n <= limits[2]:
        return "fit-md"
    return "fit-sm"


def _esc(text: str) -> str:
    return html.escape(text, quote=False)


def build_html(
    slides: List[Dict[str, str]],
    template: str = "editorial",
    size: Optional[tuple] = None,
) -> str:
    css_path = TEMPLATES_DIR / f"{template}.css"
    if not css_path.exists():
        raise FileNotFoundError(
            f"Template '{template}' non trovato in {TEMPLATES_DIR}. "
            f"Disponibili: {[p.stem for p in TEMPLATES_DIR.glob('*.css')]}"
        )
    base_css = (TEMPLATES_DIR / "base.css").read_text(encoding="utf-8")
    theme_css = css_path.read_text(encoding="utf-8")

    w, h = size or (int(cfg.get("format.width", 1080)),
                    int(cfg.get("format.height", 1350)))
    watermark = _esc(cfg.get("brand.watermark", ""))
    total = len(slides)

    parts = []
    for i, s in enumerate(slides):
        kicker = _esc(s.get("kicker", ""))
        headline = _esc(s.get("headline", ""))
        body = _esc(s.get("body", ""))
        # Puntini di progressione: dicono al lettore quanto manca. Aumentano
        # misurabilmente il completion rate del carosello.
        dots = "".join(
            f'<span class="dot{" on" if j == i else ""}"></span>' for j in range(total)
        ) if total > 1 else ""
        # Su un post a immagine singola l'indice non indica nulla: va tolto,
        # non stampato come "01" solitario.
        index = f"{i + 1:02d} / {total:02d}" if total > 1 else ""
        swipe = (
            '<div class="swipe">swipe</div>'
            if total > 1 and i < total - 1
            else ""
        )
        # `solo` marca il caso a immagine singola, dove first e last coincidono:
        # senza una classe propria il post erediterebbe lo stile della slide di
        # chiusura e sembrerebbe un'altra pagina rispetto ai caroselli.
        flags = "".join(
            [
                " first" if i == 0 else "",
                " last" if i == total - 1 else "",
                " solo" if total == 1 else "",
            ]
        )
        # Livello fotografico. Il trattamento (desaturazione + velatura di
        # colore) sta nel CSS del tema: è ciò che fa sembrare venti foto di
        # venti autori diversi la stessa pagina invece di un collage.
        image = s.get("image", "")
        credit = _esc(s.get("credit", ""))
        canvas = (
            f'<div class="canvas has-photo">'
            f'<div class="photo" style="background-image:url({image})"></div>'
            f'<div class="tone"></div><div class="scrim"></div></div>'
            if image
            else '<div class="canvas"></div>'
        )
        parts.append(f"""
<section class="slide slide-{i + 1}{flags}{' photo-slide' if image else ''}">
  {canvas}
  <div class="grain"></div>
  <header class="top">
    <span class="kicker">{kicker}</span>
    <span class="index">{index}</span>
  </header>
  <div class="stage">
    <h1 class="headline {_fit_class(headline)}">{headline}</h1>
    {f'<p class="body">{body}</p>' if body else ''}
  </div>
  <footer class="bottom">
    <span class="mark">{watermark}</span>
    <span class="nav">{dots}{swipe}</span>
  </footer>
  {f'<span class="credit">{credit}</span>' if credit else ''}
</section>""")

    return f"""<!doctype html>
<html><head><meta charset="utf-8">
<style>
{_font_face_css()}
:root {{ --w: {w}px; --h: {h}px; }}
{base_css}
{theme_css}
</style></head>
<body>{''.join(parts)}</body></html>"""


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:48] or "post"


def render_slides(
    slides: List[Dict[str, str]],
    name_hint: str,
    template: str | None = None,
    size: Optional[tuple] = None,
    transparent: bool = False,
) -> List[Path]:
    """Restituisce i path dei PNG generati, uno per slide.

    `size` permette di uscire dal formato configurato: serve ai reel, che sono
    1080×1920 mentre i post sono 1080×1350.
    """
    template = template or cfg.get("format.template", "editorial")
    out_dir = OUTPUT_DIR / _slug(name_hint)
    out_dir.mkdir(parents=True, exist_ok=True)

    html_path = out_dir / "preview.html"
    html_path.write_text(build_html(slides, template, size), encoding="utf-8")

    w, h = size or (int(cfg.get("format.width", 1080)),
                    int(cfg.get("format.height", 1350)))
    paths: List[Path] = []

    with sync_playwright() as p:
        browser = p.chromium.launch()
        # Lo sfondo trasparente serve ai reel: il testo viene sovrapposto al
        # filmato da ffmpeg, quindi il PNG deve avere il canale alfa.
        page = browser.new_page(
            viewport={"width": w, "height": h},
            **({"color_scheme": "dark"} if not transparent else {}),
        )
        page.goto(html_path.as_uri())
        page.wait_for_timeout(300)  # lascia assestare il layout dei font
        for i in range(len(slides)):
            target = out_dir / f"{i + 1:02d}.png"
            page.locator(".slide").nth(i).screenshot(
                path=str(target), omit_background=transparent
            )
            paths.append(target)
        browser.close()

    return paths
