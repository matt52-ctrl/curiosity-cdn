"""Da un capitolo scritto a un video da dodici minuti.

Perche' e' un modulo a parte e non un parametro di `lungo`.

L'episodio del sabato e' fatto di dieci curiosita' separate: ognuna ha il suo
gancio, dura mezzo minuto e non c'entra con quella prima. Montarlo e' facile
proprio perche' i blocchi esistono gia' nel materiale — basta seguirli.

Un capitolo e' l'opposto: e' un unico discorso di duemila parole che sviluppa
una tesi dall'inizio alla fine, e arriva dal modello come un paragrafo solo,
senza un punto in cui sia scritto «qui cambia scena». I blocchi non ci sono:
vanno inventati. Ed e' l'unica cosa che manca davvero, perche' il montaggio
vero e proprio — voce, filmato, musica sotto, giunture — lo fa gia' `lungo` e
non c'e' motivo di riscriverlo.

Le due scelte che contano.

DOVE SI TAGLIA. Mai a meta' frase. Il montaggio segue la voce, quindi un
taglio dentro una frase si sente come un salto: si accumulano frasi intere
fino a circa settantotto parole, che al passo misurato della nostra voce
fanno ventotto secondi. Ne escono una venticinquina di blocchi per capitolo.

COSA SI VEDE. Qui `lungo` non si poteva riusare, ed e' bene sia chiaro
perche'. `footage.per_frase` prende la frase e la usa solo come SEME CASUALE:
poi pesca da una lista fissa di dodici atmosfere per registro. Misurato su
quattordici frasi diverse, tutte molto concrete — un bambino solo in una
stanza, un centro commerciale affollato, un corridoio d'ospedale — sono
usciti orizzonti marini, campi di grano e neve nel bosco. Per una pillola da
nove secondi e' un fondale e va benissimo. Su dodici minuti di documentario
diventa carta da parati che non c'entra niente con quello che la voce sta
raccontando, ed e' anche esattamente il profilo — voce sintetica su
repertorio generico — che le regole di luglio 2026 sui contenuti non
autentici puniscono.

Quindi la scena la si chiede al modello, blocco per blocco, a partire da cio'
che quel blocco dice, e se la ricerca non trova nulla si scende per gradi
verso una ripresa vuota — mai verso un'atmosfera a caso, vedi `_clip`.

Che serva davvero e' misurato, e non dove me l'aspettavo. Sul primo capitolo
intero le venticinque scene chieste sono arrivate quasi tutte a segno; l'unico
buco l'ha aperto la rete, non il modello: su un blocco lo scaricamento e'
andato in timeout due volte di fila e il ripiego di allora ha messo «slow
river stones» sotto la frase sui pazienti in colonscopia. Il difetto non era
la scelta della scena, era che il gradino sotto non aveva niente a che vedere
con niente.
"""
from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .config import OUTPUT_DIR, cfg
from .llm import ask_json
from .lungo import WIDE, _durata, narra
from .reel import _ffmpeg, _traccia_a_caso

# Centosessantasei parole al minuto e' il passo MISURATO della nostra voce sul
# primo capitolo intero: milacinquecentosettantasei parole lette in cinquecento
# sessantotto secondi. Il numero che c'era prima, centoquaranta, era una stima
# a tavolino — voce da documentario meno l'otto per cento di rallentamento — e
# sbagliava del diciannove per cento: il capitolo e' uscito da nove minuti e
# mezzo invece che da dodici. A ventotto secondi per blocco fanno settantotto
# parole.
PAROLE_PER_BLOCCO = 78

# Duemila parole, non millesettecentocinquanta: la voce va a centosessantasei
# parole al minuto misurate, non a centoquaranta stimate, e con il numero
# vecchio il capitolo finiva a nove minuti e mezzo. Dodici per centosessantasei
# fa millenovecentonovantacinque.
PAROLE_ATTESE = 2000

SCHEMA_TESTO = {
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

SYSTEM_TESTO = """You write single chapters of an audio documentary about \
human psychology. One chapter runs twelve minutes: roughly 2000 spoken words.

THE SCRIPT IS WRITTEN IN ENGLISH. Not in any other language. The channel is \
English-speaking and a chapter in another language is unusable.

TWELVE MINUTES IS THE POINT, and it is the instruction writers fail. Six \
minutes is not a short chapter, it is a different product — it is the pill \
format this channel already has, and it is not what is being asked for. \
Before you finish, count. If the script is under eighteen hundred words, it is \
not done: go back and open up the part you rushed, which is almost always the \
middle.

To get there, the chapter moves in five movements. Do not label them in the \
script — this is the shape, not an outline to read out:

  1. The belief, told through one concrete person in one concrete moment. \
     About three hundred words.
  2. The first crack: something that does not fit the belief. About three \
     hundred words.
  3. Where the belief came from — the history, the study that founded it, \
     what was actually measured and on whom. About four hundred words. This \
     is where writers go thin: the details of how an experiment was run are \
     the most interesting part of it, not a preamble to skip.
  4. What actually happens instead, with the later evidence. About four \
     hundred and fifty words.
  5. Why the difference matters to the listener tomorrow morning. About three \
     hundred words. Not a summary — a consequence.

You are not writing a list. A chapter has one argument that develops from \
beginning to end. Studies are the evidence for the argument, never the \
subject of it.

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


SCHEMA_ARGOMENTI = {
    "type": "object",
    "properties": {"argomenti": {"type": "array", "items": {"type": "string"}}},
    "required": ["argomenti"],
    "additionalProperties": False,
}

SYSTEM_ARGOMENTI = """You choose the subjects of a psychology documentary \
series. Each subject becomes one twelve-minute chapter.

A good subject is a WIDELY HELD BELIEF ABOUT PEOPLE THAT THE EVIDENCE DOES \
NOT SUPPORT, stated as a plain sentence a stranger would react to.

  good: "why we misremember our own past"
  good: "why practice does not make you an expert"
  bad:  "memory"                     (a field, not a claim — there is no
                                      argument to develop)
  bad:  "ten facts about the brain"  (a list, and this is the pill format
                                      the channel already has)

Twelve minutes is a long time to hold one thread, so the subject must be big \
enough to carry an argument through five movements, and narrow enough that \
the chapter is about ONE thing.

The subjects must not repeat each other, and must not repeat the ones \
already made, which are listed below. Two chapters that both end at "your \
brain fools you" are the same chapter told twice, even when the studies \
differ — check the ARGUMENT, not the topic label. Spread them across \
different corners of psychology: memory, work, relationships, decisions, \
perception, groups, sleep, habit, emotion."""


def argomenti(quanti: int = 15, gia_fatti: Optional[List[str]] = None) -> List[str]:
    """Gli argomenti dei prossimi capitoli, tutti diversi fra loro.

    Si chiedono tutti in una volta e non uno per volta: il modello, chiamato
    quindici volte di fila senza sapere cosa ha appena proposto, ripete. E'
    lo stesso difetto che sull'episodio del sabato ha fatto uscire due volte
    il tema delle decisioni. Chiedendoli insieme, la richiesta di non
    ripetersi e' verificabile dentro la stessa risposta.
    """
    fatti = gia_fatti or []
    elenco = ("\n".join(f"  · {a}" for a in fatti) if fatti
              else "  (none yet — this is the first batch)")
    user = (f"CHAPTERS ALREADY MADE, do not repeat them or their "
            f"argument:\n{elenco}\n\nPropose {quanti} new subjects.")
    r = ask_json(SYSTEM_ARGOMENTI, user, SCHEMA_ARGOMENTI, max_tokens=2000,
                 use_web_search=False)

    # Il doppione lo si toglie qui e non lo si chiede per favore: confronto
    # grezzo sulle parole, che non riconosce due tesi gemelle scritte diverse
    # — quello lo puo' fare solo il modello — ma prende il caso banale del
    # titolo ripetuto, e costa niente.
    visti = {a.strip().lower() for a in fatti}
    fuori = []
    for a in r.get("argomenti", []):
        a = a.strip()
        if a and a.lower() not in visti:
            visti.add(a.lower())
            fuori.append(a)
    return fuori


def scrivi(argomento: str, semi: Optional[List[Dict]] = None) -> Dict:
    """Il testo del capitolo: titolo, tesi, copione, studi dichiarati.

    La ricerca web QUI si accende, ed e' l'unico punto della pipeline in cui
    e' giusto farlo: serve a scegliere studi che esistono davvero e a sapere
    quali sono contestati prima di scriverci sopra dodici minuti. Il rischio
    della ricerca aperta — il modello cita un blog con la stessa sicurezza di
    una meta-analisi — qui e' coperto, perche' tutto cio' che dichiara passa
    subito dopo da `fonti.verifica_capitolo`, che guarda solo Europe PMC.

    Nota per chi legge la storia di questo file: la versione di prova la
    teneva spenta, con un commento che diceva che sul piano gratuito il
    grounding ha quota zero. Era una misura sbagliata — c'era un altro
    progetto che consumava la stessa chiave, e i 429 venivano da li'.

    `pazienza` e' acceso qui e in nessun altro punto della pipeline. Il primo
    giro vero del workflow, il 4 settembre 2026, e' morto su un 503 di
    `gemini-2.5-flash` durato piu' del minuto scarso che la scala corta
    aspetta: zero capitoli su due. Un capitolo non ha un'ora di uscita — sta
    in magazzino finche' non serve — quindi qui aspettare otto minuti non
    costa niente, mentre arrendersi costa la giornata. E aspettare e' l'unica
    mossa possibile: con la ricerca accesa gli altri modelli rispondono 429
    (verificato di nuovo il 4 settembre), quindi non c'e' un ripiego a cui
    passare, c'e' solo questo modello e la sua capacita' del momento.
    """
    righe = "\n".join(
        f"- {s.get('hook','')} {s.get('fact','')} ({s.get('source_hint','')})"
        for s in (semi or [])
    )
    user = (
        f"Write one twelve-minute chapter on this subject: {argomento}\n\n"
        + (f"These are facts already in our archive on nearby ground. Use "
           f"them only if they serve the argument — you are not required to "
           f"include any of them, and you should go well beyond "
           f"them:\n{righe}\n\n" if righe else "")
        + f"Write {PAROLE_ATTESE} words of spoken script, in English. "
        f"Anything under eighteen hundred words is a failed chapter and will "
        f"be thrown away."
    )
    return _completo(ask_json(SYSTEM_TESTO, user, SCHEMA_TESTO,
                              max_tokens=16000, use_web_search=True,
                              pazienza=True))


# Parole sotto le quali il copione non e' un capitolo corto, e' un capitolo
# rotto. La voce legge a centosessantasei parole al minuto misurate, quindi
# ottocento parole sono meno di cinque minuti: meta' del formato.
#
# La soglia e' bassa di proposito, e non e' quella scritta nel prompt. Al
# modello si chiedono duemila parole e si dice che sotto le milleottocento il
# capitolo si butta, perche' chiedere tanto fa scrivere tanto; ma buttare
# davvero un copione da millequattrocento parole — otto minuti e mezzo letti,
# dentro il formato — vorrebbe dire bruciare una chiamata con la ricerca
# accesa per rifare qualcosa di gia' usabile. Qui si scarta solo il troncato.
PAROLE_MINIME = 800


def _completo(testo: Any) -> Dict:
    """Il capitolo, o un errore se il modello ha risposto a meta'.

    Serve perche' con la ricerca web accesa Gemini NON garantisce lo schema:
    `responseSchema` e `google_search` si escludono, quindi SCHEMA_TESTO qui
    e' una richiesta scritta nel prompt, non un contratto. Il 4 settembre 2026
    un capitolo e' tornato con titolo e copione ma senza `fonti`, e il comando
    e' morto con un KeyError a meta' lotto: il capitolo scritto prima si era
    salvato, quello dopo non e' mai stato tentato.

    Un dizionario incompleto non e' un caso raro da lasciare esplodere: qui
    diventa un'eccezione come le altre, che chi chiama gia' sa gestire
    saltando al prossimo argomento.
    """
    if not isinstance(testo, dict):
        raise RuntimeError(
            f"il modello non ha restituito un oggetto ma {type(testo).__name__}")

    mancanti = [k for k in ("titolo", "copione") if not str(testo.get(k) or "").strip()]
    if mancanti:
        raise RuntimeError(
            f"capitolo incompleto, manca {' e '.join(mancanti)}")

    parole = len(str(testo["copione"]).split())
    if parole < PAROLE_MINIME:
        raise RuntimeError(
            f"copione troncato: {parole} parole, sotto il minimo di "
            f"{PAROLE_MINIME}")

    testo["tesi"] = str(testo.get("tesi") or "").strip()

    # Le fonti mancanti NON annullano il capitolo. Un capitolo senza studi
    # dichiarati e' piu' povero, ma e' un capitolo: `verifica_capitolo` di una
    # lista vuota ritorna una lista vuota, e a schermo semplicemente non
    # comparira' nessuna citazione. Annullarlo butterebbe duemila parole buone
    # per un campo che il modello ha dimenticato di allegare.
    fonti = testo.get("fonti")
    if not isinstance(fonti, list):
        fonti = []
    testo["fonti"] = [
        f for f in fonti
        if isinstance(f, dict) and str(f.get("studio") or "").strip()
    ]
    return testo


SCHEMA_SCENE = {
    "type": "object",
    "properties": {
        "scene": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "scena": {"type": "string"},
                    "etichetta": {"type": "string"},
                },
                "required": ["scena", "etichetta"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["scene"],
    "additionalProperties": False,
}

SYSTEM_SCENE = """You choose the footage for a psychology documentary, one \
shot per passage of narration.

For each passage give a `scena`: a short search phrase, three to five words, \
for a stock footage library. It must be something a camera can actually \
film, and it must connect to what the narration is saying at that moment.

  narration about a child left waiting alone
     good: "child alone empty room"
     bad:  "childhood memories concept"   (not filmable)
     bad:  "calm ocean horizon"           (unrelated wallpaper)

Rules that make the difference between a documentary and a screensaver:

1. Literal beats abstract. Film the room, the hands, the street, the waiting. \
   Stock libraries have no footage of "cognition" or "bias".
2. Never name a brand, a real person, or text on screen. Libraries do not \
   have them and you will get nothing back.
3. Vary the shots. If three passages in a row describe thinking, do not ask \
   for three people staring out of windows. Change the subject, the scale, \
   the place.
4. When the narration turns abstract and there is genuinely nothing to film, \
   ask for a plain physical texture — rain on glass, moving crowd, empty \
   corridor. A calm neutral shot is better than a wrong literal one.

`etichetta` is text shown on screen for that passage. Leave it EMPTY for \
almost every passage — a documentary is watched, not read, and permanent \
text is what makes a video look automated.

YOU MAY NOT WRITE A CITATION YOURSELF. Not from the narration, not from \
memory. A list of approved references is given below; each one has already \
been checked against the published literature, and it is the only text \
allowed on screen. Fill `etichetta` only when BOTH are true: the narration \
in that passage is actually describing that study, and the reference is on \
the approved list. Then copy it across exactly as written, character for \
character. Everywhere else leave it empty — including passages that merely \
allude to a study, or continue talking after one was named a moment ago. \
A reference on screen tells the viewer "this specific sentence rests on this \
specific paper", so putting it on the wrong sentence is a false claim even \
when the paper is real."""


def spezza(copione: str, parole: int = PAROLE_PER_BLOCCO) -> List[str]:
    """Il capitolo in blocchi da mezzo minuto, tagliando solo a fine frase.

    Il modello consegna il copione come un paragrafo unico — misurato:
    milacinquecentosettantasei parole senza un solo a capo — quindi non ci si
    puo' appoggiare alla punteggiatura di paragrafo: si lavora sulle frasi.
    """
    # Il punto seguito da spazio e maiuscola. I decimali non ci interessano
    # perche' il copione e' scritto per l'orecchio e i numeri sono a lettere,
    # ma le abbreviazioni con il punto esisterebbero: si evita di spezzare
    # quando cio' che precede il punto e' una sola lettera maiuscola.
    frasi = re.split(r"(?<![A-Z])(?<=[.!?])\s+(?=[A-Z\"'])", copione.strip())

    blocchi: List[str] = []
    corrente: List[str] = []
    n = 0
    for f in frasi:
        f = f.strip()
        if not f:
            continue
        q = len(f.split())
        # Si chiude al confine PIU' VICINO all'obiettivo, non al primo che lo
        # supera. Tagliando sempre dopo si sfora solo in eccesso: sul copione
        # vero dava blocchi da novantacinque parole, cioe' quarantun secondi
        # sulla stessa inquadratura, che su un documentario si vede.
        if corrente and abs(n + q - parole) > abs(n - parole):
            blocchi.append(" ".join(corrente))
            corrente, n = [f], q
            continue
        corrente.append(f)
        n += q
        if n >= parole:
            blocchi.append(" ".join(corrente))
            corrente, n = [], 0

    if corrente:
        # La coda non si butta e non si lascia sola se e' corta: un blocco di
        # otto parole diventa un segmento di tre secondi, che a schermo si
        # legge come un errore di montaggio.
        if blocchi and n < parole // 2:
            blocchi[-1] += " " + " ".join(corrente)
        else:
            blocchi.append(" ".join(corrente))

    return blocchi


# Il copione e' scritto per essere letto ad alta voce, quindi gli anni ci
# arrivano a lettere: «nineteen ninety-five». Va benissimo per la voce ed e'
# illeggibile a schermo, dove una data si scrive in cifre.
_UNITA = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve "
    "thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split())}
_DECINE = {w: (i + 2) * 10 for i, w in enumerate(
    "twenty thirty forty fifty sixty seventy eighty ninety".split())}


def _anno_cifre(anno: str) -> str:
    """«nineteen ninety-five» → «1995». Vuoto se non e' un anno leggibile.

    Meglio nessun anno che un anno sbagliato: chi non si lascia leggere esce
    dalla didascalia, e resta il titolo dello studio da solo.
    """
    a = (anno or "").strip()
    gia = re.search(r"\b(1[5-9]\d\d|20\d\d)\b", a)
    if gia:
        return gia.group(1)

    parole = [p for p in re.split(r"[^a-z]+", a.lower()) if p and p != "and"]
    if not parole:
        return ""
    if parole[:2] == ["two", "thousand"]:
        base, resto = 2000, parole[2:]
    elif parole[0] == "twenty" and len(parole) > 1:
        base, resto = 2000, parole[1:]
    elif parole[0] in _UNITA and _UNITA[parole[0]] >= 15:
        base, resto = _UNITA[parole[0]] * 100, parole[1:]
    else:
        return ""

    n = 0
    for p in resto:
        if p in _DECINE:
            n += _DECINE[p]
        elif p in _UNITA:
            n += _UNITA[p]
        else:
            return ""
    valore = base + n
    return str(valore) if 1500 <= valore <= 2100 else ""


def riferimenti_ammessi(fonti: List[Dict[str, str]],
                        esiti: Optional[List[Dict]] = None) -> List[str]:
    """I riferimenti che si possono mostrare a schermo, e nessun altro.

    Si escludono gli studi che la verifica ha bocciato. Uno «inesistente» a
    schermo e' una citazione falsa; ma si toglie anche il «discussa», perche'
    una scritta sobria con autore e anno comunica al volo «questo pezzo
    poggia qui, e regge», ed e' l'opposto di cio' che il copione dovrebbe
    dire di uno studio contestato. Se una fonte e' discussa il posto per
    dirlo e' la voce, non una didascalia che la fa sembrare solida.
    """
    bocciati = set()
    for e in esiti or []:
        if e.get("verdetto") in ("inesistente", "discussa"):
            bocciati.add(e.get("studio", ""))
    fuori = []
    for f in fonti:
        if f.get("studio", "") in bocciati:
            continue
        titolo = (f.get("studio") or "").strip()
        anno = _anno_cifre(f.get("anno", ""))
        if titolo:
            fuori.append(f"{titolo}, {anno}" if anno else titolo)
    return fuori


def scene(blocchi: List[str], tesi: str = "",
          ammessi: Optional[List[str]] = None) -> List[Dict[str, str]]:
    """Una scena da filmare per ogni blocco, scelta su cio' che il blocco dice.

    Se la chiamata fallisce o torna il numero sbagliato di scene non si
    interrompe il video: si riempie il buco con una stringa vuota, e piu'
    avanti chi non ha scena ripiega sull'atmosfera.
    """
    ammessi = ammessi or []
    elenco = "\n\n".join(f"[{i + 1}] {b}" for i, b in enumerate(blocchi))
    lista = ("\n".join(f"  · {a}" for a in ammessi) if ammessi
             else "  (none — leave every etichetta empty)")
    user = (
        (f"The chapter argues: {tesi}\n\n" if tesi else "")
        + f"APPROVED REFERENCES, the only text allowed on screen:\n{lista}\n\n"
        + f"Here are the {len(blocchi)} passages of narration, in order. "
        f"Give exactly {len(blocchi)} entries, one per passage, in the same "
        f"order:\n\n{elenco}"
    )
    try:
        r = ask_json(SYSTEM_SCENE, user, SCHEMA_SCENE, max_tokens=4000,
                     use_web_search=False)
        fuori = r.get("scene") or []
    except Exception as exc:
        print(f"    scelta delle scene fallita: {str(exc)[:100]}")
        fuori = []

    if len(fuori) != len(blocchi):
        print(f"    attese {len(blocchi)} scene, tornate {len(fuori)}: "
              f"i blocchi scoperti useranno l'atmosfera")

    vuota = {"scena": "", "etichetta": ""}
    piani = [dict(fuori[i]) if i < len(fuori) else dict(vuota)
             for i in range(len(blocchi))]

    # L'elenco approvato si mette davanti al modello E si fa rispettare qui.
    # Lasciata libera, la prima versione componeva le citazioni a orecchio
    # dalla narrazione: metteva «Tversky & Kahneman, 1973» — che la verifica
    # aveva declassato a non verificabile — e ripeteva «Loftus & Pickrell,
    # 1995» su un passaggio che di quello studio non parla. Una citazione a
    # schermo dice «questa frase poggia su questo articolo»: sulla frase
    # sbagliata e' falsa anche quando l'articolo e' vero.
    consentiti = {a.strip() for a in ammessi}
    tolte = 0
    for p in piani:
        e = (p.get("etichetta") or "").strip()
        if e and e not in consentiti:
            p["etichetta"] = ""
            tolte += 1
    if tolte:
        print(f"    {tolte} etichette non nell'elenco verificato: rimosse")
    return piani


# Ripieghi per quando la scena chiesta non si trova. Sono tutti riprese vuote:
# nessuno di questi contraddice qualcosa che la voce possa star dicendo, ed e'
# esattamente cio' che si vuole da un ripiego. `per_frase` invece pesca fra
# mari al tramonto e campi di grano, che sotto una frase su un esperimento non
# sono neutri, sono un'altra cosa.
NEUTRE = (
    "rain on window glass", "empty corridor building", "dust in sunlight",
    "curtain moving in wind", "shadows moving on wall", "water surface ripples",
    "empty room chairs", "blurred crowd walking", "ceiling light flicker",
    "old paper texture close",
)


def _clip(scena: str, testo: str) -> Optional[Path]:
    """Il filmato per un blocco, dal piu' pertinente al piu' neutro.

    Tre gradini, e l'ordine e' il punto. Prima la scena chiesta, esigente:
    deve parlare di quello. Poi la stessa scena senza le prime parole, che
    negli archivi sono quasi sempre l'inquadratura — «close up», «hands» — e
    non il soggetto. Solo alla fine una ripresa vuota.

    Il ripiego non e' piu' `per_frase`. Sul primo capitolo un blocco ha perso
    la clip per due timeout di rete e si e' ritrovato «slow river stones»
    sotto la frase sui pazienti in colonscopia: un fiume di montagna li' non
    e' neutro, e' un'altra cosa. Una ripresa vuota non dice niente, ed e'
    quello che si vuole quando non si ha di meglio; un'atmosfera dice una cosa
    sbagliata per mezzo minuto.
    """
    from . import footage

    tentativi = []
    if scena:
        tentativi.append(scena)
        parole = scena.split()
        if len(parole) > 2:
            tentativi.append(" ".join(parole[-2:]))

    for q in tentativi:
        trovato = footage.cerca(q, orientamento="landscape", esigente=True)
        if trovato:
            path = footage.scarica(trovato)
            if path and footage.si_vede(path):
                print(f"    scena: {q}" + ("" if q == scena else f"  (da «{scena}»)"))
                return path

    ripiego = NEUTRE[int(hashlib.sha1(testo.encode("utf-8")).hexdigest(), 16)
                     % len(NEUTRE)]
    print(f"    niente per «{scena or '(nessuna scena)'}» → {ripiego}")
    trovato = footage.cerca(ripiego, orientamento="landscape")
    if trovato:
        path = footage.scarica(trovato)
        if path and footage.si_vede(path):
            return path
    return footage.per_frase("reflective", testo, orientamento="landscape")


def _sovra(etichetta: str, out: Path) -> Optional[Path]:
    """La sovrimpressione del blocco: la firma sempre, il riferimento se c'e'.

    Si mette su tutti i blocchi, non solo su quelli con la citazione. Se
    comparisse solo con la citazione, comparirebbe e sparirebbe anche la firma,
    quattro volte in dodici minuti, e i blocchi con lo studio avrebbero un
    fondo diverso dagli altri: due cambi di quadro che non corrispondono a
    niente di quello che la voce sta dicendo.

    Ne escono due sole immagini per capitolo — quella con testo e quella senza
    — quindi si tengono in cache: aprire il browser venticinque volte per
    rifare lo stesso PNG sarebbe l'unica parte lenta del montaggio.
    """
    from . import render

    testo = etichetta.strip()
    chiave = hashlib.sha1(testo.encode("utf-8")).hexdigest()[:10]
    png = out / f"sovra-{chiave}.png"
    if png.exists():
        return png

    slide = {"kicker": "", "headline": "", "body": testo,
             "image_query": "", "image_kind": "concept"}
    try:
        fatto = render.render_slides([slide], f"sovra-{chiave}", "capitolo",
                                     size=WIDE, transparent=True)[0]
    except Exception as exc:
        # La firma che manca non vale un capitolo perso: si prosegue nudi.
        print(f"    sovrimpressione non riuscita: {str(exc)[:80]}")
        return None
    shutil.copyfile(fatto, png)
    return png


def _blocco(testo: str, scena: str, etichetta: str, indice: int,
            out: Path) -> Optional[Tuple[Path, float]]:
    """Un blocco montato: narrazione, filmato, ed eventuale riferimento."""
    ff = _ffmpeg()
    w, h = WIDE

    voce = out / f"voce-{indice:03d}.mp3"
    durata = narra(testo, voce)
    if not durata:
        return None

    clip = _clip(scena, testo)
    if not clip:
        return None

    ingressi = ["-stream_loop", "-1", "-i", str(clip)]
    filtro = (f"[0:v]scale={w}:{h}:force_original_aspect_ratio=increase,"
              f"crop={w}:{h},setsar=1[v]")
    mappa_v = "[v]"

    # Il riferimento allo studio compare solo dove la voce lo nomina: una
    # citazione fissa per dodici minuti direbbe che ogni frase poggia su quel
    # foglio, e non e' vero nemmeno per il capitolo meglio documentato.
    png = _sovra(etichetta, out)
    if png:
        ingressi += ["-loop", "1", "-i", str(png)]
        filtro += ";[v][1:v]overlay=0:0:format=auto[vo]"
        mappa_v = "[vo]"

    seg = out / f"seg-{indice:03d}.mp4"
    subprocess.run([
        ff, "-y", *ingressi, "-i", str(voce),
        "-filter_complex", filtro,
        "-map", mappa_v, "-map", f"{2 if png else 1}:a",
        "-t", f"{durata:.3f}",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "21",
        "-pix_fmt", "yuv420p", "-r", "30",
        "-c:a", "aac", "-b:a", "160k",
        str(seg),
    ], check=True, capture_output=True)
    return seg, durata


def costruisci(copione: str, nome: str, tesi: str = "",
               fonti: Optional[List[Dict[str, str]]] = None,
               esiti: Optional[List[Dict]] = None
               ) -> Optional[Tuple[Path, float]]:
    """Il capitolo intero. Ritorna (video, durata in secondi).

    `fonti` sono gli studi dichiarati dal copione ed `esiti` i verdetti di
    `fonti.verifica_capitolo`. Senza, a schermo non compare nessuna citazione:
    e' il lato sicuro, perche' l'alternativa e' lasciarle inventare.
    """
    blocchi = spezza(copione)
    if len(blocchi) < 4:
        print(f"  solo {len(blocchi)} blocchi: copione troppo corto")
        return None

    ammessi = riferimenti_ammessi(fonti or [], esiti)
    print(f"  · {len(blocchi)} blocchi, {len(ammessi)} riferimenti mostrabili")
    piani = scene(blocchi, tesi, ammessi)

    ff = _ffmpeg()
    out = OUTPUT_DIR / f"capitolo-{nome}"
    out.mkdir(parents=True, exist_ok=True)

    segmenti: List[Path] = []
    t = 0.0
    for i, (b, p) in enumerate(zip(blocchi, piani)):
        print(f"  [{i + 1}/{len(blocchi)}] {b[:58]}...")
        r = _blocco(b, p.get("scena", ""), p.get("etichetta", ""), i, out)
        if not r:
            # Saltare un blocco qui non e' come saltare una curiosita'
            # nell'episodio del sabato: quelle sono indipendenti, questi no.
            # Togliendone uno il discorso perde un passaggio e la frase dopo
            # non torna. Meglio fermarsi e capire perche'.
            print("    blocco non montato: capitolo annullato")
            return None
        seg, dur = r
        segmenti.append(seg)
        t += dur

    elenco = out / "lista.txt"
    elenco.write_text("".join(f"file '{s.resolve()}'\n" for s in segmenti))
    grezzo = out / "grezzo.mp4"
    subprocess.run([ff, "-y", "-f", "concat", "-safe", "0", "-i", str(elenco),
                    "-c", "copy", str(grezzo)], check=True, capture_output=True)

    finale = out / "capitolo.mp4"
    musica = _traccia_a_caso(nome, "reflective")
    if musica:
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

    vera = _durata(finale) or t
    print(f"  → {finale}  ({vera / 60:.1f} minuti, {len(segmenti)} blocchi)")
    return finale, vera
