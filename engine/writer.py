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

  2-5 words. Concrete nouns only. No adjectives about mood ("mysterious",
  "dramatic") — the visual treatment is applied afterwards and mood words
  only make the match worse.
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
        s["image_query"] = s.get("image_query", "").strip()[:80]
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
