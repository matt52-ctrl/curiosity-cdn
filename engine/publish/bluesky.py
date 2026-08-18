"""Pubblicazione su Bluesky via AT Protocol.

Perché Bluesky esiste in questa pipeline: è l'unica delle piattaforme nuove
dove il testo viaggia più lontano del video. Instagram e TikTok premiano il
formato, qui vince la frase. Quindi non ci portiamo i reel: ci portiamo la
curiosità scritta, con lo studio citato per nome e il collegamento alla sua
pagina sul sito. È anche l'unico canale che manda traffico alle 74 pagine —
tutti gli altri tengono le persone dentro di sé.

Flusso, due chiamate:
    POST /xrpc/com.atproto.server.createSession   → accessJwt + did
    POST /xrpc/com.atproto.repo.createRecord      → uri del post

Vincoli reali:
  - 300 grafemi di testo, 3000 byte. Il primo si tocca molto prima del secondo.
  - I collegamenti e gli hashtag NON diventano cliccabili da soli: vanno
    dichiarati a parte come "facet", con gli indici in BYTE dell'UTF-8. Con gli
    indici in caratteri il post esce lo stesso e sembra giusto, ma il link è
    testo morto. È il modo peggiore di sbagliare, quindi qui gli indici si
    calcolano sempre sui byte.
  - La app password non è la password dell'account: si revoca dal profilo
    senza cambiare nulla d'altro. Non usare mai quella vera.
  - Il JWT di sessione dura poche ore. Non lo conserviamo: una sessione nuova
    per ogni esecuzione costa una chiamata e toglie di mezzo il rinnovo.
"""
from __future__ import annotations

import json
import re
from typing import Dict, List, Optional, Tuple

import httpx

from ..config import cfg, require_env

# L'host pubblico di Bluesky. Un account su un PDS proprio userebbe un altro
# indirizzo, ma il nostro sta qui e non ha motivo di spostarsi.
PDS = "https://bsky.social"

# Limite dichiarato da Bluesky. Sono grafemi, non caratteri: un'emoji composta
# ne vale uno solo. Noi scriviamo inglese senza emoji, quindi contare i
# caratteri è un'approssimazione per eccesso — sbaglia dalla parte sicura.
MAX_TESTO = 300


class BlueskyError(RuntimeError):
    pass


# ─── Sessione ─────────────────────────────────────────────────────────────────

def _sessione(client: httpx.Client) -> Tuple[str, str]:
    """Autentica e restituisce (accessJwt, did)."""
    handle = require_env("BLUESKY_HANDLE")
    password = require_env("BLUESKY_APP_PASSWORD")
    resp = client.post(
        f"{PDS}/xrpc/com.atproto.server.createSession",
        json={"identifier": handle, "password": password},
    )
    if resp.status_code >= 400:
        raise BlueskyError(f"createSession → {resp.status_code} {resp.text}")
    dati = resp.json()
    return dati["accessJwt"], dati["did"]


# ─── Facet: rendere cliccabile ciò che sembra già cliccabile ──────────────────

def _facets(testo: str) -> List[Dict]:
    """Le porzioni di testo che Bluesky deve trattare come link o hashtag.

    Gli indici sono posizioni in byte nell'UTF-8 del testo, non in caratteri.
    Con l'inglese puro le due cose coincidono, ma basta una virgoletta tipografica
    o un trattino lungo in mezzo alla frase — e noi ne scriviamo — perché tutto
    quello che viene dopo scivoli di un byte e il link finisca a coprire le
    lettere sbagliate. Quindi si lavora sui byte e si converte una volta sola.
    """
    grezzo = testo.encode("utf-8")
    trovati: List[Dict] = []

    # I link. La regex si ferma prima della punteggiatura finale: un URL a fine
    # frase seguito da un punto altrimenti si porta dentro il punto e diventa
    # un indirizzo che non esiste.
    for m in re.finditer(rb"https?://[^\s\]\)]+", grezzo):
        fine = m.end()
        while fine > m.start() and grezzo[fine - 1:fine] in (b".", b",", b";", b":", b")"):
            fine -= 1
        trovati.append({
            "index": {"byteStart": m.start(), "byteEnd": fine},
            "features": [{"$type": "app.bsky.richtext.facet#link",
                          "uri": grezzo[m.start():fine].decode("utf-8")}],
        })

    # Gli hashtag. Il cancelletto deve stare a inizio parola, o #1 dentro
    # "studio #1" diventerebbe un tag.
    for m in re.finditer(rb"(?:^|\s)(#[A-Za-z][A-Za-z0-9_]*)", grezzo):
        inizio = m.start(1)
        trovati.append({
            "index": {"byteStart": inizio, "byteEnd": m.end(1)},
            "features": [{"$type": "app.bsky.richtext.facet#tag",
                          "tag": grezzo[inizio + 1:m.end(1)].decode("utf-8")}],
        })

    return trovati


# ─── Composizione del post ────────────────────────────────────────────────────

def _slug(testo: str) -> str:
    """Lo stesso slug del sito: il post deve puntare a una pagina che esiste.

    Duplicato di engine.sito._slug di proposito. Importarlo da lì trascinerebbe
    dentro la generazione del sito per tre righe di regex, e questo modulo deve
    poter girare da solo.
    """
    s = re.sub(r"[^a-z0-9]+", "-", testo.lower()).strip("-")
    return s[:70] or "fatto"


def _accorcia(testo: str, quanto: int) -> str:
    """Taglia all'ultima parola intera che ci sta, non a metà parola."""
    testo = (testo or "").strip()
    if len(testo) <= quanto:
        return testo
    tagliato = testo[:quanto].rsplit(" ", 1)[0].rstrip(" ,;:—-")
    return tagliato + "…"


def componi(fatto, base_url: str = "") -> Dict[str, str]:
    """Testo e scheda-collegamento per una curiosità.

    La forma è: affermazione, prova, indirizzo. In quest'ordine perché è
    l'ordine in cui una persona decide se crederci — prima cosa dici, poi
    perché dovrei fidarmi, e solo alla fine dove approfondire. La fonte NON è
    facoltativa: senza il nome dello studio siamo un altro account che afferma
    cose sul cervello, che è esattamente ciò che questo progetto non vuole
    essere. Se la fonte manca, il post non si fa.
    """
    base = (base_url or cfg.get("sito.url", "") or "").rstrip("/")
    hook = (fatto["hook"] or "").strip()
    fatto_txt = (fatto["fact"] or "").strip()
    fonte = (fatto["source_hint"] or "").strip()
    if not hook or not fonte:
        raise BlueskyError(f"fatto {fatto['id']}: senza aggancio o senza fonte, non si pubblica")

    link = f"{base}/f/{_slug(hook)}/" if base else ""

    # Il conto dei caratteri si fa a ritroso: link e fonte sono incomprimibili
    # (un link accorciato non funziona, uno studio citato a metà non è una
    # citazione), quindi è la frase esplicativa a cedere spazio.
    #
    # Nota su come Bluesky conta i link: li conta per intero, non li accorcia
    # come faceva Twitter. I nostri sono lunghi perché lo slug è l'aggancio
    # stesso — quindi il posto che si mangiano va tolto davvero, non stimato.
    fisso = len(hook) + len(fonte) + len(link) + len("\n\n") * 2 + (2 if link else 0)
    spazio = MAX_TESTO - fisso
    corpo = _accorcia(fatto_txt, spazio) if spazio > 40 else ""

    righe = [hook]
    if corpo:
        righe.append(corpo)
    righe.append(fonte if not link else f"{fonte}\n{link}")
    testo = "\n\n".join(righe)

    return {
        "testo": testo[:MAX_TESTO],
        "link": link,
        "titolo": _accorcia(hook, 90),
        "descrizione": _accorcia(fatto_txt, 180),
    }


# ─── Pubblicazione ────────────────────────────────────────────────────────────

def pubblica(testo: str, link: str = "", titolo: str = "", descrizione: str = "") -> str:
    """Pubblica un post e restituisce il suo URI at://. Solleva BlueskyError."""
    if not testo.strip():
        raise BlueskyError("testo vuoto")

    from datetime import datetime, timezone

    with httpx.Client(timeout=30) as client:
        jwt, did = _sessione(client)

        record: Dict = {
            "$type": "app.bsky.feed.post",
            "text": testo,
            # L'ora deve essere in UTC con la Z finale. Bluesky ordina il feed
            # con questo campo e si fida di quello che gli mandi: un fuso
            # sbagliato non dà errore, sposta solo il post nel passato.
            "createdAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            # Dichiarare la lingua serve: senza, Bluesky mostra il post anche a
            # chi ha filtrato l'inglese, e quelle sono impression sprecate che
            # tornano indietro come "non mi interessa".
            "langs": ["en"],
            "facets": _facets(testo),
        }

        # La scheda del collegamento. Senza miniatura di proposito: l'immagine
        # richiederebbe di caricare un blob prima, e la scheda senza copertina
        # è comunque un rettangolo cliccabile con titolo e descrizione. Vale
        # la pena aggiungerla solo quando le pagine del sito avranno una
        # og:image diversa per ciascuna, altrimenti sarebbero 74 schede con la
        # stessa immagine — cioè rumore.
        if link:
            record["embed"] = {
                "$type": "app.bsky.embed.external",
                "external": {"uri": link, "title": titolo or link,
                             "description": descrizione or ""},
            }

        resp = client.post(
            f"{PDS}/xrpc/com.atproto.repo.createRecord",
            headers={"Authorization": f"Bearer {jwt}"},
            json={"repo": did, "collection": "app.bsky.feed.post", "record": record},
        )
    if resp.status_code >= 400:
        raise BlueskyError(f"createRecord → {resp.status_code} {resp.text}")
    return resp.json().get("uri", "")


def url_pubblico(uri: str) -> str:
    """Da at://did:plc:.../app.bsky.feed.post/3k... all'indirizzo leggibile."""
    handle = (require_env("BLUESKY_HANDLE") or "").strip()
    chiave = uri.rsplit("/", 1)[-1] if uri else ""
    return f"https://bsky.app/profile/{handle}/post/{chiave}" if chiave else ""


# ─── Scelta di cosa pubblicare ────────────────────────────────────────────────

def prossimi(conn, quanti: int = 1) -> List:
    """Le curiosità non ancora uscite su Bluesky, dalla più recente.

    Il filtro su fact_uses è ciò che impedisce la ripetizione: la stessa
    tabella che tiene separati Instagram e YouTube tiene fuori anche Bluesky,
    con canale 'bluesky'. Non serve una tabella nuova e non serve ricordarsi
    niente a mano.

    Si pesca solo fra le verificate e con la fonte piena, perché un post senza
    studio citato qui non lo vogliamo — e scoprirlo al momento di comporre
    significherebbe saltare un giorno.
    """
    return conn.execute(
        """SELECT f.* FROM facts f
           WHERE f.status IN ('published','rendered','approved')
             AND f.hook != '' AND COALESCE(f.source_hint,'') != ''
             AND COALESCE(f.verdict,'') != 'refuted'
             AND f.id NOT IN (SELECT fact_id FROM fact_uses WHERE channel = 'bluesky')
           ORDER BY f.created_at DESC
           LIMIT ?""",
        (quanti,),
    ).fetchall()


def segna_uso(conn, fact_id: int, uri: str) -> None:
    """Registra che questa curiosità è uscita su Bluesky."""
    import time

    conn.execute(
        "INSERT OR REPLACE INTO fact_uses (fact_id, channel, used_at, ref) VALUES (?,?,?,?)",
        (fact_id, "bluesky", time.time(), uri),
    )
    conn.commit()
