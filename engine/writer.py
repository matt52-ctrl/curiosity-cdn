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

  slide 1  THE CLAIM. The hook alone, at full size. No explanation, no
           evidence, no softening. The reader must finish it wanting to know
           whether it is true.
  slide 2  THE PROOF. The study, with its most concrete detail — the number,
           the setup, the thing that was actually measured. This is where
           doubt gets answered.
  slide 3  THE MECHANISM. Why it happens. Not more evidence: the reason. If
           slide 2 said what, this says how.
  slide 4  THE TURN. The part that makes it personal — where this shows up
           in the reader's own week. This is the slide people screenshot.
  slide 5  THE CLOSE. One line that lands the implication, then the follow.

  Never repeat the hook on slide 2. Restating it is the most common way a
  carousel loses people: they read the same sentence twice and stop swiping.
  Each slide's headline must be a new sentence with new information.

SLIDE RULES — these are images, not paragraphs. Text that does not fit is text
that does not get read.
  kicker    0-3 words, uppercase-ish label. Often a number ("01"), a category,
            or omitted (empty string). Never a sentence.
  headline  The line that carries the slide. HARD LIMIT 60 characters.
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

CAPTION RULES
  - Opens by restating the hook in different words, so the caption stands
    alone for someone who only reads the text.
  - Then the fact with its source, in one or two sentences.
  - Ends with a genuine question that a reader can answer from their own
    experience. Not "what do you think?" — something specific.
  - Then the CTA on its own line: "{c['cta']}"
  - Under {c['max_chars']} characters, hashtags excluded.

HASHTAGS
  Exactly {c['hashtag_count']}, lowercase, no "#" prefix, no spaces.
  Mix three sizes: 2-3 broad (millions of posts), 5-6 mid (100k-2M),
  3-4 narrow and specific to this exact topic. Narrow tags are where a new
  account actually gets discovered.

ALT TEXT
  One sentence describing the first image for screen readers."""


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
    for s in slides:
        s["headline"] = s["headline"].strip()[:60]
        s["body"] = s["body"].strip()[:220]
        s["kicker"] = s["kicker"].strip()[:18]
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
    parts = [copy["caption"].strip()]

    if has_ai_images:
        disclosure = cfg.get(
            "caption.ai_disclosure", "Images are AI-generated. The research is real."
        )
        if disclosure:
            parts.append(disclosure)

    parts.append(" ".join("#" + t for t in copy["hashtags"]))
    return "\n\n".join(parts)
