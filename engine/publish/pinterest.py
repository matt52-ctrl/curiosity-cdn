"""Pubblicazione su Pinterest via API v5.

Perché Pinterest, in un progetto che ha già Instagram e TikTok: è l'unico dei
tre in cui il contenuto NON scade. Un pin continua a portare visite per mesi,
mentre un reel vive due giorni — e ogni pin porta il collegamento alla pagina
della curiosità sul sito. Insieme a Bluesky è il secondo canale che manda
gente fuori invece di trattenerla, ed è quello che lo fa nel tempo.

Ci portiamo i caroselli, non i video. Le cinque slide sono già immagini
verticali con testo grande: è esattamente la forma che Pinterest premia, e non
richiede nessuna conversione. Il video richiederebbe il caricamento in più
passi su S3 e renderebbe meno, qui.

Flusso, tre chiamate:
    POST /v5/oauth/token       refresh → access token (dura 30 giorni)
    GET  /v5/boards            trova o crea la bacheca
    POST /v5/pins              crea il pin

⚠️  ACCESSO TRIAL. Un'app appena creata è in "Trial access", e i pin creati
    in quella modalità NON sono visibili al pubblico: li vedi solo tu. Il
    codice funziona identico nei due casi, quindi si può provare tutto subito,
    ma finché non passi a "Standard access" stai pubblicando per nessuno.
    Il passaggio richiede un video che mostri questo flusso mentre gira: ecco
    perché questo file deve esistere prima della richiesta, non dopo.

Vincoli reali:
  - le immagini devono stare su URL pubbliche raggiungibili da Pinterest
  - un pin a più immagini ne accetta da 2 a 5 — le nostre sono esattamente 5
  - il titolo si ferma a 100 caratteri, la descrizione a 800
  - il link è il motivo per cui siamo qui: senza, il pin non porta da nessuna
    parte e tanto varrebbe non pubblicarlo
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional

import httpx

from ..config import cfg, env, require_env

API = "https://api.pinterest.com/v5"

# Limiti dichiarati da Pinterest. Tagliare noi e' meglio che farsi rifiutare
# la chiamata: un pin scartato per due caratteri di troppo e' una curiosita'
# persa, e l'errore che restituisce non dice quale campo fosse.
MAX_TITOLO = 100
MAX_DESCRIZIONE = 800


class PinterestError(RuntimeError):
    pass


class CredenzialiAssenti(PinterestError):
    """Le credenziali non ci sono ancora — diverso dall'averle sbagliate.

    Stessa distinzione fatta per TikTok, e per lo stesso motivo: gli orari si
    accendono prima che l'app esista, e un giro che esce in errore ogni notte
    manda una mail che dopo tre giorni non legge piu' nessuno.
    """


# ─── Autenticazione ───────────────────────────────────────────────────────────

def _token() -> str:
    """Scambia il refresh token con un access token valido.

    L'access token dura 30 giorni e il refresh un anno, quindi conservarlo
    avrebbe senso — ma qui si pubblica una volta al giorno, e una chiamata in
    piu' al giorno costa meno di un token salvato che scade quando non
    guardi. Si richiede ogni volta.
    """
    cliente = env("PINTEREST_APP_ID")
    segreto = env("PINTEREST_APP_SECRET")
    refresh = env("PINTEREST_REFRESH_TOKEN")
    if not (cliente and segreto and refresh):
        raise CredenzialiAssenti(
            "Mancano PINTEREST_APP_ID, PINTEREST_APP_SECRET o "
            "PINTEREST_REFRESH_TOKEN in .env. Lancia:  python3 setup_pinterest.py"
        )

    # Le credenziali dell'app vanno in Basic auth, NON nel corpo: Pinterest
    # risponde 401 senza spiegare quale delle due cose manchi, e mandarle nel
    # corpo come fa TikTok e' l'errore che costa mezz'ora.
    r = httpx.post(
        f"{API}/oauth/token",
        auth=(cliente, segreto),
        data={"grant_type": "refresh_token", "refresh_token": refresh},
        timeout=60,
    )
    if r.status_code >= 400:
        raise PinterestError(f"rinnovo token → {r.status_code} {r.text[:200]}")
    dato = r.json().get("access_token")
    if not dato:
        raise PinterestError(f"nessun access_token nella risposta: {r.text[:200]}")
    return dato


def _intestazioni(token: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


# ─── Bacheca ──────────────────────────────────────────────────────────────────

def bacheca(token: str, nome: str = "") -> str:
    """L'id della bacheca dove pubblicare, creandola se non c'è.

    Una bacheca sola e non una per argomento: con settanta pin divisi in
    quindici bacheche nessuna raggiunge la massa che serve a Pinterest per
    capire di cosa parla il profilo. Si divide quando i pin saranno centinaia.
    """
    nome = nome or cfg.get("publish.pinterest.bacheca", "Psychology Facts")

    r = httpx.get(f"{API}/boards", headers=_intestazioni(token),
                  params={"page_size": 100}, timeout=30)
    if r.status_code >= 400:
        raise PinterestError(f"elenco bacheche → {r.status_code} {r.text[:200]}")
    for b in r.json().get("items", []):
        if (b.get("name") or "").strip().lower() == nome.strip().lower():
            return b["id"]

    r = httpx.post(
        f"{API}/boards", headers=_intestazioni(token),
        json={"name": nome,
              "description": cfg.get("publish.pinterest.descrizione_bacheca",
                                     "One checked psychology fact a day."),
              "privacy": "PUBLIC"},
        timeout=30,
    )
    if r.status_code >= 400:
        raise PinterestError(f"creazione bacheca → {r.status_code} {r.text[:200]}")
    return r.json()["id"]


# ─── Composizione ─────────────────────────────────────────────────────────────

def _slug(testo: str) -> str:
    """Lo stesso slug del sito: il pin deve puntare a una pagina che esiste."""
    s = re.sub(r"[^a-z0-9]+", "-", testo.lower()).strip("-")
    return s[:70] or "fatto"


def _taglia(testo: str, quanto: int) -> str:
    testo = " ".join((testo or "").split())
    if len(testo) <= quanto:
        return testo
    return testo[:quanto].rsplit(" ", 1)[0].rstrip(" ,;:—-") + "…"


def componi(fatto, base_url: str = "") -> Dict[str, str]:
    """Titolo, descrizione e collegamento per una curiosità.

    Il titolo è l'aggancio e basta. La descrizione porta il fatto e lo studio:
    su Pinterest la descrizione è anche il testo su cui la ricerca interna
    indicizza il pin, quindi scriverci "seguici" invece del contenuto
    significa rinunciare all'unico canale di scoperta che la piattaforma ha.

    Come su Bluesky, senza fonte non si pubblica: è la sola cosa che ci
    distingue da mille bacheche di frasi sul cervello.
    """
    base = (base_url or cfg.get("sito.url", "") or "").rstrip("/")
    hook = (fatto["hook"] or "").strip()
    fonte = (fatto["source_hint"] or "").strip()
    if not hook or not fonte:
        raise PinterestError(f"fatto {fatto['id']}: senza aggancio o senza fonte")

    corpo = _taglia(fatto["fact"] or "", MAX_DESCRIZIONE - len(fonte) - 4)
    return {
        "titolo": _taglia(hook, MAX_TITOLO),
        "descrizione": f"{corpo}\n\n{fonte}"[:MAX_DESCRIZIONE],
        "link": f"{base}/f/{_slug(hook)}/" if base else "",
    }


# ─── Pubblicazione ────────────────────────────────────────────────────────────

def pubblica(immagini: List[str], titolo: str, descrizione: str,
             link: str = "", alt: str = "", token: str = "",
             board_id: str = "") -> str:
    """Crea un pin e restituisce il suo id. Solleva PinterestError."""
    if not immagini:
        raise PinterestError("nessuna immagine")

    token = token or _token()
    board_id = board_id or bacheca(token)

    # Da due a cinque immagini diventano un pin sfogliabile; una sola resta un
    # pin normale. Oltre le cinque Pinterest rifiuta, quindi si taglia qui
    # invece di scoprirlo dalla risposta.
    if len(immagini) == 1:
        media = {"source_type": "image_url", "url": immagini[0]}
    else:
        media = {"source_type": "multiple_image_urls",
                 "items": [{"url": u} for u in immagini[:5]],
                 "index": 0}

    corpo: Dict = {
        "board_id": board_id,
        "title": _taglia(titolo, MAX_TITOLO),
        "description": _taglia(descrizione, MAX_DESCRIZIONE),
        "media_source": media,
    }
    if link:
        corpo["link"] = link
    if alt:
        # Il testo alternativo non e' solo accessibilita': Pinterest lo legge
        # per capire cosa c'e' nell'immagine, e le nostre slide sono testo su
        # sfondo, cioe' illeggibili per chi guarda i pixel.
        corpo["alt_text"] = _taglia(alt, 500)

    r = httpx.post(f"{API}/pins", headers=_intestazioni(token), json=corpo, timeout=90)
    if r.status_code >= 400:
        # Il caso che capita davvero e merita un messaggio suo: in Trial i pin
        # si creano ma non li vede nessuno, e chi legge un errore generico
        # cerca il guasto nel codice invece che nel livello di accesso.
        if "trial" in r.text.lower() or r.status_code == 403:
            raise PinterestError(
                f"{r.status_code} {r.text[:200]}\n"
                "  Se l'app è in Trial access i pin non sono pubblici: "
                "serve il passaggio a Standard su developers.pinterest.com."
            )
        raise PinterestError(f"creazione pin → {r.status_code} {r.text[:300]}")
    return r.json().get("id", "")


def url_pubblico(pin_id: str) -> str:
    return f"https://www.pinterest.com/pin/{pin_id}/" if pin_id else ""


# ─── Scelta di cosa pubblicare ────────────────────────────────────────────────

def prossimi(conn, quanti: int = 1) -> List:
    """I caroselli già costruiti e non ancora finiti su Pinterest.

    Si pesca dai `posts` e non dai `facts` perché qui servono le immagini, e
    le immagini esistono solo dove un carosello è stato costruito davvero.
    Sono anche i sei rimasti fermi per il blocco di Meta: su Pinterest quel
    blocco non esiste, e possono uscire lo stesso.

    Il filtro su fact_uses con canale 'pinterest' è ciò che impedisce la
    ripetizione, la stessa tabella che tiene separati tutti gli altri canali.
    """
    return conn.execute(
        """SELECT p.*, f.hook, f.fact, f.source_hint
             FROM posts p JOIN facts f ON f.id = p.fact_id
            WHERE p.image_urls != '[]' AND COALESCE(f.source_hint,'') != ''
              AND p.fact_id NOT IN (
                  SELECT fact_id FROM fact_uses WHERE channel = 'pinterest')
            ORDER BY p.id DESC
            LIMIT ?""",
        (quanti,),
    ).fetchall()


def segna_uso(conn, fact_id: int, pin_id: str) -> None:
    import time

    conn.execute(
        "INSERT OR REPLACE INTO fact_uses (fact_id, channel, used_at, ref) VALUES (?,?,?,?)",
        (fact_id, "pinterest", time.time(), pin_id),
    )
    conn.commit()
