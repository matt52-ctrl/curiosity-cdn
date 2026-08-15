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

COME SI LEGGONO I VERDETTI, che non e' come sembra.

Cercando repliche si vedono benissimo gli studi dubbi e quasi per niente i
classici, e il motivo e' che le due cose stanno in rapporto inverso: un
effetto traballante genera montagne di tentativi di replica, mentre uno
assodato non ne genera nessuno, perche' negli articoli successivi viene
*usato* come strumento invece che messo alla prova. Misurato: l'effetto
Stroop, del 1935, uno dei piu' saldi della disciplina, esce «non
verificabile» anche cercandolo col titolo esatto dell'articolo originale —
tutto cio' che si trova lo definisce, nessuno lo rimisura. Sparrow 2011,
invece, che e' fragile, esce «discussa» con la meta-analisi in mano.

Quindi «non verificabile» NON vuol dire dubbio: vuol dire che questo metodo
non ha niente da dire. Il segnale utile e' quello negativo. Solo «discussa» e
«inesistente» devono far cambiare il copione; «solida» e' un bonus raro, non
un requisito, e pretenderlo per ogni fonte vorrebbe dire buttare via gli
studi migliori che esistano.

NON ACCENDERE QUI LA RICERCA WEB. Sembra la correzione ovvia al problema qui
sopra — se Europe PMC non trova la meta-analisi su Stroop, la trova Google —
ed e' la strada che rimette dentro esattamente il difetto che questo file
esiste per fermare. Provato: chiedendo con la ricerca attiva una prova
sull'effetto Stroop, il modello ha risposto con una citazione precisa, con
tanto di effetto in millisecondi e Cohen's d, attribuita a «The Stroop
Effect: Cognitive Psychology's Most-Replicated Finding». Quel titolo su
Europe PMC da' zero risultati: non e' un articolo, e la frase ha tutta
l'aria di venire da un blog o di essere stata ricucita.

La differenza non e' la qualita' del modello, e' la garanzia della fonte:
Europe PMC restituisce solo articoli veri e indicizzati, il web aperto
restituisce qualsiasi cosa, e il modello cita un blog con la stessa
sicurezza con cui cita una meta-analisi — anzi con piu' sicurezza, perche' i
blog sono scritti per suonare conclusivi. Un «non verificabile» di troppo
costa un'affermazione ammorbidita nel copione. Un «solida» appoggiato a una
fonte inesistente costa il canale.

La ricerca web va invece bene a monte, a chi SCRIVE il capitolo: li' serve a
scegliere studi che esistono e a sapere quali sono contestati, e comunque
tutto quello che produce ripassa di qui.
"""

from typing import Any, Dict, List

from .llm import ask_json
from .research import _europepmc, gather

VERDETTI = ("solida", "discussa", "inesistente", "non verificabile")

# Gli unici tipi di prova che possono reggere un "solida". Il resto — una
# definizione di passaggio, la frase di metodo di uno studio che dà l'effetto
# per scontato, l'articolo originale — descrive l'effetto senza misurarlo.
PROVE_CHE_REGGONO = ("replica_riuscita", "meta_analisi", "recensione")

TIPI_PROVA = PROVE_CHE_REGGONO + (
    "replica_fallita", "definizione", "metodo", "solo_originale", "nessuna",
)

SCHEMA = {
    "type": "object",
    "properties": {
        "tipo_prova": {"type": "string", "enum": list(TIPI_PROVA)},
        "verdetto": {"type": "string", "enum": list(VERDETTI)},
        "motivo": {"type": "string"},
        "citazione": {"type": "string"},
    },
    "required": ["tipo_prova", "verdetto", "motivo", "citazione"],
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

FIRST, pick the single sentence in the retrieved material that best supports \
your verdict, and classify what KIND of sentence it is, in `tipo_prova`. \
Classify what the sentence actually says, not what you believe about the \
study. This is a description task, not a judgement:

· "replica_riuscita" — a later, independent study reports it reproduced the \
                       original finding.
· "replica_fallita"  — a later study reports it failed to reproduce it, or \
                       found a much smaller effect.
· "meta_analisi"     — pooled results across many studies, with an estimate.
· "recensione"       — a review that weighs the accumulated evidence and says \
                       how well the effect holds up.
· "definizione"      — the sentence explains what the effect IS. Later papers \
                       constantly do this in passing, to introduce their own \
                       topic. It shows the term is in circulation. It says \
                       nothing about whether the effect holds.
· "metodo"           — a later study describing what it set out to examine, \
                       or assuming the effect while studying something else. \
                       "We examined whether the availability heuristic \
                       influences physician testing" is this: a plan, not a \
                       result.
· "solo_originale"   — the sentence comes from the original article itself.
· "nessuna"          — nothing in the material speaks to this study.

Be accurate here even when it is inconvenient. If the best sentence you can \
find is a definition, say "definizione". Do not upgrade the label to protect \
a verdict you have already decided on — the label is checked separately, and \
a wrong label is worse than a cautious verdict.

The burden of proof runs against "solida". If you find yourself reasoning \
"this is a famous study, it must be fine" — stop. That reasoning is memory, \
not evidence, and it is exactly the failure this check exists to catch. \
Absence of evidence is "non verificabile".

Write `motivo` in Italian. In `citazione`, quote the sentence from the \
retrieved material that decided the verdict, word for word and in its \
original language. It must come from later work, not from the original \
article, and it must state a finding, not a definition. If nothing decided \
it, leave it empty."""


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

    # La ricerca sulle meta-analisi non c'era, e la sua assenza si e' vista
    # appena il giudizio e' diventato severo: l'effetto Stroop, che e' fra i
    # piu' replicati che esistano, e' finito "non verificabile" perche' tutto
    # cio' che si trovava lo definiva senza misurarlo. Non era un errore di
    # giudizio ma di raccolta. Una meta-analisi e' esattamente il tipo di
    # articolo che contiene la frase che adesso si pretende: un numero messo
    # insieme su molti studi, non una definizione.
    for query, etichetta in (
        (f'"{studio}" replication', "REPLICHE (per titolo)"),
        (f"{fenomeno} replication failure", "REPLICHE (per fenomeno)"),
        (f"{fenomeno} meta-analysis effect size", "META-ANALISI"),
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

    # Il declassamento lo fa Python, non il modello. Due versioni di questo
    # file hanno provato a ottenerlo scrivendo la regola nel prompt, sempre
    # piu' severa, e la seconda ha fallito in modo istruttivo: il modello ha
    # scritto «sebbene il materiale citi l'euristica solo come concetto» e
    # subito dopo ha messo "solida". Aveva capito l'obiezione e l'ha
    # scavalcata. Chiedere a un modello di sorvegliare se stesso e' la stessa
    # cosa che gli si sta togliendo di mano scrivendo questo file.
    #
    # Quindi non gli si chiede piu' di applicare la regola: gli si chiede
    # solo di dire che frase ha in mano, che e' un compito descrittivo e
    # molto piu' facile. La conseguenza la traggo qui, dove non e'
    # negoziabile.
    if r["verdetto"] == "solida" and r["tipo_prova"] not in PROVE_CHE_REGGONO:
        r["verdetto"] = "non verificabile"
        r["motivo"] = (f"declassato: l'unica prova trovata e' di tipo "
                       f"«{r['tipo_prova']}», che descrive l'effetto senza "
                       f"misurarlo. " + r["motivo"])
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
