"""Generazione idee + verifica + deduplica.

Tre stadi, deliberatamente separati:

  1. generate()  — Claude produce N curiosità nella voce della pagina
  2. dedupe()    — scarta ciò che assomiglia a un post già fatto
  3. verify()    — un secondo passaggio, con ricerca web reale, che *attacca*
                   ogni fatto e assegna una confidenza

Lo stadio 3 è quello che tiene in piedi la pagina. Le curiosità generate da un
LLM senza verifica sono vere circa l'80% delle volte, e il 20% restante è
esattamente il contenuto che fa esplodere i commenti e uccide la credibilità.
"""
from __future__ import annotations

import sqlite3
from typing import Any, Dict, List

from . import research
from .config import cfg
from .db import all_published_texts, insert_fact, set_verification
from .llm import ask_json

# ─── Schemi ───────────────────────────────────────────────────────────────────

IDEAS_SCHEMA = {
    "type": "object",
    "properties": {
        "ideas": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "hook": {"type": "string"},
                    "fact": {"type": "string"},
                    "detail": {"type": "string"},
                    "kicker": {"type": "string"},
                    "source_hint": {"type": "string"},
                    "keywords": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["hook", "fact", "detail", "kicker", "source_hint", "keywords"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["ideas"],
    "additionalProperties": False,
}

VERIFY_SCHEMA = {
    "type": "object",
    "properties": {
        # Questi due campi vengono prima del verdetto di proposito: obbligano a
        # cercare la prova e a nominare la debolezza PRIMA di poter giudicare.
        # Chiedere solo il verdetto produce un sì quasi sempre.
        "evidence_quote": {"type": "string"},
        "strongest_objection": {"type": "string"},
        "verdict": {"type": "string", "enum": ["supported", "overstated", "false", "unclear"]},
        "confidence": {"type": "number"},
        "note": {"type": "string"},
        "corrected_fact": {"type": "string"},
        "corrected_detail": {"type": "string"},
    },
    "required": [
        "evidence_quote",
        "strongest_objection",
        "verdict",
        "confidence",
        "note",
        "corrected_fact",
        "corrected_detail",
    ],
    "additionalProperties": False,
}


# ─── System prompt (stabile → viene messo in cache) ───────────────────────────

def _system_prompt() -> str:
    n = cfg["niche"]
    v = cfg["voice"]
    avoid = "\n".join("      - " + a for a in n.get("avoid", []))
    return f"""You write for {cfg.get('brand.name')}, a social account that posts one
carefully-checked curiosity at a time.

NICHE — {n['label']}
{n['brief']}

TOPICS TO AVOID
{avoid}

VOICE
{v['guide']}

STRUCTURE OF ONE IDEA
  hook        A single line, 5-12 words. This is the whole post: it is what
              appears in the feed, and nothing else gets read if it fails.

              A hook works when it creates a gap the reader needs closed.
              Four ways to open that gap, in rough order of strength:

              1. CONTRADICT what the reader believes about themselves.
                 weak:   "People often misjudge how others see them."
                 strong: "You are the worst judge of how you come across."

              2. Lead with a NUMBER that shouldn't be that number.
                 weak:   "Few people notice when you make a mistake."
                 strong: "Only 23% of people noticed the embarrassing shirt."

              3. Name a CONSEQUENCE the reader has felt but never explained.
                 weak:   "Interrupted tasks are remembered better."
                 strong: "The task you abandoned is still using your memory."

              4. State the mechanism as an UNSETTLING FACT, not a finding.
                 weak:   "Memory reconsolidation alters stored memories."
                 strong: "Every time you recall it, you change it slightly."

              Rules that hold regardless:
                - Address the reader as "you" when the fact is about them.
                  "People" is a way of making a fact happen to someone else.
                - No hedging in the hook. "May", "can", "often", "tend to"
                  belong in the body, where precision matters; in the hook
                  they only drain it. The claim must still be true — pick a
                  fact that survives being stated plainly.
                - Never open with "Did you know", "Studies show", "Turns out",
                  "Here's why", or a number in the format "5 things".
                - If a reader could guess the rest of the post from the hook,
                  the hook is too generic. If they could have written it
                  themselves, it is not a fact worth posting.
  fact        One sentence. The claim itself, stated precisely, with the
              number or the specific finding in it.
  detail      2-4 sentences. How it works or how it was found. This is where
              the reader gets the actual explanation, not more hype.
  kicker      One sentence that lands the implication for the reader's own
              life. Not a moral, not advice. An observation.
  source_hint A concrete pointer someone could verify: researcher name, year,
              journal, or the name of the effect. Never invent one — if you
              are not confident it exists, say "uncertain".
  keywords    3-6 lowercase topic tags for internal tracking.

RULES
  - Every claim must be one you actually believe is true and checkable.
    A second pass will search the web and challenge you; overstated claims
    get rejected and waste the batch.
  - Prefer findings that have replicated over single striking studies.
  - Never fabricate a citation, a number, or a researcher's name. If you are
    unsure of a number, write the claim without it.
  - Each idea must be independent — no two ideas in one batch about the
    same effect."""


# ─── Stadio 1: generazione ────────────────────────────────────────────────────

def generate(
    count: int, avoid_recent: List[str], learnings: str = ""
) -> List[Dict[str, Any]]:
    recent = "\n".join("  - " + t for t in avoid_recent[-60:])
    # I "learnings" vanno nel messaggio utente, non nel system prompt: il
    # system prompt è in cache e cambiarlo la invaliderebbe a ogni batch.
    feedback = f"\n\n{learnings}\n" if learnings else ""
    user = f"""Produce {count} ideas.

Already published — do not repeat these or restate them differently:
{recent or "  (nothing yet — this is the first batch)"}
{feedback}
Return JSON matching the schema."""
    data = ask_json(_system_prompt(), user, IDEAS_SCHEMA, effort="high")
    return data["ideas"]


# ─── Stadio 2: deduplica ──────────────────────────────────────────────────────

_STOP = {
    "the", "a", "an", "of", "to", "in", "is", "are", "was", "were", "that", "and",
    "or", "for", "on", "at", "by", "it", "its", "as", "with", "from", "than",
    "this", "these", "those", "but", "not", "you", "your", "their", "they",
    "can", "will", "more", "most", "when", "how", "what", "why", "be", "been",
    "has", "have", "had", "do", "does", "did", "about", "into", "after", "over",
}


def _stem(word: str) -> str:
    """Riduzione morfologica grezza, sufficiente a far collassare le coppie che
    rompono il confronto lessicale: memories/memory, recall/recalling,
    sleeps/sleeping. Non è Porter e non deve esserlo — serve solo a non
    trattare due flessioni della stessa parola come termini diversi."""
    if len(word) <= 4:
        return word
    for suffix in ("ingly", "edly", "ies", "ing", "ies", "ed", "es", "ly", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            root = word[: -len(suffix)]
            if suffix == "ies":
                return root + "i"      # memories → memori, memory → memori
            return root
    if word.endswith("y"):
        return word[:-1] + "i"          # memory → memori
    return word


def _tokens(text: str) -> set:
    words = "".join(c.lower() if c.isalnum() or c.isspace() else " " for c in text).split()
    # I numeri restano: "16 ore" è spesso l'elemento che identifica il fatto.
    return {
        _stem(w) for w in words if w not in _STOP and (len(w) > 2 or w.isdigit())
    }


def similarity(a: str, b: str) -> float:
    """Similarità fra due formulazioni dello stesso fatto.

    Jaccard da solo non basta: una parafrasi più verbosa dello stesso fatto
    ("cats sleep 16 hours a day" → "domestic cats spend about 16 hours per day
    asleep") gonfia l'unione e scende sotto soglia, cioè esattamente il caso
    che la deduplica deve intercettare.

    Si prende il massimo fra Jaccard e coefficiente di sovrapposizione
    (intersezione / cardinalità del testo più corto), che è insensibile alla
    verbosità: se quasi tutti i termini portanti del più corto compaiono nel
    più lungo, è lo stesso fatto raccontato in modo diverso.

    Limite noto: questo è un confronto lessicale. Due formulazioni che non
    condividono nessuna radice ("l'acqua calda ghiaccia prima" / "effetto
    Mpemba") non vengono intercettate. La difesa principale contro i doppioni
    resta il corpus passato al generatore nel prompt; questo è la rete sotto.
    """
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    inter = len(ta & tb)
    jaccard = inter / len(ta | tb)
    # Il coefficiente di sovrapposizione si applica solo se il testo più corto
    # ha abbastanza sostanza: su 2-3 token andrebbe a 1.0 per caso.
    shorter = min(len(ta), len(tb))
    overlap = inter / shorter if shorter >= 4 else 0.0
    return max(jaccard, overlap)


def dedupe(ideas: List[Dict[str, Any]], corpus: List[str]) -> List[Dict[str, Any]]:
    threshold = float(cfg.get("pipeline.max_similarity", 0.55))
    kept: List[Dict[str, Any]] = []
    seen = list(corpus)
    for idea in ideas:
        text = idea["hook"] + " " + idea["fact"]
        if any(similarity(text, prev) >= threshold for prev in seen):
            continue
        kept.append(idea)
        seen.append(text)
    return kept


# ─── Stadio 3: verifica ───────────────────────────────────────────────────────

VERIFY_SYSTEM = """You are a fact-checker. You are adversarial by design: your job is
to find the reason a claim is wrong, overstated, or misattributed — not to
confirm it.

Use whatever evidence you are given, or search if you can. Look specifically for:
  - whether the finding replicated or failed to replicate
  - whether the effect size is far smaller than the claim implies
  - whether the number has drifted in retelling (a very common failure)
  - whether the claim is a well-known misquotation of a real study
  - whether the named source actually exists and says this

Verdicts:
  supported  — the claim is accurate as written and the source checks out
  overstated — directionally real but the wording claims more than the
               evidence supports; provide a corrected wording
  false      — the claim is wrong, or the source does not exist
  unclear    — you could not find enough to judge either way

FILL THESE TWO FIRST, BEFORE DECIDING ANYTHING

  evidence_quote
    Copy the exact sentence from the reference material that supports the
    claim. Not a paraphrase — the words as they appear. If the material does
    not contain such a sentence, write exactly: NONE.
    A claim with no quotable support cannot be "supported", however plausible
    it sounds and however confident you feel about it.

  strongest_objection
    The single best argument that this claim is wrong, overstated or
    misattributed. You must produce one for every claim, including ones you
    believe are true. If the only objection you can find is weak, say so —
    but never write "no objection". There is always a way a finding can be
    less solid than it reads: sample size, one lab, no replication, a number
    that drifted, an effect real but tiny, a population it does not generalise
    beyond.

CALIBRATING confidence — the scale must discriminate, or it is decoration.
Most claims are NOT 0.95. Use the whole range:

  0.95-1.0  the quote directly states the claim, including its numbers, and
            the finding is textbook-level established. Rare.
  0.80-0.94 the quote supports the substance, but a detail is unverified, or
            it is a single well-known study without replication evidence.
  0.60-0.79 the direction is right, the specifics are not confirmed here.
  0.30-0.59 plausible, no supporting quote found.
  0.0-0.29  contradicted, or the named source does not appear to exist.

When evidence_quote is NONE, the ceiling depends on what kind of claim it is:

  - A claim that merely RESTATES a well-established effect, with no specific
    number attached, may still reach 0.85. Encyclopaedic sources describe
    these effects without quoting figures, so absence of a quote is expected
    and is not evidence against.
  - A claim carrying a SPECIFIC FIGURE — a percentage, a sample size, an
    effect size — must not exceed 0.7 without a quote containing that figure.
    Numbers are exactly what drifts in retelling, and an unverifiable number
    is the most common way a true-sounding fact turns out to be wrong.
  - A claim naming a specific researcher, year or journal that you cannot
    confirm must not exceed 0.5. A fabricated citation is worse than no
    citation.

Familiarity is not evidence. The claims that circulate most are the ones
everyone has heard, which is also how false ones survive.

Set corrected_fact / corrected_detail ONLY when the verdict is "overstated".
For every other verdict leave both as empty strings — repeating the original
text back wastes the response budget and risks truncating the JSON.
Keep `note` under 220 characters."""


def verify(idea: Dict[str, Any]) -> Dict[str, Any]:
    mode = cfg.get("pipeline.web_search", "native")

    evidence = ""
    if mode == "wikipedia":
        # Il modello non può cercare da solo: gli si porta il materiale.
        # Senza questo il fact-check sarebbe una seconda opinione dello stesso
        # modello che ha inventato il fatto, cioè nessuna verifica.
        # L'ordine conta: la ricerca di Wikipedia è letterale, e interrogarla
        # con una frase intera restituisce articoli scelti su parole di
        # contorno ("Yves Saint Laurent" per una tesi sulle magliette).
        # Prima le fonti dichiarate e le parole chiave, che colpiscono
        # l'articolo giusto; la frase solo come ripiego, ridotta ai termini
        # portanti.
        # Parole intere, NON `_tokens`: quello applica lo stemming per la
        # deduplica e restituirebbe termini mutilati ("memori", "interrupt")
        # che su Wikipedia non trovano nulla.
        import re as _re

        parole = [
            w
            for w in _re.findall(r"[A-Za-z]{4,}", idea["fact"])
            if w.lower() not in _STOP
        ][:6]
        evidence = research.gather(
            idea.get("source_hint", ""),
            " ".join(idea.get("keywords", [])[:4]),
            " ".join(parole),
            claim=idea["fact"],
        )

    if evidence:
        context = (
            f"\n\nREFERENCE MATERIAL (Wikipedia extracts — not a primary source, "
            f"but enough to catch misattributions and drifted numbers):\n{evidence}\n"
        )
    elif mode == "wikipedia":
        context = (
            "\n\nNo reference material could be retrieved for this claim. "
            "Judge on your own knowledge, and cap confidence at 0.7: absence "
            "of evidence is not verification.\n"
        )
    else:
        context = ""

    user = f"""Fact-check this claim before it is published to a large audience.

CLAIM: {idea['fact']}
SUPPORTING TEXT: {idea['detail']}
CLAIMED SOURCE: {idea.get('source_hint', '(none given)')}
{context}
Return JSON matching the schema."""

    return ask_json(
        VERIFY_SYSTEM,
        user,
        VERIFY_SCHEMA,
        effort="high",
        use_web_search=(mode == "native"),
        cache_system=True,
    )


# ─── Orchestrazione ───────────────────────────────────────────────────────────

def run_batch(
    conn: sqlite3.Connection, count: int | None = None, learnings: str = ""
) -> Dict[str, int]:
    count = count or int(cfg.get("pipeline.ideas_per_batch", 12))
    min_conf = float(cfg.get("pipeline.min_confidence", 0.85))
    niche = cfg.get("niche.slug", "general")

    corpus = all_published_texts(conn)
    print(f"→ genero {count} idee (corpus esistente: {len(corpus)} fatti)")
    ideas = generate(count, corpus, learnings)

    ideas = dedupe(ideas, corpus)
    print(f"→ {len(ideas)} idee dopo deduplica")

    stats = {"generated": count, "kept": len(ideas), "approved": 0, "rejected": 0}

    for idea in ideas:
        fact_id = insert_fact(conn, niche, idea)
        try:
            v = verify(idea)
        except Exception as exc:  # rete, rifiuto, parsing
            # Un guasto tecnico non e' un verdetto. Prima queste finivano
            # marcate "unclear" e quindi scartate per sempre, pur non essendo
            # mai state valutate: sei curiosita' ottime sono andate perse cosi'
            # per una quota Gemini esaurita. Restano in 'new' e il giro dopo
            # ci riprova.
            conn.execute("UPDATE facts SET status='new', verify_note=? WHERE id=?",
                         (f"verifica rimandata: {str(exc)[:160]}", fact_id))
            conn.commit()
            stats["rimandati"] = stats.get("rimandati", 0) + 1
            print(f"  · [{fact_id}] verifica rimandata al prossimo giro: {str(exc)[:70]}")
            continue

        verdict = v["verdict"]
        confidence = float(v["confidence"])

        corrected = (v.get("corrected_fact") or "").strip()

        # La correzione va validata prima di sovrascrivere l'originale: capita
        # che il modello restituisca un frammento ("highly competent candidate
        # for a student trivia team") invece di una frase completa, e senza
        # questo controllo il fatto pubblicato diventa illeggibile.
        if corrected and (
            len(corrected) < 40
            or len(corrected) < len(idea["fact"]) * 0.5
            or len(corrected.split()) < 8
        ):
            print(f"  ⚠ correzione scartata (frammento): {corrected[:60]!r}")
            corrected = ""

        if verdict == "overstated" and confidence >= min_conf and corrected:
            # Recuperabile: usiamo la versione corretta invece di buttare
            # l'idea. Se il modello non l'ha fornita non si sovrascrive nulla,
            # o si cancellerebbe il fatto con una stringa vuota.
            conn.execute(
                "UPDATE facts SET fact=?, detail=COALESCE(NULLIF(?,''), detail) WHERE id=?",
                (corrected, (v.get("corrected_detail") or "").strip(), fact_id),
            )
            conn.commit()
            verdict = "supported"

        ok = verdict == "supported" and confidence >= min_conf
        set_verification(
            conn, fact_id, "supported" if ok else verdict, confidence, v["note"]
        )
        if ok:
            stats["approved"] += 1
            print(f"  ✓ [{fact_id}] {confidence:.2f} — {idea['hook']}")
        else:
            stats["rejected"] += 1
            print(f"  ✗ [{fact_id}] {verdict} {confidence:.2f} — {v['note'][:90]}")

    return stats
