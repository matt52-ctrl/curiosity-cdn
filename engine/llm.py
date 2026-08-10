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
import re
from typing import Any, Dict, List, Optional

import httpx

from .config import cfg, env, require_env

PROVIDER = cfg.get("pipeline.provider", "anthropic")
MODEL = cfg.get("pipeline.model", "claude-opus-5")


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

    with httpx.Client(timeout=300) as client:
        resp = client.post(
            f"{GEMINI_URL}/{MODEL}:generateContent",
            headers={"x-goog-api-key": key, "Content-Type": "application/json"},
            json=body,
        )

    if resp.status_code == 429:
        raise RuntimeError(
            "Quota Gemini esaurita. Il free tier ha limiti giornalieri: "
            "riprova più tardi, oppure passa a un modello più leggero "
            "(pipeline.model: gemini-3.1-flash-lite)."
        )
    if resp.status_code >= 400:
        raise RuntimeError(f"Gemini {resp.status_code}: {resp.text[:300]}")

    data = resp.json()
    candidates = data.get("candidates") or []
    if not candidates:
        raise RuntimeError(f"Gemini non ha restituito candidati: {str(data)[:300]}")

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
        raise RuntimeError(f"Gemini ha restituito testo vuoto (finishReason={reason})")

    return parse_json(text)


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
