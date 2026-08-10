"""Caption, hashtag e testo delle slide.

Separato da ideas.py perché è un problema diverso: lì serve verità, qui serve
ritmo. La caption è dove si vincono i commenti; le slide sono dove si vincono
i salvataggi.
"""
from __future__ import annotations

import sqlite3
from typing import Any, Dict, List

from .config import cfg
from .llm import ask_json

COPY_SCHEMA = {
    "type": "object",
    "properties": {
        "slides": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "kicker": {"type": "string"},
                    "headline": {"type": "string"},
                    "body": {"type": "string"},
                    "image_query": {"type": "string"},
                    "image_kind": {
                        "type": "string",
                        "enum": ["concept", "real_subject"],
                    },
                },
                "required": ["kicker", "headline", "body", "image_query", "image_kind"],
                "additionalProperties": False,
            },
        },
        "caption": {"type": "string"},
        "hashtags": {"type": "array", "items": {"type": "string"}},
        "alt_text": {"type": "string"},
    },
    "required": ["slides", "caption", "hashtags", "alt_text"],
    "additionalProperties": False,
}


def _system() -> str:
    c = cfg["caption"]
    return f"""You lay out social posts for {cfg.get('brand.name')} ({cfg.get('brand.handle')}).

VOICE
{cfg.get('voice.guide')}

You receive a verified fact and turn it into slides plus a caption.

NARRATIVE ARC — the five slides are one movement, not five statements.
Each slide must earn the swipe to the next by leaving something open.

  slide 1  THE CLAIM. This is the only slide most people will ever see: it is
           what appears in the feed, and everything else depends on it. Treat
           it with the same severity as a reel hook.

             Address the reader directly. "You" — not "we", not "people", not
             "the brain". A sentence about people in general is a lecture; a
             sentence about the reader is an accusation, and accusations get
             opened.

             Concrete over abstract. "Ease of memory shapes your sense of
             reality" is a textbook sentence nobody stops for. "You are more
             afraid of the thing you can picture" is the same fact, aimed.

             No hedging. "May", "can", "tends to", "often" belong in the body
             where precision matters. In the cover they drain it.

             Never a topic label. If the sentence could be the title of a
             chapter, rewrite it as something that happens to the reader.

           No explanation, no evidence, no softening on this slide.
  slide 2  THE PROOF. The study, with its most concrete detail — the number,
           the setup, the thing that was actually measured. This is where
           doubt gets answered.
  slide 3  THE MECHANISM. Why it happens. Not more evidence: the reason. If
           slide 2 said what, this says how.
  slide 4  THE TURN. The part that makes it personal — where this shows up
           in the reader's own week. This is the slide people screenshot.
  slide 5  THE CLOSE. One line that lands the implication.

           On Instagram the signal that matters most is the SAVE, not the
           like: it is what the algorithm reads as "worth showing to more
           people". People save what they expect to need again — a sentence
           they want to reread, or one they intend to quote at someone.
           So the closing line should be quotable on its own, out of context,
           without the four slides before it. If it only makes sense as a
           conclusion, it will not be saved.
           Then the follow line, on its own, small.

  Never repeat the hook on slide 2. Restating it is the most common way a
  carousel loses people: they read the same sentence twice and stop swiping.
  Each slide's headline must be a new sentence with new information.

EVERY SLIDE MUST EARN THE NEXT ONE

  A carousel where each slide is a complete, closed statement gets abandoned
  on slide two — not because the writing is bad, but because there is no
  reason to continue. The reader has been given something finished.

  So each slide except the last should leave one thing open: a number not yet
  given, a mechanism named but not explained, a consequence stated but not
  yet applied to the reader. The next slide pays that off and opens the next.

    closed:  "The brain confuses vividness with probability."
    open:    "The brain treats one vivid image as a statistic."
             (→ the reader now wants to know what that does to them)

  Do not do this with cliffhanger phrasing — no "but here's the thing…", no
  "and the reason will surprise you". Those are the tells of accounts that
  have nothing. Withhold information, not with suspense words.

SLIDE RULES — these are images, not paragraphs. Text that does not fit is text
that does not get read.
  kicker    0-3 words. A LABEL that adds information the headline does not:
            "1973", "THE STUDY", "IN PRACTICE", "THE CATCH", "WHAT IT COSTS".
            Never a number like "01" or "02" — the slide index is already
            printed on the right, so a numeric kicker wastes the one bit of
            space that could tell the reader where they are in the argument.
            Empty is better than redundant.
  headline  The line that carries the slide. Aim for under 72 characters —
            the layout has four size steps and handles long lines, but past
            that it shrinks to a size nobody reads on a phone.
            On slide 1 this is the hook — it must work with zero context.
  body      0-220 characters. May be empty on the first and last slide.
            One idea per slide. Never continue a sentence onto the next slide.

IMAGE QUERY — this is a search query sent to a stock photo library and to
Wikimedia Commons. It is the single easiest thing to get wrong.

  Search for a PHYSICAL SUBJECT that can be photographed, never for the
  abstract concept. A photo library has nothing filed under "memory",
  "attention" or "bias" except meaningless stock imagery of people pointing
  at glowing brains.

    concept            bad query          good query
    ─────────────────────────────────────────────────────────────────────
    memory             "memory"           "old photographs on a wooden table"
    sleep deprivation  "tiredness"        "empty office at night lit by a lamp"
    crowd behaviour    "social proof"     "crowded train platform rush hour"
    time perception    "time"             "long empty road at dusk"

  Write a SCENE, not a subject. 8-16 words. A generator given two words
  invents the other twenty itself, and invents them generically.

    thin:  "empty office"
    full:  "one desk lamp lit in a dark open-plan office, chairs pushed in"

    thin:  "person thinking"
    full:  "woman at a kitchen table at night, coffee gone cold beside her"

  Say what is in frame, where the light comes from, and what someone is
  doing.

  ⚠️ HARD CONSTRAINT — the generator cannot write. Every sign, menu, poster,
  label, book cover, screen or price tag it draws comes out as unreadable
  gibberish, and it is glaring at full size. Asking it not to write text does
  not work. The only thing that works is choosing scenes that contain none.

    NEVER set a scene in: a cafe, bar, restaurant, shop, supermarket, street
    with storefronts, office with monitors, library with visible spines,
    airport, station, classroom with a board, anywhere with packaging.

    SAFE and rich: a home kitchen at night, an unmade bed, hands holding a
    cup, a stairwell, a bathroom mirror, a car interior at dusk, a park bench,
    a hallway, rain on a window, a field, a person seen from behind, a close
    crop of a face, laundry on a line, an empty swimming pool.

  Include a person in at least two slides per post — faces stop scrolling
  more reliably than objects. Frame them from behind, in profile, or cropped
  close: a face looking straight at the lens reads as stock photography.

  No mood adjectives ("mysterious", "dramatic", "haunting"): the colour
  treatment is applied afterwards, and mood words only pull the generator
  toward stock-image clichés.
  For historical or scientific facts, name the actual thing: "Apollo 11
  command module", "honey bee on clover", "Egyptian burial chamber".
  Every slide must have a DIFFERENT query. Same visual world, different
  subject each time — a carousel where all five slides show the same picture
  is worse than one with no pictures at all. Think of it as five frames of
  one scene shot from different places: the room, then the desk, then the
  hands, then the window, then the door. Never repeat a query, and never
  reuse the same noun as the main subject twice.

IMAGE KIND — decides whether the picture is generated or photographed.

  real_subject  The query names a specific real thing, place, event, artefact
                or person that actually exists. A real photograph is required.
                This account publishes facts; an invented picture of a real
                subject is a fabricated document, however convincing it looks.
                Use this for: named experiments, historical events, specific
                species, artefacts, locations, instruments, people.

  concept       The query names an ordinary scene or object standing in for an
                idea — a desk at night, a crowded platform, an empty road.
                Nothing is being documented, so the image may be generated.

  When in doubt, choose real_subject. The cost of a missing image is a plain
  background; the cost of a fabricated one is the page's credibility.

The last slide is the close: it states the implication and invites a follow.
Do not put a hashtag or an @ inside slide text.

CAPTION SHAPE — return the caption with BLANK LINES between its parts, like
this, and never as one block:

    <first line — see below>

    <the fact, with its source, one or two sentences>

    <the closing question>

    {c['cta']}

  Instagram collapses a caption after roughly 125 characters and hides the
  rest behind "more". Most people never expand it. So:

  - The FIRST LINE is the only part guaranteed to be read. Make it a complete
    thought under 100 characters that works alone — not a run-up to a point
    made later. Do not repeat the hook word for word; say the same thing from
    a different angle.
  - A caption written as one paragraph loses the question at the bottom,
    which is where the comments come from. The blank lines are not cosmetic.
  - Under {c['max_chars']} characters overall, hashtags excluded.

THE CLOSING QUESTION — this is what decides whether anyone comments

A comment costs the reader effort, and they pay it only when answering is
easy AND the answer says something about them. Most accounts ask questions
that are neither, which is why their comments are empty.

  Ask something they already know the answer to. If a reader has to think,
  recall, or research, they scroll on. The answer should already be sitting
  in their head, waiting.

  Make the answer a small self-disclosure. People comment to be seen. A
  question whose answer reveals a habit, a preference or an embarrassment
  gets answered; a question about the abstract topic does not.

  Prefer a fork over an open field. "Which of the two are you?" gets far more
  replies than "what do you think about this?", because choosing is one
  second of work and typing an opinion is thirty.

    dead:  "What do you think about the spotlight effect?"
    dead:  "Have you ever experienced this?"           (yes/no, nothing to say)
    alive: "What's the thing you're convinced everyone noticed, that nobody
            did?"
    alive: "Which one are you — the one who replays it for days, or the one
            who forgets by dinner?"

  Never ask people to tag a friend, comment a word, or answer with an emoji.
  Those inflate the count with comments that carry no meaning, and the reach
  they buy does not convert into anyone who cares about the page.

HASHTAGS — the strategy most accounts get backwards
  Exactly {c['hashtag_count']}, lowercase, no "#" prefix, no spaces.

  A hashtag is only worth using if THIS POST could plausibly rank among its
  better entries. On a tag with millions of posts, a new account is buried in
  seconds — the tag is not exposure, it is just characters. On a tag with a
  few thousand, the same post can sit near the top for hours.

  So weight the set toward the narrow end:
    7-8  NARROW — the specific effect, study, or phenomenon this post is
         about, and its close neighbours. "illusorytrutheffect",
         "zeigarnikeffect", "focusingillusion", "spotlighteffect".
         These are the ones that actually bring strangers.
    3-4  MID — the sub-field. "cognitivebias", "socialpsychology",
         "behaviouraleconomics".
    1-2  BROAD — for topical relevance only, not for reach.

  Never use a tag whose audience expects something this account does not
  provide. "mentalhealthawareness", "mindfulness", "selfcare", "therapy",
  "motivation" attract people looking for support or advice; this account
  posts research about how minds work, and the mismatch costs you followers
  who leave and an algorithm that learns to show you to the wrong people.

ALT TEXT
  One sentence describing the first image. Instagram indexes it in search on
  top of using it for screen readers, so name what is literally visible —
  people, objects, setting — rather than describing the concept behind it.

"""


def write_copy(fact_row: sqlite3.Row, slide_count: int) -> Dict[str, Any]:
    pinned = cfg.get("caption.pinned_hashtags", []) or []
    user = f"""Turn this verified fact into a {slide_count}-slide post.

HOOK:   {fact_row['hook']}
FACT:   {fact_row['fact']}
DETAIL: {fact_row['detail']}
KICKER: {fact_row['kicker']}
SOURCE: {fact_row['source_hint']}

Return exactly {slide_count} slides. Include these hashtags among yours: {', '.join(pinned) or '(none)'}."""
    data = ask_json(_system(), user, COPY_SCHEMA, effort="medium")

    # Garanzie che il modello non può dare da solo.
    slides = data["slides"][:slide_count]
    while len(slides) < slide_count:
        slides.append(
            {"kicker": "", "headline": fact_row["kicker"][:60], "body": "", "image_query": ""}
        )
    def _taglia(testo: str, limite: int) -> str:
        """Accorcia senza spezzare le parole.

        Il taglio secco produceva headline mozzate in mezzo a una parola
        ("...endured a humiliati"), e quel troncamento finiva stampato sulle
        immagini pubblicate. Meglio perdere l'ultima parola intera.
        """
        testo = testo.strip()
        if len(testo) <= limite:
            return testo
        tagliato = testo[:limite].rsplit(" ", 1)[0].rstrip(" ,;:—-")
        return tagliato or testo[:limite]

    for s in slides:
        # 72 invece di 60: il renderer ha già quattro gradini di corpo per le
        # frasi lunghe, quindi il limite stretto tagliava frasi che il layout
        # avrebbe gestito benissimo.
        s["headline"] = _taglia(s["headline"], 72)
        s["body"] = _taglia(s["body"], 220)
        s["kicker"] = _taglia(s["kicker"], 22)
        # 180 e non 80: ora si chiede una scena descritta, non due parole.
        # Con il vecchio limite le descrizioni venivano tagliate a metà frase.
        s["image_query"] = s.get("image_query", "").strip()[:180]
        # In caso di valore inatteso si ricade sul comportamento prudente:
        # cercare una foto vera, mai generarla.
        s["image_kind"] = (
            "concept" if s.get("image_kind") == "concept" else "real_subject"
        )
    data["slides"] = slides

    tags: List[str] = []
    for t in list(pinned) + data["hashtags"]:
        t = t.lstrip("#").strip().lower().replace(" ", "")
        if t and t not in tags:
            tags.append(t)
    data["hashtags"] = tags[: int(cfg.get("caption.hashtag_count", 12))]
    return data


def full_caption(copy: Dict[str, Any], has_ai_images: bool = False) -> str:
    """Compone la caption finale.

    La riga di dichiarazione AI non è cortesia: l'Articolo 50 dell'AI Act
    europeo, in vigore dal 2 agosto 2026, impone che i contenuti sintetici
    siano dichiarati in modo percepibile senza strumenti tecnici e al primo
    contatto. Sulle slide il credito è già stampato; questa riga copre chi
    legge la caption senza guardare le immagini.
    """
    testo = copy["caption"].strip()

    # Rete di sicurezza: se il modello ha ignorato le righe vuote e ha
    # restituito un unico blocco, la domanda finale e la CTA finiscono
    # nascoste dietro il "altro" di Instagram. Si separa almeno la chiusura.
    if "\n" not in testo:
        cta = cfg.get("caption.cta", "")
        if cta and cta in testo:
            testo = testo.replace(cta, "\n\n" + cta).strip()
        # E la domanda, che è ciò che genera i commenti.
        pos = testo.rfind("? ")
        if pos > 0:
            testo = testo[: pos + 1] + "\n\n" + testo[pos + 2 :]
        pos = testo.find(". ")
        if 0 < pos < 160:
            testo = testo[: pos + 1] + "\n\n" + testo[pos + 2 :]

    parts = [testo]

    if has_ai_images:
        disclosure = cfg.get(
            "caption.ai_disclosure", "Images are AI-generated. The research is real."
        )
        if disclosure:
            parts.append(disclosure)

    parts.append(" ".join("#" + t for t in copy["hashtags"]))
    return "\n\n".join(parts)
