"""Motore di generazione testo, con due provider intercambiabili.

  anthropic  Claude. Migliore, soprattutto nel ruolo critico: il fact-check
             ostile che smonta le curiosità false. Richiede credito a consumo.
  gemini     Google. Ha un free tier vero sui modelli di TESTO (a differenza
             di quelli immagine, che hanno quota zero). Qualità inferiore, ma
             permette di far girare tutta la pipeline a costo zero.

Il resto del progetto non sa quale dei due sta usando: entrambi passano da
`ask_json`, che restituisce sempre un oggetto conforme allo schema.
"""
from __future__ import annotations

import json
import time
import re
from typing import Any, Dict, List, Optional

import httpx

from .config import cfg, env, require_env

PROVIDER = cfg.get("pipeline.provider", "anthropic")
MODEL = cfg.get("pipeline.model", "claude-opus-5")

# Il modello per le sole chiamate che accendono la ricerca web. Vedi
# `_chiedi_con_ritenta`: la quota di grounding e' separata da quella di testo
# e su gemini-3.1-flash-lite e' a zero. Vale solo per Gemini; su Anthropic la
# ricerca non ha una quota sua e questo campo non viene letto.
MODEL_RICERCA = cfg.get("pipeline.model_ricerca", "gemini-2.5-flash")


# ─── Parsing comune ───────────────────────────────────────────────────────────

def parse_json(raw: str) -> Any:
    """Parsing tollerante: JSON puro, oppure il primo oggetto/array nel testo.

    Serve davvero: con la ricerca web attiva nessuno dei due provider può
    garantire output strutturato, quindi il JSON arriva dentro del testo.
    """
    raw = raw.strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    fenced = re.search(r"```(?:json)?\s*(.+?)```", raw, re.S)
    if fenced:
        try:
            return json.loads(fenced.group(1).strip())
        except json.JSONDecodeError:
            pass
    match = re.search(r"[\[{].*[\]}]", raw, re.S)
    if match:
        return json.loads(match.group(0))
    raise ValueError(f"Nessun JSON valido nella risposta:\n{raw[:500]}")


# ─── Anthropic ────────────────────────────────────────────────────────────────

_anthropic_client = None

WEB_SEARCH_TOOL = {"type": "web_search_20260209", "name": "web_search", "max_uses": 6}


def _client():
    global _anthropic_client
    if _anthropic_client is None:
        import anthropic

        require_env("ANTHROPIC_API_KEY")
        _anthropic_client = anthropic.Anthropic()
    return _anthropic_client


def _anthropic_ask_json(
    system: str,
    user: str,
    schema: Dict[str, Any],
    effort: str,
    max_tokens: int,
    use_web_search: bool,
    cache_system: bool,
) -> Any:
    system_blocks: List[Dict[str, Any]] = [{"type": "text", "text": system}]
    if cache_system:
        system_blocks[0]["cache_control"] = {"type": "ephemeral"}

    params: Dict[str, Any] = {
        "model": MODEL,
        "max_tokens": max_tokens,
        "system": system_blocks,
        "messages": [{"role": "user", "content": user}],
        "thinking": {"type": "adaptive"},
        "output_config": {
            "effort": effort,
            "format": {"type": "json_schema", "schema": schema},
        },
    }
    if use_web_search:
        params["tools"] = [WEB_SEARCH_TOOL]

    response = _client().messages.create(**params)

    # La ricerca web gira in un loop server-side con un tetto di iterazioni;
    # se lo tocca, torna stop_reason="pause_turn" e va ripresa.
    resumes = 0
    while response.stop_reason == "pause_turn" and resumes < 4:
        resumes += 1
        params["messages"] = [
            {"role": "user", "content": user},
            {"role": "assistant", "content": response.content},
        ]
        response = _client().messages.create(**params)

    if response.stop_reason == "refusal":
        raise RuntimeError(
            "Claude ha rifiutato la richiesta "
            f"({getattr(response.stop_details, 'category', 'n/d')})."
        )

    return parse_json("".join(b.text for b in response.content if b.type == "text"))


# ─── Gemini ───────────────────────────────────────────────────────────────────

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models"


def _gemini_schema(node: Any) -> Any:
    """Gemini accetta un sottoinsieme di JSON Schema: `additionalProperties`
    lo fa fallire con INVALID_ARGUMENT, quindi va rimosso ricorsivamente."""
    if isinstance(node, dict):
        return {
            k: _gemini_schema(v)
            for k, v in node.items()
            if k != "additionalProperties"
        }
    if isinstance(node, list):
        return [_gemini_schema(v) for v in node]
    return node


# Errori che passano da soli: il modello e' sovraccarico o c'e' stato un
# singhiozzo di rete. Prima non erano distinti da quelli veri, e un 503 di
# trenta secondi buttava giu' l'intero ciclo — un lotto da undici video morto
# sul primo perche' Google aveva un picco di richieste.
PASSEGGERI = {500, 502, 503, 504}

# Attese crescenti. La prima e' corta perche' la maggior parte dei 503 dura
# pochi secondi; le ultime sono lunghe perche' se dopo un minuto ancora non
# risponde, insistere ogni due secondi non aiuta e consuma soltanto.
ATTESE = [3, 8, 20, 45]


def _chiedi_con_ritenta(key: str, body: Dict[str, Any],
                        modello: str = "") -> "httpx.Response":
    """Interroga Gemini ritentando sugli errori passeggeri.

    Il 429 e' un caso a parte: puo' voler dire "troppe richieste al minuto"
    (passa da solo) oppure "quota giornaliera finita" (non passa fino a
    domani). Non essendo distinguibili dal codice, si ritenta una volta sola
    con un'attesa lunga: se era il primo caso si risolve, se era il secondo si
    sono persi trenta secondi invece di un ciclo intero.

    `modello` esiste perche' la quota NON e' una sola: e' per coppia
    (modello, funzionalita'). Misurato il 3 settembre 2026 con la chiave di
    Mattia, una richiesta identica con `google_search` acceso:

        gemini-3.1-flash-lite   429    <- il modello della pipeline
        gemini-3.5-flash        429
        gemini-2.5-flash        200

    Gli stessi modelli che danno 429 rispondono 200 alla stessa richiesta
    SENZA ricerca. Quindi non e' la quota di testo a essere finita, e non
    serve aspettare domani: serve un altro modello per le sole chiamate
    che cercano.
    """
    ultimo = None
    for tentativo, attesa in enumerate([*ATTESE, None]):
        try:
            with httpx.Client(timeout=300) as client:
                resp = client.post(
                    f"{GEMINI_URL}/{modello or MODEL}:generateContent",
                    headers={"x-goog-api-key": key, "Content-Type": "application/json"},
                    json=body,
                )
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            ultimo = f"rete: {exc}"
            resp = None
        else:
            if resp.status_code < 400:
                return resp
            if resp.status_code == 429 and tentativo >= 1:
                raise RuntimeError(
                    "Quota Gemini esaurita. Il free tier ha limiti giornalieri: "
                    "riprova più tardi, oppure passa a un modello più leggero "
                    "(pipeline.model: gemini-3.1-flash-lite)."
                )
            if resp.status_code not in PASSEGGERI and resp.status_code != 429:
                raise RuntimeError(f"Gemini {resp.status_code}: {resp.text[:300]}")
            ultimo = f"{resp.status_code}"

        if attesa is None:
            break
        # Il 429 aspetta piu' a lungo: se e' un limite al minuto, tre secondi
        # non bastano a farlo scadere.
        pausa = 35 if (resp is not None and resp.status_code == 429) else attesa
        print(f"    Gemini {ultimo}, riprovo fra {pausa}s "
              f"(tentativo {tentativo + 1}/{len(ATTESE)})")
        time.sleep(pausa)

    raise RuntimeError(
        f"Gemini non risponde dopo {len(ATTESE)} tentativi (ultimo: {ultimo}). "
        f"Se persiste, cambia pipeline.model in config.yaml: ogni modello ha "
        f"la sua quota indipendente."
    )



def _gemini_ask_json(
    system: str,
    user: str,
    schema: Dict[str, Any],
    max_tokens: int,
    use_web_search: bool,
) -> Any:
    key = env("GEMINI_API_KEY") or env("GOOGLE_API_KEY")
    if not key:
        raise RuntimeError(
            "GEMINI_API_KEY non impostata. Serve perché pipeline.provider "
            "è \"gemini\" in config.yaml."
        )

    body: Dict[str, Any] = {
        "contents": [{"role": "user", "parts": [{"text": user}]}],
        "systemInstruction": {"parts": [{"text": system}]},
        "generationConfig": {
            "maxOutputTokens": max_tokens,
            "temperature": 1.0,
            # Su Gemini il ragionamento consuma lo stesso budget dell'output:
            # senza un tetto, una risposta lunga viene troncata a metà JSON e
            # il parsing fallisce. Limitarlo lascia spazio alla risposta vera.
            "thinkingConfig": {"thinkingBudget": int(cfg.get("pipeline.thinking_budget", 2048))},
        },
    }

    if use_web_search:
        # Vincolo di Gemini: la ricerca e l'output strutturato si escludono a
        # vicenda. Con la ricerca attiva lo schema va chiesto nel prompt e il
        # JSON estratto dal testo — per questo parse_json è tollerante.
        body["tools"] = [{"google_search": {}}]
        body["contents"][0]["parts"][0]["text"] = (
            f"{user}\n\nReturn ONLY a JSON object matching this schema, "
            f"with no prose before or after:\n{json.dumps(schema)}"
        )
    else:
        body["generationConfig"]["responseMimeType"] = "application/json"
        body["generationConfig"]["responseSchema"] = _gemini_schema(schema)

    # Quante volte si rifà la domanda se il JSON torna rotto. Vale SOLO con la
    # ricerca accesa, e il motivo è nel vincolo di Gemini spiegato qui sopra:
    # ricerca e output strutturato si escludono, quindi in quel caso lo schema
    # è una richiesta scritta nel prompt e non una garanzia del formato. Su
    # una risposta corta si nota poco; su un copione da milleottocento parole
    # dentro una stringa JSON basta una virgoletta non protetta e l'oggetto
    # non si apre più.
    #
    # Misurato il 3 settembre 2026 sullo stesso capitolo: la prima
    # generazione è uscita rotta ("Expecting ',' delimiter", riga 4), la
    # seconda valida. Non è un difetto sistematico da correggere nel parser —
    # il punto di rottura cade ogni volta altrove — è rumore del modello, e
    # alla domanda rifatta risponde bene. Senza ricerca il formato lo impone
    # `responseSchema` e un ritentativo non servirebbe a niente.
    tentativi = 3 if use_web_search else 1
    ultimo_errore = None

    for giro in range(tentativi):
        resp = _chiedi_con_ritenta(
            key, body, MODEL_RICERCA if use_web_search else "")

        data = resp.json()
        candidates = data.get("candidates") or []
        if not candidates:
            raise RuntimeError(
                f"Gemini non ha restituito candidati: {str(data)[:300]}")

        reason = candidates[0].get("finishReason", "")
        parts = candidates[0].get("content", {}).get("parts", [])
        text = "".join(p.get("text", "") for p in parts)

        if reason == "MAX_TOKENS":
            raise RuntimeError(
                "Risposta troncata dal limite di token: il JSON risulta incompleto. "
                "Alza pipeline.thinking_budget al contrario — cioè abbassalo — "
                "oppure riduci la lunghezza del materiale di riferimento."
            )
        if not text.strip():
            raise RuntimeError(
                f"Gemini ha restituito testo vuoto (finishReason={reason})")

        try:
            return parse_json(text)
        except (ValueError, json.JSONDecodeError) as exc:
            ultimo_errore = exc
            if giro + 1 < tentativi:
                print(f"    JSON rotto ({exc}), rifaccio la domanda "
                      f"(tentativo {giro + 2}/{tentativi})")

    raise RuntimeError(
        f"Gemini ha restituito JSON non valido {tentativi} volte di fila "
        f"(ultimo: {ultimo_errore}). Con la ricerca accesa il formato non è "
        f"garantito: se capita spesso, conviene spegnerla per questa chiamata."
    )


# ─── Interfaccia unica ────────────────────────────────────────────────────────

def ask_json(
    system: str,
    user: str,
    schema: Dict[str, Any],
    *,
    effort: str = "high",
    max_tokens: int = 16000,
    use_web_search: bool = False,
    cache_system: bool = True,
) -> Any:
    """Una richiesta → un oggetto JSON conforme allo schema.

    `effort` e `cache_system` valgono solo per Anthropic; Gemini li ignora.
    """
    if PROVIDER == "gemini":
        return _gemini_ask_json(system, user, schema, max_tokens, use_web_search)
    if PROVIDER == "anthropic":
        return _anthropic_ask_json(
            system, user, schema, effort, max_tokens, use_web_search, cache_system
        )
    raise ValueError(
        f"pipeline.provider sconosciuto: {PROVIDER!r} (attesi: anthropic, gemini)"
    )
