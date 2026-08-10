"""Hosting delle immagini.

Vincolo non negoziabile: **Instagram rifiuta le URL con query string.**
Questo esclude gli URL firmati di S3/R2 e la maggior parte dei link "temporanei".
Servono URL pulite, pubbliche e raggiungibili nel momento della chiamata.

Tre backend:
  github     — repo pubblico + raw.githubusercontent.com. Gratis, URL pulite,
               zero configurazione oltre a un token. Il default.
  cloudinary — free tier generoso, CDN vera. Se ti serve anche un dominio
               proprio (prerequisito per TikTok), è la strada.
  local      — nessun upload, solo per test del rendering.
"""
from __future__ import annotations

import base64
import hashlib
import time
from pathlib import Path
from typing import List

import httpx

from .config import env, require_env


def _upload_github(paths: List[Path], prefix: str) -> List[str]:
    token = require_env("GITHUB_TOKEN")
    repo = require_env("GITHUB_REPO")
    branch = env("GITHUB_BRANCH", "main")
    urls: List[str] = []

    with httpx.Client(timeout=60) as client:
        for path in paths:
            remote = f"posts/{prefix}/{path.name}"
            content = base64.b64encode(path.read_bytes()).decode("ascii")
            api = f"https://api.github.com/repos/{repo}/contents/{remote}"
            headers = {
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
            }
            # Se il file esiste già serve il suo sha per sovrascriverlo.
            existing = client.get(api, headers=headers, params={"ref": branch})
            payload = {
                "message": f"add {remote}",
                "content": content,
                "branch": branch,
            }
            if existing.status_code == 200:
                payload["sha"] = existing.json()["sha"]

            resp = client.put(api, headers=headers, json=payload)
            resp.raise_for_status()
            urls.append(f"https://raw.githubusercontent.com/{repo}/{branch}/{remote}")

    return urls


def _upload_cloudinary(paths: List[Path], prefix: str) -> List[str]:
    cloud = require_env("CLOUDINARY_CLOUD_NAME")
    key = require_env("CLOUDINARY_API_KEY")
    secret = require_env("CLOUDINARY_API_SECRET")
    urls: List[str] = []

    with httpx.Client(timeout=120) as client:
        for path in paths:
            public_id = f"posts/{prefix}/{path.stem}"
            timestamp = str(int(time.time()))
            # Firma: parametri ordinati alfabeticamente + api_secret, sha1.
            to_sign = f"public_id={public_id}&timestamp={timestamp}{secret}"
            signature = hashlib.sha1(to_sign.encode()).hexdigest()

            resp = client.post(
                f"https://api.cloudinary.com/v1_1/{cloud}/image/upload",
                data={
                    "public_id": public_id,
                    "timestamp": timestamp,
                    "api_key": key,
                    "signature": signature,
                },
                files={"file": (path.name, path.read_bytes(), "image/png")},
            )
            resp.raise_for_status()
            # secure_url è già senza query string.
            urls.append(resp.json()["secure_url"])

    return urls


def upload(paths: List[Path], prefix: str) -> List[str]:
    backend = env("IMAGE_HOST_BACKEND", "github").lower()
    if backend == "github":
        return _upload_github(paths, prefix)
    if backend == "cloudinary":
        return _upload_cloudinary(paths, prefix)
    if backend == "local":
        return [p.as_uri() for p in paths]
    raise ValueError(f"IMAGE_HOST_BACKEND sconosciuto: {backend}")
