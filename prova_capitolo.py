"""Prova: la macchina sa scrivere DODICI MINUTI su un argomento solo?

Non e' ancora una funzione della pipeline. Serve a rispondere a una domanda
sola, prima di costruire qualsiasi cosa sopra: il modello, se gli si chiede
profondita' invece che pillole, produce un capitolo da documentario o una
pappa lunga?

La differenza fra le due si vede in tre punti, e sono i tre su cui misuro:
  · un filo. Un capitolo ha una tesi che regge dall'inizio alla fine; una
    pappa e' un elenco travestito da discorso.
  · le fonti. Ogni affermazione deve nominare lo studio. E' l'unico argomento
    di vendita del canale, e su tre ore non verificate lo perdiamo tutto.
  · l'orecchio. Si ascolta, non si legge: niente parentesi, niente sigle,
    frasi che si reggono dette a voce.

Uso: .venv/bin/python prova_capitolo.py [argomento]
"""

import json
import sqlite3
import sys
from pathlib import Path

from engine.llm import ask_json

# ~150 parole al minuto e' il passo di una voce da documentario; la nostra e'
# rallentata dell'8%, quindi il conto vero e' piu' vicino a 140. Dodici minuti
# stanno fra 1.650 e 1.800 parole.
PAROLE_ATTESE = 1750

SCHEMA = {
    "type": "object",
    "properties": {
        "titolo": {"type": "string"},
        "tesi": {"type": "string"},
        "copione": {"type": "string"},
        "fonti": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "affermazione": {"type": "string"},
                    "studio": {"type": "string"},
                    "anno": {"type": "string"},
                    "rivista": {"type": "string"},
                    "certezza": {"type": "string"},
                },
                "required": ["affermazione", "studio", "anno", "rivista",
                             "certezza"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["titolo", "tesi", "copione", "fonti"],
    "additionalProperties": False,
}

SYSTEM = """You write single chapters of an audio documentary about human \
psychology. One chapter runs twelve minutes: roughly 1750 spoken words.

You are not writing a list. A chapter has one argument that develops from \
beginning to end: it opens with something the listener believes about \
themselves, complicates it, shows where the belief comes from, then shows \
what actually happens and why the difference matters. Studies are the \
evidence for the argument, never the subject of it.

Rules you do not break:

1. EVERY empirical claim names its source out loud, in the script itself, in \
   the way a person would say it: "in nineteen seventy-two, at Stanford, \
   Walter Mischel sat a child down in front of a marshmallow". Not a \
   citation. A sentence.
2. If you are not certain a study exists as you describe it, you say so in \
   the script — "this one is often repeated, and it has never replicated \
   cleanly" — or you leave it out. A confident sentence about a study that \
   does not exist destroys the only thing this channel sells.
3. Written for the ear. No parentheses, no abbreviations, no numbers written \
   as digits, no "as we saw earlier". Every sentence has to survive being \
   said aloud once, to someone who cannot rewind.
4. No filler. No "in this chapter we will explore". No summary at the end \
   that repeats what was just said. If a paragraph could be cut without loss, \
   cut it yourself.
5. Plain words. The subject is difficult enough that the language must not be.

In `fonti`, list every study you named, and mark `certezza` as one of: \
"solida" (replicated, uncontested), "discussa" (real but contested or failed \
to replicate), "aneddotica" (widely told, weak evidence). Be honest. A \
chapter with three solid studies is worth more than one with ten shaky ones."""


def semi(argomento: str, quanti: int = 6) -> list:
    """Curiosita' d'archivio sull'argomento, come punti di partenza."""
    conn = sqlite3.connect("data/engine.db")
    conn.row_factory = sqlite3.Row
    righe = conn.execute(
        "SELECT hook, fact, detail, source_hint FROM facts "
        "WHERE status IN ('published','rendered','approved')"
    ).fetchall()
    conn.close()
    fuori = [dict(r) for r in righe
             if argomento.lower() in (r["hook"] + r["fact"]).lower()]
    return (fuori or [dict(r) for r in righe])[:quanti]


def main() -> int:
    argomento = " ".join(sys.argv[1:]) or "why we misremember our own past"
    punti = semi(argomento)

    elenco = "\n".join(
        f"- {p['hook']} {p['fact']} ({p['source_hint']})" for p in punti
    )
    user = (
        f"Write one twelve-minute chapter on this subject: {argomento}\n\n"
        f"These are facts already in our archive on nearby ground. Use them "
        f"only if they serve the argument — you are not required to include "
        f"any of them, and you should go well beyond them:\n{elenco}\n\n"
        f"Aim for {PAROLE_ATTESE} words of spoken script."
    )

    print(f"argomento : {argomento}")
    print(f"semi      : {len(punti)} curiosita' d'archivio")
    print("scrivo... (con ricerca web attiva, puo' volerci un minuto)\n")

    # Ricerca web spenta di proposito. Misurato oggi: sul free tier di Gemini
    # il grounding ha quota ZERO — la stessa richiesta passa senza ricerca e
    # torna 429 con la ricerca attiva. Quindi il capitolo lo scrive a memoria,
    # e questo e' esattamente il punto debole da misurare: quanto si puo'
    # fidare di quello che afferma quando non puo' controllare.
    r = ask_json(SYSTEM, user, SCHEMA, max_tokens=16000, use_web_search=False)

    copione = r["copione"]
    parole = len(copione.split())
    minuti = parole / 140

    print("=" * 72)
    print(f"TITOLO : {r['titolo']}")
    print(f"TESI   : {r['tesi']}")
    print("=" * 72)
    print(copione)
    print("=" * 72)
    print(f"\nparole: {parole}  →  ~{minuti:.1f} minuti parlati "
          f"(obiettivo 12)")

    print(f"\nfonti dichiarate: {len(r['fonti'])}")
    conta = {}
    for f in r["fonti"]:
        conta[f["certezza"]] = conta.get(f["certezza"], 0) + 1
        print(f"  [{f['certezza']:11}] {f['studio']}, {f['anno']} — "
              f"{f['rivista']}")
        print(f"                → {f['affermazione'][:88]}")
    print(f"\nriepilogo certezza: {conta}")

    dest = Path("output/prova_capitolo.json")
    dest.parent.mkdir(exist_ok=True)
    dest.write_text(json.dumps(r, indent=2, ensure_ascii=False))
    print(f"\nsalvato in {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
