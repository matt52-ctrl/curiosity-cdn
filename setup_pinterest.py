#!/usr/bin/env python3
"""Ottiene il refresh token di Pinterest. Si esegue una volta all'anno.

    python3 setup_pinterest.py

⚠️  QUESTO SCRIPT È ANCHE IL VIDEO DEMO.

Pinterest concede l'accesso Standard — quello in cui i pin sono davvero
visibili al pubblico — solo dopo aver visto un filmato del tuo flusso OAuth
mentre gira: la schermata di consenso, l'autorizzazione, e un'azione di
scrittura riuscita. È esattamente quello che fa questa procedura.

Quindi: avvia la registrazione dello schermo PRIMA di lanciarla (su Mac,
Cmd+Shift+5 → Registra schermo intero), lasciala andare fino in fondo, e alla
fine avrai insieme il token e il video da allegare alla richiesta.

PRIMA DI LANCIARLO servono, su developers.pinterest.com:
  1. un profilo BUSINESS con lo username giusto — non quello personale. I pin
     escono a nome di quel profilo, e non si cambia dopo.
  2. un'app creata da "Connect app"
  3. fra i Redirect URI dell'app, ESATTAMENTE lo stesso indirizzo che metti
     in .env sotto PINTEREST_REDIRECT_URI (barra finale compresa)

Poi in .env:
  PINTEREST_APP_ID=...
  PINTEREST_APP_SECRET=...
  PINTEREST_REDIRECT_URI=...
"""
from __future__ import annotations

import sys
import urllib.parse
import webbrowser
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

AUTORIZZA = "https://www.pinterest.com/oauth/"
TOKEN = "https://api.pinterest.com/v5/oauth/token"

# I permessi minimi per fare quello che facciamo: leggere le bacheche per
# trovare la nostra, crearla se manca, e creare i pin. Non si chiede altro —
# ogni permesso in piu' e' una domanda in piu' a cui rispondere nella
# richiesta di accesso Standard, e una ragione in piu' per vedersela negare.
SCOPE = "boards:read,boards:write,pins:read,pins:write"


def _errore(messaggio: str) -> int:
    print(f"\n✗ {messaggio}")
    return 1


def main() -> int:
    from engine.config import env

    print(__doc__)
    print("─" * 70)

    chiave = env("PINTEREST_APP_ID")
    segreto = env("PINTEREST_APP_SECRET")
    ritorno = env("PINTEREST_REDIRECT_URI")
    if not (chiave and segreto):
        return _errore("mancano PINTEREST_APP_ID o PINTEREST_APP_SECRET in .env.")
    if not ritorno:
        return _errore("manca PINTEREST_REDIRECT_URI in .env.")
    if not ritorno.startswith("https://"):
        return _errore(f"PINTEREST_REDIRECT_URI deve essere https:// (ora è {ritorno}).")

    url = f"{AUTORIZZA}?" + urllib.parse.urlencode({
        "client_id": chiave,
        "redirect_uri": ritorno,
        "response_type": "code",
        "scope": SCOPE,
    })

    print("\n1. Apro il browser. Autorizza con il profilo BUSINESS del canale.\n")
    print(f"   Se non si apre, incolla questo:\n\n   {url}\n")
    try:
        webbrowser.open(url)
    except Exception:
        pass

    print("2. Dopo aver autorizzato finirai su una pagina che probabilmente")
    print("   darà errore. È NORMALE: quello che serve è nella barra degli")
    print("   indirizzi.\n")
    print("3. Copia TUTTO l'indirizzo di quella pagina e incollalo qui.\n")

    try:
        incollato = input("   indirizzo: ").strip()
    except (EOFError, KeyboardInterrupt):
        return _errore("annullato.")
    if not incollato:
        return _errore("non hai incollato nulla.")

    codice = incollato
    if "?" in incollato or incollato.startswith("http"):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(incollato).query)
        if q.get("error"):
            return _errore(f"Pinterest ha negato: {q['error'][0]}")
        if not q.get("code"):
            return _errore("in quell'indirizzo non c'è nessun `code`. Assicurati "
                           "di aver copiato la pagina DOPO l'autorizzazione.")
        codice = q["code"][0]
    codice = urllib.parse.unquote(codice)

    print("\n   scambio il codice…")
    # Credenziali in Basic auth e non nel corpo: Pinterest risponde 401 senza
    # dire quale delle due cose manchi, ed e' l'errore che costa mezz'ora.
    r = httpx.post(
        TOKEN,
        auth=(chiave, segreto),
        data={"grant_type": "authorization_code", "code": codice,
              "redirect_uri": ritorno},
        timeout=60,
    )
    if r.status_code >= 400 or "refresh_token" not in r.text:
        aiuto = ""
        if "redirect" in r.text.lower():
            aiuto = ("\n  L'indirizzo di ritorno non combacia. Dev'essere IDENTICO\n"
                     "  a quello registrato nell'app, barra finale compresa.")
        return _errore(f"scambio fallito: {r.status_code} {r.text[:300]}{aiuto}")

    refresh = r.json()["refresh_token"]
    _scrivi_env(refresh)

    print("\n✓ Autorizzato. Ora l'azione di scrittura, che è la parte che")
    print("  Pinterest vuole vedere nel filmato:\n")
    _verifica()

    print("\n" + "─" * 70)
    print("RESTA UNA COSA, a mano perché l'API non la permette:")
    repo = env("GITHUB_REPO") or "TUO-UTENTE/TUO-REPO"
    print(f"  1. apri  https://github.com/{repo}/settings/secrets/actions")
    print("  2. crea PINTEREST_APP_ID, PINTEREST_APP_SECRET, PINTEREST_REFRESH_TOKEN")
    print(f"  3. il refresh token è:\n\n     {refresh}\n")
    print("Adesso ferma la registrazione: quel filmato è ciò che allegherai")
    print("alla richiesta di Standard access.")
    return 0


def _scrivi_env(refresh: str) -> None:
    """Aggiorna .env da sé: ricopiare a mano è dove si sbaglia."""
    f = ROOT / ".env"
    if not f.exists():
        print("  (.env non trovato: mettilo tu)")
        return
    righe = f.read_text().splitlines()
    for i, r in enumerate(righe):
        if r.startswith("PINTEREST_REFRESH_TOKEN="):
            righe[i] = f"PINTEREST_REFRESH_TOKEN={refresh}"
            break
    else:
        righe.append(f"PINTEREST_REFRESH_TOKEN={refresh}")
    f.write_text("\n".join(righe) + "\n")
    print("  ✓ scritto in .env")


def _verifica() -> None:
    """Prova subito una lettura e una scrittura: se non va, si scopre adesso."""
    import importlib

    from engine import config as _cfg
    importlib.reload(_cfg)
    from engine.publish import pinterest as p
    importlib.reload(p)

    try:
        token = p._token()
        print("  ✓ token valido")
        board = p.bacheca(token)
        print(f"  ✓ bacheca pronta: {board}")
    except Exception as exc:
        print(f"  ✗ {exc}")


if __name__ == "__main__":
    raise SystemExit(main())
