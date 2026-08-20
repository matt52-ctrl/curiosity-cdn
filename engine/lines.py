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


def _system(variante: str = "osservazione") -> str:
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

So each reel is built as a withheld answer:

  hook    Appears first, alone. It must open a gap the viewer needs closed.
          4-9 words. Blunt, specific, a little accusatory.
          It must NOT contain the answer.
  reveal  Appears after. It closes the gap and pays off the wait.
          5-14 words. This is the part people screenshot and send.

    hook:   "Nobody can tell you're panicking."
    reveal: "Observers spot it about half as often as you feel it."

    hook:   "You don't remember your holiday."
    reveal: "You remember its best hour, and its last one. That's all."

    hook:   "The thing you're still cringing about?"
    reveal: "They forgot it the same week."

WHAT MAKES BOTH BEATS WORK

  About the reader, not about people. "You" is the whole value — the small
  shock of being described. "People tend to…" is a lecture.

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
  describe the actual mechanism, not the field.""" + REGISTRO.get(variante, "")


def generate(conn: sqlite3.Connection, count: int,
             imparato: str = "", canale: str = "instagram",
             variante: str = "osservazione") -> List[Dict[str, Any]]:
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

    data = ask_json(_system(variante), user, LINES_SCHEMA,
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
