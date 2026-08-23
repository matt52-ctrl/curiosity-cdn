"""Pubblicazione su TikTok (photo post) via Content Posting API.

⚠️  Leggi questo prima di attivarlo.

TikTok non è simmetrico a Instagram. Due muri reali:

1. **Audit dell'app.** Finché il tuo client non passa l'audit TikTok, TUTTO
   ciò che pubblichi è forzato a visibilità privata (`SELF_ONLY`) e ricevi
   l'errore `unaudited_client_can_only_post_to_private_accounts` se provi a
   fare altrimenti. L'audit richiede 2-4 settimane e più giri di feedback.

2. **Verifica del dominio.** Le foto si caricano solo via `PULL_FROM_URL`, e
   TikTok pretende che tu abbia verificato la proprietà del dominio da cui
   servi le immagini. `raw.githubusercontent.com` non è tuo → non lo puoi
   verificare. Per TikTok serve un dominio tuo (backend Cloudinary con
   dominio custom, o un tuo bucket con CNAME).

Perciò il default in config.yaml è `mode: inbox`: le foto arrivano nella tua
inbox TikTok come bozza e sei tu a premere "Post" dall'app. È l'unico modo
onesto di partire senza audit. Un giro di 10 secondi al giorno sul telefono.

Limiti: max 35 foto per post, 6 richieste/minuto per access token,
5 upload pendenti per 24h.
"""
from __future__ import annotations

from typing import Dict, List

import httpx

from ..config import cfg, require_env

API = "https://open.tiktokapis.com/v2"


class TikTokError(RuntimeError):
    pass


def publish_photos(image_urls: List[str], title: str, description: str) -> str:
    """Restituisce il publish_id. In modalità `inbox` il post resta bozza."""
    token = require_env("TIKTOK_ACCESS_TOKEN")
    mode = cfg.get("publish.tiktok.mode", "inbox")

    if len(image_urls) > 35:
        raise TikTokError(f"{len(image_urls)} foto: il massimo è 35")

    post_mode = "DIRECT_POST" if mode == "direct" else "MEDIA_UPLOAD"

    payload: Dict = {
        "media_type": "PHOTO",
        "post_mode": post_mode,
        "source_info": {
            "source": "PULL_FROM_URL",
            "photo_cover_index": 0,
            "photo_images": image_urls,
        },
        "post_info": {
            "title": title[:90],          # limite: 90 rune UTF-16
            "description": description[:4000],
        },
    }

    with httpx.Client(timeout=120) as client:
        resp = client.post(
            f"{API}/post/publish/content/init/",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json; charset=UTF-8",
            },
            json=payload,
        )

    if resp.status_code >= 400:
        body = resp.text
        if "url_ownership_unverified" in body:
            raise TikTokError(
                "TikTok non riconosce il dominio delle immagini. Devi verificare "
                "la proprietà del prefisso URL nel developer portal. "
                "raw.githubusercontent.com non è verificabile — serve un dominio tuo."
            )
        if "unaudited_client" in body:
            raise TikTokError(
                "Il client non ha passato l'audit: puoi pubblicare solo in privato. "
                "Usa mode: inbox in config.yaml finché l'audit non è approvato."
            )
        raise TikTokError(f"{resp.status_code} {body}")

    data = resp.json()
    if data.get("error", {}).get("code") not in (None, "ok"):
        raise TikTokError(str(data["error"]))
    return data["data"]["publish_id"]


def status(publish_id: str) -> Dict:
    token = require_env("TIKTOK_ACCESS_TOKEN")
    with httpx.Client(timeout=30) as client:
        resp = client.post(
            f"{API}/post/publish/status/fetch/",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json; charset=UTF-8",
            },
            json={"publish_id": publish_id},
        )
    resp.raise_for_status()
    return resp.json()


# ─── Preparazione dei lotti ───────────────────────────────────────────────────
# Non si pubblica via API, e non è una rinuncia: l'API può solo depositare una
# bozza nell'inbox dell'app, mentre il programmatore di TikTok vive in TikTok
# Studio, sul desktop. Una bozza nell'inbox non si può passare a Studio — sono
# due percorsi che non si toccano. Passando direttamente dai file si evitano
# l'app sviluppatore, l'audit di 2-4 settimane e la verifica del dominio, e si
# ottiene in cambio l'unica cosa che serve davvero: la programmazione.

def componi_didascalia(frasi: list) -> Dict[str, str]:
    """Didascalia, hashtag e commento da fissare, per un video del lotto.

    La didascalia resta corta di proposito. Su TikTok viene troncata dopo poche
    righe e il resto sta sotto un "altro" che quasi nessuno apre: metterci il
    contesto significa scriverlo per nessuno. L'aggancio della prima curiosità
    fa da titolo, il resto lo dice il video.
    """
    apertura = frasi[0]["hook"].rstrip(".").strip()

    # Gli hashtag stanno in fondo e sono pochi. Venti hashtag generici erano
    # una tattica di anni fa: oggi diluiscono il segnale su cui TikTok decide
    # a chi mostrarti, ed è meglio essere classificati con precisione su tre
    # temi che vagamente su venti.
    tag = ["psychology", "humanbehavior", "psychologyfacts", "learnontiktok"]

    def _chiave(h: str) -> str:
        """Forma normalizzata per riconoscere lo stesso hashtag scritto in due modi.

        Il caso vero, non teorico: le curiosita' sono scritte in inglese
        britannico e gli hashtag fissi in americano, quindi #humanbehaviour e
        #humanbehavior finivano entrambi nella stessa lista bruciando uno dei
        cinque posti per dire due volte la stessa cosa.
        """
        return h.lower().replace("our", "or").rstrip("s")

    propri = []
    visti = {_chiave(x) for x in tag}
    for f in frasi:
        for h in (f.get("hashtags") or [])[:2]:
            h = h.lstrip("#")
            if _chiave(h) not in visti:
                visti.add(_chiave(h))
                propri.append(h)
    scelti = (propri[:2] + tag)[:5]

    # "three more" era scritto a mano e valeva solo per i video da quattro
    # curiosita'. Bastava cambiare `publish.tiktok.facts_per_video` perche' la
    # didascalia annunciasse un numero diverso da quello nel video — una bugia
    # verificabile in dieci secondi da chiunque guardi.
    altre = max(0, len(frasi) - 1)
    numeri = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five"}
    coda = (f" — and {numeri.get(altre, altre)} more thing"
            f"{'' if altre == 1 else 's'} your mind does without asking"
            if altre else "")

    # La richiesta sta in didascalia e non solo nel commento fissato: il
    # commento e' il ponte verso gli altri canali — dove trovare il resto — e
    # non chiede niente. Prima di oggi il video non chiedeva mai nulla, ne'
    # nel filmato ne' nel testo.
    richiesta = (cfg.get("cta.testo.tiktok", "") or "").strip()

    pezzi = [f"{apertura}{coda}."]
    if richiesta:
        pezzi.append(richiesta)
    pezzi.append(" ".join("#" + x for x in scelti))

    return {
        "didascalia": "\n\n".join(pezzi),
        # Il commento fissato è il ponte verso le altre due piattaforme. Sta
        # nei commenti e non nella didascalia per due motivi: la didascalia
        # viene troncata, e TikTok tratta con più sospetto ciò che spinge
        # fuori dalla piattaforma quando sta nel corpo del post.
        "commento": _ponte(),
    }


def _ponte() -> str:
    """Il commento da fissare sotto al video: dove trovare il resto.

    Il sito viene per primo, e non per vanita': e' l'unico posto dove la
    curiosita' porta lo studio con autore e anno, ed e' l'unica destinazione
    che non appartiene a nessuna piattaforma. Instagram e YouTube mandano il
    pubblico da una parte all'altra della stessa recinzione; il sito e' l'unica
    porta che resta aperta se una delle due cambia idea domani.

    Sta nel commento fissato e non nella didascalia per due motivi gia' validi
    prima: la didascalia viene troncata dopo poche righe, e TikTok tratta con
    piu' sospetto cio' che spinge fuori quando sta nel corpo del post.
    """
    ig = (cfg.get("brand.handle", "") or "").lstrip("@")
    yt = (cfg.get("brand.youtube", "") or "").lstrip("@")
    sito = (cfg.get("sito.url", "") or "").rstrip("/")
    pezzi = ["One checked fact a day."]
    if sito:
        # Senza "https://": scritto per esteso l'indirizzo occupa piu' spazio e
        # non diventa comunque cliccabile nei commenti di TikTok.
        pezzi.append(f"Every study behind every claim: {sito.split('//')[-1]}")
    if ig:
        pezzi.append(f"Longer versions on Instagram: @{ig}")
    if yt:
        pezzi.append(f"Full ones on YouTube: @{yt}")
    return "\n".join(pezzi)


# ─── Caricamento video nell'inbox ─────────────────────────────────────────────
#
# Perché questa strada e non il caricamento a mano da TikTok Studio: il
# caricamento multiplo su Studio richiede **1.000 follower**. Sotto quella
# soglia si carica un video alla volta, e tredici video sono mezz'ora di
# trascinamenti. L'API invece deposita le bozze da sola, e non richiede alcun
# audit finché si resta sull'inbox.
#
# Cosa si perde: la programmazione all'orario esatto. Le bozze arrivano nell'app
# e sei tu a premere "pubblica". Su TikTok pesa poco — la distribuzione avviene
# su ore e giorni, non al momento della pubblicazione — e vale ampiamente il
# cambio: due minuti al giorno invece di mezz'ora ogni tre giorni.
#
# ⚠️  Il video si carica a PEZZI, non con PULL_FROM_URL: quest'ultimo pretende
# la verifica del dominio da cui servi il file, e `raw.githubusercontent.com`
# non è tuo. Con FILE_UPLOAD si mandano i byte e il problema non esiste.

TOKEN_URL = f"{API}/oauth/token/"
INBOX_INIT = f"{API}/post/publish/inbox/video/init/"
# La pubblicazione vera. Stesso protocollo dell'inbox, indirizzo diverso: e'
# l'unica differenza di trasporto fra "te lo metto fra le bozze" e "lo metto
# online". Tutto il resto della distanza fra le due e' burocrazia — lo scope
# `video.publish` e l'audit dell'app.
DIRECT_INIT = f"{API}/post/publish/video/init/"
CREATOR_INFO = f"{API}/post/publish/creator_info/query/"
STATO = f"{API}/post/publish/status/fetch/"

# Vincoli di TikTok sui pezzi: minimo 5 MB, massimo 64 MB. I nostri video
# stanno fra 7 e 25 MB, quindi vanno sempre in un pezzo solo — ma il codice
# regge anche i casi fuori scala, che prima o poi capitano.
MIN_PEZZO = 5 * 1024 * 1024
MAX_PEZZO = 64 * 1024 * 1024


class TikTokError(RuntimeError):
    pass


class LimiteRaggiunto(TikTokError):
    """Le 5 bozze pendenti nelle 24 ore sono esaurite: non è un guasto."""


class NonAuditata(TikTokError):
    """L'app non ha passato l'audit: la pubblicazione diretta non è concessa.

    Non è un guasto ed è per questo che ha una classe sua: è lo stato normale
    di ogni app finché TikTok non la approva, e va distinta perché chi chiama
    deve ripiegare sulla bozza invece di fermarsi.
    """


class ScopeMancante(TikTokError):
    """Il permesso non è mai stato autorizzato: `scope_not_authorized`.

    Diverso da `NonAuditata`, e la differenza dice cosa fare. Qui il permesso
    non è stato nemmeno CHIESTO — si aggiunge lo scope all'app e si rifà
    l'autorizzazione dal browser, cosa che dipende da noi. `NonAuditata`
    invece dipende da TikTok e si può solo aspettare.
    """


class CredenzialiAssenti(TikTokError):
    """Le credenziali non ci sono ancora — diverso dall'averle sbagliate.

    La distinzione conta e non è pedanteria. Chi chiama può accendere gli
    orari PRIMA che l'app sviluppatore esista: finché è così il giro deve
    finire in silenzio e uscire a zero, o GitHub manda una mail di workflow
    fallito ogni notte finché non ti stanchi e spegni tutto. Un token invece
    presente e rifiutato è un guasto vero e deve restare rumoroso.
    """


def _token_accesso() -> str:
    """Scambia il refresh token con un token d'accesso valido 24 ore.

    Il refresh token dura 365 giorni, quindi l'autorizzazione dal browser si fa
    una volta sola all'anno. L'access token invece scade ogni giorno e non ha
    senso salvarlo: si richiede a ogni giro, costa una chiamata.
    """
    from ..config import env

    chiave = env("TIKTOK_CLIENT_KEY")
    segreto = env("TIKTOK_CLIENT_SECRET")
    refresh = env("TIKTOK_REFRESH_TOKEN")
    if not (chiave and segreto and refresh):
        raise CredenzialiAssenti(
            "Mancano TIKTOK_CLIENT_KEY, TIKTOK_CLIENT_SECRET o "
            "TIKTOK_REFRESH_TOKEN in .env. Lancia:  python3 setup_tiktok.py"
        )

    r = httpx.post(
        TOKEN_URL,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "client_key": chiave,
            "client_secret": segreto,
            "grant_type": "refresh_token",
            "refresh_token": refresh,
        },
        timeout=60,
    )
    d = r.json()
    if r.status_code >= 400 or "access_token" not in d:
        raise TikTokError(
            f"rinnovo del token fallito: {d.get('error_description') or r.text[:200]}. "
            f"Se dice che il refresh token non è valido, rilancia setup_tiktok.py."
        )
    return d["access_token"]


def _pezzatura(dimensione: int) -> tuple:
    """Quanti pezzi e di che dimensione, secondo le regole di TikTok.

    Le regole non sono intuitive: `total_chunk_count` è la divisione arrotondata
    per DIFETTO, quindi l'ultimo pezzo è più grande degli altri, non più
    piccolo. E i video sotto i 5 MB vanno mandati interi, perché il minimo di
    5 MB per pezzo non si applica quando il pezzo è tutto il video.
    """
    if dimensione < MIN_PEZZO:
        return dimensione, 1
    if dimensione <= MAX_PEZZO:
        return dimensione, 1
    pezzo = MAX_PEZZO
    return pezzo, max(1, dimensione // pezzo)


def _init(url_init: str, corpo: dict, token: str) -> tuple:
    """Apre un caricamento e ritorna (upload_url, publish_id).

    Sta fuori dalle due funzioni che la usano perche' l'inbox e la
    pubblicazione diretta differiscono SOLO per l'indirizzo e per il blocco
    `post_info`: tutto il resto — la pezzatura, la lettura degli errori, la
    distinzione fra tetto raggiunto e guasto — e' identico, e duplicarlo
    significherebbe correggere i bug una volta sola su due.
    """
    r = httpx.post(
        url_init,
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json; charset=UTF-8"},
        json=corpo,
        timeout=60,
    )
    d = r.json()
    errore = (d.get("error") or {}).get("code", "ok")
    if errore not in ("ok", "", None):
        messaggio = (d.get("error") or {}).get("message", "")
        # Il tetto delle 5 bozze in 24 ore non e' un guasto: e' il ritmo
        # previsto. Va distinto, o il ciclo manderebbe un allarme ogni giorno.
        if "spam" in errore.lower() or "rate_limit" in errore.lower():
            raise LimiteRaggiunto(f"tetto giornaliero raggiunto ({errore})")
        if "unaudited_client" in errore or "unaudited_client" in messaggio:
            raise NonAuditata(f"{errore} — {messaggio[:160]}")
        raise TikTokError(f"init rifiutata: {errore} — {messaggio[:160]}")

    dati = d.get("data", {})
    url = dati.get("upload_url")
    publish_id = dati.get("publish_id")
    if not (url and publish_id):
        raise TikTokError(f"risposta senza upload_url: {str(d)[:200]}")
    return url, publish_id


def _invia_byte(url: str, video, dimensione: int, pezzo: int, quanti: int) -> None:
    """Manda i byte del video all'indirizzo aperto dalla init.

    Un pezzo solo nel caso normale; il ciclo regge comunque la frammentazione
    perche' i video crescono e il limite di TikTok e' a 64 MB.
    """
    with open(video, "rb") as fh:
        for i in range(quanti):
            inizio = i * pezzo
            # L'ultimo pezzo si prende tutto quello che resta: la divisione era
            # arrotondata per difetto, quindi avanza sempre qualcosa.
            fine = dimensione - 1 if i == quanti - 1 else inizio + pezzo - 1
            fh.seek(inizio)
            blocco = fh.read(fine - inizio + 1)
            u = httpx.put(
                url,
                headers={
                    "Content-Type": "video/mp4",
                    "Content-Length": str(len(blocco)),
                    "Content-Range": f"bytes {inizio}-{fine}/{dimensione}",
                },
                content=blocco,
                timeout=600,
            )
            if u.status_code >= 400:
                raise TikTokError(f"invio pezzo {i+1}/{quanti} fallito: "
                                  f"{u.status_code} {u.text[:160]}")


def _misura(video) -> tuple:
    """(Path, dimensione, pezzo, quanti) — i conti comuni ai due percorsi."""
    from pathlib import Path as _P

    video = _P(video)
    if not video.exists():
        raise TikTokError(f"video non trovato: {video}")
    dimensione = video.stat().st_size
    pezzo, quanti = _pezzatura(dimensione)
    return video, dimensione, pezzo, quanti


def carica_bozza(video: "Path", token: str = "") -> str:
    """Deposita un video come bozza nell'inbox TikTok. Ritorna il publish_id.

    La bozza NON è pubblicata: arriva come notifica nell'app e sei tu a
    completarla. È anche il motivo per cui non serve l'audit.
    """
    video, dimensione, pezzo, quanti = _misura(video)
    # Il token si puo' passare da fuori: caricando piu' video di fila,
    # richiederlo ogni volta sarebbe una chiamata sprecata per file.
    token = token or _token_accesso()

    url, publish_id = _init(
        INBOX_INIT,
        {"source_info": {"source": "FILE_UPLOAD", "video_size": dimensione,
                         "chunk_size": pezzo, "total_chunk_count": quanti}},
        token,
    )
    _invia_byte(url, video, dimensione, pezzo, quanti)
    return publish_id


def info_creatore(token: str = "") -> Dict:
    """Cosa l'account permette di fare, chiesto a TikTok invece che indovinato.

    Va chiamata PRIMA di ogni pubblicazione diretta, e non e' una formalita':
    `privacy_level` viene rifiutato se non e' fra le opzioni che questa
    risposta elenca, e le opzioni dipendono dall'account. Un profilo privato
    non ha `PUBLIC_TO_EVERYONE`, e scriverlo fisso nel codice significherebbe
    fallire ogni giorno senza capire perche'.

    Ritorna anche quante pubblicazioni restano nelle 24 ore, che e' l'unico
    modo che abbiamo di vedere il tetto prima di sbatterci contro.
    """
    token = token or _token_accesso()
    r = httpx.post(
        CREATOR_INFO,
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json; charset=UTF-8"},
        json={},
        timeout=60,
    )
    d = r.json()
    errore = (d.get("error") or {}).get("code", "ok")
    if errore not in ("ok", "", None):
        messaggio = (d.get("error") or {}).get("message", "")
        if "scope_not_authorized" in errore:
            raise ScopeMancante(
                "manca lo scope `video.publish`: l'app non e' ancora abilitata "
                "alla pubblicazione diretta. Finche' e' cosi' si resta su "
                "`mode: inbox`."
            )
        raise TikTokError(f"creator_info rifiutata: {errore} — {messaggio[:160]}")
    return d.get("data", {})


def pubblica_diretto(video: "Path", titolo: str, token: str = "") -> str:
    """Pubblica davvero, senza passare dall'inbox. Ritorna il publish_id.

    Differenze dalla bozza, tutte e tre necessarie:

    · L'indirizzo e' `/post/publish/video/init/` invece di quello dell'inbox.
    · Serve `post_info`, e dentro `privacy_level` — che NON si scrive a mano
      ma si prende da `info_creatore()`, vedi li' il perche'.
    · Serve lo scope `video.publish` e l'audit dell'app. Senza audit TikTok
      non rifiuta: accetta e mette il video in visibilita' privata, che e' il
      modo peggiore di fallire perche' sembra funzionare. Per questo l'errore
      `unaudited_client` diventa `NonAuditata` e chi chiama puo' ripiegare
      sulla bozza invece di pubblicare nel vuoto.

    `is_aigc=True` non e' opzionale per noi. I video li scrive un modello, e
    TikTok chiede che i contenuti generati siano dichiarati. Ometterlo
    sarebbe una violazione delle loro regole per guadagnare niente — e
    all'audit ci guardano proprio quello.
    """
    video, dimensione, pezzo, quanti = _misura(video)
    token = token or _token_accesso()

    permessi = info_creatore(token).get("privacy_level_options") or []
    voluto = cfg.get("publish.tiktok.privacy", "PUBLIC_TO_EVERYONE")
    if voluto not in permessi:
        # `NonAuditata` e non `TikTokError`, ed e' la differenza fra ripiegare
        # sulla bozza e non pubblicare niente quel giorno.
        #
        # Un client non auditato non riceve un rifiuto quando prova a
        # pubblicare: gli spariscono le opzioni. La documentazione lo dice
        # chiaro — «All content posted by unaudited clients will be restricted
        # to private viewing mode» — quindi `privacy_level_options` torna senza
        # `PUBLIC_TO_EVERYONE` e il controllo qui sopra scatta. E' la
        # situazione NORMALE di oggi, e in piu' e' esattamente quella del
        # Sandbox, dove Direct Post funziona ma pubblica solo SELF_ONLY.
        # Trattarla come guasto significherebbe che il giorno in cui si accende
        # il sandbox smettono di uscire anche le bozze che oggi escono.
        #
        # L'altra causa possibile — profilo TikTok privato — vuole la stessa
        # cura: meglio una bozza che un video pubblicato dove non lo vede
        # nessuno. Per questo il messaggio le nomina tutte e due invece di
        # indovinare quale sia, e la decisione la prende chi legge.
        raise NonAuditata(
            f"`{voluto}` non e' fra le opzioni concesse (ci sono: "
            f"{permessi or 'nessuna'}). O l'app non ha ancora passato l'audit, "
            f"o il profilo TikTok e' privato."
        )

    url, publish_id = _init(
        DIRECT_INIT,
        {"post_info": {"privacy_level": voluto,
                       "title": titolo[:2200],
                       "disable_comment": False,
                       "disable_duet": False,
                       "disable_stitch": False,
                       "is_aigc": True},
         "source_info": {"source": "FILE_UPLOAD", "video_size": dimensione,
                         "chunk_size": pezzo, "total_chunk_count": quanti}},
        token,
    )
    _invia_byte(url, video, dimensione, pezzo, quanti)
    return publish_id


def carica(video: "Path", titolo: str = "", token: str = "") -> tuple:
    """Il punto d'ingresso unico: pubblica o deposita, secondo la config.

    Ritorna (publish_id, modo_effettivo), dove il modo e' 'direct' o 'inbox'.
    Non e' lo stesso di quello chiesto in config, ed e' il punto di questa
    funzione: con `mode: direct` ma senza audit, ripiega sulla bozza invece
    di pubblicare un video che nessuno vedra' mai.

    Perche' il ripiego e non un errore. L'audit arriva in un giorno che non
    sappiamo in anticipo, e l'unico modo di accorgersene sarebbe provare. Con
    il ripiego l'interruttore si puo' girare su `direct` SUBITO: finche'
    l'audit non passa il sistema continua a fare bozze come oggi, e il giorno
    che passa comincia a pubblicare da solo senza che nessuno tocchi niente.
    Senza ripiego bisognerebbe indovinare la data, e sbagliarla in un verso
    significa giorni di video pubblicati in privato.
    """
    modo = str(cfg.get("publish.tiktok.mode", "inbox")).lower()
    token = token or _token_accesso()
    if modo == "direct":
        try:
            return pubblica_diretto(video, titolo, token), "direct"
        except (NonAuditata, ScopeMancante) as exc:
            print(f"    TikTok: pubblicazione diretta non ancora abilitata "
                  f"({str(exc)[:200]}) — deposito come bozza")
    return carica_bozza(video, token), "inbox"


def stato_bozza(publish_id: str) -> Dict:
    """Come è andata: PROCESSING_UPLOAD, SEND_TO_USER_INBOX, FAILED."""
    token = _token_accesso()
    r = httpx.post(
        STATO,
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json; charset=UTF-8"},
        json={"publish_id": publish_id},
        timeout=60,
    )
    return (r.json().get("data") or {})
