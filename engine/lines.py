"""Frasi autonome per i reel.

Diverse dagli hook dei caroselli: un hook promette che il resto del post
spiegherà: una frase da reel non ha un resto. Deve chiudere il cerchio da
sola, in sei secondi, senza fonte, senza spiegazione e senza contesto.

Ogni frase porta con sé il proprio **registro emotivo**, che decide la musica
e il tipo di filmato. È il pezzo che tiene insieme il reel: una frase amara
sotto un ukulele allegro è peggio di un reel muto.

Le frasi nascono dai fatti già verificati in magazzino, non dal nulla: così
anche i reel restano ancorati a qualcosa di vero invece di diventare aforismi
motivazionali, che è esattamente ciò che questa pagina non è.
"""
from __future__ import annotations

import sqlite3
from typing import Any, Dict, List

from .config import cfg
from .llm import ask_json

MOODS = ("reflective", "unsettling", "warm", "bright")

LINES_SCHEMA = {
    "type": "object",
    "properties": {
        "lines": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    # Due tempi invece di una frase sola: una frase statica si
                    # legge in due secondi e poi non trattiene più nessuno,
                    # mentre il tempo di visione oltre i 3 secondi è il segnale
                    # che decide la distribuzione dei reel.
                    # L'indice del fatto usato: permette di legare il reel
                    # alla curiosita' di partenza e non riusarla mai due volte.
                    "source_index": {"type": "integer"},
                    "hook": {"type": "string"},
                    "reveal": {"type": "string"},
                    "mood": {"type": "string", "enum": list(MOODS)},
                    # A pezzi, non come stringa: chiedere di formattare con
                    # righe vuote dentro una stringa non funziona — il
                    # modello le comprime, e la didascalia esce come blocco
                    # unico con la fonte sepolta. Si assembla in codice.
                    "caption": {
                        "type": "object",
                        "properties": {
                            "apertura": {"type": "string"},
                            "prova": {"type": "string"},
                        },
                        "required": ["apertura", "prova"],
                        "additionalProperties": False,
                    },
                    "hashtags": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["source_index", "hook", "reveal", "mood", "caption", "hashtags"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["lines"],
    "additionalProperties": False,
}



# ─── Prova A/B sul registro ───────────────────────────────────────────────────
#
# Ipotesi da verificare: la pagina ha like buoni (3,2%) ma pochissime
# iscrizioni (0,19%). Il sospetto è che manchi un motivo per restare — i video
# funzionano, la pagina no, perché ogni video si chiude su se stesso.
#
# Il solo indizio nei dati: il video più forte dei primi cinque (497 viste
# contro 115, e l'unico commento del canale) non raccontava come funziona la
# mente. Raccontava una cosa su di te che ti fa stare meglio: "gli altri ti
# apprezzano più di quanto tu creda".
#
# Quindi si prova a metà: stessa riserva di curiosità, stesse regole, cambia
# solo il registro. Se dopo una settimana i due gruppi hanno gli stessi
# numeri, l'ipotesi era sbagliata e si torna indietro — che è il motivo per
# cui il gruppo di controllo esiste.

VARIANTI = ("osservazione", "riconoscimento")

REGISTRO = {
    # Gruppo di controllo: nessuna istruzione aggiuntiva, esattamente ciò che
    # la pagina fa da sempre. Non va "migliorato" mentre la prova è in corso,
    # o il confronto perde significato.
    "osservazione": "",

    "riconoscimento": """

REGISTER FOR THIS BATCH — read this before writing

Write these lines as recognition, not as information.

The difference: an observation tells the reader how minds work. A recognition
tells the reader something about *themselves* that they had been carrying
alone. The first is interesting. The second is a relief.

  observation:   "Your memory of an event ignores its duration."
  recognition:   "You are not remembering it wrong. Everyone stores it that
                  way — the worst minute and the last one."

  observation:   "People underestimate how much strangers like them."
  recognition:   "They liked you more than you thought. They always do."

What to aim for, in order:
  1. The reader should think "that is me" before they think "interesting".
  2. Where the research honestly allows it, let the line absolve rather than
     accuse. Most of these findings are about something ordinary that people
     quietly believe is their private defect. Saying "this is not your fault,
     it is how everyone is built" is both truer and kinder.
  3. Prefer the findings that touch being judged, being seen, being wrong
     about yourself in a way that turns out to be forgivable.

WHICH FINDINGS TO PICK

You are given more findings than lines you need. Choose the ones that carry a
private worry: being judged, being seen, misjudging yourself, believing you are
the only one. Leave the mechanical curiosities — how perception works, how
memory encodes — to another day. They are interesting, but nobody recognises
themselves in them.

If none of the findings offered can carry that weight, write them straight
rather than forcing warmth onto material that does not have it. A forced
consolation is more damaging than a dry line: it reads as manipulation, and
this account cannot afford to sound like it is selling comfort.

What does NOT change:
  · Still true. Absolution is not permission to overstate — if the research
    does not support the comfort, pick another fact. A false consolation is
    worse than a dry observation, because people act on it.
  · Still no advice, no commands, no motivational register. You are not
    reassuring the reader, you are telling them a fact that happens to
    relieve them. The difference is audible.
  · Still two beats, same lengths, same mood rules.""",
}


# ─── Prova sull'apertura: il divario contro lo scontro ────────────────────────
#
# Il ragionamento completo sta in `config.yaml` sotto `esperimento.apertura`.
# Qui basta la differenza fra i due modi di aprire, perché è quella che il
# modello deve capire:
#
#   divario   L'aggancio toglie qualcosa. "The thing you're still cringing
#             about?" non dice niente di falso: lascia un buco e chi guarda
#             resta per vederlo chiuso. È curiosità, ed è un sentimento tiepido.
#
#   scontro   L'aggancio contraddice. "Your closest friend reads you worse than
#             a stranger does" dice una cosa che chi guarda crede falsa, e resta
#             per vederla smontare. È obiezione, ed è più forte della curiosità
#             perché tira in mezzo l'amor proprio.
#
# La differenza si gioca tutta nei primi tre secondi — che è esattamente la
# finestra in cui l'aggancio sta da solo sullo schermo (`reel.reveal_at: 3.4`)
# ed esattamente ciò che sappiamo misurare bene.
#
# ATTENZIONE, e vale più di tutto il resto: lo scontro è la scorciatoia più
# vicina alla bugia che questo account possa prendere. Un'affermazione è tanto
# più contestabile quanto più è estrema, quindi il modello ha un incentivo
# strutturale a esagerare. Per questo il blocco qui sotto spende metà del testo
# a dire cosa NON è uno scontro. Se la prova la vince ma le frasi hanno smesso
# di essere vere, abbiamo perso comunque.

APERTURE = ("divario", "scontro")

# I due blocchi hanno la stessa lunghezza e lo stesso impegno, e non e' una
# simmetria estetica: e' la difesa contro l'errore piu' facile da fare qui.
#
# Il gruppo di controllo NON e' "il prompt senza aggiunte". Lo era fino al 21
# agosto 2026, e non funzionava: il prompt di sistema chiedeva agganci "a
# little accusatory" e due dei suoi tre esempi erano scontri belli e buoni
# ("You don't remember your holiday"). Il controllo quindi non era neutro, era
# uno scontro annacquato — generando frasi di prova e' uscito dal gruppo
# divario "You are unhappy with your own success", che e' un'affermazione
# contestabile quanto quelle dell'altro braccio. Due bracci che si somigliano
# misurano zero, e uno zero del genere si legge come "l'apertura non conta"
# quando in realta' dice "non abbiamo provato due cose diverse".
#
# La correzione ovvia sarebbe stata togliere forza al controllo. Sarebbe stato
# l'errore peggiore: lo scontro avrebbe battuto un fantoccio costruito da noi,
# cioe' esattamente la cosa che il blocco scontro qui sotto vieta di fare col
# pubblico ("Not a fight with a strawman"). Il risultato avrebbe detto "lo
# scontro batte un aggancio scritto male", che sappiamo gia' e non serve a
# nessuno.
#
# Quindi: due bracci forti uguali, che differiscono nel MECCANISMO con cui
# tengono lo spettatore. Curiosita' contro obiezione. Se lo scontro vince, ha
# battuto il meglio che sappiamo fare nell'altro modo.

APERTURA = {
    "divario": """

HOW TO OPEN, FOR THIS BATCH — the withheld answer

THE ONE TEST, and everything else in this block serves it:

    The viewer must AGREE with the hook, and still need the next line.

They recognise the situation instantly — they nod — and what is missing is
WHY it happens. The force comes from the gap, never from an argument. If the
viewer's reaction is "no I don't" or "that's not true", the hook has started
a fight instead of opening a gap, and a fight is the wrong tool for this
batch. Rewrite it.

  hook:   "There is one conversation you keep replaying."
  reveal: "Unfinished things hold memory better than settled ones."

  hook:   "Some insults outlast the day they were said."
  reveal: "One criticism is filed with the weight of five compliments."

  hook:   "You can feel the exact moment a room turns."
  reveal: "You read the shift from posture, a half-second before the words."

Notice what those three have in common: the viewer would say "yes, that
happens to me" to every one of them, and none of them says why. That is the
whole shape.

WHAT A WITHHELD ANSWER IS NOT — this is where it usually goes wrong

  Not a claim they would argue with. "You are convinced you are a better
  driver than average" is a fine hook, but it belongs to a different batch:
  it works by contradicting the viewer, and here we are measuring what the
  gap alone can do. Anything the viewer would answer back to is out — no
  matter how strong it is. Describe what they already know they do; withhold
  only the reason.

  One template slips through more than any other: a statement about how
  accurately the viewer sees, judges or knows something. Every finding about
  bias invites it, and it is always a collision. Same finding, both ways:

    collision, wrong batch:  "You see the world exactly as it is."
    withheld, this batch:    "There is someone you have decided is simply
                              being difficult."

  Both come from the same research. Only the second one gets a nod.

  Not vague. "Your brain does something odd here" withholds everything and
  promises nothing, so there is nothing to wait for. Name the situation, name
  the moment, name the feeling — and withhold the finding, only the finding.

  Not a tease. No "you won't believe what happens next", no "the reason will
  surprise you". Those announce that something is coming instead of making
  the viewer want it, and they read as the register of accounts people mute.

  Not a formula. Asked for a withheld answer it is easy to write every hook
  as a dangling fragment with a question mark — "The thing you just said?",
  "Why that memory sticks?" — and across a batch that becomes a tic. At most
  one hook may end in a question mark; the rest are complete sentences that
  simply stop short of the explanation.

The reveal must be the exact piece the hook removed. Not a related thought,
not a broader lesson: the missing part, handed over.

Last pass, one hook at a time: would the viewer answer yes, or no? Not
"interesting" — yes or no. Every "no" is a collision that wandered into the
wrong batch, however good the line is. Rewrite it or use another finding.""",

    "scontro": """

HOW TO OPEN, FOR THIS BATCH — the collision

THE ONE TEST, and everything else in this block serves it:

    The viewer must want to ARGUE with the hook, and stay to see it settled.

The hook states something they believe is wrong about themselves. They stay to
watch it be taken apart, and the reveal shows the research siding with the
hook rather than with them. The force comes from contradiction, never from
something being held back. If the natural reaction is "hm, go on" instead of
"no I don't", the hook is withholding rather than colliding — rewrite it.

  hook:   "Your closest friend reads you worse than a stranger does."
  reveal: "Strangers judge from behaviour. Friends judge from who they need
           you to be."

  hook:   "You never remember a holiday. You remember two minutes of it."
  reveal: "The best moment and the last one. The rest is gone."

  hook:   "Nobody spent the evening thinking about your message."
  reveal: "They were busy assuming you weren't thinking about theirs."

Almost every finding supports both openings, so the choice is always
available — it is never the fact's fault:

  withholding:  "Something odd happens to a crowd when one person freezes."
  colliding:    "You would not step in either. Nobody does."

WHAT A COLLISION IS NOT — read this twice

  Not an overstatement. The clash comes from the finding being genuinely
  counter-intuitive, never from stretching it. If you have to widen the claim
  to make it collide, the fact is wrong for this batch: pick another. A line
  that is 20% more striking and 5% less true is a bad trade for this account,
  because the whole value here is that everything can be checked.

  Not an insult. "You are bad at X" is a collision only if the research says so
  about everyone, and only if the reveal explains why it is not a personal
  failing. Attacking the viewer without paying it off is the register of
  accounts that farm angry comments. The reveal must land as relief or as
  explanation, never as a verdict left standing.

  Not a question. No hook ending in a question mark. A question invites
  waiting; a statement invites disagreement, and disagreement is the point.

  Not a fight with a strawman. "Everyone says X, but science says Y" is the
  laziest collision there is, and every account uses it. Collide with what the
  VIEWER believes about themselves, not with what "people say".

The reveal's job changes slightly too: it must resolve the collision, not just
complete the sentence. Afterwards the viewer should feel the hook was fair —
not that they were tricked into staying.

Last pass, one hook at a time: would the viewer answer yes, or no? Every "yes"
is a withheld answer that wandered into the wrong batch, however good the line
is. Rewrite it or use another finding.""",
}


def scegli_apertura(giorno: int, indice_fascia: int) -> str:
    """Quale apertura tocca a questa uscita: 'divario' o 'scontro'.

    Gira INSIEME alla prova sulla lunghezza, e non e' pigrizia: e' un disegno
    fattoriale 2x2. Il punto che lo rende conveniente e' che l'effetto
    principale di ciascun fattore si legge su TUTTI i video, non su meta'. Con
    60 uscite: 60 video per dire se lo scontro tiene di piu', 60 per dire se il
    corto rende di piu', e solo l'interazione fra i due — "lo scontro serve di
    piu' sui video corti?" — resta a 15 per casella. L'interazione e' la
    domanda a cui teniamo meno, quindi e' quella giusta da sacrificare.

    Perche' `giorno // 2` e non `giorno`: con `giorno` l'apertura cambierebbe
    allo stesso ritmo della lunghezza, i due fattori sarebbero identici a ogni
    uscita e non si potrebbero piu' distinguere — misureremmo una cosa sola
    credendo di misurarne due. Dividendo per due, l'apertura gira a meta'
    velocita', e su quattro giorni tutte e quattro le combinazioni escono una
    volta ciascuna.

    A prova chiusa (`attiva: false`) si torna all'aggancio normale, cioe' le
    TWO BEATS del prompt di sistema senza aggiunte.
    """
    if not cfg.get("esperimento.apertura.attiva", False):
        return "divario"
    if not cfg.get("esperimento.apertura.alterna_fascia", True):
        indice_fascia = 0
    return "scontro" if (giorno // 2 + indice_fascia) % 2 == 0 else "divario"


def scegli_variante(conn) -> str:
    """Il registro con cui scrivere. Dal 20 agosto 2026 non e' piu' una prova.

    La prova A/B e' finita e ha un vincitore. Su sette video per gruppo con
    dati di visione:

        osservazione     34, 36, 40, 41, 51, 55, 61   mediana 41%
        riconoscimento   40, 45, 45, 50, 52, 74, 115  mediana 50%

    Non e' un video fuori scala a trascinare la media: il PEGGIORE di
    riconoscimento vale quanto la mediana di osservazione, e togliendo il 115
    la mediana resta 47,5. Ogni quantile e' piu' alto. I like sono identici
    nei due gruppi (0,7%), quindi la differenza sta esattamente dove conta —
    nella percentuale di visione, che e' il numero con cui YouTube decide se
    rilanciare uno Short.

    Sette video per gruppo non sono una certezza statistica. Ma da quando il
    ritmo e' sceso a uno Short al giorno, aspettarla vorrebbe dire mesi di
    pubblicazioni scritte per meta' nel registro che sappiamo peggiore. Il
    costo dell'attesa supera il valore della certezza in piu'.

    `scegli_variante` resta come funzione, e resta l'unico punto da cambiare
    se un giorno si vuole aprire una prova nuova: quel giorno si rimette il
    bilanciamento e si aggiunge una terza voce a VARIANTI.
    """
    return "riconoscimento"


def scegli_lunghezza(giorno: int, indice_fascia: int) -> str:
    """Quale gruppo tocca a questa uscita: 'corto' o 'lungo'.

    Prova aperta il 21 agosto 2026. Un video con UNA curiosita' rende piu' di
    uno con tre? Il ragionamento completo, con i numeri che dicono quanto
    rumore c'e' e perche' la percentuale di visione non puo' decidere, sta in
    `config.yaml` sotto `esperimento.lunghezza`. Qui c'e' solo la scelta.

    `giorno` = giorni dall'inizio della prova. `indice_fascia` = 0 per il video
    delle 13:00, 1 per quello delle 19:00.

    NON e' un sorteggio e NON e' "tocca a chi e' indietro". Il conteggio
    sembrava la scelta ovvia — bilancia i gruppi — ma su questo disegno e'
    sbagliato: a fine giornata i due gruppi sono sempre pari, quindi il primo
    video del giorno dopo tocca sempre allo stesso, che si prende sempre le
    13:00. Dopo un mese il gruppo corto avrebbe trenta uscite di pranzo e il
    lungo trenta uscite di sera, e staremmo misurando l'orario.

    La somma `giorno + indice_fascia` risolve entrambe le cose insieme: ogni
    giornata contiene un video per gruppo (quindi il giorno della settimana non
    entra), e i gruppi si scambiano la fascia ogni giorno (quindi l'ora non
    entra). E' deterministica, quindi si puo' anche ricostruire a posteriori
    quale gruppo AVREBBE dovuto uscire in un giorno in cui il caricamento e'
    fallito.

    A prova chiusa (`attiva: false`) si torna al comportamento normale: tutto
    'lungo', cioe' `publish.youtube.facts_per_video`.
    """
    if not cfg.get("esperimento.lunghezza.attiva", False):
        return "lungo"
    if not cfg.get("esperimento.lunghezza.alterna_fascia", True):
        indice_fascia = 0
    return "corto" if (giorno + indice_fascia) % 2 == 0 else "lungo"


def data_inizio_prova(prova: str = "lunghezza"):
    """Il giorno di partenza di una prova, come `date`. None se non impostato.

    Il parametro `prova` esiste da quando le prove sono due. Prima la data era
    una sola, letta da `esperimento.lunghezza.inizio`, e quando e' arrivata la
    prova sull'apertura il conteggio dei giorni ha continuato a leggere quella
    — funzionava, perche' le due date sono uguali per costruzione, ma solo
    finche' restavano uguali. Due chiavi in `config.yaml` che DEVONO combaciare
    senza che niente lo controlli sono un guasto che aspetta il giorno in cui
    qualcuno ne sposta una: la prova sull'apertura avrebbe cambiato gruppo al
    momento sbagliato e nessuna riga di output lo avrebbe detto.
    """
    import datetime as _dt

    testo = str(cfg.get(f"esperimento.{prova}.inizio", "") or "").strip()
    try:
        return _dt.datetime.strptime(testo, "%Y-%m-%d").date()
    except ValueError:
        return None


def inizio_prova(prova: str = "lunghezza") -> float:
    """L'istante da cui contare, come tempo unix.

    Serve un taglio netto: nella tabella `esperimento` ci sono ancora le righe
    della prova sul registro (osservazione/riconoscimento), e senza filtro
    finirebbero mescolate a queste. Sono distinguibili anche dal valore, ma la
    data e' la difesa che regge pure se un giorno qualcuno riusa un nome.
    """
    import datetime as _dt

    d = data_inizio_prova(prova)
    return _dt.datetime.combine(d, _dt.time.min).timestamp() if d else 0.0


def giorni_di_prova(prova: str = "lunghezza") -> int:
    """Giorni trascorsi dall'inizio. 0 il primo giorno."""
    import datetime as _dt

    d = data_inizio_prova(prova)
    return (_dt.date.today() - d).days if d else 0


def _system(variante: str = "osservazione", apertura: str = "divario") -> str:
    return f"""You write single lines for {cfg.get('brand.name')} ({cfg.get('brand.handle')}),
an account about how the human mind actually works.

VOICE
{cfg.get('voice.guide')}

Each line becomes a six-second video: the sentence sits alone over footage,
with music. There is no second slide, no source shown, no explanation. The
line is the entire post.

TWO BEATS, NOT ONE SENTENCE

The single most important number for a reel is how many people are still
watching after three seconds. A full sentence sitting still on screen is read
in two, and then there is nothing left to wait for — so people leave exactly
when it counts.

So each reel is built in two parts:

  hook    Appears first, alone, and holds the screen by itself. 4-9 words.
          Blunt and specific. It must NOT contain the answer.
  reveal  Appears after. It settles what the hook raised.
          5-14 words. This is the part people screenshot and send.

HOW the hook should grip the viewer — and worked examples of it — are in the
final block of this brief. That instruction changes between batches, because
it is the one thing about these videos currently being measured. Follow it
closely: it is not a stylistic preference, it is the variable under test.

Those examples show the SHAPE, and are not stock to draw from. Several of them
were written from findings that may well be in today's material; if one of them
matches a finding you were given, that is the one case where you must write the
line yourself rather than reach for the example. A hook that repeats an example
word for word has been published already.

WHAT MAKES BOTH BEATS WORK

  About the reader, not about people. "You" is the whole value — the small
  shock of being described. "People tend to…" is a lecture.

  Second person, but not the same first word eight times. Written straight,
  that rule makes every single hook begin with "You", and a feed of those
  reads like one line repeated. The sentence can still be about the viewer
  while starting elsewhere: "There is one conversation you keep replaying",
  "Some insults outlast the day they were said", "The middle of the list is
  the part that goes". Across a batch, at most half the hooks may open with
  "You" or "Your".

  True. This is the hard part: stripped of its evidence a line drifts into a
  motivational aphorism, which is the one thing this account is not. Say only
  what the research supports. If making it punchy requires overstating, use a
  different fact.

  Sendable. The strongest reels are the ones somebody forwards to a friend
  saying "this is you". Sends per reach now weigh more than likes, so prefer
  lines that describe a person the viewer knows.

  No advice, no commands, no "remember that…". No question marks in the
  reveal. Never open with "The fact that", "Studies show", "Science says" or
  "Your brain is" — the four openings every account already uses.

MOOD — decides the music and the footage, so it must be honest

  reflective  quiet, thoughtful, a little melancholy. The default.
  unsettling  the fact is uncomfortable: self-deception, bias, being wrong
              about yourself without knowing.
  warm        connection, being liked, being seen, forgiveness.
  bright      genuinely surprising or lightly funny.

  Choosing the wrong mood ruins the video more than a weak line would:
  cheerful music under a bleak sentence reads as a mistake.

CAPTION — the first line decides whether the rest is ever seen

  Instagram shows roughly the first line in the feed and hides the rest behind
  "more". Most people never expand it. So the caption does NOT open with the
  study: sixteen posts opened with "In a 1985 study, Richard Thaler found…"
  and between them collected four likes. A citation is what a reader scrolls
  past, however good the finding is.

  Two separate fields. Do not format them, do not join them: they are
  assembled with blank lines in code, and the CTA is added there too.

  apertura  What it means for the reader. A complete thought, under 90
            characters, that stands on its own. Not a run-up to a point made
            later, and not the on-screen line repeated word for word — the
            same idea from another angle.
  prova     One or two sentences with the study, the year, what was measured.
            This is what a sceptical reader looks for, and finding a real
            source is what turns a viewer into a follower. It belongs after
            the reason to keep reading, not before it.

HASHTAGS
  Exactly 5, lowercase, no "#".

  In 2026 hashtags no longer drive discovery — Instagram uses them to file
  content by topic, not to distribute it. Thirty tags do nothing that five do
  not, and a wall of them reads as spam. So: five narrow, specific tags that
  describe the actual mechanism, not the field.""" + REGISTRO.get(variante, "") \
        + APERTURA.get(apertura, APERTURA["divario"])


def generate(conn: sqlite3.Connection, count: int,
             imparato: str = "", canale: str = "instagram",
             variante: str = "riconoscimento",
             apertura: str = "divario") -> List[Dict[str, Any]]:
    """Ricava frasi dai fatti verificati, escludendo quelli gia' usati.

    L'esclusione avviene sul FATTO, non sulla frase: due reel possono
    raccontare lo stesso studio con parole completamente diverse, e in quel
    caso nessun confronto testuale li riconosce come doppioni. E' gia'
    successo — due reel sul peak-end rule pubblicati a poche ore di distanza.

    `imparato` porta dentro le frasi che hanno tenuto piu' a lungo lo
    spettatore su YouTube. Sta qui e non solo nella generazione delle
    curiosita' perche' la percentuale di visione dipende tanto da COME e'
    scritta la frase quanto da cosa racconta: l'aggancio ha due secondi per
    funzionare, e quello e' un fatto di formulazione.

    Il default di `variante` e' "riconoscimento", cioe' il VINCITORE della
    prova di agosto. Fino al 21 agosto 2026 era "osservazione", il gruppo di
    controllo, ed e' costato caro: i due punti di chiamata che non passano il
    parametro — TikTok e i reel Instagram — hanno scritto per un mese nel
    registro che sapevamo peggiore (41% di visione contro 50%), mentre YouTube
    usava quello buono. Il valore di default di un parametro e' una decisione
    presa una volta e poi invisibile: se deve esistere, che sia la scelta
    giusta, cosi' un punto di chiamata scritto domani non eredita in silenzio
    un gruppo di controllo chiuso da un pezzo.
    """
    # Esclusione dura su tutto cio' che e' gia' uscito su Instagram, caroselli
    # compresi. Prima era una preferenza — le mai usate venivano prima, ma in
    # mancanza si ripescava — e quel ripiego faceva ricomparire lo stesso
    # studio a giorni di distanza sullo stesso profilo. Il ripiego esisteva
    # perche' la produzione era scarsa; ora le scorte si rigenerano da sole
    # quando scendono, quindi non serve piu'.
    fatti = conn.execute(
        """SELECT id, hook, fact, detail, source_hint FROM facts
            WHERE status IN ('approved','rendered','published')
              AND id NOT IN (SELECT fact_id FROM fact_uses WHERE channel = ?)
            ORDER BY RANDOM()
            LIMIT ?""",
        (canale, count * 2),
    ).fetchall()

    if not fatti:
        return []

    materiale = "\n\n".join(
        f"[{i}] FACT: {f['fact']}\n    DETAIL: {f['detail']}\n    SOURCE: {f['source_hint']}"
        for i, f in enumerate(fatti)
    )
    user = f"""Turn these verified findings into {count} standalone lines.

Use a different finding for each line, and set source_index to the number in
brackets of the finding you used. Pick the ones that survive being stripped to
a single sentence — some facts need their evidence to make sense, and those
are not suitable here.

{materiale}

Return JSON matching the schema."""

    # Va in coda al messaggio utente e non nel prompt di sistema: il sistema
    # e' identico a ogni chiamata e resta in cache, questo cambia ogni giorno.
    if imparato:
        user += "\n\n" + imparato

    data = ask_json(_system(variante, apertura), user, LINES_SCHEMA,
                    effort="medium", max_tokens=8000)
    linee = data.get("lines", [])[:count]

    pinned = cfg.get("caption.pinned_hashtags", []) or []
    visti: set = set()
    for l in linee:
        idx = l.get("source_index", -1)
        l["fact_id"] = fatti[idx]["id"] if 0 <= idx < len(fatti) else None
        # Il prompt chiede una curiosita' diversa per ogni frase, ma non e' una
        # garanzia. L'esclusione a monte guarda il database, e dentro lo stesso
        # lotto non ha ancora nulla da vedere: due frasi sullo stesso studio
        # passerebbero entrambe e uscirebbero a poche ore di distanza.
        if l["fact_id"] is not None and l["fact_id"] in visti:
            l["fact_id"] = None
            l["_doppione"] = True
        elif l["fact_id"] is not None:
            visti.add(l["fact_id"])
        l["hook"] = l["hook"].strip()
        l["reveal"] = l["reveal"].strip().rstrip("?")
        # L'aggancio DEVE chiudersi con un punto. Il database salva una stringa
        # sola e chi la rilegge — il titolo YouTube, il testo del video lungo —
        # la rispezza sul primo ". ". Senza il punto quel taglio cade dentro la
        # rivelazione e il titolo esce con mezza frase di troppo.
        if l["hook"] and l["hook"][-1] not in ".!?":
            l["hook"] += "."
        # `line` resta come testo unico per il database e i controlli
        # anti-duplicato, che ragionano su una stringa sola.
        l["line"] = f"{l['hook']} {l['reveal']}"
        if l.get("mood") not in MOODS:
            l["mood"] = "reflective"
        tag: List[str] = []
        for t in list(pinned) + l.get("hashtags", []):
            t = t.lstrip("#").strip().lower().replace(" ", "")
            if t and t not in tag:
                tag.append(t)
        l["hashtags"] = tag[:5]

    # Le frasi che raccontavano una curiosità già presa da un'altra frase dello
    # stesso lotto vengono scartate qui, non lasciate senza `fact_id`: senza il
    # fact_id uscirebbero lo stesso e sarebbero invisibili a ogni controllo
    # futuro, che è precisamente il guasto da cui questo pezzo nasce.
    scartate = [l for l in linee if l.get("_doppione")]
    if scartate:
        print(f"  · {len(scartate)} frasi scartate: stessa curiosità di un'altra del lotto")
    return [l for l in linee if not l.get("_doppione")]


def corpo_didascalia(line: Dict[str, Any], ponte: bool = True,
                     canale: str = "") -> str:
    """La didascalia senza hashtag.

    Sta separata da `full_caption` perche' i due usi vogliono cose diverse e
    confonderli e' gia' costato due volte. Nel database il reel salva SOLO il
    corpo: gli hashtag vengono riletti dalla colonna dedicata e aggiunti al
    momento della pubblicazione. Chi salva qui la versione completa se li
    ritrova stampati due volte sotto al video.

    Il dict va convertito PRIMA di toccare il database: sqlite non sa legare un
    dict e la generazione dei reel moriva li', in silenzio, una riga per volta.

    `ponte=False` toglie il rimando al canale YouTube. Serve su YouTube stesso,
    dove quella riga invitava a cercare il canale che si sta gia' guardando —
    e nella stessa descrizione compariva due righe sotto il rimando opposto,
    verso Instagram. Il richiamo incrociato ha senso solo verso l'altra parte.

    `canale` decide quale richiesta finale mettere. Serve perche' `caption.cta`
    e' scritta per Instagram — porta la chiocciola Instagram — e finiva tale e
    quale nella descrizione degli Short: il canale YouTube chiedeva due volte
    di andare altrove e non chiedeva mai di iscriversi. Senza `canale` il
    comportamento resta quello di prima, per i richiami che non sanno dove
    stanno andando.
    """
    # La didascalia arriva a pezzi e si unisce qui con le righe vuote: prima
    # si chiedeva al modello di formattarla e la comprimeva in un blocco solo,
    # con la fonte e la CTA sepolte dietro il "altro" di Instagram.
    grezza = line.get("caption", "")
    if isinstance(grezza, dict):
        pezzi = [x.strip() for x in (grezza.get("apertura"), grezza.get("prova"))
                 if x and x.strip()]
        cta = (cfg.get(f"cta.testo.{canale}", "") if canale else "") \
            or cfg.get("caption.cta", "")
        if cta:
            pezzi.append(cta)
    else:
        # I reel gia' in coda hanno la didascalia come stringa unica.
        pezzi = [str(grezza).strip()]
    # Rimando al canale YouTube, prima degli hashtag: dopo non lo legge
    # nessuno. Instagram non rende cliccabili i link in didascalia, quindi si
    # scrive il nome del canale, che si puo' cercare, invece di un URL.
    rimando = cfg.get("caption.cross_promo", "") if ponte else ""
    if rimando:
        pezzi.append(rimando)
    return "\n\n".join(pezzi)


def full_caption(line: Dict[str, Any]) -> str:
    """Corpo piu' hashtag: la forma da mandare a Instagram, non da salvare."""
    corpo = corpo_didascalia(line)
    tags = " ".join("#" + t for t in line.get("hashtags") or [])
    return "\n\n".join(x for x in (corpo, tags) if x)


# ─── Riscrittura delle didascalie in coda ─────────────────────────────────────

DIDASCALIA_SCHEMA = {
    "type": "object",
    "properties": {
        "apertura": {"type": "string"},
        "prova": {"type": "string"},
    },
    "required": ["apertura", "prova"],
    "additionalProperties": False,
}


def riscrivi_didascalia(hook: str, reveal: str, fatto: str,
                        fonte: str = "") -> Optional[Dict[str, str]]:
    """Rifà la didascalia di un reel gia' montato, senza rifare il video.

    Serve perche' la vecchia specifica faceva aprire con lo studio, e i
    contenuti gia' in coda se la portano dietro. Rifare il video per cambiare
    due righe di testo sarebbe uno spreco: il montaggio va bene, e' la
    didascalia a essere sbagliata.
    """
    sistema = f"""You write the Instagram caption for a short video from
{cfg.get('brand.name')}.

VOICE
{cfg.get('voice.guide')}

Instagram shows roughly the first line in the feed and hides the rest behind
"more". Most people never expand it. So the caption must NOT open with the
study: a citation is what a reader scrolls past, however good the finding is.

Two separate fields, not formatted and not joined — they are assembled in code.

  apertura  What it means for the reader. A complete thought, under 90
            characters, standing on its own. NOT the on-screen line repeated
            word for word: the same idea from another angle.
  prova     One or two sentences with the study, the year, what was measured.
            After the reason to keep reading, not before it."""

    try:
        return ask_json(
            sistema,
            f"The video shows these two lines on screen:\n"
            f"  1. {hook}\n  2. {reveal}\n\n"
            f"The verified finding behind it:\n{fatto}\n"
            + (f"Source: {fonte}\n" if fonte else "")
            + "\nWrite the caption.",
            DIDASCALIA_SCHEMA, effort="medium", max_tokens=1500,
        )
    except Exception as exc:
        print(f"    riscrittura fallita: {str(exc)[:90]}")
        return None
