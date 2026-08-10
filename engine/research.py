"""Materiale di verifica gratuito, per quando il modello non può cercare da solo.

La ricerca web nativa costa: su Anthropic è inclusa nel tool server-side, su
Google il grounding con `google_search` richiede la fatturazione attiva (free
tier zero, verificato l'8 agosto 2026).

Senza una fonte esterna il "fact-check" diventa una seconda opinione dello
stesso modello che ha inventato il fatto — cioè niente. Wikipedia risolve il
problema a costo zero e senza chiavi, ed è particolarmente forte proprio dove
le curiosità false circolano: psicologia, storia, scienza divulgativa.

Limite da tenere presente: Wikipedia non è una fonte primaria e non copre
tutto. Un fatto assente da Wikipedia non è necessariamente falso — per questo
la verifica distingue "false" da "unclear".
"""
from __future__ import annotations

from typing import List

import httpx

from .config import cfg

WIKI_API = "https://en.wikipedia.org/w/api.php"


def _user_agent() -> str:
    contact = cfg.get("brand.contact", "") or "https://github.com/"
    return f"CuriosityEngine/1.0 ({contact})"


def _search_titles(client: httpx.Client, query: str, limit: int) -> List[str]:
    resp = client.get(
        WIKI_API,
        params={
            "action": "query",
            "format": "json",
            "list": "search",
            "srsearch": query,
            "srlimit": limit,
        },
        headers={"User-Agent": _user_agent()},
    )
    resp.raise_for_status()
    hits = resp.json().get("query", {}).get("search", [])
    return [h["title"] for h in hits]


def _extracts(client: httpx.Client, titles: List[str], chars: int) -> List[str]:
    if not titles:
        return []
    resp = client.get(
        WIKI_API,
        params={
            "action": "query",
            "format": "json",
            "prop": "extracts",
            "exintro": 1,
            "explaintext": 1,
            "titles": "|".join(titles),
        },
        headers={"User-Agent": _user_agent()},
    )
    resp.raise_for_status()
    pages = resp.json().get("query", {}).get("pages", {})

    out: List[str] = []
    for page in pages.values():
        extract = (page.get("extract") or "").strip().replace("\n", " ")
        if extract:
            out.append(f"— {page['title']}: {extract[:chars]}")
    return out


def gather(*queries: str, limit: int = 3, chars: int = 900) -> str:
    """Raccoglie estratti di Wikipedia per le query date.

    Restituisce testo pronto da inserire nel prompt, o stringa vuota se non
    trova nulla — nel qual caso la verifica lo dichiara esplicitamente invece
    di far finta di aver controllato.
    """
    seen: List[str] = []
    blocks: List[str] = []

    try:
        with httpx.Client(timeout=45, follow_redirects=True) as client:
            for query in queries:
                query = (query or "").strip()
                if not query:
                    continue
                titles = [t for t in _search_titles(client, query, limit) if t not in seen]
                seen.extend(titles)
                blocks.extend(_extracts(client, titles, chars))
    except Exception as exc:
        print(f"    ricerca Wikipedia fallita: {exc}")
        return ""

    return "\n\n".join(blocks[:6])
