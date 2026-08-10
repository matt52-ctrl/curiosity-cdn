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

import re
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


def _relevant_passages(text: str, claim: str, chars: int) -> str:
    """Sceglie i passaggi dell'articolo che possono davvero verificare la tesi.

    L'introduzione di Wikipedia riassume, non documenta: percentuali, anni,
    numeri di partecipanti stanno nel corpo. Prendere solo l'incipit faceva
    bocciare fatti veri per mancanza di una frase citabile — il verificatore
    non trovava prove semplicemente perché non gliele davamo.
    """
    import re as _re

    claim_words = {
        w for w in _re.findall(r"[a-z]{5,}", claim.lower())
    }
    numbers = set(_re.findall(r"\d+", claim))

    paragraphs = [p.strip() for p in text.split("\n") if len(p.strip()) > 80]
    scored = []
    for p in paragraphs:
        low = p.lower()
        score = sum(1 for w in claim_words if w in low)
        # Un paragrafo che contiene gli stessi numeri della tesi vale molto:
        # è lì che si conferma o si smonta una cifra.
        score += 4 * sum(1 for n in numbers if n in p)
        if _re.search(r"\b(19|20)\d{2}\b", p):
            score += 1          # contiene un anno: probabile riferimento a uno studio
        if score:
            scored.append((score, p))

    scored.sort(key=lambda x: -x[0])
    out, used = [], 0
    for _, p in scored:
        if used + len(p) > chars:
            continue
        out.append(p)
        used += len(p)
        if used > chars * 0.9:
            break
    return " … ".join(out)


def _extracts(client: httpx.Client, titles: List[str], chars: int, claim: str = "") -> List[str]:
    if not titles:
        return []
    resp = client.get(
        WIKI_API,
        params={
            "action": "query",
            "format": "json",
            "prop": "extracts",
            # Niente exintro: serve il corpo dell'articolo, non il riassunto.
            "explaintext": 1,
            "exlimit": 1 if len(titles) == 1 else "max",
            "titles": "|".join(titles),
        },
        headers={"User-Agent": _user_agent()},
    )
    resp.raise_for_status()
    pages = resp.json().get("query", {}).get("pages", {})

    out: List[str] = []
    for page in pages.values():
        full = (page.get("extract") or "").strip()
        if not full:
            continue
        body = _relevant_passages(full, claim, chars) if claim else full[:chars]
        if not body:
            body = full[:chars].replace("\n", " ")
        out.append(f"— {page['title']}: {body}")
    return out


PMC_API = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"


def _europepmc(query: str, limit: int = 3, chars: int = 1200) -> List[str]:
    """Abstract di articoli scientifici. Gratuito, nessuna chiave.

    Serve dove Wikipedia non arriva: le percentuali, le dimensioni del
    campione e i risultati esatti degli studi stanno negli abstract, non
    nelle voci enciclopediche, che riassumono senza riportare i numeri.
    Copertura forte su biomedicina e scienze della vita, più irregolare sulla
    psicologia sociale — per questo resta affiancata a Wikipedia, non
    sostitutiva.
    """
    try:
        with httpx.Client(timeout=45) as client:
            resp = client.get(
                PMC_API,
                params={
                    "query": query,
                    "format": "json",
                    "pageSize": limit,
                    "resultType": "core",
                },
                headers={"User-Agent": _user_agent()},
            )
            resp.raise_for_status()
            results = resp.json().get("resultList", {}).get("result", [])
    except Exception as exc:
        print(f"    Europe PMC non raggiungibile: {exc}")
        return []

    out: List[str] = []
    for r in results:
        abstract = (r.get("abstractText") or "").strip()
        if len(abstract) < 120:
            continue
        # Gli abstract arrivano con marcatori HTML dei paragrafi strutturati.
        abstract = re.sub(r"<[^>]+>", " ", abstract)
        abstract = re.sub(r"\s+", " ", abstract)
        titolo = (r.get("title") or "").strip()
        anno = r.get("pubYear", "")
        out.append(f"— [{anno}] {titolo}: {abstract[:chars]}")
    return out


def gather(*queries: str, limit: int = 3, chars: int = 1600, claim: str = "") -> str:
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
                blocks.extend(_extracts(client, titles, chars, claim or queries[0]))
    except Exception as exc:
        print(f"    ricerca Wikipedia fallita: {exc}")

    # Europe PMC è stato provato come seconda fonte e poi rimosso: su
    # psicologia sociale restituisce articoli tangenziali che diluiscono il
    # materiale buono, e in prova ha fatto peggiorare verdetti che prima erano
    # corretti. La funzione resta disponibile per nicchie biomediche, dove la
    # sua copertura è invece ottima.
    return "\n\n".join(blocks[:5])
