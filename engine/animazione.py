"""Anima un'immagine ferma chiamando una GPU gratuita di Hugging Face.

PERCHE' ESISTE. Gli sfondi dei video sono passati per tre stadi: filmato
d'archivio scelto per umore (si muove, parla d'altro), immagine generata in
tema (parla del video, sta ferma), filmato d'archivio in tema (si muove e
parla del video). Questo e' il quarto: una scena in tema che si muove come
nessun archivio puo' fare, perche' non esiste in nessun archivio.

COME. Gli Space di Hugging Face con hardware `zero-a10g` girano su GPU
condivise che l'utente non paga. Ogni Space Gradio e' anche un'API, quindi
non serve un fornitore: si chiama lo Space come lo chiamerebbe un browser.

    | account        | GPU al giorno |
    |----------------|---------------|
    | anonimo        | 2 minuti      |
    | gratuito       | 5 minuti      |
    | PRO ($9/mese)  | 40 minuti     |

Si sta in anonimo per scelta di Mattia (4 settembre 2026). Sono 2 minuti di
CALCOLO, non di pellicola: un clip da due secondi occupa la scheda per
decine di secondi, quindi la giornata vale due o tre clip, non un video
intero. Da qui la regola: si anima l'APERTURA di un video al giorno, non il
video.

E non e' solo la quota a impedirlo. I tetti letti dalla configurazione degli
Space, il 4 settembre 2026:

    wan2-1-fast            3,4 secondi
    ltx-video-distilled    8,5 secondi
    nava (con audio)      10   secondi

Non esiste il parametro per chiederne trenta. Un video lungo si farebbe con
nove chiamate, che sono nove inquadrature slegate: uno slideshow, non una
ripresa.

⚠️ NON PROVATO IN PRODUZIONE al momento in cui e' stato scritto. Tre
tentativi anonimi sono falliti tutti con lo stesso errore muto, e la
spiegazione che regge — la quota anonima esaurita dai tentativi stessi — e'
una deduzione, non una misura. Per questo OGNI errore qui dentro ritorna
None invece di alzare un'eccezione: se la strada e' chiusa, i video escono
lo stesso con lo sfondo di prima e nessuno se ne accorge se non nei log.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

from .config import OUTPUT_DIR, cfg, env


def _intestazioni() -> Dict[str, str]:
    """Il token Hugging Face, se c'e'.

    Serve a dire A CHI attribuire il consumo di GPU, e senza non si ottiene
    niente. La tabella delle quote elenca una riga "anonimo, 2 minuti", ma
    quei due minuti valgono per chi sta sul sito col browser, dove Hugging
    Face riconosce la sessione. Da script il 5 settembre 2026 la GPU non e'
    stata assegnata mai — provato da due indirizzi diversi, il portatile di
    Mattia e un runner di GitHub, a sette ore di distanza.

    Che non fosse un difetto nostro lo dice un errore ricevuto lungo la
    strada: «9:16 (544x960) is not in the list of choices». La richiesta
    arriva, viene letta e validata; e' solo la GPU che non viene data.

    Il token e' di sola lettura e si revoca da huggingface.co/settings/tokens.
    """
    t = env("HF_TOKEN") or env("HUGGINGFACE_TOKEN")
    return {"Authorization": f"Bearer {t}"} if t else {}


# Quante aperture sono gia' uscite da questo processo.
#
# Il conto sta in memoria e non su disco, e regge lo stesso: il giro del
# mattino carica e programma TUTTI gli Short della giornata in una sola
# esecuzione, quindi "una per processo" e "una al giorno" sono la stessa cosa.
# Su disco sarebbe peggio, non meglio: i runner di GitHub sono usa e getta e
# un contatore in un file finirebbe committato nel database per niente.
#
# Serve a garantire il GRUPPO DI CONTROLLO. Senza, i due Short della giornata
# ci provano tutti e due e a decidere chi lo prende e' la quota — cioe' il
# caso. Con il tetto, il secondo Short non ce l'ha mai: stesso giorno, stesso
# pubblico, unica differenza l'apertura, ed e' l'unico modo di leggere fra tre
# settimane se e' servita a qualcosa.
_fatte = 0


class NienteGPU(RuntimeError):
    """Lo Space non ha prodotto niente. Non e' un guasto: e' il caso normale
    quando la quota gratuita del giorno e' finita."""


def _spazi() -> List[Dict[str, Any]]:
    voci = cfg.get("animazione.spazi", []) or []
    return [v for v in voci if isinstance(v, dict) and v.get("host")]


def _carica(host: str, immagine: Path, attesa: float) -> Optional[str]:
    """Deposita l'immagine sullo Space e ritorna il percorso che lui usa.

    Serve perche' l'endpoint di animazione vuole un file gia' suo: passargli
    un indirizzo pubblico non funziona, Gradio si aspetta il proprio
    `FileData`.
    """
    try:
        with open(immagine, "rb") as f:
            r = httpx.post(f"https://{host}/gradio_api/upload",
                           files={"files": (immagine.name, f, "image/png")},
                           headers=_intestazioni(), timeout=attesa)
        if r.status_code >= 400:
            return None
        elenco = r.json()
        return elenco[0] if isinstance(elenco, list) and elenco else None
    except Exception:
        return None


def _video_dentro(nodo: Any) -> Optional[str]:
    """Pesca l'indirizzo del video nella risposta, che non ha forma fissa.

    Ogni Space restituisce una struttura sua — a volte il video e' in cima,
    a volte dentro una tupla insieme ai sottotitoli o al seme usato. Invece
    di scrivere un parser per ognuno si cerca in profondita' la prima cosa
    che somigli a un file video: e' l'unico modo perche' aggiungere uno Space
    all'elenco resti una riga di configurazione e non una riga di codice.
    """
    if isinstance(nodo, str):
        return nodo if nodo.lower().endswith((".mp4", ".webm")) else None
    if isinstance(nodo, dict):
        for chiave in ("url", "path"):
            v = nodo.get(chiave)
            if isinstance(v, str) and v.lower().endswith((".mp4", ".webm")):
                return v
        for v in nodo.values():
            trovato = _video_dentro(v)
            if trovato:
                return trovato
        return None
    if isinstance(nodo, (list, tuple)):
        for v in nodo:
            trovato = _video_dentro(v)
            if trovato:
                return trovato
    return None


def _attendi(host: str, endpoint: str, evento: str,
             attesa: float) -> Optional[Any]:
    """Segue il flusso di eventi finche' lo Space non consegna o rinuncia.

    In anonimo la coda ha priorita' bassa: l'attesa e' fatta di minuti, non
    di secondi, ed e' accettabile solo perche' questo sfondo si prepara ore
    prima che il video esca. Il tetto lo mette chi chiama.
    """
    url = f"https://{host}/gradio_api/call/{endpoint}/{evento}"
    try:
        with httpx.stream("GET", url, headers=_intestazioni(),
                          timeout=attesa) as r:
            tipo = ""
            for riga in r.iter_lines():
                if not riga:
                    continue
                if riga.startswith("event:"):
                    tipo = riga.split(":", 1)[1].strip()
                elif riga.startswith("data:"):
                    corpo = riga.split(":", 1)[1].strip()
                    if tipo == "complete":
                        try:
                            return json.loads(corpo)
                        except ValueError:
                            return None
                    if tipo == "error":
                        # `null` e' come ZeroGPU dice "quota finita": non ha
                        # un messaggio, e non e' un guasto da segnalare.
                        raise NienteGPU(corpo[:120] or "nessun dettaglio")
    except NienteGPU:
        raise
    except Exception:
        return None
    return None


def _scarica(host: str, indirizzo: str, dove: Path,
             attesa: float) -> Optional[Path]:
    if indirizzo.startswith("http"):
        url = indirizzo
    else:
        url = f"https://{host}/gradio_api/file={indirizzo.lstrip('/')}"
    try:
        r = httpx.get(url, headers=_intestazioni(), timeout=attesa,
                      follow_redirects=True)
        if r.status_code >= 400 or len(r.content) < 10000:
            return None
        dove.parent.mkdir(parents=True, exist_ok=True)
        dove.write_bytes(r.content)
        return dove
    except Exception:
        return None


def anima(fai_immagine, soggetto: str, nome: str) -> Optional[Path]:
    """Un breve filmato dal soggetto dato. None se non se ne fa niente.

    `fai_immagine` e' una FUNZIONE che produce l'immagine di partenza, non
    l'immagine: viene chiamata al massimo una volta, e solo se si arriva a
    uno Space che la vuole davvero. La differenza non e' stilistica. Quella
    immagine e' generata da Cloudflare e costa 163 neuroni; con l'immagine
    pronta in anticipo, ogni giro in cui la GPU gratuita e' esaurita — cioe'
    quasi tutti, finche' restiamo anonimi — pagava un'immagine per poi
    buttarla, mentre il filmato in tema copriva lo sfondo lo stesso.
    Misurato il 4 settembre: 163 neuroni buttati per video, due volte al
    giorno.

    Non alza mai eccezioni verso il montaggio. Uno sfondo mancante non deve
    costare l'uscita di una fascia oraria: chi chiama ripiega e va avanti.
    """
    global _fatte
    if not cfg.get("animazione.attiva", False):
        return None
    soggetto = (soggetto or "").strip()
    if not soggetto:
        return None
    tetto = int(cfg.get("animazione.max_al_giorno", 1))
    if _fatte >= tetto:
        return None

    attesa = float(cfg.get("animazione.attesa_massima", 420))
    out = OUTPUT_DIR / "animazioni" / f"{nome}.mp4"

    # L'immagine si fa una volta sola e solo su richiesta. `fatta` distingue
    # "non ancora chiesta" da "chiesta e non riuscita", che senza sentinella
    # sarebbero lo stesso None e farebbero ritentare a ogni Space.
    deposito = {"fatta": False, "img": None}

    def immagine_di_partenza() -> Optional[Path]:
        if not deposito["fatta"]:
            deposito["fatta"] = True
            try:
                deposito["img"] = fai_immagine()
            except Exception:
                deposito["img"] = None
        return deposito["img"]

    for spazio in _spazi():
        host = str(spazio["host"])
        etichetta = host.split(".")[0]
        vuole_immagine = bool(spazio.get("vuole_immagine", False))
        immagine = immagine_di_partenza() if vuole_immagine else None
        if vuole_immagine and not (immagine and immagine.exists()):
            continue

        dati = []
        for campo in (spazio.get("campi") or []):
            if campo == "@immagine":
                percorso = _carica(host, immagine, attesa=90) if immagine else None
                if vuole_immagine and not percorso:
                    dati = None
                    break
                dati.append({"path": percorso,
                             "meta": {"_type": "gradio.FileData"}}
                            if percorso else None)
            elif campo == "@soggetto":
                dati.append(soggetto)
            else:
                dati.append(campo)
        if dati is None:
            print(f"    {etichetta}: immagine non accettata, provo il prossimo")
            continue

        t0 = time.time()
        try:
            r = httpx.post(
                f"https://{host}/gradio_api/call/{spazio['endpoint']}",
                json={"data": dati}, headers=_intestazioni(), timeout=60,
            )
            if r.status_code >= 400:
                print(f"    {etichetta}: rifiutato ({r.status_code})")
                continue
            evento = r.json().get("event_id")
            if not evento:
                continue
            risposta = _attendi(host, str(spazio["endpoint"]), evento, attesa)
        except NienteGPU as exc:
            # Ci si FERMA, non si passa al prossimo. La quota ZeroGPU e' una
            # sola per chi chiama — vale su TUTTI gli Space insieme, non uno
            # per Space — quindi se il primo dice che non c'e' GPU, gli altri
            # diranno lo stesso. Insistere costerebbe minuti di attesa per
            # nulla e, peggio, farebbe generare l'immagine di partenza dello
            # Space successivo: 163 neuroni Cloudflare spesi per un video che
            # non nascera'.
            print(f"    {etichetta}: niente GPU gratuita adesso ({exc}), "
                  f"lascio perdere il resto")
            break
        except Exception as exc:
            print(f"    {etichetta}: non risponde ({str(exc)[:60]})")
            continue

        indirizzo = _video_dentro(risposta)
        if not indirizzo:
            print(f"    {etichetta}: nessun video nella risposta")
            continue

        video = _scarica(host, indirizzo, out, attesa=180)
        if video:
            _fatte += 1
            print(f"    apertura animata da {etichetta} "
                  f"({time.time() - t0:.0f}s)")
            return video
        print(f"    {etichetta}: video non scaricabile")

    return None
