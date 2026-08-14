"""Segnalazioni che devono arrivare a una persona, non restare a schermo.

Il problema che risolve: quasi ogni guasto qui è dentro un `try/except` che
stampa una riga e prosegue. È la scelta giusta — un errore su YouTube non deve
far fallire un reel già uscito su Instagram — ma ha un effetto collaterale
grave su un sistema che gira da solo tre volte al giorno: **il workflow resta
verde mentre il sistema si spegne**. Un token scaduto, una quota finita o un
permesso revocato non producono nessun segnale, e ce ne si accorge giorni dopo
guardando il profilo fermo.

Telegram non è configurato, quindi `review.notify` non manda niente. L'unico
canale che raggiunge davvero il proprietario senza altra configurazione è la
mail che GitHub spedisce quando un workflow **fallisce**. Quindi: si raccolgono
i guasti gravi durante il giro, e alla fine si fa fallire il job apposta —
dopo aver fatto tutto il resto, così un problema su YouTube non impedisce la
pubblicazione su Instagram.

La distinzione fra grave e non grave è la sola cosa che conta. Se fallisce
tutto, la mail diventa rumore e viene ignorata; ed è esattamente allora che
serve. Grave = "da qui in poi qualcosa non uscirà più finché non intervieni":
credenziali, quote, permessi. Non grave = un filmato non trovato, una foto
saltata, una rete che ha singhiozzato.
"""
from __future__ import annotations

import os
from typing import List, Tuple

_raccolti: List[Tuple[str, str]] = []


def segnala(ambito: str, messaggio: str) -> None:
    """Registra un guasto che richiede un intervento umano."""
    _raccolti.append((ambito, str(messaggio).strip().replace("\n", " ")[:400]))


def critico(exc: Exception) -> bool:
    """Dice se un'eccezione è di quelle che non si risolvono da sole.

    Si guarda il testo e non il tipo perché queste arrivano da API diverse,
    ognuna con la propria gerarchia di eccezioni, e quasi tutte le sollevano
    come errore generico con il motivo nel messaggio.
    """
    t = str(exc).lower()
    spie = (
        "invalid_grant",        # refresh token YouTube scaduto o revocato
        "insufficient",         # permesso OAuth mancante
        "force-ssl",
        "quota",                # quota giornaliera finita
        "rate limit",
        "resource_exhausted",
        "access token",         # token Meta scaduto o non valido
        "oauthexception",
        "permission",
        "401",
        "403",
    )
    return any(s in t for s in spie)


# L'elenco opposto: i guasti che passano da soli e non meritano una mail.
_PASSEGGERI = (
    "timeout", "timed out",
    "connection", "connessione",
    "temporarily", "try again",
    "502", "503", "504",
    "read operation",       # httpx quando la rete cade a metà scaricamento
    "nessun filmato",       # Pexels non aveva clip per quell'umore
)


def serio(exc: Exception) -> bool:
    """Come `critico`, ma al contrario: serio finché non si dimostra passeggero.

    `critico()` elenca le cause note — token scaduti, quote finite — e tace su
    tutto il resto. Sembra prudente e invece è il contrario: un elenco di cause
    è sempre incompleto, e ciò che non è nell'elenco passa inosservato proprio
    perché è nuovo. È andata così con la didascalia arrivata come dizionario:
    nessuna parola dell'elenco compariva nel messaggio, YouTube veniva saltato
    a ogni giro e il workflow restava verde per venti ore.

    Qui l'onere della prova è rovesciato. Si tace solo per i guasti che si
    riconoscono come passeggeri — una rete caduta, un 503, un filmato non
    trovato — e si segnala tutto il resto, compreso quello che non è ancora
    stato inventato. Un elenco di eccezioni benigne si può chiudere davvero;
    un elenco di modi di rompersi no.
    """
    t = str(exc).lower()
    return not any(s in t for s in _PASSEGGERI)


# Ogni canale ha il suo ritmo, quindi ognuno ha la sua soglia: una sola
# soglia buona per tutti sarebbe troppo stretta per l'episodio settimanale o
# troppo larga per i reel, e in entrambi i casi inutile. La regola con cui
# sono scelte: circa tre uscite mancate di fila. Meno sarebbe rumore — una
# rete storta, un giro perso — e le mail che arrivano per nulla si smettono
# di leggere proprio quando servono.
CANALI = (
    # nome            ore    query
    ("Instagram reel", 24.0,
     "SELECT MAX(published_at) FROM reels WHERE status = 'published'"),
    ("Instagram post", 30.0,
     "SELECT MAX(published_at) FROM posts WHERE status = 'published'"),
    ("YouTube Short",  24.0,
     "SELECT MAX(used_at) FROM fact_uses WHERE channel = 'youtube'"),
    # L'episodio esce il sabato: si allarma dopo tre settimane saltate, non
    # dopo un giorno.
    ("YouTube episodio", 24.0 * 22,
     "SELECT MAX(creato) FROM episodi"),
)

# Se un canale non ha MAI pubblicato, `MAX(...)` non restituisce niente e non
# c'e' nessuna data da confrontare — cioe' il caso peggiore, un canale che non
# ha mai funzionato, sarebbe l'unico a non allarmare mai. Si prende allora come
# riferimento il momento in cui il sistema ha dato il primo segno di vita: se
# gira da tre settimane e quel canale non ha prodotto nulla, non e' che deve
# ancora cominciare, e' che non parte.
_PRIMA_VITA = "SELECT MIN(published_at) FROM reels WHERE status = 'published'"


def silenzio(conn, ore: float | None = None) -> None:
    """Segnala i canali che non pubblicano da troppo tempo.

    Nasce da un guasto vero: un mio errore faceva arrivare la didascalia come
    dizionario invece che come testo, sqlite rifiutava la riga e il reel non
    veniva creato. Nessuna credenziale scaduta, nessuna quota finita — quindi
    `critico()` non riconosceva niente, i giri restavano verdi e il canale e'
    stato fermo un giorno intero senza che nulla lo dicesse.

    La lezione: non si puo' elencare in anticipo l'insieme dei modi in cui un
    sistema si rompe. Si puo' pero' controllare l'unica cosa che conta davvero,
    che e' sempre la stessa qualunque sia la causa — se sta ancora uscendo
    qualcosa. Un controllo sull'esito vale tutti quelli sulle cause messi
    insieme, perche' copre anche i guasti che non sono ancora stati inventati.

    Le soglie stanno in `CANALI`, una per canale. `ore` serve solo alle prove,
    per abbassarle tutte insieme e vedere se il controllo scatta davvero.
    """
    import time

    adesso = time.time()
    try:
        nascita = conn.execute(_PRIMA_VITA).fetchone()[0]
    except Exception:
        nascita = None

    for nome, soglia, sql in CANALI:
        if ore is not None:
            soglia = ore
        try:
            ultimo = conn.execute(sql).fetchone()[0]
        except Exception:
            continue          # tabella non ancora creata: non e' un guasto

        mai = ultimo is None
        riferimento = nascita if mai else ultimo
        if not riferimento or riferimento >= adesso - soglia * 3600:
            continue

        fermo = (adesso - riferimento) / 3600
        if mai:
            segnala(nome, f"non ha MAI pubblicato, e il sistema gira da "
                          f"{fermo / 24:.0f} giorni. Non deve cominciare: "
                          f"non parte.")
        else:
            segnala(nome, f"nessuna pubblicazione da {fermo:.0f} ore. "
                          f"Il giro gira ma non esce niente: guardare i log.")


# Uscite attese al giorno, canale per canale, piu' la query che conta quelle
# vere e quella che dice da quando il canale esiste. Tre al giorno perche' il
# workflow dei reel gira in tre fasce e ognuna pubblica al massimo un reel e
# uno Short.
RITMO = (
    ("YouTube Short", 3,
     "SELECT COUNT(DISTINCT ref) FROM fact_uses "
     "WHERE channel = 'youtube' AND ref LIKE 'yt-%' AND used_at > ?",
     "SELECT MIN(used_at) FROM fact_uses "
     "WHERE channel = 'youtube' AND ref LIKE 'yt-%'"),
    ("Instagram reel", 3,
     "SELECT COUNT(*) FROM reels WHERE status = 'published' "
     "AND published_at > ?",
     "SELECT MIN(published_at) FROM reels WHERE status = 'published'"),
)

# Tre giorni, non uno. La finestra e' stata scelta misurando, non a occhio:
# rigiocando il controllo sui quattro giri realmente eseguiti durante la
# giornata in cui il canale era guasto, la finestra di 24 ore dava sempre e
# solo "2 su 3" — mai abbastanza per far scattare una soglia che non sia
# rumorosa — mentre quella di 72 ore dava 4, 5, 5 e 6 su 9. Il motivo e' che
# una finestra mobile di un giorno prende sempre un pezzo del giorno prima e
# ci spalma dentro la giornata storta; su tre giorni il calo resta visibile.
_FINESTRA_ORE = 72.0


def cadenza(conn, ore: float = _FINESTRA_ORE) -> None:
    """Segnala i canali che pubblicano, ma molto meno di quanto dovrebbero.

    `silenzio()` sopra risponde a "esce ancora qualcosa?". Questo risponde a
    una domanda piu' fine: "ne esce quanto dovrebbe?". La differenza non e'
    accademica, e' successa. Su tre fasce orarie due non hanno prodotto nulla e
    una si': per `silenzio()` il canale era vivo — una pubblicazione nelle
    ultime ventiquattr'ore c'era — mentre aveva perso due terzi della resa.

    Ogni fascia saltata e' persa per sempre: non si recupera al giro dopo, ed
    e' una scelta. Due Short pubblicati a pochi minuti l'uno dall'altro entrano
    insieme nella stessa prova di pubblico e si tolgono spazio a vicenda, per
    cui recuperare renderebbe meno che accettare il buco. Ma proprio perche'
    non si recupera, il numero di uscite *e'* il risultato del sistema.

    La soglia sta a due terzi dell'atteso. Sotto quella, il canale non sta
    avendo una giornata storta: sta rendendo stabilmente meno di quanto e'
    costruito per rendere. Una fascia persa ogni tanto resta sotto silenzio di
    proposito — dipendiamo da cinque servizi esterni e da un programmatore che
    parte con un'ora di ritardo, segnalarla vorrebbe dire una mail quasi ogni
    giorno, e le mail che arrivano quasi ogni giorno non si leggono piu'.
    """
    import time

    limite = time.time() - ore * 3600

    for nome, al_giorno, sql, sql_prima in RITMO:
        try:
            uscite = conn.execute(sql, (limite,)).fetchone()[0] or 0
            prima = conn.execute(sql_prima).fetchone()[0]
        except Exception:
            continue          # tabella non ancora creata: non e' un guasto

        # Un canale piu' giovane della finestra ha pochi numeri perche' e'
        # appena nato, non perche' e' rotto. Senza questa riga ogni canale
        # nuovo manderebbe una mail il primo giorno — cioe' il controllo
        # direbbe "guasto" proprio quando tutto sta andando come previsto.
        if not prima or prima > limite:
            continue

        # Il canale del tutto muto e' gia' compito di `silenzio()`, che sa
        # anche distinguere "si e' fermato" da "non ha mai iniziato". Qui si
        # guarda solo cio' che a quello sfugge: pubblica, ma troppo poco.
        atteso_finestra = al_giorno * ore / 24.0
        if uscite and uscite < atteso_finestra * 2 / 3:
            segnala(nome, f"{uscite} uscite in {ore / 24:.0f} giorni invece di "
                          f"{atteso_finestra:.0f}. Il canale non e' fermo, ma "
                          f"rende sotto i due terzi: guardare quali giri non "
                          f"hanno prodotto e perche'.")


def riepiloga(contesto: str = "") -> int:
    """Stampa i guasti raccolti e dice quanti erano. Zero = tutto a posto.

    Le annotazioni `::error::` compaiono in cima alla pagina del workflow, così
    il motivo si legge senza aprire i log.
    """
    if not _raccolti:
        return 0

    su_actions = bool(os.environ.get("GITHUB_ACTIONS"))
    print(f"\n{'─' * 62}")
    print(f"{len(_raccolti)} problema/i che non si risolvono da soli:")
    for ambito, msg in _raccolti:
        print(f"  ✗ [{ambito}] {msg}")
        if su_actions:
            print(f"::error title=Curiosity {ambito}::{msg}")

    # Se Telegram c'è, arriva anche di là: la mail di GitHub è il minimo
    # garantito, non l'unico canale possibile.
    try:
        from . import review

        if review.enabled():
            righe = "\n".join(f"• <b>{a}</b>: {m}" for a, m in _raccolti)
            review.notify(f"⚠️ <b>Curiosity {contesto}</b>\n{righe}")
    except Exception:
        pass

    print(
        "\nIl giro ha comunque fatto tutto il resto: questo passo fallisce "
        "apposta,\nperché la mail di GitHub è l'unico modo di accorgersene."
    )
    print("─" * 62)
    return len(_raccolti)


def azzera() -> None:
    """Solo per i test: lo stato è per processo e ogni giro parte pulito."""
    _raccolti.clear()
