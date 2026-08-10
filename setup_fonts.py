#!/usr/bin/env python3
"""Scarica i font usati dai template in assets/fonts/.

I font vengono incorporati come data URI nell'HTML al momento del rendering,
quindi devono esistere in locale. Senza questo passaggio i template ripiegano
sui font di sistema e su Linux (VPS) il risultato è brutto.

    python3 setup_fonts.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import httpx

DEST = Path(__file__).resolve().parent / "assets" / "fonts"
RAW = "https://raw.githubusercontent.com/google/fonts/main"

FONTS = {
    "InstrumentSerif-Regular.ttf": f"{RAW}/ofl/instrumentserif/InstrumentSerif-Regular.ttf",
    "InstrumentSerif-Italic.ttf": f"{RAW}/ofl/instrumentserif/InstrumentSerif-Italic.ttf",
    "ArchivoBlack-Regular.ttf": f"{RAW}/ofl/archivoblack/ArchivoBlack-Regular.ttf",
    "Inter-Regular.ttf": f"{RAW}/ofl/inter/Inter%5Bopsz,wght%5D.ttf",
    "Inter-Medium.ttf": f"{RAW}/ofl/inter/Inter%5Bopsz,wght%5D.ttf",
    "JetBrainsMono-Regular.ttf": f"{RAW}/ofl/jetbrainsmono/JetBrainsMono%5Bwght%5D.ttf",
}


def main() -> int:
    DEST.mkdir(parents=True, exist_ok=True)
    failures = 0
    with httpx.Client(timeout=90, follow_redirects=True) as client:
        for name, url in FONTS.items():
            target = DEST / name
            if target.exists() and target.stat().st_size > 1000:
                print(f"  ✓ {name} (già presente)")
                continue
            try:
                resp = client.get(url)
                resp.raise_for_status()
                target.write_bytes(resp.content)
                print(f"  ↓ {name}  {len(resp.content) // 1024} KB")
            except Exception as exc:
                failures += 1
                print(f"  ✗ {name}: {exc}")

    if failures:
        print(
            f"\n{failures} font non scaricati. I template useranno i font di sistema "
            f"per quelli mancanti — su macOS accettabile, su VPS Linux no.\n"
            f"Puoi metterli a mano in {DEST}."
        )
    else:
        print(f"\nTutti i font in {DEST}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
