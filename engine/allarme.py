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
