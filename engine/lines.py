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
                    "line": {"type": "string"},
                    "mood": {"type": "string", "enum": list(MOODS)},
                    "caption": {"type": "string"},
                    "hashtags": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["line", "mood", "caption", "hashtags"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["lines"],
    "additionalProperties": False,
}


def _system() -> str:
    return f"""You write single lines for {cfg.get('brand.name')} ({cfg.get('brand.handle')}),
an account about how the human mind actually works.

VOICE
{cfg.get('voice.guide')}

Each line becomes a six-second video: the sentence sits alone over footage,
with music. There is no second slide, no source shown, no explanation. The
line is the entire post.

WHAT MAKES A LINE WORK ALONE

  It must land without context. The reader arrives mid-scroll knowing nothing
  and leaves four seconds later. If understanding it requires the study behind
  it, it belongs in a carousel, not here.

  It must be about the reader. "You" beats "people" every time — the whole
  value is the small shock of being described.

  It must be true. This is the hard part: a line stripped of its evidence
  drifts easily into a motivational aphorism, and that is the one thing this
  account is not. Say only what the underlying research supports. If making
  the line punchy requires overstating it, use a different fact.

  8-16 words. Under 8 it reads as a slogan, over 16 nobody finishes it.

  No question marks — a question invites scrolling past. No commands, no
  advice, no "remember that...". State the thing.

  Never open with "The fact that", "Studies show", "Science says", or
  "Your brain is". Those are the four openings every account uses.

MOOD — decides the music and the footage, so it must be honest

  reflective  quiet, thoughtful, a little melancholy. The default.
  unsettling  the fact is uncomfortable: self-deception, bias, being wrong
              about yourself without knowing.
  warm        connection, being liked, being seen, forgiveness.
  bright      genuinely surprising or lightly funny.

  Choosing the wrong mood ruins the video more than a weak line would:
  cheerful music under a bleak sentence reads as a mistake.

CAPTION
  Two or three sentences that give the line its evidence — the study, the
  year, what was measured. People who stop on a reel and want to know if it
  is real look here, and finding a real source is what converts a viewer into
  a follower. End with the CTA: "{cfg.get('caption.cta')}"

HASHTAGS
  Exactly 8, lowercase, no "#". Mostly narrow and specific to the mechanism
  in the line. Broad tags like "psychology" bury a small account instantly."""


def generate(conn: sqlite3.Connection, count: int) -> List[Dict[str, Any]]:
    """Ricava frasi dai fatti verificati già in magazzino."""
    fatti = conn.execute(
        """SELECT hook, fact, detail, source_hint FROM facts
           WHERE status IN ('approved','rendered','published')
           ORDER BY RANDOM() LIMIT ?""",
        (count * 2,),
    ).fetchall()

    if not fatti:
        return []

    materiale = "\n\n".join(
        f"FACT: {f['fact']}\nDETAIL: {f['detail']}\nSOURCE: {f['source_hint']}"
        for f in fatti
    )
    user = f"""Turn these verified findings into {count} standalone lines.

Use a different finding for each line. Pick the ones that survive being
stripped to a single sentence — some facts need their evidence to make sense,
and those are not suitable here.

{materiale}

Return JSON matching the schema."""

    data = ask_json(_system(), user, LINES_SCHEMA, effort="medium", max_tokens=8000)
    linee = data.get("lines", [])[:count]

    pinned = cfg.get("caption.pinned_hashtags", []) or []
    for l in linee:
        l["line"] = l["line"].strip().rstrip("?")
        if l.get("mood") not in MOODS:
            l["mood"] = "reflective"
        tag: List[str] = []
        for t in list(pinned) + l.get("hashtags", []):
            t = t.lstrip("#").strip().lower().replace(" ", "")
            if t and t not in tag:
                tag.append(t)
        l["hashtags"] = tag[:8]
    return linee


def full_caption(line: Dict[str, Any]) -> str:
    tags = " ".join("#" + t for t in line["hashtags"])
    return f"{line['caption'].strip()}\n\n{tags}"
