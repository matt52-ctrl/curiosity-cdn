"""Stato persistente: fatti generati, post pubblicati, metriche.

SQLite perché il dato è piccolo, relazionale e deve sopravvivere ai riavvii.
La tabella `facts` è anche la memoria anti-duplicato: senza questa la pipeline
ripubblica le stesse tre curiosità in loop.
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import DATA_DIR

DB_PATH = DATA_DIR / "engine.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS facts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at    REAL NOT NULL,
    niche         TEXT NOT NULL,
    hook          TEXT NOT NULL,
    fact          TEXT NOT NULL,
    detail        TEXT NOT NULL,
    kicker        TEXT NOT NULL,
    source_hint   TEXT NOT NULL DEFAULT '',
    keywords      TEXT NOT NULL DEFAULT '[]',
    verdict       TEXT NOT NULL DEFAULT 'unverified',
    confidence    REAL NOT NULL DEFAULT 0.0,
    verify_note   TEXT NOT NULL DEFAULT '',
    status        TEXT NOT NULL DEFAULT 'new'
        -- new | rejected | approved | rendered | published | failed
);

CREATE TABLE IF NOT EXISTS posts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    fact_id       INTEGER NOT NULL REFERENCES facts(id),
    created_at    REAL NOT NULL,
    format        TEXT NOT NULL,          -- carousel | single
    caption       TEXT NOT NULL,
    hashtags      TEXT NOT NULL DEFAULT '[]',
    image_paths   TEXT NOT NULL DEFAULT '[]',
    image_urls    TEXT NOT NULL DEFAULT '[]',
    ig_media_id   TEXT,
    tiktok_id     TEXT,
    published_at  REAL,
    status        TEXT NOT NULL DEFAULT 'draft'
        -- draft | pending_review | approved | published | failed
);

CREATE TABLE IF NOT EXISTS metrics (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id       INTEGER NOT NULL REFERENCES posts(id),
    collected_at  REAL NOT NULL,
    reach         INTEGER DEFAULT 0,
    likes         INTEGER DEFAULT 0,
    comments      INTEGER DEFAULT 0,
    saves         INTEGER DEFAULT 0,
    shares        INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_facts_status ON facts(status);
CREATE INDEX IF NOT EXISTS idx_posts_status ON posts(status);
"""

# Aggiunte dopo la prima versione dello schema. SQLite non ha
# "ADD COLUMN IF NOT EXISTS", quindi si tenta e si ignora l'errore.
REELS_SCHEMA = """
-- Tabella separata dai post di proposito: reel e caroselli hanno cicli,
-- cadenze e modalità di fallimento diverse, e mescolarli significherebbe che
-- un problema sui video blocca anche le immagini.
CREATE TABLE IF NOT EXISTS reels (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at    REAL NOT NULL,
    line          TEXT NOT NULL,
    mood          TEXT NOT NULL DEFAULT 'reflective',
    caption       TEXT NOT NULL DEFAULT '',
    hashtags      TEXT NOT NULL DEFAULT '[]',
    video_path    TEXT NOT NULL DEFAULT '',
    video_url     TEXT NOT NULL DEFAULT '',
    ig_media_id   TEXT,
    published_at  REAL,
    status        TEXT NOT NULL DEFAULT 'draft'
        -- draft | approved | published | failed
);

CREATE INDEX IF NOT EXISTS idx_reels_status ON reels(status);
"""

MIGRATIONS = [
    # Il testo delle slide: senza, un ritocco al CSS obbliga a rigenerare
    # tutto via API. Con questo si ri-renderizza a costo zero.
    "ALTER TABLE posts ADD COLUMN slides TEXT NOT NULL DEFAULT '[]'",
    # Descrizione della prima immagine. Instagram la indicizza nella ricerca
    # oltre a usarla per gli screen reader: veniva generata e poi buttata via.
    "ALTER TABLE posts ADD COLUMN alt_text TEXT NOT NULL DEFAULT ''",
    # Da quale curiosita' nasce un reel. Senza, due reel possono raccontare
    # lo stesso studio con parole diverse: e' gia' successo, e la deduplica
    # lessicale non lo intercetta perche' le frasi non si somigliano.
    "ALTER TABLE reels ADD COLUMN fact_id INTEGER",
    # Id del video su YouTube: serve a non ricaricare due volte lo stesso
    # Short se il ciclo rigira su un reel gia' pubblicato altrove.
    "ALTER TABLE reels ADD COLUMN youtube_id TEXT",
    # Quante volte la curiosita' e' comparsa in un video lungo di YouTube.
    # I video lunghi mettono in fila piu' curiosita': la prima e' sempre
    # nuova, le altre si pescano fra le gia' uscite. Senza questo contatore
    # si ripescherebbero sempre le stesse.
    "ALTER TABLE reels ADD COLUMN yt_uses INTEGER NOT NULL DEFAULT 0",
    # Quando la curiosita' e' comparsa l'ultima volta in un video YouTube.
    # Il solo contatore non basta: a parita' di riusi bisogna sapere QUANDO,
    # o si ripesca quella appena andata in onda.
    "ALTER TABLE reels ADD COLUMN yt_last_used REAL NOT NULL DEFAULT 0",
]

# Metriche dei reel, tenute separate da quelle dei post: la tabella `metrics`
# ha una chiave esterna su posts(id) e i reel stanno in un'altra tabella. Le
# colonne sono anche diverse — su YouTube il segnale non sono i salvataggi ma
# la percentuale di visione — e forzarle in un'unica tabella renderebbe
# entrambe le letture ambigue.
# Registro dei consumi: quale curiosita' e' gia' stata usata, su quale
# piattaforma. Prima l'informazione era sparsa — i caroselli la deducevano da
# posts.fact_id, i reel da reels.fact_id, e nessuno dei due guardava l'altro.
# Il risultato era che la stessa curiosita' usciva come carosello e poi come
# reel: il fatto #3 e' andato in onda quattro volte.
#
# Il canale e' la piattaforma, non il formato: su Instagram carosello e reel
# arrivano alle stesse persone, quindi contano come una cosa sola. YouTube e'
# un pubblico diverso e ha un registro suo — la stessa curiosita' puo' uscire
# su entrambe le piattaforme, e' normale e voluto, ma mai due volte sulla
# stessa.
FACT_USES_SCHEMA = """
CREATE TABLE IF NOT EXISTS fact_uses (
    fact_id   INTEGER NOT NULL REFERENCES facts(id),
    channel   TEXT    NOT NULL,          -- instagram | youtube
    used_at   REAL    NOT NULL,
    ref       TEXT,                      -- da dove: post-12, reel-8, yt-3
    PRIMARY KEY (fact_id, channel)
);
CREATE INDEX IF NOT EXISTS idx_fact_uses ON fact_uses(channel, fact_id);
"""

REEL_METRICS_SCHEMA = """
CREATE TABLE IF NOT EXISTS reel_metrics (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    reel_id       INTEGER NOT NULL REFERENCES reels(id),
    platform      TEXT NOT NULL,          -- youtube | instagram
    collected_at  REAL NOT NULL,
    views         INTEGER DEFAULT 0,
    likes         INTEGER DEFAULT 0,
    comments      INTEGER DEFAULT 0,
    -- Percentuale media del video guardata, 0-100. È il numero che conta:
    -- YouTube distribuisce gli Short in base a quanto della clip viene
    -- guardato, non a quanti like riceve.
    avg_view_pct  REAL DEFAULT 0,
    avg_view_sec  REAL DEFAULT 0,
    UNIQUE(reel_id, platform, collected_at)
);
CREATE INDEX IF NOT EXISTS idx_reel_metrics ON reel_metrics(reel_id, platform);
"""


def connect(path: Optional[Path] = None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path or DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.executescript(REELS_SCHEMA)
    conn.executescript(REEL_METRICS_SCHEMA)
    conn.executescript(FACT_USES_SCHEMA)
    _recupera_consumi(conn)
    for statement in MIGRATIONS:
        try:
            conn.execute(statement)
        except sqlite3.OperationalError:
            pass          # colonna già presente
    conn.commit()
    return conn


# ─── facts ────────────────────────────────────────────────────────────────────

def insert_fact(conn: sqlite3.Connection, niche: str, f: Dict[str, Any]) -> int:
    cur = conn.execute(
        """INSERT INTO facts
           (created_at, niche, hook, fact, detail, kicker, source_hint, keywords)
           VALUES (?,?,?,?,?,?,?,?)""",
        (
            time.time(),
            niche,
            f["hook"],
            f["fact"],
            f["detail"],
            f["kicker"],
            f.get("source_hint", ""),
            json.dumps(f.get("keywords", [])),
        ),
    )
    conn.commit()
    return int(cur.lastrowid)


def set_verification(
    conn: sqlite3.Connection, fact_id: int, verdict: str, confidence: float, note: str
) -> None:
    status = "approved" if verdict == "supported" else "rejected"
    conn.execute(
        "UPDATE facts SET verdict=?, confidence=?, verify_note=?, status=? WHERE id=?",
        (verdict, confidence, note, status, fact_id),
    )
    conn.commit()


def set_fact_status(conn: sqlite3.Connection, fact_id: int, status: str) -> None:
    conn.execute("UPDATE facts SET status=? WHERE id=?", (status, fact_id))
    conn.commit()


def next_approved_fact(conn: sqlite3.Connection) -> Optional[sqlite3.Row]:
    """La prossima curiosita' per un carosello.

    L'esclusione su fact_uses non c'era, ed e' il motivo per cui il fatto #3
    e' uscito quattro volte: due caroselli e due reel. Il carosello guardava
    solo lo stato della curiosita', che resta 'approved' anche dopo che un
    reel l'ha usata.
    """
    return conn.execute(
        """SELECT * FROM facts
           WHERE status='approved'
             AND id NOT IN (SELECT fact_id FROM fact_uses WHERE channel='instagram')
           ORDER BY confidence DESC, id ASC LIMIT 1"""
    ).fetchone()


def all_published_texts(conn: sqlite3.Connection) -> List[str]:
    """Corpus anti-duplicato: tutto ciò che è già uscito o è in coda."""
    rows = conn.execute(
        "SELECT fact FROM facts WHERE status IN ('approved','rendered','published')"
    ).fetchall()
    return [r["fact"] for r in rows]


# ─── posts ────────────────────────────────────────────────────────────────────

def insert_post(
    conn: sqlite3.Connection,
    fact_id: int,
    fmt: str,
    caption: str,
    hashtags: List[str],
    image_paths: List[str],
    status: str = "draft",
    slides: Optional[List[Dict[str, Any]]] = None,
) -> int:
    cur = conn.execute(
        """INSERT INTO posts
           (fact_id, created_at, format, caption, hashtags, image_paths, status, slides)
           VALUES (?,?,?,?,?,?,?,?)""",
        (
            fact_id,
            time.time(),
            fmt,
            caption,
            json.dumps(hashtags),
            json.dumps(image_paths),
            status,
            json.dumps(senza_immagini(slides or [])),
        ),
    )
    conn.commit()
    pid = int(cur.lastrowid)
    # Si registra alla CREAZIONE, non alla pubblicazione: dal momento in
    # cui la curiosita' e' impegnata in un contenuto non deve piu' essere
    # pescabile, o il giro successivo ne costruisce un secondo identico
    # mentre il primo e' ancora in coda.
    segna_uso_fatto(conn, fact_id, 'instagram', f'post-{pid}')
    return pid


def set_post_urls(conn: sqlite3.Connection, post_id: int, urls: List[str]) -> None:
    conn.execute(
        "UPDATE posts SET image_urls=? WHERE id=?", (json.dumps(urls), post_id)
    )
    conn.commit()


def set_post_status(conn: sqlite3.Connection, post_id: int, status: str) -> None:
    conn.execute("UPDATE posts SET status=? WHERE id=?", (status, post_id))
    conn.commit()


def mark_published(
    conn: sqlite3.Connection,
    post_id: int,
    ig_media_id: Optional[str] = None,
    tiktok_id: Optional[str] = None,
) -> None:
    conn.execute(
        """UPDATE posts SET status='published', published_at=?,
           ig_media_id=COALESCE(?, ig_media_id),
           tiktok_id=COALESCE(?, tiktok_id) WHERE id=?""",
        (time.time(), ig_media_id, tiktok_id, post_id),
    )
    conn.commit()
    alleggerisci_slides(conn)


def senza_immagini(slides: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Copia delle slide senza le immagini incorporate.

    Le slide portano la foto dentro di se' in base64 — circa un megabyte
    l'una — perche' il rendering avviene in memoria subito dopo. Sul database
    quei byte non devono arrivare: il database sta nel repo, e a cinque
    megabyte per post GitHub smette di accettare il push nel giro di giorni.

    Restano `image_file` e `image_src`, che dicono dov'era la foto: bastano a
    ricostruire il post se serve, e pesano qualche decina di byte.
    """
    pulite = []
    for s in slides:
        c = dict(s)
        if isinstance(c.get("image"), str) and c["image"].startswith("data:"):
            c["image"] = ""
        pulite.append(c)
    return pulite


def alleggerisci_slides(conn: sqlite3.Connection) -> int:
    """Toglie le immagini incorporate dai post che non si ri-renderizzano piu'.

    Ogni slide porta dentro il proprio PNG in base64 — circa 1,3 MB — perche'
    `rerender` possa rifare la grafica senza riscaricare o rigenerare nulla.
    Il testo va conservato, l'immagine no: `rerender` lavora solo sui post
    ancora in revisione, quindi su un post pubblicato quel megabyte non serve
    piu' a niente.

    Perche' e' urgente e non una pulizia di garbo: il database e' versionato
    nel repo (e deve esserlo, o su Actions la pipeline ripubblicherebbe tutto
    da capo). A cinque megabyte per post e due post al giorno, GitHub rifiuta
    il push in pochi giorni e il ciclo si ferma di colpo.

    Restituisce i byte liberati.
    """
    liberati = 0
    righe = conn.execute(
        """SELECT id, slides FROM posts
           WHERE status IN ('published','superseded','failed','rejected')
             AND slides LIKE '%data:image%'"""
    ).fetchall()
    for r in righe:
        try:
            slides = json.loads(r["slides"] or "[]")
        except json.JSONDecodeError:
            continue
        prima = len(r["slides"])
        for s in slides:
            # Solo le immagini incorporate: un URL normale pesa nulla ed e'
            # l'unica traccia di che foto era.
            if isinstance(s.get("image"), str) and s["image"].startswith("data:"):
                s["image"] = ""
        dopo = json.dumps(slides)
        liberati += prima - len(dopo)
        conn.execute("UPDATE posts SET slides=? WHERE id=?", (dopo, r["id"]))
    if righe:
        conn.commit()
        # Senza questo SQLite tiene le pagine svuotate e il file non cala:
        # il repo continuerebbe a portarsi dietro gli stessi megabyte.
        conn.execute("VACUUM")
    return liberati


def get_post(conn: sqlite3.Connection, post_id: int) -> Optional[sqlite3.Row]:
    return conn.execute("SELECT * FROM posts WHERE id=?", (post_id,)).fetchone()


def posts_by_status(conn: sqlite3.Connection, status: str) -> List[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM posts WHERE status=? ORDER BY id ASC", (status,)
    ).fetchall()


def published_posts(conn: sqlite3.Connection) -> List[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM posts WHERE status='published' AND ig_media_id IS NOT NULL"
    ).fetchall()


# ─── metrics ──────────────────────────────────────────────────────────────────

def insert_metrics(conn: sqlite3.Connection, post_id: int, m: Dict[str, int]) -> None:
    conn.execute(
        """INSERT INTO metrics (post_id, collected_at, reach, likes, comments, saves, shares)
           VALUES (?,?,?,?,?,?,?)""",
        (
            post_id,
            time.time(),
            m.get("reach", 0),
            m.get("likes", 0),
            m.get("comments", 0),
            m.get("saves", 0),
            m.get("shares", 0),
        ),
    )
    conn.commit()


def top_performers(conn: sqlite3.Connection, limit: int = 10) -> List[sqlite3.Row]:
    """I post con più salvataggi per reach — il segnale che conta davvero su IG."""
    return conn.execute(
        """
        SELECT f.hook, f.fact, f.keywords,
               MAX(m.reach) AS reach, MAX(m.saves) AS saves,
               CAST(MAX(m.saves) AS REAL) / NULLIF(MAX(m.reach), 0) AS save_rate
        FROM metrics m
        JOIN posts p ON p.id = m.post_id
        JOIN facts f ON f.id = p.fact_id
        GROUP BY p.id
        HAVING reach > 0
        ORDER BY save_rate DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()


# ─── reels ────────────────────────────────────────────────────────────────────
# Volutamente separati dai post: stessa struttura concettuale, cicli distinti.

def insert_reel(conn: sqlite3.Connection, r: Dict[str, Any], status: str = "approved") -> int:
    cur = conn.execute(
        """INSERT INTO reels
           (created_at, line, mood, caption, hashtags, video_path, status, fact_id)
           VALUES (?,?,?,?,?,?,?,?)""",
        (time.time(), r["line"], r.get("mood", "reflective"), r.get("caption", ""),
         json.dumps(r.get("hashtags", [])), str(r.get("video_path", "")), status,
         r.get("fact_id")),
    )
    conn.commit()
    rid = int(cur.lastrowid)
    segna_uso_fatto(conn, r.get("fact_id"), "instagram", f"reel-{rid}")
    return rid


def reel_spalle(conn, lead, quante: int = 2) -> list:
    """Curiosita' di supporto per il video lungo di YouTube.

    Il video lungo apre sempre con la curiosita' appena uscita e prosegue con
    altre gia' pubblicate. Si scelgono quelle riusate meno volte, cosi' il
    riuso gira su tutto l'archivio invece di battere sempre le stesse; a
    parita' si preferisce la piu' recente, che e' anche la meglio scritta
    (il generatore migliora nel tempo).

    Il filtro sui doppioni non e' una precauzione teorica: in archivio ci sono
    gia' due reel che raccontano lo stesso effetto con parole diverse. Nel
    feed passano a giorni di distanza, dentro lo stesso video sarebbero uno
    dopo l'altro.
    """
    from .config import cfg
    from .ideas import similarity

    # Due criteri, in quest'ordine: prima chi e' stato riusato meno, poi chi
    # e' fermo da piu' tempo. Il secondo non e' un dettaglio — ordinando per
    # id decrescente, come faceva prima, a parita' di riusi usciva sempre la
    # curiosita' appena pubblicata, e finiva come spalla nel video successivo
    # al proprio. La distanza media fra due apparizioni era di un video.
    candidati = conn.execute(
        """SELECT * FROM reels
           WHERE status = 'published' AND id != ?
           ORDER BY yt_uses ASC, yt_last_used ASC, id ASC
           LIMIT 60""",
        (lead["id"],),
    ).fetchall()

    # Periodo di riposo: una curiosita' uscita da poco non torna, nemmeno se
    # il contatore la indica come la meno usata. E' la garanzia dura; il
    # contatore da solo e' solo una preferenza, e nei primi giorni — quando
    # l'archivio e' corto — non basta.
    riposo = float(cfg.get("publish.youtube.reuse_cooldown_hours", 48)) * 3600
    limite = time.time() - riposo
    riposati = [c for c in candidati if (c["yt_last_used"] or 0) < limite]

    # Se non ne restano abbastanza si allarga, invece di consegnare un video
    # piu' corto: nei primi giorni l'archivio e' piccolo e il riposo da solo
    # svuoterebbe la scelta. Con tre video al giorno da tre curiosita', in 48
    # ore ne servono diciotto distinte: finche' l'archivio non le ha, il
    # riposo non puo' essere rispettato e va allentato.
    if len(riposati) < quante:
        # Ordine per sola anzianita' d'uso: qui il contatore va ignorato.
        # E' proprio lui il problema — una curiosita' appena usata come
        # apertura ha un solo riuso all'attivo, quindi il contatore la fa
        # sembrare la piu' riposata di tutte pochi secondi dopo che e' uscita.
        avanzo = sorted(
            (c for c in candidati if c["id"] not in {x["id"] for x in riposati}),
            key=lambda c: (c["yt_last_used"] or 0),
        )
        riposati = riposati + avanzo

    # Regola dura, valida anche quando il riposo e' allentato: mai riprendere
    # le curiosita' del video precedente. Il riposo e' una preferenza che nei
    # primi giorni cede; questa no. Due Short di fila che condividono una
    # curiosita' sono la cosa piu' visibile di tutte per chi ne guarda due.
    ultimo = conn.execute(
        "SELECT MAX(yt_last_used) u FROM reels WHERE yt_last_used > 0"
    ).fetchone()["u"]
    if ultimo:
        senza_ultimi = [c for c in riposati if (c["yt_last_used"] or 0) < ultimo]
        # Si applica solo se lascia abbastanza materiale: con l'archivio agli
        # inizi, un video piu' corto sarebbe un danno peggiore.
        if len(senza_ultimi) >= quante:
            riposati = senza_ultimi

    candidati = riposati
    scelti: list = []
    testi = [lead["line"]]
    visti_fact = {lead["fact_id"]} - {None}
    for c in candidati:
        if c["fact_id"] is not None and c["fact_id"] in visti_fact:
            continue
        if any(similarity(c["line"], t) > 0.45 for t in testi):
            continue
        scelti.append(c)
        testi.append(c["line"])
        if c["fact_id"] is not None:
            visti_fact.add(c["fact_id"])
        if len(scelti) >= quante:
            break
    return scelti


def segna_uso_yt(conn, ids: list) -> None:
    if not ids:
        return
    ora = time.time()
    conn.executemany(
        "UPDATE reels SET yt_uses = yt_uses + 1, yt_last_used = ? WHERE id=?",
        [(ora, i) for i in ids],
    )
    conn.commit()


def reels_by_status(conn: sqlite3.Connection, status: str) -> List[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM reels WHERE status=? ORDER BY id ASC", (status,)
    ).fetchall()


def set_reel_url(conn: sqlite3.Connection, reel_id: int, url: str) -> None:
    conn.execute("UPDATE reels SET video_url=? WHERE id=?", (url, reel_id))
    conn.commit()


def set_reel_status(conn: sqlite3.Connection, reel_id: int, status: str) -> None:
    conn.execute("UPDATE reels SET status=? WHERE id=?", (status, reel_id))
    conn.commit()


def mark_reel_published(conn: sqlite3.Connection, reel_id: int, media_id: str) -> None:
    conn.execute(
        "UPDATE reels SET status='published', published_at=?, ig_media_id=? WHERE id=?",
        (time.time(), media_id, reel_id),
    )
    conn.commit()


def reel_lines_used(conn: sqlite3.Connection) -> List[str]:
    """Frasi già usate: evita che lo stesso concetto torni fra i reel."""
    return [r["line"] for r in conn.execute("SELECT line FROM reels").fetchall()]


def facts_used_in_reels(conn: sqlite3.Connection) -> List[int]:
    """Curiosita' gia' diventate reel: non vanno riusate."""
    return [
        r["fact_id"]
        for r in conn.execute(
            "SELECT DISTINCT fact_id FROM reels WHERE fact_id IS NOT NULL"
        ).fetchall()
    ]


def salva_metriche_reel(conn: sqlite3.Connection, reel_id: int, piattaforma: str,
                        m: Dict[str, Any]) -> None:
    conn.execute(
        """INSERT OR REPLACE INTO reel_metrics
           (reel_id, platform, collected_at, views, likes, comments,
            avg_view_pct, avg_view_sec)
           VALUES (?,?,?,?,?,?,?,?)""",
        (reel_id, piattaforma, time.time(), m.get("views", 0), m.get("likes", 0),
         m.get("comments", 0), m.get("avg_view_pct", 0.0), m.get("avg_view_sec", 0.0)),
    )
    conn.commit()


def reel_migliori(conn: sqlite3.Connection, limit: int = 8) -> List[sqlite3.Row]:
    """I reel piu' visti fino in fondo, non i piu' visti e basta.

    L'ordinamento e' sulla percentuale media di visione perche' e' quella che
    YouTube usa per decidere se continuare a distribuire uno Short. Le
    visualizzazioni sono una conseguenza: ordinare su quelle vorrebbe dire
    imparare dal risultato invece che dalla causa.

    Il filtro sulle visualizzazioni minime evita che un video con nove
    visualizzazioni e il 90% di visione detti la linea: a quei numeri la
    percentuale e' rumore.
    """
    return conn.execute(
        """
        SELECT r.id, r.line, r.mood, r.hashtags,
               MAX(m.views) AS views,
               MAX(m.avg_view_pct) AS pct,
               MAX(m.avg_view_sec) AS secondi,
               MAX(m.likes) AS likes
        FROM reel_metrics m
        JOIN reels r ON r.id = m.reel_id
        WHERE m.platform = 'youtube'
        GROUP BY r.id
        HAVING views >= 25
        ORDER BY pct DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()


# ─── registro dei consumi ─────────────────────────────────────────────────────

def _recupera_consumi(conn: sqlite3.Connection) -> None:
    """Ricostruisce il registro dai contenuti gia' esistenti.

    Serve una volta sola, al primo avvio dopo l'introduzione della tabella:
    senza, tutte le curiosita' gia' pubblicate risulterebbero libere e
    tornerebbero in circolo. Gira a ogni apertura ma non costa nulla — le
    righe hanno chiave primaria e la seconda volta non entrano.
    """
    conn.execute(
        """INSERT OR IGNORE INTO fact_uses (fact_id, channel, used_at, ref)
           SELECT fact_id, 'instagram', COALESCE(published_at, created_at, 0),
                  'post-' || id
           FROM posts WHERE fact_id IS NOT NULL"""
    )
    conn.execute(
        """INSERT OR IGNORE INTO fact_uses (fact_id, channel, used_at, ref)
           SELECT fact_id, 'instagram', COALESCE(published_at, created_at, 0),
                  'reel-' || id
           FROM reels WHERE fact_id IS NOT NULL"""
    )
    # Su YouTube risultano consumate le curiosita' dei reel gia' caricati e
    # quelle che hanno fatto da spalla in un video lungo.
    conn.execute(
        """INSERT OR IGNORE INTO fact_uses (fact_id, channel, used_at, ref)
           SELECT fact_id, 'youtube', COALESCE(published_at, created_at, 0),
                  'reel-' || id
           FROM reels
           WHERE fact_id IS NOT NULL AND (youtube_id IS NOT NULL OR yt_uses > 0)"""
    )
    conn.commit()


def segna_uso_fatto(conn: sqlite3.Connection, fact_id: Optional[int],
                    canale: str, ref: str = "") -> None:
    if not fact_id:
        return
    conn.execute(
        """INSERT OR IGNORE INTO fact_uses (fact_id, channel, used_at, ref)
           VALUES (?,?,?,?)""",
        (fact_id, canale, time.time(), ref),
    )
    conn.commit()


def fatti_liberi(conn: sqlite3.Connection, canale: str,
                 limite: int = 50) -> List[sqlite3.Row]:
    """Curiosita' verificate mai uscite su questo canale.

    L'esclusione e' dura, non una preferenza. Prima era una preferenza perche'
    la produzione era scarsa e restare fermi sembrava peggio che ripetersi;
    ora la produzione si alza da sola quando le scorte scendono, quindi il
    ripiego non serve piu' — e quel ripiego era esattamente cio' che faceva
    uscire la stessa curiosita' due volte.
    """
    return conn.execute(
        """SELECT * FROM facts
           WHERE status IN ('approved','rendered','published')
             AND id NOT IN (SELECT fact_id FROM fact_uses WHERE channel = ?)
           ORDER BY confidence DESC, id ASC
           LIMIT ?""",
        (canale, limite),
    ).fetchall()


def quanti_liberi(conn: sqlite3.Connection, canale: str) -> int:
    return conn.execute(
        """SELECT COUNT(*) n FROM facts
           WHERE status IN ('approved','rendered','published')
             AND id NOT IN (SELECT fact_id FROM fact_uses WHERE channel = ?)""",
        (canale,),
    ).fetchone()["n"]
