"""Legge le visite al sito da Cloudflare Web Analytics.

Perché serve: il sito è l'unica cosa del progetto che si accumula — settanta
pagine che restano indicizzate mentre un reel muore in due giorni — ed era
l'unica di cui non sapevamo niente. Cloudflare misurava da giorni e quei
numeri non erano visibili da nessuna parte se non aprendo il pannello.

Le tre domande a cui deve rispondere, e sono le uniche che contano:
  · quali pagine legge davvero qualcuno — cioè quali curiosità funzionano
    fuori dai social, dove non c'è un algoritmo a spingerle
  · da dove arrivano — e in particolare se Bluesky porta traffico, visto che
    è l'unico canale che manda la gente fuori invece di trattenerla
  · da quali paesi — perché tutti gli orari di pubblicazione sono tarati su
    un pubblico anglofono, e se il pubblico è italiano quella scelta è sbagliata

⚠️  Serve un token DIVERSO da CLOUDFLARE_API_TOKEN, che è quello di Workers AI
    e genera le immagini. Questo vuole `Account → Account Analytics → Read`,
    di sola lettura. Tenerli separati non è pedanteria: un errore sui permessi
    di quell'altro spegne la generazione dei caroselli.

⚠️  L'endpoint REST `rum/site_info/list` risponde 403 anche con il permesso
    giusto — verificato il 20 agosto 2026. I dati stanno solo nella GraphQL,
    ed è il motivo per cui questo modulo non usa l'API REST come tutti gli altri.

⚠️  La finestra utile è di circa una settimana. Con 14 o 30 giorni la stessa
    query risponde zero, non un errore: è un altro rollup e per il piano
    gratuito è vuoto. Chiedere più indietro non dà più storia, dà silenzio.
"""
from __future__ import annotations

import datetime as _dt
from typing import Dict, List, Tuple

import httpx

from .config import env

GRAPHQL = "https://api.cloudflare.com/client/v4/graphql"
FINESTRA_MASSIMA = 7


class MetricheAssenti(RuntimeError):
    """Token non configurato. Non è un guasto: è una misura che non c'è."""


def _interroga(dimensione: str, giorni: int = FINESTRA_MASSIMA) -> List[Tuple[str, int]]:
    """Conta le visite raggruppate per una dimensione, dalla più frequente."""
    token = env("CLOUDFLARE_ANALYTICS_TOKEN")
    conto = env("CLOUDFLARE_ACCOUNT_ID")
    if not (token and conto):
        raise MetricheAssenti(
            "Manca CLOUDFLARE_ANALYTICS_TOKEN (o CLOUDFLARE_ACCOUNT_ID) in .env. "
            "Si crea su dash.cloudflare.com/profile/api-tokens con il solo "
            "permesso Account → Account Analytics → Read."
        )

    fine = _dt.datetime.now(_dt.timezone.utc)
    inizio = fine - _dt.timedelta(days=min(giorni, FINESTRA_MASSIMA))
    query = (
        "query($acc:String!,$da:Time!,$a:Time!){viewer{accounts(filter:{accountTag:$acc}){"
        "rumPageloadEventsAdaptiveGroups(limit:20, orderBy:[count_DESC], "
        "filter:{datetime_geq:$da,datetime_leq:$a}){count dimensions{" + dimensione + "}}}}}"
    )
    r = httpx.post(
        GRAPHQL,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"query": query, "variables": {
            "acc": conto,
            "da": inizio.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "a": fine.strftime("%Y-%m-%dT%H:%M:%SZ")}},
        timeout=40,
    )
    dati = r.json()
    if dati.get("errors"):
        raise RuntimeError(str(dati["errors"])[:200])
    gruppi = dati["data"]["viewer"]["accounts"][0]["rumPageloadEventsAdaptiveGroups"]
    return [(list(g["dimensions"].values())[0] or "(diretto)", g["count"]) for g in gruppi]


def riassunto(giorni: int = FINESTRA_MASSIMA) -> Dict[str, List[Tuple[str, int]]]:
    """Tutto insieme: pagine, provenienza, paese, dispositivo."""
    return {
        "pagine": _interroga("requestPath", giorni),
        "provenienza": _interroga("refererHost", giorni),
        "paese": _interroga("countryName", giorni),
        "dispositivo": _interroga("deviceType", giorni),
    }


def stampa(giorni: int = FINESTRA_MASSIMA) -> int:
    """Scrive il riassunto a schermo. Ritorna il totale delle visite."""
    try:
        dati = riassunto(giorni)
    except MetricheAssenti as exc:
        print(f"  · sito non misurato: {exc}")
        return 0
    except Exception as exc:
        print(f"  ✗ lettura analitiche fallita: {str(exc)[:160]}")
        return 0

    totale = sum(n for _, n in dati["pagine"])
    print(f"\nSITO — ultimi {min(giorni, FINESTRA_MASSIMA)} giorni: {totale} visite")
    if not totale:
        # Zero visite non è un guasto su un sito nuovo, ed è meglio dirlo che
        # lasciare quattro elenchi vuoti a far pensare a un errore.
        print("  nessuna visita registrata. Su un sito appena nato è normale:")
        print("  i motori devono ancora indicizzarlo e i link dai social sono pochi.")
        return 0

    etichette = (("pagine", "pagina"), ("provenienza", "arrivano da"),
                 ("paese", "paese"), ("dispositivo", "dispositivo"))
    for chiave, titolo in etichette:
        righe = dati[chiave][:6]
        if not righe:
            continue
        print(f"\n  {titolo}")
        for nome, n in righe:
            print(f"    {n:4d}  {str(nome)[:56]}")
    return totale
