#!/usr/bin/env python3
"""Ottiene il refresh token di YouTube. Si esegue una volta sola.

Perché serve uno script: l'autorizzazione OAuth richiede un passaggio dal
browser, e il codice che Google restituisce va scambiato con un refresh token
entro pochi minuti. Farlo a mano significa comporre due URL e un curl senza
sbagliare un parametro; qui è automatico.

    python3 setup_youtube.py

Prima di lanciarlo servono CLIENT_ID e CLIENT_SECRET in .env — vedi SETUP.md.
"""
from __future__ import annotations

import http.server
import json
import socketserver
import sys
import threading
import urllib.parse
import webbrowser
from pathlib import Path

import httpx

from engine.config import DATA_DIR, env

PORTA = 8723
REDIRECT = f"http://localhost:{PORTA}"
# Due permessi, non uno:
#   youtube.upload    caricare i video — il minimo per pubblicare
#   youtube.force-ssl leggere e scrivere i commenti sotto ai propri video
# Il secondo è ampio (copre tutta la gestione del canale) ma è l'unico che
# Google offre per rispondere ai commenti: non esiste un permesso "solo
# commenti". Senza, il ciclo pubblica lo stesso e salta le risposte.
#   yt-analytics.readonly  visualizzazioni e, soprattutto, percentuale media
#                          di visione: su Shorts è quella a decidere se il
#                          video viene spinto, non i like.
SCOPE = " ".join([
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.force-ssl",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
])
TOKEN_FILE = DATA_DIR / "youtube_token.json"

_codice = {"valore": None}


class _Ricevi(http.server.BaseHTTPRequestHandler):
    """Riceve il redirect di Google e cattura il codice di autorizzazione."""

    def do_GET(self):  # noqa: N802
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        _codice["valore"] = (q.get("code") or [None])[0]
        errore = (q.get("error") or [None])[0]

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        if _codice["valore"]:
            msg = "<h2>Fatto.</h2><p>Puoi chiudere questa pagina e tornare al terminale.</p>"
        else:
            msg = f"<h2>Autorizzazione negata</h2><p>{errore or 'motivo sconosciuto'}</p>"
        self.wfile.write(f"<html><body style='font-family:sans-serif;padding:3rem'>{msg}</body></html>".encode())

    def log_message(self, *a):  # silenzia il log del server
        pass


SPIEGAZIONE = """
Cosa sblocca questa autorizzazione:
  · caricare gli Short              (già attivo)
  · rispondere ai commenti          (nuovo)
  · leggere visualizzazioni e, soprattutto, la percentuale media di visione —
    il numero con cui YouTube decide se rilanciare uno Short. È quello che fa
    migliorare le frasi da sole: senza, il sistema scrive alla cieca.
"""


def main() -> int:
    client_id = env("YOUTUBE_CLIENT_ID")
    client_secret = env("YOUTUBE_CLIENT_SECRET")
    if not (client_id and client_secret):
        print(
            "Mancano YOUTUBE_CLIENT_ID e YOUTUBE_CLIENT_SECRET in .env.\n"
            "Si creano su console.cloud.google.com — vedi SETUP.md, sezione YouTube."
        )
        return 1

    url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode({
        "client_id": client_id,
        "redirect_uri": REDIRECT,
        "response_type": "code",
        "scope": SCOPE,
        # `offline` è ciò che fa rilasciare un refresh token; `consent` forza
        # la schermata anche se hai già autorizzato, altrimenti Google non lo
        # restituisce una seconda volta e ti ritrovi senza.
        "access_type": "offline",
        "prompt": "consent",
    })

    print(SPIEGAZIONE)
    print("Apro il browser per l'autorizzazione…")
    print(f"Se non si apre, incolla questo:\n\n{url}\n")

    server = socketserver.TCPServer(("", PORTA), _Ricevi)
    threading.Thread(target=server.handle_request, daemon=True).start()
    webbrowser.open(url)

    print("In attesa dell'autorizzazione…")
    for _ in range(120):
        if _codice["valore"]:
            break
        threading.Event().wait(1)
    server.server_close()

    if not _codice["valore"]:
        print("Nessun codice ricevuto entro due minuti.")
        return 1

    r = httpx.post(
        "https://oauth2.googleapis.com/token",
        data={
            "code": _codice["valore"],
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": REDIRECT,
            "grant_type": "authorization_code",
        },
        timeout=60,
    )
    if r.status_code >= 400:
        print(f"scambio fallito: {r.text[:300]}")
        return 1

    dati = r.json()
    refresh = dati.get("refresh_token")
    if not refresh:
        print(
            "Google non ha restituito un refresh token. Succede quando l'app "
            "era già stata autorizzata: revoca l'accesso su "
            "myaccount.google.com/permissions e riprova."
        )
        return 1

    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(json.dumps({"refresh_token": refresh}, indent=2))

    print("\n✓ Refresh token ottenuto.\n")
    print("Aggiungilo a .env e ai secret di GitHub:\n")
    print(f"YOUTUBE_REFRESH_TOKEN={refresh}\n")
    print(
        "⚠️ Se la schermata di consenso è in stato 'Testing', questo token\n"
        "   scade fra 7 giorni. Portala in 'Production' su Google Cloud:\n"
        "   resta senza verifica e mostra un avviso, ma non scade più."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
