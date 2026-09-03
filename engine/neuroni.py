"""Quanti neuroni Cloudflare restano oggi, e quale modello ci sta dentro.

PERCHÉ ESISTE QUESTO FILE. Il free tier di Workers AI dà 10.000 neuroni al
giorno. Fino a ieri quel tetto non lo guardava nessuno, e andava bene finché
girava un modello solo. Da quando i modelli sono tre a rotazione non va più
bene, perché costano MOLTO diversamente. Misurato il 3 settembre 2026 sul
conto di Mattia, interrogando `aiInferenceAdaptiveGroups` dopo aver generato
immagini vere:

    @cf/black-forest-labs/flux-1-schnell    652,8 neuroni / 4 immagini =   163
    @cf/leonardo/phoenix-1.0              7.020,0 neuroni / 3 immagini = 2.340
    @cf/leonardo/lucid-origin             8.510,6 neuroni / 3 immagini = 2.837

Diciassette volte tanto. Con 10.000 neuroni al giorno vuol dire:

    flux-1-schnell    61 immagini al giorno
    phoenix-1.0        4 immagini al giorno
    lucid-origin       3 immagini al giorno

Tre immagini non bastano nemmeno per UN carosello da cinque slide. La
rotazione, così com'era, spendeva l'intera giornata sulle prime tre immagini
e poi falliva in silenzio: `_generate_cloudflare` prende il 429, stampa una
riga e ripiega. Le immagini non sparivano con un errore, peggioravano — che è
lo stesso modo in cui ci era sfuggito il 400 sul `width` di flux.

COSA FA. Prima di generare, chiede quanti neuroni sono già stati spesi oggi e
risponde se il modello scelto ci sta ancora. Se non ci sta, propone quello
economico invece di far fallire l'immagine. Il tetto è sotto i 10.000 veri
(vedi `visuals.neuroni_al_giorno`) perché il conteggio di Cloudflare arriva
con qualche minuto di ritardo e il margine serve a coprirlo.

PERCHÉ CHIEDE A CLOUDFLARE E NON TIENE UN CONTATORE PROPRIO. Un contatore
andrebbe salvato nel database, che sta in git: ogni immagine diventerebbe una
riga in più da committare e un'altra occasione di conflitto sullo stato — il
genere di cosa che ad agosto è costato dati veri. E sarebbe comunque sbagliato,
perché la quota è del CONTO, non di questa pipeline: le prove fatte a mano dal
portatile la consumano e un contatore locale non le vedrebbe. La domanda a
Cloudflare invece vede tutto, non ha stato da mantenere e si corregge da sola.

Il ritardo del conteggio si copre sommando le immagini fatte in questo processo
alla cifra dell'API, letta una volta sola all'inizio.
"""
from __future__ import annotations

import datetime as _dt
from typing import Dict, Optional

import httpx

from .config import cfg, env

GRAPHQL = "https://api.cloudflare.com/client/v4/graphql"

# Neuroni per immagine, misurati (vedi il docstring). Non sono listini presi
# da una pagina di documentazione: sono il consumo reale del corpo che manda
# `visuals._generate_cloudflare`, alle dimensioni che chiede lui. Cambiando
# `visuals.larghezza`/`altezza` questi numeri si spostano, perché i modelli
# Leonardo si pagano a pixel prodotti.
COSTO: Dict[str, float] = {
    "@cf/black-forest-labs/flux-1-schnell": 163.0,
    "@cf/leonardo/phoenix-1.0": 2340.0,
    "@cf/leonardo/lucid-origin": 2837.0,
}

# Quando il modello non è in tabella si assume il costo del più caro. È la
# direzione giusta in cui sbagliare: sottostimare fa esaurire la quota a metà
# giornata, sovrastimare fa solo ripiegare su flux un'immagine prima.
COSTO_IGNOTO = max(COSTO.values())

ECONOMICO = "@cf/black-forest-labs/flux-1-schnell"

# Letto una volta per processo: la domanda a Cloudflare costa un giro di rete
# e la risposta non cambia abbastanza in fretta da rifarla a ogni immagine.
# None = non ancora chiesto. -1 = chiesto e non disponibile (manca il token,
# oppure l'API ha risposto male): in quel caso non si blocca niente.
_spesi_api: Optional[float] = None
_spesi_qui: float = 0.0


def _chiedi_a_cloudflare() -> Optional[float]:
    """Neuroni consumati dal conto oggi, o None se non si può sapere."""
    conto = env("CLOUDFLARE_ACCOUNT_ID")
    # Il token di sola analitica basta e avanza; se manca si prova con quello
    # di Workers AI, che di solito NON ha il permesso di lettura e fallisce.
    # Va bene: fallire qui significa solo tornare al comportamento di prima.
    token = env("CLOUDFLARE_ANALYTICS_TOKEN") or env("CLOUDFLARE_API_TOKEN")
    if not (conto and token):
        return None

    oggi = _dt.datetime.now(_dt.timezone.utc).date().isoformat()
    query = (
        "query($acc:String!,$g:Date!){viewer{accounts(filter:{accountTag:$acc}){"
        "aiInferenceAdaptiveGroups(limit:100,filter:{date_geq:$g,date_leq:$g})"
        "{sum{totalNeurons}}}}}"
    )
    try:
        r = httpx.post(
            GRAPHQL,
            headers={"Authorization": f"Bearer {token}",
                     "Content-Type": "application/json"},
            json={"query": query, "variables": {"acc": conto, "g": oggi}},
            timeout=20,
        )
        dati = r.json()
        if dati.get("errors"):
            return None
        gruppi = dati["data"]["viewer"]["accounts"][0]["aiInferenceAdaptiveGroups"]
    except Exception:
        return None
    return float(sum((g.get("sum") or {}).get("totalNeurons") or 0 for g in gruppi))


def spesi_oggi() -> Optional[float]:
    """Neuroni spesi oggi, o None se la misura non è disponibile."""
    global _spesi_api
    if _spesi_api is None:
        letto = _chiedi_a_cloudflare()
        _spesi_api = -1.0 if letto is None else letto
    if _spesi_api < 0:
        return None
    return _spesi_api + _spesi_qui


def residuo() -> Optional[float]:
    """Quanti neuroni restano prima del tetto. None se non si sa."""
    spesi = spesi_oggi()
    if spesi is None:
        return None
    tetto = float(cfg.get("visuals.neuroni_al_giorno", 9000))
    return max(0.0, tetto - spesi)


def costo(modello: str) -> float:
    return COSTO.get(modello, COSTO_IGNOTO)


def ci_sta(modello: str, quante: int = 1) -> bool:
    """Se non si sa, si dice di sì: il guardiano non deve fermare la pipeline
    solo perché gli manca il token dell'analitica."""
    resta = residuo()
    return True if resta is None else costo(modello) * quante <= resta


def modello_possibile(preferito: str, quante: int = 1) -> str:
    """Il modello preferito se ci sta, altrimenti quello economico.

    Non restituisce mai None e non solleva: chi genera deve poter chiamare
    questa funzione e proseguire comunque. Se anche l'economico è fuori
    budget lo restituisce lo stesso — sarà Cloudflare a dire di no, e la
    catena di ripiego di `visuals.generate` farà il suo mestiere.
    """
    if ci_sta(preferito, quante):
        return preferito
    resta = residuo()
    if preferito != ECONOMICO:
        print(f"    neuroni: restano {resta:.0f}, {preferito.split('/')[-1]} ne "
              f"vuole {costo(preferito)*quante:.0f} — passo a "
              f"{ECONOMICO.split('/')[-1]}")
    return ECONOMICO


def registra(modello: str) -> None:
    """Da chiamare dopo ogni immagine riuscita, per non aspettare che il
    conteggio di Cloudflare si aggiorni."""
    global _spesi_qui
    _spesi_qui += costo(modello)
