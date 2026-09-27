"""TikTok pubblicato da Buffer, al posto della nostra app.

Nasce il 27 settembre 2026, quando TikTok ha rifiutato l'audit dell'app
"Oddly Wired Publisher" con una motivazione che non lascia margini: TikTok for
Developers «does not support personal or internal company use», e fra i casi
esclusi c'e' esattamente il nostro — «a utility tool to help upload contents to
the account(s) you or your team manages». Non e' un difetto da correggere e
ripresentare: e' la regola. Senza audit restava solo la bozza nell'inbox, cioe'
un tocco di Mattia per ogni video, e quei tocchi non li faceva piu'.

Buffer invece e' uno strumento per molti utenti, gia' approvato da TikTok per
la pubblicazione automatica: passare da li' e' il percorso previsto, non un
aggiramento. Verificato lo stesso giorno sul piano GRATUITO: un post TikTok
creato con `schedulingType: automatic` torna `scheduled`/`automatic`, non
declassato a promemoria.

Tre cose dell'API che decidono come e' fatto questo modulo:

  - Non esiste un upload. Il video si passa come URL pubblico, e Buffer lo
    scarica quando PUBBLICA, non quando crei il post. Quindi il file sul CDN
    deve restare vivo fino a `sent`: cancellarlo prima fa fallire il post in
    silenzio. Da qui il registro `tiktok_buffer` e la pulizia differita.
  - `shareNow` e non la coda. L'orario lo decide gia' il workflow, come per
    gli altri canali; la coda di Buffer sul gratuito tiene 10 post per canale
    e aggiungerebbe un secondo calendario da tenere allineato al primo.
  - `isAiGenerated: true` sempre. TikTok tratta l'IA non dichiarata come
    violazione, e su questa pagina immagini e voce sono generate.
"""
from __future__ import annotations

import time
from typing import Dict, Optional

import httpx

from ..config import env

API = "https://api.buffer.com"


class BufferError(RuntimeError):
    pass


def attivo() -> bool:
    """Buffer e' configurato? Senza chiave si resta sulla vecchia strada."""
    return bool((env("BUFFER_API_KEY") or "").strip())


def _gql(query: str, variabili: Optional[Dict] = None) -> Dict:
    chiave = (env("BUFFER_API_KEY") or "").strip()
    if not chiave:
        raise BufferError("manca BUFFER_API_KEY")
    r = httpx.post(API, json={"query": query, "variables": variabili or {}},
                   headers={"Authorization": f"Bearer {chiave}"}, timeout=60)
    if r.status_code == 401:
        raise BufferError("chiave Buffer rifiutata (401): rigenerala in "
                          "Buffer → Settings → API e aggiorna il secret")
    if r.status_code >= 400:
        raise BufferError(f"Buffer ha risposto {r.status_code}: {r.text[:160]}")
    d = r.json()
    if d.get("errors"):
        raise BufferError(f"Buffer: {d['errors'][0].get('message', d['errors'])}")
    return d["data"]


def canale_tiktok() -> str:
    """L'id del canale TikTok collegato a Buffer.

    Letto ogni volta invece di scriverlo in configurazione: se Mattia scollega
    e ricollega l'account, Buffer gli da' un id nuovo, e un id scritto a mano
    diventerebbe un errore che nessuno capisce.
    """
    org = _gql("query { account { organizations { id } } }")
    for o in org["account"]["organizations"]:
        d = _gql("query($o: OrganizationId!) { channels(input:{organizationId:$o})"
                 " { id service isQueuePaused } }", {"o": o["id"]})
        for c in d["channels"]:
            if c["service"] == "tiktok":
                if c.get("isQueuePaused"):
                    raise BufferError("la coda TikTok su Buffer e' in pausa")
                return c["id"]
    raise BufferError("nessun account TikTok collegato a Buffer "
                      "(account.buffer.com/channels)")


def pubblica_video(url: str, testo: str, canale: Optional[str] = None) -> str:
    """Pubblica ORA un video su TikTok tramite Buffer. Ritorna l'id del post."""
    d = _gql(
        """mutation($i: CreatePostInput!) { createPost(input: $i) {
             ... on PostActionSuccess { post { id status schedulingType } }
             ... on MutationError { message } } }""",
        {"i": {
            "text": testo,
            "channelId": canale or canale_tiktok(),
            "schedulingType": "automatic",
            "mode": "shareNow",
            "assets": [{"video": {"url": url}}],
            "metadata": {"tiktok": {"isAiGenerated": True}},
        }},
    )["createPost"]
    if "post" not in d:
        raise BufferError(f"post rifiutato: {d.get('message', d)}")
    # Se un giorno Buffer declassasse il gratuito a promemoria, il post
    # esisterebbe ma non uscirebbe mai da solo: meglio saperlo subito.
    if d["post"].get("schedulingType") != "automatic":
        raise BufferError("Buffer ha accettato il post solo come promemoria: "
                          "la pubblicazione automatica non e' piu' disponibile")
    return d["post"]["id"]


def stato(post_id: str) -> str:
    """scheduled | sending | sent | error | ... — o 'sparito' se cancellato."""
    d = _gql("query($i: PostId!) { post(input:{id:$i}) { status } }", {"i": post_id})
    return (d.get("post") or {}).get("status") or "sparito"


def attendi(post_id: str, secondi: int = 300) -> str:
    """Aspetta che Buffer abbia finito con il post, fino a `secondi`.

    Con `shareNow` di solito bastano pochi secondi. Se non basta non e' un
    guasto: il post resta nel registro e il giro dopo lo ricontrolla.
    """
    fine = time.time() + secondi
    s = stato(post_id)
    while s in ("scheduled", "sending") and time.time() < fine:
        time.sleep(15)
        s = stato(post_id)
    return s
