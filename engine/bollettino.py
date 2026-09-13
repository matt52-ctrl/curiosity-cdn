"""Il riepilogo su Telegram: come stanno andando i canali.

DUE MESSAGGI DIVERSI, e la differenza e' il punto.

  · `componi_allarmi` — gira OGNI GIORNO e tace quando va tutto bene. Manda
    solo se un canale si e' fermato o pubblica meno del dovuto. E' la parte
    che non puo' aspettare: un canale fermo scoperto sette giorni dopo e'
    una settimana persa.
  · `componi` — gira UNA VOLTA A SETTIMANA, il lunedi', e porta i numeri.
    Settimanale per scelta di Mattia del 13 settembre 2026: giorno per giorno
    i numeri di questo canale non dicono niente — tre viste in piu' o in meno
    sono rumore — e un messaggio che arriva tutti i giorni e non chiede mai
    niente si smette di leggere. A sette giorni il movimento e' vero.

Il messaggio settimanale e' scritto per il telefono: prima cosa NON va, poi
cosa e' uscito, poi i numeri. Se qualcosa e' rotto il resto puo' aspettare.

Le fonti sono quelle che abbiamo davvero, e il riepilogo dice quando una
manca invece di lasciare uno zero ambiguo:
  · YouTube    API Analytics — indietro di 2-3 giorni, non e' un guasto
  · Instagram  like, commenti e follower. Le insights no: vogliono un
               permesso che non c'e'. Le viste quindi non si sanno.
  · TikTok     API ufficiale: viste, like, commenti e condivisioni video per
               video, piu' i totali del profilo. E' l'unico canale che ci da'
               le VISUALIZZAZIONI.
  · Bluesky    like e repost, leggibili senza autenticazione
  · sito       Cloudflare, finestra di 7 giorni
"""
from __future__ import annotations

import datetime as _dt
import sqlite3
import time
from typing import List, Optional

import httpx

from .config import cfg, env


# La finestra del riepilogo. Sette giorni da quando gira, non "l'ultima
# settimana di calendario": il messaggio parte il lunedi' mattina e deve
# coprire tutto quello che e' uscito da quello prima.
FINESTRA_GIORNI = 7


def _uscite(conn: sqlite3.Connection) -> List[str]:
    """Cosa è uscito nella finestra, canale per canale."""
    da = time.time() - FINESTRA_GIORNI * 86400
    righe = []

    n = conn.execute("SELECT COUNT(*) FROM reels WHERE status='published' "
                     "AND published_at > ?", (da,)).fetchone()[0]
    righe.append(f"reel Instagram    {n}")

    n = conn.execute("SELECT COUNT(DISTINCT ref) FROM fact_uses "
                     "WHERE channel='youtube' AND ref LIKE 'yt-%' AND used_at > ?",
                     (da,)).fetchone()[0]
    righe.append(f"Short YouTube     {n}")

    n = conn.execute("SELECT COUNT(DISTINCT ref) FROM fact_uses "
                     "WHERE channel='tiktok' AND used_at > ?", (da,)).fetchone()[0]
    righe.append(f"bozze TikTok      {n}")

    n = conn.execute("SELECT COUNT(*) FROM fact_uses "
                     "WHERE channel='bluesky' AND used_at > ?", (da,)).fetchone()[0]
    righe.append(f"post Bluesky      {n}")

    # I caroselli si nominano solo se sono accesi: in pausa uno zero direbbe
    # una cosa che sappiamo già, e sposterebbe l'attenzione dalle righe vere.
    if cfg.get("publish.instagram.enabled", True):
        n = conn.execute("SELECT COUNT(*) FROM posts WHERE status='published' "
                         "AND published_at > ?", (da,)).fetchone()[0]
        righe.append(f"caroselli IG      {n}")
    return righe


def _bluesky() -> str:
    """Like e repost degli ultimi post. Leggibili senza autenticazione.

    `limit: 30` e non 10: a un post al giorno dieci non coprivano nemmeno la
    settimana, e la riga avrebbe contato meta' periodo dicendo di contarlo
    tutto."""
    handle = (env("BLUESKY_HANDLE") or "").strip()
    if not handle:
        return ""
    try:
        r = httpx.get("https://public.api.bsky.app/xrpc/app.bsky.feed.getAuthorFeed",
                      params={"actor": handle, "limit": 30}, timeout=20)
        feed = r.json().get("feed", [])
    except Exception:
        return ""
    da = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=FINESTRA_GIORNI)
    recenti = [i["post"] for i in feed
               if i["post"]["record"]["createdAt"] >= da.strftime("%Y-%m-%dT%H:%M:%S")]
    if not recenti:
        recenti = []
    like = sum(p.get("likeCount", 0) for p in recenti)
    rep = sum(p.get("repostCount", 0) for p in recenti)
    riga = f"{len(recenti)} post · {like} like · {rep} repost"

    # I FOLLOWER, che prima non comparivano. Su Bluesky non esiste un "per te"
    # algoritmico: si arriva alle persone da chi ti segue e dai feed tematici.
    # Quindi il numero che dice se la distribuzione si sta muovendo e' questo,
    # non i like — e dal 13 settembre 2026 ci sono tre cose nuove che
    # dovrebbero muoverlo (biografia, hashtag, 24 account seguiti). Senza
    # questa riga il riepilogo non avrebbe mostrato l'effetto di nessuna.
    try:
        pr = httpx.get("https://public.api.bsky.app/xrpc/app.bsky.actor.getProfile",
                       params={"actor": handle}, timeout=20).json()
        riga += (f"\nfollower: {pr.get('followersCount', 0)} · "
                 f"seguiti: {pr.get('followsCount', 0)}")
    except Exception:
        pass
    return riga


def _youtube_dati() -> Optional[dict]:
    """I numeri YouTube della finestra, grezzi. `None` se l'API non risponde.

    Separato dalla formattazione perche' li usa anche il totale, e una
    funzione che restituisce righe di testo non si puo' sommare.

    Chiede anche `likes`, `comments` e `shares`, che prima non chiedeva: senza
    di quelli il totale in fondo al riepilogo avrebbe dovuto escludere il
    canale che fa piu' visualizzazioni di tutti.
    """
    try:
        from .publish import youtube as yt
        token = yt.access_token()
    except Exception:
        return None
    oggi = _dt.date.today()
    try:
        r = httpx.get("https://youtubeanalytics.googleapis.com/v2/reports",
                      headers={"Authorization": f"Bearer {token}"},
                      params={"ids": "channel==MINE",
                              "startDate": str(oggi - _dt.timedelta(
                                  days=FINESTRA_GIORNI - 1)),
                              "endDate": str(oggi),
                              "metrics": "views,likes,comments,shares,"
                                         "subscribersGained,"
                                         "averageViewDuration,"
                                         "averageViewPercentage",
                              "dimensions": "day", "sort": "day"}, timeout=30)
        righe = r.json().get("rows", [])
    except Exception as exc:
        return {"errore": str(exc)[:40]}
    if not righe:
        return {}
    viste = sum(x[1] for x in righe)
    d = {"viste": viste,
         "like": sum(x[2] for x in righe),
         "commenti": sum(x[3] for x in righe),
         "condivisioni": sum(x[4] for x in righe),
         "iscritti": sum(x[5] for x in righe),
         "ultimo": (righe[-1][0], righe[-1][1])}
    # Media pesata sulle viste, non media delle medie: un giorno da 3 viste
    # peserebbe come uno da 2500 e il numero direbbe una cosa che non e'
    # successa. Con `dimensions=day` l'API da' una media per giornata, quindi
    # il peso va rimesso a mano.
    if viste and len(righe[0]) > 6:
        d["secondi"] = sum(x[1] * x[6] for x in righe) / viste
        d["quota"] = sum(x[1] * x[7] for x in righe) / viste
    return d


def _iscritti_totali() -> Optional[int]:
    """Gli iscritti del canale, totali. Serve al conto del pubblico."""
    try:
        from .publish import youtube as yt
        r = httpx.get("https://www.googleapis.com/youtube/v3/channels",
                      headers={"Authorization": f"Bearer {yt.access_token()}"},
                      params={"part": "statistics", "mine": "true"}, timeout=20)
        voci = r.json().get("items", [])
        return int(voci[0]["statistics"]["subscriberCount"]) if voci else None
    except Exception:
        return None


def _youtube(dati: Optional[dict]) -> List[str]:
    """Le viste degli ultimi giorni. L'API è indietro: si dice, non si nasconde.

    Perché ci sono sia i secondi che la percentuale, e i secondi vengono prima:
    dal 20 agosto 2026 i video portano un fotogramma finale con la richiesta, e
    sono due secondi e mezzo più lunghi. La percentuale scende da sola — gli
    stessi 22 secondi guardati valgono 50% su 45 e 48% su 47 — mentre i secondi
    guardati non si muovono. Chi confronta le percentuali attraverso quella
    data conclude che la richiesta ha peggiorato i video, che è il contrario di
    quello che i dati dicono. Il numero da leggere è il primo.
    """
    if dati is None:
        return ["YouTube: credenziali non disponibili"]
    if "errore" in dati:
        return [f"YouTube: lettura fallita ({dati['errore']})"]
    if not dati:
        return ["YouTube: nessun dato ancora (l'API è indietro di 2-3 giorni)"]
    fuori = [f"{FINESTRA_GIORNI} giorni: {dati['viste']} viste · "
             f"+{dati['iscritti']} iscritti",
             f"ultimo dato ({dati['ultimo'][0][5:]}): {dati['ultimo'][1]} viste"]
    if "secondi" in dati:
        fuori.append(f"visione media: {dati['secondi']:.0f}s "
                     f"({dati['quota']:.0f}%)")
    return fuori



def _prova_lunghezza(conn: sqlite3.Connection) -> List[str]:
    """Una riga al giorno sulla prova aperta, senza doverla andare a cercare.

    Perché in chiaro e non solo in `run.py esperimento`: una prova che dura un
    mese e si consulta solo a comando è una prova che ci si dimentica di avere
    aperta, e i video escono in due formati senza che nessuno se ne ricordi.

    Il verdetto NON si scrive qui. Compare il conteggio dei giorni e il numero
    grezzo, perché il senso della soglia scritta in config è proprio che non si
    decida guardando il bollettino a metà mese.
    """
    from .config import cfg
    from .db import esito_esperimento
    from .lines import giorni_di_prova, inizio_prova

    if not cfg.get("esperimento.lunghezza.attiva", False):
        return []
    giorni = int(cfg.get("esperimento.lunghezza.giorni", 30))
    passati = giorni_di_prova()
    try:
        dati = {r["variante"]: r for r in esito_esperimento(conn, inizio_prova())}
    except Exception:
        return []
    if passati < 0:
        giorno = "giorno" if passati == -1 else "giorni"
        return [f"prova lunghezza: parte fra {-passati} {giorno}"]
    if not dati:
        return [f"prova lunghezza: giorno {passati}/{giorni}, nessun video ancora"]

    conteggi = " · ".join(
        f"{g}: {dati[g]['video']} video, {dati[g]['viste_per_video'] or 0:.0f} "
        f"viste l'uno" for g in ("corto", "lungo") if g in dati)
    fuori = [f"prova lunghezza — giorno {passati}/{giorni}", conteggi]
    if passati >= giorni:
        fuori.append("⏰ è ora di leggerla:  python3 run.py esperimento")
    return fuori


def _prova_apertura(conn: sqlite3.Connection) -> List[str]:
    """Una riga al giorno sulla seconda prova: lo scontro contro il divario.

    Sta accanto all'altra e non dentro, perché sono due domande diverse lette
    sugli stessi video — è il senso del disegno 2x2 — e perché il giudice è un
    altro: la lunghezza si decide sulle viste per video, l'apertura sulla
    tenuta a 3 secondi.

    Qui compare il numero, non il verdetto, per la stessa ragione dell'altra:
    la soglia è scritta in config apposta perché non si decida guardando il
    telefono a metà mese.

    `video_con_dati` e non `video`: la curva di ritenzione arriva solo quando
    il video ha abbastanza visite, e nei primi giorni metà delle righe è
    ancora a zero. Mostrare il totale farebbe sembrare che stiamo misurando
    più di quanto stiamo misurando davvero.
    """
    from .config import cfg
    from .db import esito_apertura
    from .lines import giorni_di_prova, inizio_prova

    if not cfg.get("esperimento.apertura.attiva", False):
        return []
    giorni = int(cfg.get("esperimento.apertura.giorni", 30))
    passati = giorni_di_prova("apertura")
    try:
        dati = {r["apertura"]: r for r in esito_apertura(conn, inizio_prova("apertura"))}
    except Exception:
        return []
    if passati < 0:
        return []          # lo dice già la riga della prova sulla lunghezza
    if not dati:
        return [f"prova apertura: giorno {passati}/{giorni}, nessun video ancora"]

    conteggi = " · ".join(
        f"{g}: {dati[g]['video_con_dati'] or 0}/{dati[g]['video']} video, "
        f"{(dati[g]['tenuta_media'] or 0) * 100:.1f}% a 3s"
        for g in ("scontro", "divario") if g in dati)
    return [f"prova apertura — giorno {passati}/{giorni}", conteggi]


def _prova_cta(conn: sqlite3.Connection) -> List[str]:
    """Il promemoria della terza prova: la richiesta anticipata.

    Non ha conteggi da mostrare, e non e' una dimenticanza. Si giudica sui
    follower ogni mille viste. Il conto lo fa `analytics/confronto.py`, ma
    qualcuno deve ricordarsi di lanciarlo: senza questa riga il 14 ottobre
    arriva e non se ne accorge nessuno.
    """
    from datetime import date
    from .config import cfg

    if not cfg.get("esperimento.cta_anticipata.attiva", False):
        return []
    dal = str(cfg.get("esperimento.cta_anticipata.dal", "") or "")
    giorni = int(cfg.get("esperimento.cta_anticipata.giorni", 30))
    try:
        y, m, d = (int(x) for x in dal.split("-"))
        passati = (date.today() - date(y, m, d)).days
    except Exception:
        return []
    if passati < 0:
        return []
    fuori = [f"prova richiesta anticipata — giorno {passati}/{giorni}"]
    if passati >= giorni:
        fuori.append("⏰ leggila:  python3 analytics/confronto.py")
    return fuori



def _prova_bluesky(conn: sqlite3.Connection) -> List[str]:
    """La prova sulla risposta: post con rimandi contro post senza.

    Legge i due bracci dalla tabella `esperimento` (piattaforma 'bluesky') e
    le interazioni dal feed pubblico, che non chiede autenticazione.

    ⚠️ NON stampa un verdetto, e nemmeno un vincitore provvisorio. Al 13
    settembre 2026 tutte le metriche del profilo valgono zero, e zero contro
    zero non e' un pareggio: e' assenza di misura. La riga serve a ricordare
    che la prova esiste e a mostrare quando comincia a esserci qualcosa da
    leggere — la soglia e' `publish.bluesky.interazioni_minime`.
    """
    from .config import cfg

    if cfg.get("publish.bluesky.risposta", "alterna") != "alterna":
        return []
    righe = conn.execute(
        "SELECT video_id, variante FROM esperimento WHERE piattaforma='bluesky'"
    ).fetchall()
    if not righe:
        return ["prova risposta Bluesky: nessun post ancora"]
    gruppo = {r["video_id"]: r["variante"] for r in righe}

    handle = (env("BLUESKY_HANDLE") or "").strip()
    conti = {"con-risposta": [0, 0], "senza-risposta": [0, 0]}   # post, interazioni
    try:
        r = httpx.get("https://public.api.bsky.app/xrpc/app.bsky.feed.getAuthorFeed",
                      params={"actor": handle, "limit": 100}, timeout=20)
        feed = r.json().get("feed", [])
    except Exception:
        feed = []
    for i in feed:
        p = i["post"]
        g = gruppo.get(p.get("uri", ""))
        if not g:
            continue
        conti[g][0] += 1
        conti[g][1] += (p.get("likeCount", 0) + p.get("repostCount", 0)
                        + p.get("quoteCount", 0))

    totale = sum(v[1] for v in conti.values())
    soglia = int(cfg.get("publish.bluesky.interazioni_minime", 20))
    fuori = ["prova risposta Bluesky — " + " · ".join(
        f"{g.replace('-', ' ')}: {v[0]} post, {v[1]} interazioni"
        for g, v in conti.items())]
    if totale < soglia:
        fuori.append(f"non leggibile: {totale}/{soglia} interazioni in tutto")
    else:
        fuori.append("⏰ c'e' abbastanza per leggerla")
    return fuori


def _instagram(d: Optional[dict]) -> List[str]:
    """Like, commenti e follower. Le viste no: quelle vogliono le insights.

    Divide reel e caroselli perche' e' l'unica domanda sul formato che
    Instagram ci lascia fare, e la risposta non e' scontata: al 13 settembre
    2026, su 80 post, i 58 reel avevano raccolto 37 like e i 22 caroselli 2.
    Se il divario resta, e' l'informazione piu' utile che questo canale
    produce.
    """
    if not d:
        return ["Instagram: lettura fallita (token o rete)"]
    p = d["periodo"]
    fuori = [f"{FINESTRA_GIORNI} giorni: {p['post']} post · {p['like']} like "
             f"· {p['commenti']} commenti",
             f"follower: {d['follower']}"]
    if d["reel"]["post"] or d["caroselli"]["post"]:
        fuori.append(f"reel {d['reel']['post']}p/{d['reel']['like']}l · "
                     f"caroselli {d['caroselli']['post']}p/"
                     f"{d['caroselli']['like']}l")
    fuori.append("(le viste non sono leggibili: insights negate)")
    return fuori


def _tiktok(d: Optional[dict]) -> List[str]:
    """Viste, like, commenti e follower dall'API ufficiale.

    E' l'unico dei tre canali che ci da' le VISUALIZZAZIONI: su Instagram le
    insights sono negate e su YouTube arrivano con due giorni di ritardo.

    I totali del profilo sono cumulativi e stanno in una riga a parte: il
    numero della settimana e quello di sempre non vanno letti insieme.
    """
    if not d:
        return ["TikTok: lettura fallita (token o rete)"]
    p = d["periodo"]
    fuori = [f"{FINESTRA_GIORNI} giorni: {p['video']} video · {p['viste']} viste "
             f"· {p['like']} like · {p['commenti']} commenti"]
    if p["viste"]:
        fuori.append(f"{1000 * p['like'] / p['viste']:.1f} like e "
                     f"{1000 * p['commenti'] / p['viste']:.1f} commenti ogni "
                     f"mille viste · {p['condivisioni']} condivisioni")
    fuori.append(f"in tutto: {d['follower']} follower · {d['like_totali']} like "
                 f"· {d['video_totali']} video")
    return fuori


def _totale(yt: Optional[dict], ig: Optional[dict],
            tt: Optional[dict]) -> List[str]:
    """Tutto sommato insieme: la riga che dice quanto e' grande la cosa.

    Perche' serve, visto che i canali sono gia' elencati sopra: perche' tre
    numeri da tremila, tremila e "non leggibile" non si sommano a mente, e la
    domanda vera — quante persone ci hanno visto questa settimana — non aveva
    mai una risposta in nessuna riga del riepilogo.

    ⚠️ DUE CAUTELE, e stanno stampate nel messaggio invece che solo qui.
    · Le VISTE sono YouTube piu' TikTok. Instagram non le da' — le insights
      sono negate — quindi il totale e' per forza una sottostima, e dirlo e'
      l'unico modo di non farlo leggere come il numero vero.
    · Il PUBBLICO e' la somma di tre contatori cumulativi, non un numero di
      persone: chi ci segue su due canali e' contato due volte. E' un ordine
      di grandezza, non un'anagrafe.
    """
    def v(d, *strada, default=0):
        for k in strada:
            if not isinstance(d, dict) or k not in d:
                return default
            d = d[k]
        return d if isinstance(d, (int, float)) else default

    yt = yt if isinstance(yt, dict) and "errore" not in yt else {}
    viste = v(yt, "viste") + v(tt, "periodo", "viste")
    like = v(yt, "like") + v(tt, "periodo", "like") + v(ig, "periodo", "like")
    comm = (v(yt, "commenti") + v(tt, "periodo", "commenti")
            + v(ig, "periodo", "commenti"))
    cond = v(yt, "condivisioni") + v(tt, "periodo", "condivisioni")

    fuori = [f"{viste} viste · {like} like · {comm} commenti · {cond} "
             f"condivisioni"]
    if viste:
        fuori.append(f"{1000 * like / viste:.1f} like e {1000 * comm / viste:.2f} "
                     f"commenti ogni mille viste")
    fuori.append("(le viste sono YouTube + TikTok: Instagram non le da')")

    iscritti = _iscritti_totali()
    pezzi = []
    if iscritti is not None:
        pezzi.append(f"YouTube {iscritti}")
    if tt:
        pezzi.append(f"TikTok {tt['follower']}")
    if ig:
        pezzi.append(f"Instagram {ig['follower']}")
    if pezzi:
        somma = ((iscritti or 0) + v(tt, "follower") + v(ig, "follower"))
        fuori.append(f"pubblico: {somma} in tutto — " + " · ".join(pezzi))
        fuori.append(f"questa settimana: +{v(yt, 'iscritti')} iscritti YouTube "
                     f"(gli altri due danno solo il totale)")
    return fuori


def componi_allarmi(conn: sqlite3.Connection) -> str:
    """Solo cosa non va. Stringa vuota se va tutto bene — e allora non si manda.

    Gira ogni giorno mentre il riepilogo coi numeri gira una volta a
    settimana. La separazione e' il punto: i numeri a sette giorni si leggono
    meglio, ma un canale fermo non puo' aspettare fino a lunedi'.

    Non dice mai "tutto bene". Un messaggio quotidiano che quasi sempre non
    porta niente e' esattamente il messaggio che si smette di aprire, ed e'
    allora che quello vero passa inosservato. Qui il silenzio e' la notizia
    buona: se arriva qualcosa, c'e' qualcosa.
    """
    from . import allarme

    allarme.azzera()
    allarme.silenzio(conn)
    allarme.cadenza(conn)
    problemi = allarme.elenco() if hasattr(allarme, "elenco") else []
    if not problemi:
        return ""
    return "\n".join(["⚠️ <b>DA GUARDARE</b>"] + [f"· {p}" for p in problemi])


def componi(conn: sqlite3.Connection) -> str:
    """Il riepilogo settimanale, in HTML per Telegram."""
    from . import allarme, sito_metriche

    parti: List[str] = []

    # 1. Cosa non va. Sta in cima perché se qualcosa è rotto il resto può
    #    aspettare, e perché un messaggio che comincia coi numeri buoni si
    #    smette di leggere prima di arrivare al problema.
    allarme.azzera()
    allarme.silenzio(conn)
    allarme.cadenza(conn)
    problemi = allarme.elenco() if hasattr(allarme, "elenco") else []
    if problemi:
        parti.append("⚠️ <b>DA GUARDARE</b>")
        parti += [f"· {p}" for p in problemi]
    else:
        parti.append("✅ <b>Tutti i canali nei tempi previsti.</b>")

    # 2. Cosa è uscito.
    parti.append(f"\n<b>Ultimi {FINESTRA_GIORNI} giorni</b>\n<code>" + "\n".join(_uscite(conn)) + "</code>")

    # 3. I numeri, per le fonti che li danno davvero. Si leggono UNA volta e
    #    si passano sia alla sezione del canale sia al totale: chiedere due
    #    volte gli stessi dati costerebbe due chiamate per riga.
    from . import metriche_social

    yt = _youtube_dati()
    ig = metriche_social.instagram(FINESTRA_GIORNI)
    tt = metriche_social.tiktok(FINESTRA_GIORNI)

    parti.append("\n<b>Tutto insieme</b>\n" + "\n".join(_totale(yt, ig, tt)))
    parti.append("\n<b>YouTube</b>\n" + "\n".join(_youtube(yt)))
    parti.append("\n<b>Instagram</b>\n" + "\n".join(_instagram(ig)))
    parti.append("\n<b>TikTok</b>\n" + "\n".join(_tiktok(tt)))

    prova = (_prova_lunghezza(conn) + _prova_apertura(conn)
             + _prova_cta(conn) + _prova_bluesky(conn))
    if prova:
        parti.append("\n<code>" + "\n".join(prova) + "</code>")

    bs = _bluesky()
    if bs:
        parti.append(f"\n<b>Bluesky</b>\n{bs}")

    try:
        dati = sito_metriche.riassunto()
        tot = sum(n for _, n in dati["pagine"])
        riga = f"{tot} visite in 7 giorni"
        if dati["provenienza"]:
            da = ", ".join(f"{k} {v}" for k, v in dati["provenienza"][:3])
            riga += f"\narrivano da: {da}"
        parti.append(f"\n<b>Sito</b>\n{riga}")
    except Exception:
        pass

    return "\n".join(parti)


def manda(conn: sqlite3.Connection) -> bool:
    """Compone e invia il riepilogo settimanale. False se Telegram non c'è."""
    from . import review

    if not review.enabled():
        print("· Telegram non configurato: riepilogo non inviato")
        return False
    review.notify(componi(conn))
    return True


def manda_allarmi(conn: sqlite3.Connection) -> bool:
    """Manda SOLO se c'è un problema. Ritorna True se ha mandato qualcosa."""
    from . import review

    testo = componi_allarmi(conn)
    if not testo:
        print("· nessun problema: non mando niente")
        return False
    if not review.enabled():
        print("· Telegram non configurato: allarme non inviato")
        print(testo)
        return False
    review.notify(testo)
    return True
