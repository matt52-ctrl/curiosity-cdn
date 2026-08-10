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
                    "hook": {"type": "string"},
                    "reveal": {"type": "string"},
                    "mood": {"type": "string", "enum": list(MOODS)},
                    "caption": {"type": "string"},
                    "hashtags": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["hook", "reveal", "mood", "caption", "hashtags"],
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

CAPTION
  Two or three sentences that give the line its evidence — the study, the
  year, what was measured. People who stop on a reel and want to know if it
  is real look here, and finding a real source is what converts a viewer into
  a follower. End with the CTA: "{cfg.get('caption.cta')}"

HASHTAGS
  Exactly 5, lowercase, no "#".

  In 2026 hashtags no longer drive discovery — Instagram uses them to file
  content by topic, not to distribute it. Thirty tags do nothing that five do
  not, and a wall of them reads as spam. So: five narrow, specific tags that
  describe the actual mechanism, not the field."""


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
        l["hook"] = l["hook"].strip()
        l["reveal"] = l["reveal"].strip().rstrip("?")
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
    return linee


def full_caption(line: Dict[str, Any]) -> str:
    tags = " ".join("#" + t for t in line["hashtags"])
    return f"{line['caption'].strip()}\n\n{tags}"
