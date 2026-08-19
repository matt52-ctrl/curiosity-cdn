#!/usr/bin/env python3
"""Collega il bot Telegram. Si esegue una volta sola.

    python3 setup_telegram.py

A cosa serve, in concreto: è il canale con cui la pipeline ti parla. Oggi
serve a tre cose che senza di lui non esistono —

  · le didascalie TikTok. L'endpoint inbox accetta solo i byte del video,
    `post_info` non esiste, quindi titolo e hashtag NON sono allegabili alla
    bozza. Arrivano qui, in un blocco che sulle app si copia con un tocco.
    Senza Telegram torni a copiarle da un foglio HTML sul computer.
  · l'approvazione dei post prima che escano, se la accendi.
  · gli allarmi quando un canale si ferma. I caroselli sono rimasti fermi
    cinque giri di fila e te ne sei accorto solo perché l'ho guardato io.

PRIMA DI LANCIARLO, due minuti su Telegram:

  1. apri una conversazione con  @BotFather
  2. scrivi  /newbot
  3. dai un nome (quello che vuoi) e uno username che finisca per `bot`,
     per esempio  oddlywired_bot
  4. BotFather ti risponde con un token tipo  8123456789:AAG...
  5. APRI IL TUO BOT e premi «Avvia» — è il passo che tutti saltano: finché
     non gli scrivi tu per primo, Telegram non permette al bot di scriverti,
     e questo script non riesce a scoprire il tuo identificativo.

Il token non è una password del tuo account: è la chiave del bot, e da
BotFather si revoca con /revoke senza toccare nient'altro.
"""
from __future__ import annotations

import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

API = "https://api.telegram.org"


def _errore(messaggio: str) -> int:
    print(f"\n✗ {messaggio}")
    return 1


def main() -> int:
    print(__doc__)
    print("─" * 68)

    try:
        token = input("\n  Incolla qui il token che ti ha dato BotFather: ").strip()
    except (EOFError, KeyboardInterrupt):
        return _errore("annullato.")

    if not token:
        return _errore("non hai incollato nulla.")
    # Un errore che capita davvero: si incolla la riga intera copiata dalla
    # chat, cioè "Use this token to access the HTTP API: 8123:AAG...".
    if ":" not in token or token.split(":")[0].strip().isdigit() is False:
        if "token" in token.lower() and ":" in token:
            token = token.rsplit(":", 1)[-1].strip()
        if ":" not in token:
            return _errore("non sembra un token. Deve avere la forma  123456789:AAG…")

    print("\n  verifico il bot…")
    r = httpx.get(f"{API}/bot{token}/getMe", timeout=30)
    if r.status_code != 200 or not r.json().get("ok"):
        return _errore(f"Telegram rifiuta il token: {r.text[:200]}")
    bot = r.json()["result"]
    print(f"  ✓ bot @{bot['username']} ({bot.get('first_name','')})")

    # L'identificativo della chat non si può chiedere: si ricava dai messaggi
    # che il bot ha ricevuto. Per questo il passo 5 delle istruzioni non è
    # facoltativo — senza un tuo messaggio qui non c'è niente da leggere.
    print("\n  cerco la tua chat…")
    u = httpx.get(f"{API}/bot{token}/getUpdates", timeout=30).json()
    chat = None
    for agg in reversed(u.get("result", [])):
        msg = agg.get("message") or agg.get("edited_message") or {}
        if msg.get("chat", {}).get("id"):
            chat = msg["chat"]
            break

    if not chat:
        print("\n  Nessun messaggio trovato. Manca il passo 5:")
        print(f"    apri  https://t.me/{bot['username']}  e premi «Avvia»")
        print("    (o scrivigli qualsiasi cosa), poi rilancia questo script.")
        return 1

    chat_id = str(chat["id"])
    nome = chat.get("username") or chat.get("first_name") or chat_id
    print(f"  ✓ chat trovata: {nome}  (id {chat_id})")

    _scrivi_env(token, chat_id)

    print("\n  mando un messaggio di prova…")
    p = httpx.post(
        f"{API}/bot{token}/sendMessage",
        data={"chat_id": chat_id, "parse_mode": "HTML",
              "text": "✓ <b>Oddly Wired collegato.</b>\n\nDa qui in avanti ti arrivano "
                      "le didascalie TikTok, gli allarmi quando un canale si ferma e "
                      "le richieste di approvazione.\n\nQuesto è un blocco che si copia "
                      "con un tocco:\n<code>funziona</code>"},
        timeout=30,
    )
    if p.status_code != 200 or not p.json().get("ok"):
        return _errore(f"invio fallito: {p.text[:200]}")
    print("  ✓ guarda Telegram: dovresti averlo ricevuto.")

    print("\n" + "─" * 68)
    print("RESTA UNA COSA, a mano perché l'API non la permette:")
    print("  i due valori vanno anche nei secret di GitHub, o i cicli che")
    print("  girano lì continueranno a non dirti niente.\n")
    from engine.config import env as _env

    repo = _env("GITHUB_REPO") or "TUO-UTENTE/TUO-REPO"
    print(f"  1. apri  https://github.com/{repo}/settings/secrets/actions")
    print(f"  2. TELEGRAM_BOT_TOKEN  →  {token}")
    print(f"  3. TELEGRAM_CHAT_ID    →  {chat_id}")
    return 0


def _scrivi_env(token: str, chat_id: str) -> None:
    """Aggiorna .env da sé: ricopiare a mano è dove si sbaglia."""
    f = ROOT / ".env"
    if not f.exists():
        print("  (.env non trovato: mettili tu)")
        return
    righe = f.read_text().splitlines()
    valori = {"TELEGRAM_BOT_TOKEN": token, "TELEGRAM_CHAT_ID": chat_id}
    for chiave, valore in valori.items():
        for i, r in enumerate(righe):
            if r.startswith(f"{chiave}="):
                righe[i] = f"{chiave}={valore}"
                break
        else:
            righe.append(f"{chiave}={valore}")
    f.write_text("\n".join(righe) + "\n")
    print("  ✓ scritti in .env")


if __name__ == "__main__":
    raise SystemExit(main())
