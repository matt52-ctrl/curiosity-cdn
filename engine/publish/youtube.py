"""Pubblicazione su YouTube Shorts.

Perché YouTube e non TikTok: TikTok pretende che l'app superi un audit prima
di poter pubblicare in pubblico — senza, ogni contenuto resta privato o in
bozza, e l'attesa è di settimane. YouTube pubblica subito sul proprio canale.

Un video diventa uno Short automaticamente se è verticale e dura meno di tre
minuti: i nostri sono 1080×1920 da nove secondi, quindi non serve dichiarare
nulla — basta caricarlo.

⚠️ Quota: l'API concede 10.000 unità al giorno e un caricamento ne costa
1.600, cioè **sei video al giorno**. Ai tre reel attuali sta larga, ma non si
può salire molto senza chiedere un aumento a Google.

⚠️ Il refresh token: se la schermata di consenso OAuth resta in stato
"Testing", Google fa scadere il refresh token dopo **sette giorni** e la
pubblicazione si ferma senza preavviso. La schermata va portata in
"Production" — resta senza verifica, mostra un avviso a chi accede, ma il
token non scade più. È il singolo errore che fa fallire questa integrazione.
"""
from __future__ import annotations

import datetime
import json
import re as _re
from pathlib import Path
from typing import Dict, Optional

import httpx

from ..config import DATA_DIR, env

TOKEN_URL = "https://oauth2.googleapis.com/token"
UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"

# Il refresh token vive qui: è l'unica credenziale che non si può rigenerare
# senza rifare l'accesso dal browser.
TOKEN_FILE = DATA_DIR / "youtube_token.json"


class YouTubeError(RuntimeError):
    pass


def _refresh_token() -> str:
    """Legge il refresh token dall'ambiente o dal file salvato."""
    dall_ambiente = env("YOUTUBE_REFRESH_TOKEN")
    if dall_ambiente:
        return dall_ambiente
    if TOKEN_FILE.exists():
        try:
            return json.loads(TOKEN_FILE.read_text()).get("refresh_token", "")
        except json.JSONDecodeError:
            return ""
    return ""


def access_token() -> str:
    """Scambia il refresh token con un token d'accesso valido un'ora."""
    refresh = _refresh_token()
    client_id = env("YOUTUBE_CLIENT_ID")
    client_secret = env("YOUTUBE_CLIENT_SECRET")

    if not (refresh and client_id and client_secret):
        raise YouTubeError(
            "Credenziali YouTube mancanti: servono YOUTUBE_CLIENT_ID, "
            "YOUTUBE_CLIENT_SECRET e YOUTUBE_REFRESH_TOKEN. "
            "Vedi SETUP.md → YouTube."
        )

    r = httpx.post(
        TOKEN_URL,
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh,
            "grant_type": "refresh_token",
        },
        timeout=60,
    )
    if r.status_code >= 400:
        detail = r.json().get("error_description") or r.text[:200]
        if "invalid_grant" in r.text:
            raise YouTubeError(
                f"Refresh token non più valido ({detail}). Succede quando la "
                f"schermata di consenso OAuth è rimasta in stato 'Testing': "
                f"Google li fa scadere dopo 7 giorni. Portala in 'Production' "
                f"e rifai l'autorizzazione una volta sola."
            )
        raise YouTubeError(f"rinnovo token fallito: {detail}")
    return r.json()["access_token"]


def componi_metadati(hook: str, reveal: str, caption: str, tags: list,
                     altre: Optional[list] = None) -> Dict:
    """Titolo e descrizione pensati per YouTube, non riciclati da Instagram.

    Tre regole, tutte verificate contro le pratiche 2026 e tutte diverse da
    quello che serve su Instagram:

    1. **Titolo sotto i 40 caratteri.** Su mobile viene troncato fra i 50 e i
       60, e i video che rendono di piu' stanno intorno alle otto parole.
       Quindi si usa il solo aggancio, non aggancio + rivelazione: la seconda
       parte e' gia' nel video, ripeterla nel titolo lo taglia a meta'.

    2. **Hashtag nella descrizione, mai nel titolo.** I primi tre compaiono
       come link cliccabili sopra il titolo, e un titolo pulito lascia le
       parole chiave in evidenza.

    3. **#Shorts serve ancora.** E' il segnale con cui YouTube classifica il
       formato. Sopra i 15 hashtag totali YouTube li ignora tutti.
    """
    # Solo l'aggancio, tagliato su parola intera.
    titolo = hook.rstrip(".").strip()
    if len(titolo) > 45:
        titolo = titolo[:45].rsplit(" ", 1)[0].rstrip(" ,;:—-")

    # Tre tag tematici piu' #Shorts: quattro in tutto, dentro l'intervallo
    # che YouTube considera.
    scelti = [x for x in tags[:3] if x.lower() != "shorts"] + ["Shorts"]

    # Ponte fra le piattaforme. Chi arriva da YouTube deve poter trovare
    # l'account Instagram senza cercarlo, e viceversa: il filigrana
    # @oddlywireddaily e' gia' impresso nel video, quindi chi guarda su
    # YouTube vede l'handle anche senza leggere la descrizione. Qui si
    # aggiunge il collegamento diretto, per chi invece la apre.
    from ..config import cfg

    # ⚠️ La richiesta di iscriversi NON sta qui: arriva dentro `caption`, che
    # il chiamante compone con `lines.corpo_didascalia(..., canale="youtube")`.
    # Prima che quel parametro esistesse la descrizione ereditava `caption.cta`
    # tale e quale — una riga scritta per Instagram, con la chiocciola
    # Instagram — e sommata al rimando qui sotto chiedeva DUE VOLTE di andare
    # altrove senza chiedere mai un'iscrizione. Chi cambia il chiamante e si
    # dimentica `canale` fa tornare quel difetto, e non se ne accorge nessuno.
    handle = (cfg.get("brand.handle", "") or "").lstrip("@")
    # Chiocciola e non indirizzo: nelle descrizioni degli Short YouTube NON
    # rende cliccabili i link, per scelta anti-spam. Un URL che non si clicca
    # sembra rotto; una chiocciola si legge come un nome e si cerca — che è
    # l'unica azione davvero possibile lì.
    ponte = (f"\n\nOne of these every day on Instagram too: @{handle}"
             if handle else "")

    descrizione = caption.strip()

    # Il video lungo contiene piu' curiosita', ma la didascalia racconta solo
    # la prima. Elencare anche le altre non e' un vezzo: YouTube indicizza la
    # descrizione, e senza questo il video risulta a tema solo dell'apertura
    # mentre due terzi del parlato riguardano altro.
    if altre:
        righe = "\n".join(f"· {x.rstrip('.')}." for x in altre if x)
        if righe:
            descrizione += "\n\nAlso in this video:\n" + righe

    descrizione += ponte
    descrizione += "\n\n" + " ".join("#" + x for x in scelti)

    return {
        "title": titolo,
        "description": descrizione,
        "tags": tags[:15],
    }


def publish(
    video: Path,
    title: str,
    description: str,
    tags: Optional[list] = None,
    privacy: str = "public",
    quando: Optional[float] = None,
) -> str:
    """Carica un video come Short pubblico. Restituisce l'id del video.

    Con `quando` (epoch UTC) il video viene caricato privato e reso pubblico da
    YouTube all'ora indicata. Due vantaggi rispetto a caricare al momento
    giusto: l'ora di uscita e' esatta, mentre il programmatore di GitHub parte
    con ritardi misurati fino a 64 minuti; e l'intera giornata si vede gia'
    caricata la mattina, quindi un guasto si nota subito invece che a sera.
    """
    if not video.exists():
        raise YouTubeError(f"file non trovato: {video}")

    token = access_token()

    metadata: Dict = {
        "snippet": {
            # 100 caratteri è il limite di YouTube; oltre, l'API rifiuta.
            "title": title[:100],
            "description": description[:4900],
            "tags": (tags or [])[:15],
            # 22 = People & Blogs. Le categorie sono obbligatorie e questa è
            # quella che YouTube stesso consiglia per i contenuti divulgativi
            # brevi non riconducibili a Education formale.
            "categoryId": "22",
        },
        "status": {
            # `private` serve alle prove: permette di verificare che l'intera
            # catena funzioni senza mettere nulla davanti al pubblico.
            # E' anche l'unico stato che YouTube accetta insieme a publishAt:
            # un video gia' pubblico non si puo' programmare.
            "privacyStatus": "private" if quando else privacy,
            "selfDeclaredMadeForKids": False,
        },
    }
    if quando:
        metadata["status"]["publishAt"] = (
            datetime.datetime.fromtimestamp(quando, datetime.timezone.utc)
            .isoformat().replace("+00:00", "Z")
        )

    # Caricamento in due passi: prima i metadati, poi il file. L'endpoint
    # "resumable" è l'unico che regge file grandi senza timeout.
    inizio = httpx.post(
        UPLOAD_URL,
        params={"uploadType": "resumable", "part": "snippet,status"},
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "X-Upload-Content-Type": "video/mp4",
            "X-Upload-Content-Length": str(video.stat().st_size),
        },
        json=metadata,
        timeout=90,
    )
    if inizio.status_code >= 400:
        raise YouTubeError(f"apertura caricamento fallita: {inizio.text[:250]}")

    sessione = inizio.headers.get("Location")
    if not sessione:
        raise YouTubeError("YouTube non ha restituito l'URL di caricamento")

    with open(video, "rb") as f:
        r = httpx.put(
            sessione,
            content=f.read(),
            headers={"Content-Type": "video/mp4"},
            timeout=600,
        )
    if r.status_code >= 400:
        if "quotaExceeded" in r.text:
            raise YouTubeError(
                "Quota YouTube esaurita: 10.000 unità al giorno, e un "
                "caricamento ne costa 1.600 (sei video al giorno). "
                "Si azzera a mezzanotte ora del Pacifico."
            )
        raise YouTubeError(f"caricamento fallito: {r.text[:250]}")

    return r.json()["id"]


def quota_note() -> str:
    return "6 caricamenti al giorno (10.000 unità, 1.600 a video)"


# ─── Commenti ─────────────────────────────────────────────────────────────────
# Servono il permesso `youtube.force-ssl`, che è più ampio del solo
# caricamento. Chi ha autorizzato prima che questa parte esistesse ha un token
# senza quel permesso: le chiamate qui sotto falliscono con 403 e il ciclo
# tira dritto. Si sistema rilanciando una volta `python3 setup_youtube.py`.

API = "https://www.googleapis.com/youtube/v3"


class PermessoMancante(YouTubeError):
    """Il token c'è ma non copre i commenti — non è un guasto, è un setup vecchio."""


def _intestazioni() -> Dict:
    return {"Authorization": f"Bearer {access_token()}"}


def leggi_commenti(video_id: str, limite: int = 25) -> list:
    """Commenti di primo livello sotto un video, nella forma usata da engine.comments.

    Si normalizzano i campi ai nomi di Instagram (`id`, `text`, `username`)
    perché la parte che redige le risposte non deve sapere da quale
    piattaforma arriva il commento: la voce e le regole sono le stesse.
    """
    try:
        r = httpx.get(
            f"{API}/commentThreads",
            params={"part": "snippet", "videoId": video_id,
                    "maxResults": min(limite, 100), "order": "time",
                    "textFormat": "plainText"},
            headers=_intestazioni(), timeout=40,
        )
    except YouTubeError:
        raise
    except Exception as exc:
        print(f"    lettura commenti YouTube fallita: {exc}")
        return []

    if r.status_code == 403:
        testo = r.text[:200]
        if "insufficient" in testo.lower() or "Scope" in testo:
            raise PermessoMancante(
                "il token YouTube non copre i commenti (manca youtube.force-ssl). "
                "Rilancia una volta:  python3 setup_youtube.py"
            )
        # I commenti disattivati sul video non sono un errore da segnalare.
        return []
    if r.status_code >= 400:
        print(f"    lettura commenti YouTube: {r.status_code} {r.text[:120]}")
        return []

    fuori = []
    for t in r.json().get("items", []):
        c = t["snippet"]["topLevelComment"]
        s = c["snippet"]
        fuori.append({
            "id": c["id"],
            "text": s.get("textOriginal", ""),
            "username": s.get("authorDisplayName", ""),
            "timestamp": s.get("publishedAt", ""),
            # Serve a non rispondere due volte: YouTube dice già quante
            # risposte ha il thread, Instagram va interrogato a parte.
            "reply_count": t["snippet"].get("totalReplyCount", 0),
        })
    return fuori


def rispondi_commento(comment_id: str, testo: str) -> Optional[str]:
    r = httpx.post(
        f"{API}/comments",
        params={"part": "snippet"},
        json={"snippet": {"parentId": comment_id, "textOriginal": testo[:9000]}},
        headers=_intestazioni(), timeout=40,
    )
    if r.status_code == 403:
        raise PermessoMancante(
            "il token YouTube non permette di rispondere (manca youtube.force-ssl). "
            "Rilancia una volta:  python3 setup_youtube.py"
        )
    if r.status_code >= 400:
        raise YouTubeError(f"risposta rifiutata: {r.status_code} {r.text[:200]}")
    return r.json().get("id")



def commenta_video(video_id: str, testo: str) -> Optional[str]:
    """Il primo commento sotto un proprio video, scritto dal canale.

    Perche' esiste. Su 18 Short recenti: 3.786 visualizzazioni, 36 like, UN
    commento. La ritenzione non era il problema — quei video si guardavano dal
    35 all'88 per cento — e nemmeno la richiesta, che dal 20 agosto c'e' e sta
    scritta sul fotogramma finale. Il problema e' che sotto ogni video c'era
    una sezione commenti vuota, e una sezione vuota comunica che qui non si
    commenta. Rispondere a qualcuno costa un tocco; aprire il discorso da soli
    ne costa molti di piu', e quasi nessuno lo fa.

    Perche' proprio noi e perche' quel testo. Il commento non chiede niente di
    nuovo: nomina lo studio da cui viene la curiosita' e rifa' la domanda. E'
    l'unica cosa che questo canale ha e le pagine di curiosita' non hanno, e
    nel posto dove serve — chi arriva ai commenti sta gia' cercando di capire
    se fidarsi.

    Non si puo' fissare in cima: l'API di YouTube non espone il "pin", e' una
    funzione della sola interfaccia. Su un video con pochi commenti il proprio
    compare comunque fra i primi, e chiedere a Mattia di fissarlo a mano
    sarebbe lavoro manuale quotidiano — cioe' esattamente cio' che questo
    progetto non fa.

    ⚠️ Il video dev'essere gia' PUBBLICO. Gli Short escono programmati, e su un
    video ancora privato YouTube rifiuta il commento (403). Per questo la
    chiamata sta nel ciclo dei commenti, che gira sui contenuti gia' usciti, e
    non subito dopo il caricamento.
    """
    if not (video_id and testo.strip()):
        return None
    r = httpx.post(
        f"{API}/commentThreads",
        params={"part": "snippet"},
        json={"snippet": {"videoId": video_id, "topLevelComment": {
            "snippet": {"textOriginal": testo[:9000]}}}},
        headers=_intestazioni(), timeout=40,
    )
    if r.status_code == 403:
        testo_err = r.text[:200]
        if "insufficient" in testo_err.lower() or "Scope" in testo_err:
            raise PermessoMancante(
                "il token YouTube non copre i commenti (manca youtube.force-ssl). "
                "Rilancia una volta:  python3 setup_youtube.py"
            )
        # Commenti chiusi sul video, o video non ancora pubblico: non e' un
        # guasto da segnalare, e' il caso normale di un video programmato.
        print(f"    primo commento non accettato: {testo_err[:100]}")
        return None
    if r.status_code >= 400:
        print(f"    primo commento rifiutato: {r.status_code} {r.text[:120]}")
        return None
    return (r.json().get("snippet", {}).get("topLevelComment", {}).get("id")
            or r.json().get("id"))

# ─── Statistiche ──────────────────────────────────────────────────────────────

ANALYTICS = "https://youtubeanalytics.googleapis.com/v2/reports"


def statistiche(video_ids: list) -> Dict[str, Dict]:
    """Visualizzazioni, like e commenti per un gruppo di video.

    Una sola chiamata per un massimo di 50 video: interrogarli uno alla volta
    costerebbe una unità di quota ciascuno e, con tre pubblicazioni al giorno,
    l'elenco cresce senza smettere.
    """
    if not video_ids:
        return {}
    r = httpx.get(
        f"{API}/videos",
        params={"part": "statistics", "id": ",".join(video_ids[:50])},
        headers=_intestazioni(), timeout=40,
    )
    if r.status_code == 403:
        raise PermessoMancante(
            "il token YouTube non permette di leggere le statistiche. "
            "Rilancia una volta:  python3 setup_youtube.py"
        )
    if r.status_code >= 400:
        raise YouTubeError(f"statistiche rifiutate: {r.status_code} {r.text[:160]}")

    fuori = {}
    for v in r.json().get("items", []):
        s = v.get("statistics", {})
        fuori[v["id"]] = {
            "views": int(s.get("viewCount", 0) or 0),
            "likes": int(s.get("likeCount", 0) or 0),
            "comments": int(s.get("commentCount", 0) or 0),
        }
    return fuori


def ritenzione(giorni: int = 30) -> Dict[str, Dict]:
    """Percentuale media di video guardata, per video.

    È il numero che conta davvero. YouTube decide se continuare a mostrare uno
    Short in base a quanto della clip viene guardato: un video con poche
    visualizzazioni ma alta percentuale viene rilanciato, uno con tante
    visualizzazioni e bassa percentuale muore lì. I like non entrano nel
    calcolo, ed è per questo che imparare dai like porta fuori strada.

    Richiede il permesso `yt-analytics.readonly`, che è separato da quello di
    caricamento: senza, si solleva PermessoMancante e il resto prosegue.
    """
    import datetime as dt

    fine = dt.date.today()
    inizio = fine - dt.timedelta(days=giorni)
    r = httpx.get(
        ANALYTICS,
        params={
            "ids": "channel==MINE",
            "startDate": inizio.isoformat(),
            "endDate": fine.isoformat(),
            # `subscribersGained` con `dimensions=video` funziona: verificato
            # con una chiamata reale il 21 agosto 2026 (200, colonna presente).
            # Da qualche parte era finito scritto il contrario.
            "metrics": ("views,averageViewPercentage,averageViewDuration,"
                        "subscribersGained"),
            "dimensions": "video",
            "sort": "-views",
            "maxResults": 200,
        },
        headers=_intestazioni(), timeout=60,
    )
    if r.status_code in (401, 403):
        raise PermessoMancante(
            "il token YouTube non copre le statistiche di visione "
            "(manca yt-analytics.readonly). Rilancia una volta:  "
            "python3 setup_youtube.py"
        )
    if r.status_code >= 400:
        raise YouTubeError(f"analytics rifiutata: {r.status_code} {r.text[:160]}")

    d = r.json()
    # Le colonne arrivano descritte a parte: leggerle per posizione fissa
    # significherebbe rompersi in silenzio il giorno che Google ne aggiunge una.
    colonne = [c["name"] for c in d.get("columnHeaders", [])]
    fuori = {}
    for riga in d.get("rows", []):
        v = dict(zip(colonne, riga))
        fuori[v["video"]] = {
            "views": int(v.get("views", 0) or 0),
            "avg_view_pct": float(v.get("averageViewPercentage", 0) or 0),
            "avg_view_sec": float(v.get("averageViewDuration", 0) or 0),
            "sub_gained": int(v.get("subscribersGained", 0) or 0),
        }
    return fuori


def _durate(video_ids: list) -> Dict[str, float]:
    """Durata in secondi dei video, dalla Data API. Serve a `tenuta_iniziale`.

    Perche' non ci basta la durata che conosciamo al montaggio: quella e' la
    durata del file che abbiamo caricato, e YouTube ritranscodifica. Le due
    coincidono quasi sempre, ma "quasi" su una divisione diventa un secchiello
    di scarto, e qui i secchielli sono l'unita' di misura.
    """
    fuori: Dict[str, float] = {}
    for i in range(0, len(video_ids), 50):        # il massimo per chiamata
        r = httpx.get(
            "https://www.googleapis.com/youtube/v3/videos",
            headers=_intestazioni(),
            params={"part": "contentDetails", "id": ",".join(video_ids[i:i + 50])},
            timeout=30,
        )
        if r.status_code >= 400:
            continue
        for it in r.json().get("items", []):
            testo = it.get("contentDetails", {}).get("duration", "")
            m = _re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:([\d.]+)S)?", testo)
            if not m:
                continue
            ore, minuti, sec = (float(g or 0) for g in m.groups())
            fuori[it["id"]] = ore * 3600 + minuti * 60 + sec
    return fuori


def tenuta_iniziale(video_ids: list, secondi: float = 3.0,
                    giorni: int = 90) -> Dict[str, float]:
    """Quanti spettatori sono ancora li' al terzo secondo, per video.

    E' la misura su cui si decide la prova sull'apertura, e vale la pena
    sapere esattamente cosa fa, perche' due dettagli la separano da un numero
    senza senso.

    PRIMO: la curva arriva in RATIO, non in secondi. `elapsedVideoTimeRatio`
    da' cento punti da 0,01 a 1,00, cioe' centesimi di video. Tre secondi non
    sono lo stesso punto in tutti i video: su uno da 13 secondi cadono al 23%,
    su uno da 35 all'8,5%. E' una differenza che conta il doppio adesso, con
    la prova sulla lunghezza in corso che quei due video li produce apposta.
    Quindi la conversione si fa con la durata VERA di ciascun video, e il
    valore si interpola fra i due punti vicini invece di prendere il piu'
    vicino: un secchiello e' l'1% del video, su tredici secondi sono 0,13s, e
    arrotondare li' dentro sposterebbe la misura piu' di quanto la prova
    cerchi di misurare.

    SECONDO, ed e' il punto che rende utilizzabile tutto il resto: il valore
    si divide per il PRIMO punto della curva, non si legge in assoluto. Sugli
    Short quella curva parte sopra 1 — misurati 1,86, 1,28, 1,13, 1,07 — perche'
    il video va in loop e chi resta ripassa sullo stesso istante piu' volte.
    Il valore assoluto quindi non dice "quanti sono rimasti" ma "quanti sono
    rimasti, moltiplicato per quanto ha girato il video", e quel secondo
    fattore e' proprio quello che vogliamo togliere di mezzo. Dividendo per il
    primo punto della STESSA curva si elide, e resta la frazione.

    Ritorna solo i video per cui la curva esiste davvero. YouTube non la da'
    finche' il video non ha abbastanza visite, e restituire zero per quelli
    significherebbe farli entrare nella media come pessimi invece che come
    ancora-non-pervenuti.
    """
    import datetime as dt

    if not video_ids:
        return {}
    durate = _durate(list(video_ids))
    fine = dt.date.today()
    inizio = fine - dt.timedelta(days=giorni)
    fuori: Dict[str, float] = {}

    for vid in video_ids:
        durata = durate.get(vid, 0)
        # Una durata sotto i secondi che cerchiamo renderebbe la domanda priva
        # di senso: "quanti restano al terzo secondo" su un video da due.
        if durata <= secondi:
            continue
        try:
            r = httpx.get(
                ANALYTICS,
                params={
                    "ids": "channel==MINE",
                    "startDate": inizio.isoformat(),
                    "endDate": fine.isoformat(),
                    "metrics": "audienceWatchRatio",
                    "dimensions": "elapsedVideoTimeRatio",
                    "filters": f"video=={vid}",
                    "sort": "elapsedVideoTimeRatio",
                    "maxResults": 200,
                },
                headers=_intestazioni(), timeout=60,
            )
            if r.status_code >= 400:
                continue
            punti = sorted((float(a), float(b)) for a, b in r.json().get("rows", []))
        except Exception:
            continue
        if len(punti) < 2 or punti[0][1] <= 0:
            continue

        base = punti[0][1]
        quota = secondi / durata
        valore = punti[-1][1]
        prec = punti[0]
        for x, y in punti:
            if x >= quota:
                valore = y if x == prec[0] else prec[1] + \
                    (quota - prec[0]) / (x - prec[0]) * (y - prec[1])
                break
            prec = (x, y)
        fuori[vid] = valore / base
    return fuori


# ─── Playlist ─────────────────────────────────────────────────────────────────
#
# Perché serve: un episodio isolato finisce quando finisce, e lo spettatore
# torna al feed generale. Dentro una playlist YouTube propone il successivo, e
# la sessione continua sul nostro canale invece che su un altro. Il tempo di
# sessione è uno dei segnali con cui YouTube decide chi spingere, ed è l'unico
# che si può migliorare senza toccare il contenuto.

def playlist_id(titolo: str, descrizione: str = "") -> Optional[str]:
    """Trova la playlist per titolo, o la crea. Ritorna l'id.

    Cercarla per titolo invece di salvarne l'id: se qualcuno la cancella a
    mano da Studio, al giro dopo viene semplicemente ricreata invece di
    fallire per un id che non esiste piu'.
    """
    h = _intestazioni()
    r = httpx.get(f"{API}/playlists",
                  params={"part": "snippet", "mine": "true", "maxResults": 50},
                  headers=h, timeout=40)
    if r.status_code == 200:
        for x in r.json().get("items", []):
            if x["snippet"]["title"].strip().lower() == titolo.strip().lower():
                return x["id"]
    elif r.status_code == 403:
        raise PermessoMancante("il token non permette di leggere le playlist")

    c = httpx.post(
        f"{API}/playlists", params={"part": "snippet,status"},
        headers={**h, "Content-Type": "application/json"},
        json={"snippet": {"title": titolo, "description": descrizione},
              "status": {"privacyStatus": "public"}},
        timeout=40,
    )
    if c.status_code >= 300:
        print(f"    playlist non creata: {c.status_code} {c.text[:130]}")
        return None
    return c.json().get("id")


def aggiungi_a_playlist(playlist: str, video_id: str) -> bool:
    r = httpx.post(
        f"{API}/playlistItems", params={"part": "snippet"},
        headers={**_intestazioni(), "Content-Type": "application/json"},
        json={"snippet": {"playlistId": playlist,
                          "resourceId": {"kind": "youtube#video",
                                         "videoId": video_id}}},
        timeout=40,
    )
    if r.status_code >= 300:
        print(f"    non aggiunto alla playlist: {r.status_code} {r.text[:110]}")
        return False
    return True


def imposta_miniatura(video_id: str, immagine: "Path") -> bool:
    """Carica la miniatura personalizzata.

    ⚠️ Richiede il canale VERIFICATO (verifica telefonica su youtube.com/verify).
    Senza, YouTube risponde 403 e resta il fotogramma estratto automaticamente —
    che su un video lungo è quasi sempre una dissolvenza, cioè il peggior
    biglietto da visita possibile.
    """
    from pathlib import Path as _P

    immagine = _P(immagine)
    if not immagine.exists():
        return False
    r = httpx.post(
        "https://www.googleapis.com/upload/youtube/v3/thumbnails/set",
        params={"videoId": video_id},
        headers={**_intestazioni(), "Content-Type": "image/jpeg"},
        content=immagine.read_bytes(), timeout=120,
    )
    if r.status_code == 403:
        print("    miniatura rifiutata: il canale non è verificato "
              "(youtube.com/verify, due minuti, una volta sola)")
        return False
    if r.status_code >= 300:
        print(f"    miniatura rifiutata: {r.status_code} {r.text[:110]}")
        return False
    return True
