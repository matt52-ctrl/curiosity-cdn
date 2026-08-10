#!/usr/bin/env python3
"""Scarica la libreria musicale, divisa per stato d'animo.

Fonte: la raccolta Audionautix (Jason Shaw) su archive.org — 147 brani sotto
**CC0**, cioè pubblico dominio: uso commerciale libero, nessuna attribuzione
dovuta. È musica di produzione, scritta per stare sotto a un video, e si sente
la differenza rispetto alle registrazioni amatoriali che si trovano cercando
"instrumental" su Wikimedia.

Perché divisa per registro: una canzone allegra sotto una frase amara rovina
il reel più del silenzio. La musica non è decoro, è metà del messaggio — e per
sceglierla in base al tono della frase bisogna averla catalogata prima.

La classificazione avviene sui titoli, che in questa raccolta sono
descrittivi ("ADarkerHeart", "AcousticMeditation2", "90SecondsOfFunk"). È un
metodo grezzo ma verificabile: i titoli si leggono, e un brano finito nella
cartella sbagliata si sposta a mano.

⚠️ Nessuna di queste è "musica di tendenza": quella vive solo dentro l'app di
Instagram, è protetta, e incorporarla nel file farebbe azzerare l'audio o
rimuovere il post.

    python3 setup_music.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import httpx

DEST = Path(__file__).resolve().parent / "assets" / "music"
RACCOLTA = "audionautix-music-collection"
BASE = f"https://archive.org/download/{RACCOLTA}"
UA = {"User-Agent": "CuriosityEngine/1.0 (https://instagram.com/oddlywireddaily)"}

PER_REGISTRO = 6

# Parole chiave nei titoli. L'ordine conta: si assegna al primo registro che
# combacia, quindi i termini più specifici vanno prima.
REGISTRI = {
    "unsettling": [
        "dark", "ashes", "assasin", "shadow", "storm", "cold", "zero", "fear",
        "ghost", "haunt", "tension", "suspense", "grim", "night", "drone",
        "empire", "war", "danger", "creep",
    ],
    "warm": [
        "acoustic", "morning", "heart", "wood", "sunset", "alison", "gentle",
        "warm", "home", "folk", "campfire", "sunny", "smile", "friend",
        "together", "porch", "amber",
    ],
    "bright": [
        "funk", "rock", "boom", "dance", "party", "happy", "ukulele", "jump",
        "groove", "swing", "upbeat", "bounce", "sunshine", "cheer", "playful",
        "disco", "pop",
    ],
    "reflective": [
        "adagio", "meditation", "classical", "earth", "canvas", "azimuth",
        "drift", "calm", "quiet", "slow", "piano", "ambient", "still",
        "distant", "horizon", "reflect", "memory", "rain",
    ],
}


def _registro(nome: str) -> str:
    """Assegna un titolo a un registro. `reflective` è il ripiego."""
    n = nome.lower()
    for reg, parole in REGISTRI.items():
        if any(p in n for p in parole):
            return reg
    return "reflective"


def main() -> int:
    DEST.mkdir(parents=True, exist_ok=True)

    # Se le cartelle sono già piene non si riscarica: su GitHub Actions questo
    # script gira a ogni esecuzione e rifare 24 download ogni volta sarebbe
    # tempo sprecato.
    gia = {
        reg: len(list((DEST / reg).glob("*.mp3")))
        for reg in REGISTRI
    }
    if all(n >= PER_REGISTRO for n in gia.values()):
        print(f"  libreria già completa: {sum(gia.values())} tracce")
        return 0

    try:
        with httpx.Client(timeout=120, follow_redirects=True, headers=UA) as client:
            meta = client.get(f"https://archive.org/metadata/{RACCOLTA}").json()
    except Exception as exc:
        print(f"archive.org non raggiungibile: {exc}")
        return 1

    licenza = meta.get("metadata", {}).get("licenseurl", "")
    if "publicdomain" not in licenza and "/zero/" not in licenza:
        print(f"⚠️ licenza inattesa ({licenza}): interrompo per prudenza")
        return 1

    brani = [
        f["name"] for f in meta.get("files", [])
        if f.get("name", "").lower().endswith(".mp3")
        and 500_000 < int(f.get("size", 0) or 0) < 15_000_000
    ]
    print(f"  {len(brani)} brani CC0 disponibili")

    # Si distribuiscono nei registri e se ne scaricano PER_REGISTRO ciascuno.
    per_reg: dict = {r: [] for r in REGISTRI}
    for nome in brani:
        per_reg[_registro(nome)].append(nome)

    scaricati = 0
    with httpx.Client(timeout=180, follow_redirects=True, headers=UA) as client:
        for reg, elenco in per_reg.items():
            cartella = DEST / reg
            cartella.mkdir(parents=True, exist_ok=True)
            presenti = len(list(cartella.glob("*.mp3")))
            print(f"  {reg:11} {len(elenco):3} candidati, {presenti} già scaricati")

            for nome in elenco:
                if presenti >= PER_REGISTRO:
                    break
                pulito = re.sub(r"[^a-z0-9.]+", "-", Path(nome).name.lower())
                target = cartella / pulito
                if target.exists():
                    continue
                try:
                    r = client.get(f"{BASE}/{nome}")
                    r.raise_for_status()
                    target.write_bytes(r.content)
                except Exception as exc:
                    print(f"    ✗ {pulito[:36]}: {str(exc)[:40]}")
                    continue
                presenti += 1
                scaricati += 1
                print(f"    ↓ {pulito[:40]} {len(r.content)//1024} KB")

    totale = sum(len(list((DEST / r).glob("*.mp3"))) for r in REGISTRI)
    if totale == 0:
        print("\nNessuna traccia: i reel uscirebbero muti.")
        return 1
    print(f"\n{scaricati} nuove, {totale} tracce totali in {DEST}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
