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


def _a_frase(testo: str, quanto: int) -> str:
    """Taglia alla fine di una FRASE, non a metà periodo.

    Perché non basta `_accorcia`. Misurato sui post veri del 12 settembre
    2026: le spiegazioni uscivano così —

        «Groupthink occurs when the desire for harmony or conformity in a
        decision-making group results in an irrational or dysfunctional…»

    Una frase che si interrompe su un aggettivo non è una spiegazione
    accorciata: è una spiegazione rotta, e su un account il cui unico
    argomento è «di noi ti puoi fidare» costa più di quanto valgano le parole
    salvate. Meglio un periodo intero in meno che un periodo a metà.

    Se NESSUNA frase intera ci sta, si restituisce vuoto invece di un
    troncone: il post resta aggancio + fonte + link, che è già un post
    completo. Il fatto per esteso sta a un clic di distanza, ed è
    esattamente il clic che vogliamo.
    """
    testo = (testo or "").strip()
    if not testo:
        return ""
    if len(testo) <= quanto:
        return testo
    tenuto = ""
    for pezzo in re.split(r"(?<=[.!?])\s+", testo):
        prova = (tenuto + " " + pezzo).strip()
        if len(prova) > quanto:
            break
        tenuto = prova
    return tenuto if tenuto.endswith((".", "!", "?")) else ""


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

    # Gli hashtag, e sono la ragione per cui questa funzione è stata toccata.
    #
    # Su Bluesky non c'è un "per te" algoritmico che ripesca un account senza
    # pubblico: la distribuzione passa da chi ti segue e dai FEED, e una buona
    # parte dei feed tematici si costruisce sulle parole e sugli hashtag. Un
    # post senza tag non entra in nessun feed, e a zero follower non lo legge
    # nessuno — misurato il 13 settembre 2026: 148 post, 0 like, 0 repost.
    #
    # Pochi e veri. Non sono gli hashtag di Instagram: là servono a farsi
    # trovare da chi cerca, qui a finire in un feed che qualcuno ha già
    # aperto. Tre sono il massimo che un post da 300 caratteri si può
    # permettere senza rubare spazio alla spiegazione.
    tag = [t.strip().lstrip("#") for t in
           (cfg.get("publish.bluesky.hashtags", []) or []) if t and t.strip()][:3]

    # Il conto dei caratteri si fa a ritroso: link e fonte sono incomprimibili
    # (un link accorciato non funziona, uno studio citato a metà non è una
    # citazione), quindi è la frase esplicativa a cedere spazio.
    #
    # Nota su come Bluesky conta i link: li conta per intero, non li accorcia
    # come faceva Twitter. I nostri sono lunghi perché lo slug è l'aggancio
    # stesso — quindi il posto che si mangiano va tolto davvero, non stimato.
    # ⚠️ IL LINK NON VA PIU' NEL TESTO, ed è la modifica che rende possibile
    # tutto il resto. `pubblica` allega al post una scheda
    # `app.bsky.embed.external` con lo stesso indirizzo: scriverlo anche nel
    # corpo lo metteva DUE VOLTE nello stesso post — una come testo, una come
    # rettangolo cliccabile sotto — e la copia di testo costava fra i 70 e i
    # 95 caratteri su 300.
    #
    # Sono i caratteri che mancavano alla spiegazione, che infatti usciva
    # tagliata a metà periodo. Rimossa la copia, entrano sia il fatto per
    # intero sia gli hashtag, e il collegamento resta cliccabile esattamente
    # come prima.
    #
    # Se un giorno la scheda non ci fosse (nessun `link`), non c'è niente da
    # togliere e la riga della fonte resta da sola.
    # Gli hashtag cedono il posto alla spiegazione, non il contrario.
    #
    # `_a_frase` restituisce vuoto quando nemmeno la prima frase ci sta, e con
    # tre tag capitava su un fatto su tre: il post usciva a 161 caratteri su
    # 300, senza spiegazione, con centoquaranta caratteri buttati. Allora si
    # riprova togliendo un tag per volta, e ci si ferma appena la frase entra.
    # Un hashtag in meno costa un feed; una spiegazione in meno costa il post.
    corpo, coda_tag = "", " ".join(f"#{t}" for t in tag)
    for quanti in range(len(tag), -1, -1):
        prova_tag = " ".join(f"#{t}" for t in tag[:quanti])
        fisso = (len(hook) + len(fonte) + len("\n\n") * 2
                 + (len(prova_tag) + 2 if prova_tag else 0))
        spazio = MAX_TESTO - fisso
        # `_a_frase` e non `_accorcia`: meglio nessuna spiegazione che una
        # spiegazione interrotta in mezzo a un periodo.
        corpo = _a_frase(fatto_txt, spazio) if spazio > 40 else ""
        if corpo:
            coda_tag = prova_tag
            break

    righe = [hook]
    if corpo:
        righe.append(corpo)
    ultima = fonte
    if coda_tag:
        ultima += f"\n{coda_tag}"
    righe.append(ultima)
    testo = "\n\n".join(righe)

    return {
        "testo": testo[:MAX_TESTO],
        "link": link,
        "titolo": _accorcia(hook, 90),
        "descrizione": _accorcia(fatto_txt, 180),
    }



def braccio(fact_id: int) -> str:
    """A quale gruppo della prova appartiene il post di questa curiosita'.

    `con-risposta` o `senza-risposta`. Deciso dalla PARITA' dell'id, non a
    caso: due giri sullo stesso fatto devono dare lo stesso gruppo, o un
    ritentativo dopo un errore di rete sposterebbe un post da un braccio
    all'altro e il conteggio non tornerebbe piu'.

    ⚠️ La parita' degli id e' un'alternanza vera solo perche' i fatti si
    pubblicano in ordine e senza salti sistematici. Se un giorno la selezione
    diventasse "prima i fatti con la fonte piu' lunga" o qualcosa che correla
    con l'id, questa riga andrebbe rifatta con un sorteggio registrato.
    """
    return "con-risposta" if int(fact_id) % 2 == 0 else "senza-risposta"

# ─── Pubblicazione ────────────────────────────────────────────────────────────

def _coda() -> str:
    """La richiesta, più dove sono gli altri canali.

    Perché in risposta al post e non dentro il post: i post stanno fra i 290 e
    i 296 caratteri su 300 — misurato su quelli veri, non stimato — e quello
    spazio lo paga già la spiegazione, che esce troncata con i puntini. Una
    riga di richiesta nel corpo la toglierebbe al fatto o alla fonte, cioè alle
    due cose per cui il post esiste. In risposta non costa niente.

    È anche l'unico posto dove Bluesky sa che gli altri canali esistono: nel
    post non c'è mai stato un rimando a YouTube, TikTok o Instagram, quindi chi
    ci arrivava non aveva modo di scoprire il resto.
    """
    righe = []
    testa = (cfg.get("cta.bluesky_coda", "") or "").strip()
    if testa:
        righe.append(testa)

    # ⚠️ `https://` per esteso su OGNI indirizzo, e non è pignoleria tipografica:
    # `_facets` riconosce i link con l'espressione `https?://`, quindi
    # "youtube.com/@x" scritto senza schema NON diventa cliccabile. Misurato
    # sulla prima risposta pubblicata davvero: quattro indirizzi nel testo, una
    # sola facet costruita, tre link morti. Il post esce lo stesso e sembra
    # giusto — è il tipo di guasto che si vede solo andando a leggere il record.
    #
    # Costano 24 caratteri in tutto e qui lo spazio c'è: la risposta sta sotto
    # i 280 su 300. Una chiocciola scritta a mano invece resterebbe comunque
    # testo morto, perché non è una menzione vera del protocollo.
    altrove = []
    sito = (cfg.get("sito.url", "") or "").rstrip("/")
    yt = (cfg.get("brand.youtube", "") or "").lstrip("@")
    tk = (cfg.get("brand.tiktok", "") or "").lstrip("@")
    ig = (cfg.get("brand.handle", "") or "").lstrip("@")
    if sito:
        altrove.append(f"All of them: {sito}")
    if yt:
        altrove.append(f"https://youtube.com/@{yt}")
    if tk:
        altrove.append(f"https://tiktok.com/@{tk}")
    if ig:
        altrove.append(f"https://instagram.com/{ig}")
    if altrove:
        righe.append("\n".join(altrove))

    # Il tetto vale anche qui: una risposta più lunga di 300 caratteri viene
    # rifiutata dal PDS, e farebbe segnalare come guasto un post già uscito.
    return "\n\n".join(righe)[:MAX_TESTO]


def pubblica(testo: str, link: str = "", titolo: str = "", descrizione: str = "",
             coda: bool = False) -> str:
    """Pubblica un post e restituisce il suo URI at://. Solleva BlueskyError.

    Con `coda` accoda in risposta al post appena creato la richiesta e i
    rimandi agli altri canali. Se quella seconda chiamata fallisce, il post
    resta pubblicato e l'errore si stampa: la richiesta è un di più, il fatto
    no, e far fallire l'uno per l'altra brucerebbe la curiosità — che risulta
    già segnata come usata e non tornerebbe mai più.
    """
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
        creato = resp.json()
        uri = creato.get("uri", "")

        if coda:
            testo_coda = _coda()
            # `cid` insieme a `uri`: il protocollo vuole entrambi in ogni
            # riferimento. Con il solo uri la risposta viene accettata ma resta
            # orfana — appare nel profilo e NON sotto al post, che è il
            # contrario di quello che serve.
            #
            # `root` e `parent` puntano tutti e due al post: è una catena di
            # due, quindi la radice è il post stesso.
            rif = {"uri": uri, "cid": creato.get("cid", "")}
            if testo_coda and rif["cid"]:
                r2 = client.post(
                    f"{PDS}/xrpc/com.atproto.repo.createRecord",
                    headers={"Authorization": f"Bearer {jwt}"},
                    json={"repo": did, "collection": "app.bsky.feed.post",
                          "record": {
                              "$type": "app.bsky.feed.post",
                              "text": testo_coda,
                              "createdAt": datetime.now(timezone.utc)
                                  .isoformat().replace("+00:00", "Z"),
                              "langs": ["en"],
                              "facets": _facets(testo_coda),
                              "reply": {"root": rif, "parent": rif},
                          }},
                )
                if r2.status_code >= 400:
                    # Non si solleva: il post c'è già ed è quello che conta.
                    print(f"    richiesta in coda non pubblicata: "
                          f"{r2.status_code} {r2.text[:120]}")
    return uri


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
