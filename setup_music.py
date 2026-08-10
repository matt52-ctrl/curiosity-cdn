#!/usr/bin/env python3
"""Scarica una libreria musicale libera, divisa per stato d'animo.

Perché divisa: una canzone allegra sotto una frase amara rovina il reel più di
quanto lo rovinerebbe il silenzio. La musica non è decorazione, è la metà del
messaggio — quindi le tracce vanno scelte in base al tono della frase, e per
sceglierle bisogna averle catalogate prima.

Quattro registri, che coprono ciò che questa pagina pubblica:
  reflective  la maggior parte dei fatti: calmo, pensoso, sospeso
  unsettling  fatti su autoinganno e bias — qualcosa non torna
  warm        fatti su legami, vicinanza, essere visti
  bright      fatti sorprendenti o buffi, dove serve leggerezza

⚠️ Nessuna di queste è "musica di tendenza". L'audio di tendenza vive solo
dentro l'app di Instagram, è protetto da copyright, e incorporarlo nel file
farebbe azzerare l'audio o rimuovere il post. Qui si usano solo CC0 e CC BY,
che si possono usare commercialmente senza rischi.

    python3 setup_music.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import httpx

DEST = Path(__file__).resolve().parent / "assets" / "music"
UA = {"User-Agent": "CuriosityEngine/1.0 (https://instagram.com/oddlywireddaily)"}

# Solo licenze senza obbligo di condividere allo stesso modo: una CC BY-SA
# imporrebbe la stessa licenza al video finale.
LICENZE_OK = re.compile(r"^(cc0|public domain|pd|cc by( \d(\.\d)?)?)$", re.I)

# Le ricerche sono pensate per far uscire brani diversi fra loro: cercare
# "musica calma" quattro volte restituirebbe quattro volte lo stesso genere.
REGISTRI = {
    "reflective": [
        "Komiku instrumental",
        "calm piano instrumental",
        "slow ambient instrumental",
    ],
    "unsettling": [
        "dark ambient instrumental",
        "minor key piano instrumental",
        "suspense instrumental",
    ],
    "warm": [
        "acoustic guitar instrumental",
        "folk instrumental",
        "gentle strings instrumental",
    ],
    "bright": [
        "ukulele instrumental",
        "upbeat acoustic instrumental",
        "playful instrumental",
    ],
}

PER_REGISTRO = 3


def _pulisci(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", html or "")).strip()


def _scarica_registro(client: httpx.Client, nome: str, ricerche, visti: set) -> int:
    cartella = DEST / nome
    cartella.mkdir(parents=True, exist_ok=True)
    presenti = len(list(cartella.glob("*.ogg")) + list(cartella.glob("*.mp3")))
    if presenti >= PER_REGISTRO:
        print(f"  {nome:11} {presenti} tracce già presenti")
        return presenti

    for query in ricerche:
        if presenti >= PER_REGISTRO:
            break
        try:
            r = client.get(
                "https://commons.wikimedia.org/w/api.php",
                params={
                    "action": "query",
                    "generator": "search",
                    "gsrsearch": f"filetype:audio {query}",
                    "gsrlimit": 10,
                    "gsrnamespace": 6,
                    "prop": "imageinfo",
                    "iiprop": "url|extmetadata|size",
                    "format": "json",
                },
            )
            r.raise_for_status()
            pagine = r.json().get("query", {}).get("pages", {})
        except Exception as exc:
            print(f"  {nome:11} ricerca '{query}' fallita: {exc}")
            continue

        for p in pagine.values():
            if presenti >= PER_REGISTRO:
                break
            info = (p.get("imageinfo") or [{}])[0]
            licenza = _pulisci(
                (info.get("extmetadata", {}).get("LicenseShortName", {}) or {}).get("value", "")
            )
            if not LICENZE_OK.match(licenza):
                continue

            url = (info.get("url") or "").split("?")[0]
            size = info.get("size") or 0
            # Troppo corta non copre un reel, troppo lunga gonfia il repo.
            if not url or url in visti or not (200_000 < size < 12_000_000):
                continue
            visti.add(url)

            target = cartella / re.sub(r"[^a-z0-9.]+", "-", Path(url).name.lower())
            try:
                audio = client.get(url)
                audio.raise_for_status()
                target.write_bytes(audio.content)
            except Exception as exc:
                print(f"  {nome:11} ✗ {target.name[:34]}: {exc}")
                continue

            presenti += 1
            print(f"  {nome:11} ↓ {target.name[:40]} [{licenza}] {len(audio.content)//1024} KB")

    return presenti


def main() -> int:
    DEST.mkdir(parents=True, exist_ok=True)
    visti: set = set()
    totale = 0

    with httpx.Client(timeout=90, follow_redirects=True, headers=UA) as client:
        for nome, ricerche in REGISTRI.items():
            totale += _scarica_registro(client, nome, ricerche, visti)

    # Le tracce sciolte della versione precedente finiscono in reflective, che
    # è il registro di ripiego quando un tono non ha corrispondenze.
    sciolte = list(DEST.glob("*.ogg")) + list(DEST.glob("*.mp3"))
    if sciolte:
        (DEST / "reflective").mkdir(exist_ok=True)
        for f in sciolte:
            f.rename(DEST / "reflective" / f.name)
        print(f"\n  {len(sciolte)} tracce sciolte spostate in reflective/")

    if totale == 0:
        print("\nNessuna traccia: i reel uscirebbero muti.")
        return 1
    print(f"\n{totale} tracce in {DEST}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
