#!/usr/bin/env python3
"""Genera l'immagine di copertina del canale YouTube e l'immagine profilo.

    python3 setup_youtube_art.py

Perché uno script a parte e non un comando del ciclo: si fa una volta sola.
Il banner non cambia a ogni pubblicazione, e tenerlo dentro `run.py` vorrebbe
dire portarsi dietro un percorso che non viene mai eseguito.

Le misure non sono decorative. YouTube ritaglia lo stesso file in modo diverso
su televisore, computer e telefono: 2048×1152 è il file intero, ma l'unica
porzione garantita su tutti i dispositivi è un rettangolo di 1546×423 al
centro. Tutto ciò che deve leggersi sta lì dentro; fuori ci va solo sfondo,
perché sul telefono viene tagliato via.

Il caricamento non è automatizzato di proposito: l'immagine profilo non si può
cambiare da API, quindi una parte resterebbe comunque a mano e due strade
diverse per la stessa cosa confondono. Sono due trascinamenti nel browser.
"""
from __future__ import annotations

import base64
from pathlib import Path

from playwright.sync_api import sync_playwright

from engine.config import ROOT, cfg

OUT = ROOT / "output" / "youtube-art"

BANNER = (2048, 1152)
SICURA = (1546, 423)      # l'unica zona visibile su tutti i dispositivi
AVATAR = (800, 800)


def _font(nome: str) -> str:
    p = ROOT / "assets" / "fonts" / nome
    if not p.exists():
        raise SystemExit(f"font mancante: {p}\nLancia prima:  python3 setup_fonts.py")
    return base64.b64encode(p.read_bytes()).decode("ascii")


def _css() -> str:
    return f"""
@font-face {{ font-family:'Display'; src:url(data:font/ttf;base64,{_font('InstrumentSerif-Regular.ttf')}); }}
@font-face {{ font-family:'Body'; src:url(data:font/ttf;base64,{_font('Inter-Regular.ttf')}); }}
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ background:#000; }}

/* Stessa base delle slide: se il canale avesse un fondo diverso dai video,
   la copertina si leggerebbe come presa da un'altra pagina. */
.tela {{
  position:relative; overflow:hidden;
  background: radial-gradient(125% 95% at 30% 15%, #221d17 0%, #0d0b09 100%);
  display:flex; align-items:center; justify-content:center;
}}
.grana {{
  position:absolute; inset:0; opacity:.05; pointer-events:none;
  background-image:url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='180' height='180'><filter id='n'><feTurbulence type='fractalNoise' baseFrequency='.85' numOctaves='3'/></filter><rect width='180' height='180' filter='url(%23n)'/></svg>");
}}
/* Alone caldo defilato: dà profondità senza entrare nella zona sicura, dove
   ruberebbe contrasto al testo. */
.alone {{
  position:absolute; width:60%; aspect-ratio:1; border-radius:50%;
  background:radial-gradient(circle, rgba(232,192,122,.15) 0%, transparent 68%);
}}
</style>
"""


# Il segno del profilo Instagram, ridisegnato in vettoriale invece che
# riusato come file: quello è un PNG su fondo nero pieno, e sulla sfumatura
# della copertina si vedrebbe il suo quadrato. Le proporzioni sono ricavate
# dall'originale — deve essere lo stesso segno, non uno simile.
MARCHIO = """
<svg viewBox="0 0 1080 1080" fill="none" xmlns="http://www.w3.org/2000/svg">
  <circle cx="547" cy="550" r="300" stroke="#c8843f" stroke-width="23"/>
  <circle cx="540" cy="535" r="180" stroke="#f2ede4" stroke-width="35"/>
  <rect x="532" y="193" width="17" height="205" fill="#c8843f"/>
</svg>"""


def banner_html() -> str:
    w, h = BANNER
    sw, sh = SICURA
    return f"""<meta charset="utf-8"><style>{_css()}
<style>
.tela {{ width:{w}px; height:{h}px; }}
.alone.a {{ left:-14%; top:-24%; }}
.alone.b {{ right:-16%; bottom:-30%; }}
/* Tutto ciò che deve leggersi sta qui dentro: fuori YouTube taglia. */
.sicura {{
  position:relative; width:{sw}px; height:{sh}px;
  display:flex; align-items:center; justify-content:center; gap:66px;
}}
.marchio {{ width:290px; height:290px; flex:none; }}
.testo {{ display:flex; flex-direction:column; }}
.nome {{
  font-family:'Display', Georgia, serif; font-size:132px; line-height:.94;
  color:#fdfbf7; letter-spacing:-.03em;
}}
.claim {{
  font-family:'Display', Georgia, serif; font-size:50px; color:#e8c07a;
  letter-spacing:-.01em; margin-top:16px;
}}
.sotto {{
  font-family:'Body', system-ui, sans-serif; font-size:22px; color:#fdfbf7;
  opacity:.45; letter-spacing:.26em; text-transform:uppercase; margin-top:26px;
}}
</style>
<div class="tela">
  <div class="alone a"></div><div class="alone b"></div>
  <div class="grana"></div>
  <div class="sicura">
    <div class="marchio">{MARCHIO}</div>
    <div class="testo">
      <div class="nome">Oddly Wired</div>
      <div class="claim">Why people do what they do.</div>
      <div class="sotto">one checked fact a day</div>
    </div>
  </div>
</div>"""


def scarica_profilo(dest: Path) -> bool:
    """Prende l'immagine profilo da Instagram invece di disegnarne una nuova.

    Il segno è già quello del profilo Instagram, e chi arriva su YouTube da un
    reel deve riconoscerlo subito: due immagini diverse per lo stesso progetto
    disfano esattamente il collegamento fra le due piattaforme che serve.
    """
    import httpx

    from engine.config import env

    tok, uid = env("IG_ACCESS_TOKEN"), env("IG_USER_ID")
    if not (tok and uid):
        print("  (Instagram non configurato: immagine profilo non scaricata)")
        return False
    try:
        d = httpx.get(f"https://graph.facebook.com/v21.0/{uid}",
                      params={"fields": "profile_picture_url", "access_token": tok},
                      timeout=40).json()
        url = d.get("profile_picture_url")
        if not url:
            print(f"  (Instagram non ha restituito l'immagine: {d.get('error', {}).get('message', '?')})")
            return False
        r = httpx.get(url, timeout=40)
        r.raise_for_status()
        dest.write_bytes(r.content)
        print(f"  {dest.name:22} da Instagram, {dest.stat().st_size // 1024} KB")
        return True
    except Exception as exc:
        print(f"  (immagine profilo non scaricata: {exc})")
        return False


def _scatta(html: str, size: tuple, dest: Path) -> None:
    w, h = size
    tmp = OUT / f"_{dest.stem}.html"
    tmp.write_text(html, encoding="utf-8")
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={"width": w, "height": h})
        pg.goto(tmp.as_uri())
        pg.wait_for_timeout(400)          # i font incorporati vanno lasciati assestare
        pg.locator(".tela").screenshot(path=str(dest))
        b.close()
    tmp.unlink(missing_ok=True)
    print(f"  {dest.name:22} {w}×{h}  {dest.stat().st_size // 1024} KB")


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    print(f"marchio: {cfg.get('brand.name')} — {cfg.get('brand.handle')}\n")
    _scatta(banner_html(), BANNER, OUT / "copertina-canale.png")
    scarica_profilo(OUT / "immagine-profilo.jpg")
    print(f"\n→ {OUT}")
    print("\nDa caricare a mano su studio.youtube.com → Personalizzazione:")
    print("  · Immagine del banner  ← copertina-canale.png")
    print("  · Immagine del profilo ← immagine-profilo.jpg")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
