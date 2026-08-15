"""Verifica gli studi nominati in un copione, contro la letteratura vera.

Perche' esiste. Il modello che scrive i capitoli lunghi nomina gli studi a
memoria — sul piano gratuito di Gemini il collegamento a internet ha quota
zero, misurato: la stessa richiesta passa senza ricerca e torna 429 con la
ricerca attiva. E quando gli si chiede quanto e' sicuro di cio' che ha
scritto, si da' i voti da solo. Alla prima prova ha marcato «solida» anche la
ricerca di Sparrow del 2011 sull'effetto Google, che e' uno dei casi di
mancata replica piu' noti della psicologia recente.

Su una pillola da 28 secondi il danno e' contenuto. Su tre ore di documentario
che si vende dicendo «ogni affermazione cita lo studio», una fonte sbagliata
su tre e' la fine della credibilita' del canale.

Come lavora. Non chiede al modello se si ricorda bene: gli mette davanti degli
abstract veri e gli vieta di uscirne. Due ricerche su Europe PMC, che e'
gratuita e indicizza gli articoli veri:

  · TITLE:"<studio>"        trova l'originale e cio' che gli e' venuto dietro
  · "<studio>" replication  trova i tentativi di replica, che sono la cosa
                            che il modello da solo non sa mai

Piu' Wikipedia, che sugli studi celebri le mancate repliche le riporta.

L'onere della prova e' rovesciato di proposito: se le ricerche non trovano
nulla il verdetto e' «non verificabile», non «solida». Un capitolo con tre
studi solidi vale piu' di uno con dieci traballanti, e questa funzione serve a
togliere, non ad aggiungere.
"""

from typing import Any, Dict, List

from .llm import ask_json
from .research import _europepmc, gather

VERDETTI = ("solida", "discussa", "inesistente", "non verificabile")

SCHEMA = {
    "type": "object",
    "properties": {
        "verdetto": {"type": "string", "enum": list(VERDETTI)},
        "motivo": {"type": "string"},
        "citazione": {"type": "string"},
    },
    "required": ["verdetto", "motivo", "citazione"],
    "additionalProperties": False,
}

SYSTEM = """You check whether a psychology study, as described by a script \
writer, holds up. You are given real abstracts retrieved from Europe PMC and \
Wikipedia. Judge ONLY from those. You may not use anything you remember.

Verdicts:

· "solida"           — the retrieved material confirms the study exists and \
                       describes it as the writer does, and nothing in the \
                       material reports a failed replication or serious \
                       dispute.
· "discussa"         — the study is real, but the material shows failed \
                       replications, a much smaller effect than claimed, or \
                       active controversy. This is the verdict for anything \
                       where a replication attempt is mentioned without a \
                       clear success.
· "inesistente"      — the material contradicts the description: wrong \
                       author, wrong year, wrong finding, or the study does \
                       not appear to exist.
· "non verificabile" — the retrieved material does not speak to this study \
                       one way or the other.

THE RULE THAT MATTERS MOST: the original article's own abstract is NEVER \
evidence that a finding is solid. Every original article claims its effect is \
real — that is what an original article is. Quoting it back as proof is \
circular, and it is the single most likely way for you to get this wrong. If \
the only supporting material you have is the original paper, the verdict is \
"non verificabile", not "solida".

Only work published AFTER the original counts: replication attempts, \
meta-analyses, reviews. If a meta-analysis or a replication attempt is \
present, IT decides the verdict, and it overrides the original paper no \
matter how famous the original is. A meta-analysis reporting that an effect \
is "smaller than originally claimed" or "context-dependent" means "discussa".

The burden of proof runs against "solida". If you find yourself reasoning \
"this is a famous study, it must be fine" — stop. That reasoning is memory, \
not evidence, and it is exactly the failure this check exists to catch. \
Absence of evidence is "non verificabile".

In `citazione`, quote the sentence from the retrieved material that decided \
the verdict, word for word. It must come from later work, not from the \
original article. If nothing decided it, leave it empty."""


def _prove(studio: str, affermazione: str) -> str:
    """Materiale vero su cui giudicare: repliche, originale, enciclopedia."""
    blocchi: List[str] = []

    # Il titolo dello studio da solo non basta a trovare le repliche: la prova
    # su Sparrow 2011 lo mostra: cercare «"Google Effects on Memory"
    # replication» non trova nulla, mentre «"Google Stroop" replication» trova
    # subito lo studio del 2020 che non replica. Chi replica quasi mai
    # ricicla il titolo dell'originale: nomina il fenomeno. Quindi si cerca su
    # due chiavi, il titolo e le parole dell'affermazione.
    fenomeno = " ".join(affermazione.split()[:10])

    for query, etichetta in (
        (f'"{studio}" replication', "REPLICHE (per titolo)"),
        (f"{fenomeno} replication failure", "REPLICHE (per fenomeno)"),
        (f'TITLE:"{studio}"', "ORIGINALE, META-ANALISI E SEGUITI"),
    ):
        estratti = _europepmc(query, limit=3, chars=900)
        if estratti:
            blocchi.append(f"### {etichetta} (Europe PMC)\n" + "\n".join(estratti))

    wiki = gather(studio, limit=2, chars=1200, claim=affermazione)
    if wiki:
        blocchi.append("### WIKIPEDIA\n" + wiki)

    return "\n\n".join(blocchi)


def verifica(studio: str, anno: str, rivista: str,
             affermazione: str) -> Dict[str, Any]:
    """Un verdetto su un singolo studio nominato nel copione."""
    prove = _prove(studio, affermazione)

    if not prove.strip():
        # Nessun materiale: si dichiara, non si indovina. E' il punto per cui
        # la funzione esiste.
        return {
            "verdetto": "non verificabile",
            "motivo": "nessun materiale trovato su Europe PMC ne' Wikipedia",
            "citazione": "",
            "studio": studio,
        }

    user = (
        f"The writer's script says:\n  «{affermazione}»\n\n"
        f"and attributes it to:\n"
        f"  study   : {studio}\n"
        f"  year    : {anno}\n"
        f"  journal : {rivista}\n\n"
        f"Here is what was actually retrieved:\n\n{prove}"
    )
    r = ask_json(SYSTEM, user, SCHEMA, max_tokens=2000, use_web_search=False)
    r["studio"] = studio
    return r


def verifica_capitolo(fonti: List[Dict[str, str]]) -> List[Dict[str, Any]]:
    """Verifica tutte le fonti dichiarate da un capitolo."""
    esiti = []
    for i, f in enumerate(fonti, 1):
        print(f"  [{i}/{len(fonti)}] {f['studio'][:58]}")
        try:
            e = verifica(f["studio"], f.get("anno", ""), f.get("rivista", ""),
                         f["affermazione"])
        except Exception as exc:
            # Una verifica che esplode non deve promuovere lo studio: resta
            # non verificato, che e' il lato sicuro.
            e = {"verdetto": "non verificabile", "studio": f["studio"],
                 "motivo": f"verifica fallita: {exc}", "citazione": ""}
        print(f"        → {e['verdetto'].upper()}: {e['motivo'][:90]}")
        esiti.append(e)
    return esiti
