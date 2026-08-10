"""Caricamento configurazione + segreti.

config.yaml = tutto ciò che definisce la pagina (voce, nicchia, formato).
.env        = solo segreti.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

OUTPUT_DIR = ROOT / "output"
DATA_DIR = ROOT / "data"
ASSETS_DIR = ROOT / "assets"
TEMPLATES_DIR = ROOT / "engine" / "templates"

for _d in (OUTPUT_DIR, DATA_DIR, ASSETS_DIR / "fonts"):
    _d.mkdir(parents=True, exist_ok=True)


class Config:
    def __init__(self, path: Path | None = None):
        path = path or ROOT / "config.yaml"
        with open(path, "r", encoding="utf-8") as f:
            self._raw: Dict[str, Any] = yaml.safe_load(f)

    def __getitem__(self, key: str) -> Any:
        return self._raw[key]

    def get(self, dotted: str, default: Any = None) -> Any:
        """cfg.get('pipeline.model') → 'claude-opus-5'"""
        node: Any = self._raw
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    @property
    def raw(self) -> Dict[str, Any]:
        return self._raw


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default) or default


# Valori che compaiono in .env.example: non sono vuoti, quindi senza questo
# controllo passano per credenziali valide e l'errore arriva molto più tardi,
# come rifiuto incomprensibile da parte di Instagram o di Anthropic.
_PLACEHOLDERS = (
    "sk-ant-...",
    "EAAG...",
    "ghp_...",
    "17841400000000000",
    "tuo-username/curiosity-cdn",
)


def is_placeholder(value: str) -> bool:
    v = value.strip()
    return (not v) or v in _PLACEHOLDERS or v.endswith("...")


def require_env(name: str) -> str:
    value = os.environ.get(name, "")
    if is_placeholder(value):
        detail = (
            "contiene ancora il valore segnaposto di .env.example"
            if value.strip()
            else "non è impostata"
        )
        raise RuntimeError(
            f"{name} {detail}. Aprila in .env e mettici il valore vero."
        )
    return value


cfg = Config()
