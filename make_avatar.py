#!/usr/bin/env python3
"""Genera proposte di foto profilo.

Perché non usare FLUX: la foto profilo su Instagram viene mostrata in un
cerchio da 110 px. Una fotografia, per quanto bella, a quella dimensione
diventa una macchia. Serve un segno grafico: poche forme, molto contrasto,
riconoscibile anche a 32 px nella lista dei commenti.

    python3 make_avatar.py
"""
from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

from engine.config import OUTPUT_DIR
from engine.render import _font_face_css

SIZE = 1080

# Ogni variante è (nome, css, html-interno).
VARIANTS = [
    (
        "monogram",
        """
        .a { background: radial-gradient(120% 100% at 30% 20%, #221f1b, #100f0d); }
        .a .m { font-family: 'Display', Georgia, serif; font-size: 560px;
                color: #e8b878; letter-spacing: -.06em; line-height: 1; }
        """,
        '<div class="m">ow</div>',
    ),
    (
        "circuit",
        """
        .a { background: #100f0d; }
        .a .ring { width: 620px; height: 620px; border-radius: 50%;
                   border: 26px solid #c98a4b; position: relative; }
        .a .ring::after { content: ""; position: absolute; inset: 96px;
                          border-radius: 50%; border: 26px solid #f2ede4; }
        .a .ring::before { content: ""; position: absolute;
                           left: 50%; top: -60px; width: 26px; height: 200px;
                           background: #c98a4b; transform: translateX(-50%); }
        """,
        '<div class="ring"></div>',
    ),
    (
        "wordmark",
        """
        .a { background: #f0ece1; }
        .a .w { font-family: 'Heavy', 'Arial Black', sans-serif;
                font-size: 188px; line-height: .82; color: #12100d;
                text-transform: uppercase; letter-spacing: -.045em;
                text-align: center; }
        .a .w em { font-style: normal; color: #b5471f; }
        """,
        '<div class="w">oddly<br><em>wired</em></div>',
    ),
    (
        "spark",
        """
        .a { background: radial-gradient(circle at 50% 45%, #2a2621 0%, #0e0d0c 70%); }
        .a .s { font-family: 'Display', Georgia, serif; font-size: 720px;
                color: #e8b878; line-height: .8; }
        """,
        '<div class="s">?</div>',
    ),
]

PAGE = """<!doctype html><html><head><meta charset="utf-8"><style>
{fonts}
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ width:{size}px; background:#000; }}
.a {{ width:{size}px; height:{size}px; display:flex;
      align-items:center; justify-content:center; overflow:hidden; }}
{css}
</style></head><body><div class="a">{inner}</div></body></html>"""


def main() -> int:
    out = OUTPUT_DIR / "avatar"
    out.mkdir(parents=True, exist_ok=True)
    fonts = _font_face_css()

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": SIZE, "height": SIZE})
        for name, css, inner in VARIANTS:
            html = out / f"{name}.html"
            html.write_text(
                PAGE.format(fonts=fonts, size=SIZE, css=css, inner=inner),
                encoding="utf-8",
            )
            page.goto(html.as_uri())
            page.wait_for_timeout(220)
            target = out / f"{name}.png"
            page.locator(".a").screenshot(path=str(target))
            print(f"  ✓ {name:10} {target}")
        browser.close()

    print(f"\nProposte in {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
