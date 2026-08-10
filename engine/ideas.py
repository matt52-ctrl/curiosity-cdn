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
        "verdict": {"type": "string", "enum": ["supported", "overstated", "false", "unclear"]},
        "confidence": {"type": "number"},
        "note": {"type": "string"},
        "corrected_fact": {"type": "string"},
        "corrected_detail": {"type": "string"},
    },
    "required": ["verdict", "confidence", "note", "corrected_fact", "corrected_detail"],
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
  hook        A single line, 5-11 words, that makes someone stop scrolling.
              A statement or a question. No command, no clickbait formula,
              no "did you know". It must be specific enough that a reader
              could not have guessed the rest.
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

confidence is 0.0-1.0 and means: how sure are you that a well-informed reader
in this field would let this pass without objection.

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
        evidence = research.gather(
            idea["fact"],
            " ".join(idea.get("keywords", [])[:4]),
            idea.get("source_hint", ""),
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
            set_verification(conn, fact_id, "unclear", 0.0, f"errore verifica: {exc}")
            stats["rejected"] += 1
            print(f"  ✗ [{fact_id}] verifica fallita: {exc}")
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
