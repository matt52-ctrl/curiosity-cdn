#!/usr/bin/env python3
"""Ottiene il refresh token di TikTok. Si esegue una volta all'anno.

    python3 setup_tiktok.py

Perché è più scomodo di quello di YouTube: TikTok **pretende che l'indirizzo di
ritorno sia HTTPS**. Con YouTube bastava `http://localhost`, e lo script apriva
un server locale che catturava il codice da solo. Qui non si può: un server
locale in HTTPS richiederebbe un certificato, e generarne uno per un'operazione
annuale è peggio del problema.

Quindi il codice te lo passi a mano: autorizzi, il browser ti manda su una
pagina qualsiasi (anche una che dà 404 — non deve funzionare, deve solo essere
un indirizzo tuo e in HTTPS), e tu incolli qui l'indirizzo che vedi nella barra.
Trenta secondi, una volta all'anno.

PRIMA DI LANCIARLO servono, su developers.tiktok.com:
  1. un account sviluppatore (gratuito, nessun audit per quello che ci serve)
  2. un'app con il prodotto **Content Posting API** attivo
  3. il permesso **video.upload** — NON serve `video.publish`, che è quello
     che richiede l'audit di 2-4 settimane
  4. l'indirizzo di ritorno registrato fra i "Redirect URI" dell'app

Poi in .env:
  TIKTOK_CLIENT_KEY=...
  TIKTOK_CLIENT_SECRET=...
  TIKTOK_REDIRECT_URI=...      (lo stesso registrato al punto 4)
"""
from __future__ import annotations

import json
import sys
import urllib.parse
import webbrowser
from pathlib import Path

import httpx

from engine.config import DATA_DIR, ROOT, env

AUTORIZZA = "https://www.tiktok.com/v2/auth/authorize/"
TOKEN = "https://open.tiktokapis.com/v2/oauth/token/"

# `video.upload` deposita bozze nell'inbox e NON richiede l'audit.
# `video.publish` pubblica direttamente, ma lo richiede: 2-4 settimane.
# `video.list` e `user.info.stats` danno viste, like e follower.
#
# ⚠️  LA TRAPPOLA, scritta qui perche' e' il punto in cui si perde una
# settimana senza capire perche'. L'audit di TikTok e il permesso nel token
# sono due cose separate. Il giorno che TikTok approva l'app NON succede
# niente da solo: il refresh token in .env e' stato ottenuto chiedendo
# `video.upload` e basta, e continuera' a valere solo per quello finche' non
# si rifa' l'autorizzazione dal browser. Il sistema resterebbe a depositare
# bozze, senza errori, con l'audit gia' in tasca.
#
# Quindi il giorno dell'approvazione servono DUE gesti, in quest'ordine:
#   1. aggiungere gli scope qui sotto tramite TIKTOK_SCOPE in .env
#   2. rilanciare  python3 setup_tiktok.py  e riautorizzare dal browser
# Da li' in poi la config e' gia' su `mode: direct` e non serve altro.
#
# Sta in una variabile d'ambiente e non nel codice perche' chiedere uno scope
# che l'app non ha ancora abilitato fa fallire l'autorizzazione: scriverlo
# fisso adesso romperebbe l'unica strada che oggi funziona.
SCOPE_COMPLETO = "video.upload,video.publish,video.list,user.info.stats"

FILE_TOKEN = DATA_DIR / "tiktok_token.json"
FILE_TOKEN_PROVA = DATA_DIR / "tiktok_token_prova.json"

# `--prova` serve a autorizzare l'app **Sandbox** senza rompere quella vera.
#
# Il sandbox di TikTok ha client key e secret propri, e permette Direct Post
# senza aspettare l'audit — ma pubblica in SELF_ONLY, visibile al solo autore.
# E' quindi l'unico modo di provare per davvero tutto il percorso della
# pubblicazione diretta (creator_info, init, invio a pezzi, is_aigc) prima del
# giorno dell'approvazione, invece di scoprire i difetti quando contano.
#
# Senza questo interruttore la prova sarebbe distruttiva, e in un modo
# silenzioso: `_scrivi_env()` riscrive TIKTOK_REFRESH_TOKEN dentro .env, quindi
# autorizzare col sandbox sovrascriverebbe il token di produzione. Il
# caricamento del giorno dopo fallirebbe con la causa lontana ore, che e'
# esattamente il tipo di guasto che costa una serata.
#
# Le credenziali del sandbox non vanno in .env: si passano davanti al comando,
#   TIKTOK_CLIENT_KEY=... TIKTOK_CLIENT_SECRET=... python3 setup_tiktok.py --prova
# e restano solo in quel processo, perche' load_dotenv non sovrascrive quello
# che c'e' gia' nell'ambiente.
PROVA = "--prova" in sys.argv

# Nel sandbox gli scope pieni ci sono gia' — e' il senso del sandbox — quindi
# li si chiede senza aspettare TIKTOK_SCOPE. In produzione resta `video.upload`
# finche' l'audit non passa, perche' chiedere un permesso non abilitato fa
# fallire l'autorizzazione.
SCOPE = env("TIKTOK_SCOPE") or (SCOPE_COMPLETO if PROVA else "video.upload")

SPIEGAZIONE = """
Cosa sblocca questa autorizzazione:
  · i video montati dal ciclo arrivano da soli come BOZZE nella tua app TikTok
  · niente più caricamenti a mano: il caricamento multiplo su TikTok Studio
    richiede 1.000 follower, e sotto quella soglia si va uno alla volta
  · resta a te un tocco per pubblicare — la bozza non esce da sola

Limite di TikTok: 5 bozze ogni 24 ore. Il ciclo lo rispetta da sé.
"""


def _errore(msg: str) -> int:
    print(f"\n✗ {msg}")
    return 1


def main() -> int:
    chiave = env("TIKTOK_CLIENT_KEY")
    segreto = env("TIKTOK_CLIENT_SECRET")
    ritorno = env("TIKTOK_REDIRECT_URI")

    if not (chiave and segreto):
        return _errore(
            "Mancano TIKTOK_CLIENT_KEY e TIKTOK_CLIENT_SECRET in .env.\n"
            "  Si creano su developers.tiktok.com → la tua app → Basic information."
        )
    if not ritorno:
        return _errore(
            "Manca TIKTOK_REDIRECT_URI in .env.\n"
            "  Dev'essere un indirizzo HTTPS registrato fra i Redirect URI\n"
            "  dell'app su developers.tiktok.com. Non deve mostrare nulla di\n"
            "  utile: va bene anche una pagina che dà 404, serve solo a\n"
            "  ricevere il codice nella barra degli indirizzi.\n"
            "  Se non ne hai uno, va bene la pagina del tuo repo su GitHub."
        )
    if not ritorno.startswith("https://"):
        return _errore(
            f"TIKTOK_REDIRECT_URI deve cominciare per https:// (ora è {ritorno}).\n"
            "  TikTok rifiuta http, anche su localhost. È una loro regola, non\n"
            "  aggirabile."
        )

    url = AUTORIZZA + "?" + urllib.parse.urlencode({
        "client_key": chiave,
        "response_type": "code",
        "scope": SCOPE,
        "redirect_uri": ritorno,
        "state": "curiosity",
    })

    if PROVA:
        print("\n── MODALITA' PROVA (Sandbox) ────────────────────────────────")
        print("  .env non verra' toccato: il token finisce in un file a parte.")
        print("  Autorizza con l'account gia' registrato fra i Target Users.")
        print("  Quello che pubblicherai sara' visibile SOLO a te (SELF_ONLY).")
        print("─" * 62 + "\n")
    else:
        print(SPIEGAZIONE)
    print("1. Apro il browser. Autorizza con l'account del canale.\n")
    print(f"   Se non si apre, incolla questo:\n\n   {url}\n")
    try:
        webbrowser.open(url)
    except Exception:
        pass

    print("2. Dopo aver autorizzato finirai su una pagina che probabilmente")
    print("   darà errore. È NORMALE e previsto: quello che serve è nella")
    print("   barra degli indirizzi.\n")
    print("3. Copia TUTTO l'indirizzo di quella pagina e incollalo qui sotto.\n")

    try:
        incollato = input("   indirizzo: ").strip()
    except (EOFError, KeyboardInterrupt):
        return _errore("annullato.")

    if not incollato:
        return _errore("non hai incollato nulla.")

    # Si accetta sia l'indirizzo intero sia il solo codice: incollare l'URL è
    # più naturale, ma qualcuno estrae il codice a mano e non deve fallire.
    codice = incollato
    if "?" in incollato or incollato.startswith("http"):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(incollato).query)
        if q.get("error"):
            return _errore(f"TikTok ha negato: {q['error'][0]} "
                           f"{(q.get('error_description') or [''])[0]}")
        if not q.get("code"):
            return _errore(
                "in quell'indirizzo non c'è nessun `code`. Assicurati di aver\n"
                "  copiato la pagina DOPO l'autorizzazione, non quella prima."
            )
        codice = q["code"][0]

    # TikTok restituisce il codice già codificato per l'URL: va decodificato
    # prima dello scambio, o il confronto fallisce con un errore che non lo dice.
    codice = urllib.parse.unquote(codice)

    print("\n   scambio il codice…")
    r = httpx.post(
        TOKEN,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "client_key": chiave,
            "client_secret": segreto,
            "code": codice,
            "grant_type": "authorization_code",
            "redirect_uri": ritorno,
        },
        timeout=60,
    )
    d = r.json()
    if r.status_code >= 400 or "refresh_token" not in d:
        descrizione = d.get("error_description") or r.text[:300]
        aiuto = ""
        if "redirect" in str(descrizione).lower():
            aiuto = ("\n  L'indirizzo di ritorno non combacia. Dev'essere IDENTICO\n"
                     "  a quello registrato nell'app, barra finale compresa.")
        elif "code_verifier" in str(descrizione).lower():
            aiuto = ("\n  L'app è registrata come Desktop/Mobile, che richiede PKCE.\n"
                     "  Cambiala in tipo Web su developers.tiktok.com.")
        return _errore(f"scambio fallito: {descrizione}{aiuto}")

    refresh = d["refresh_token"]
    dove = FILE_TOKEN_PROVA if PROVA else FILE_TOKEN
    dove.parent.mkdir(parents=True, exist_ok=True)
    dove.write_text(json.dumps({"refresh_token": refresh,
                                "scope": d.get("scope", ""),
                                "prova": PROVA}, indent=2))

    if PROVA:
        print(f"\n✓ Autorizzato (SANDBOX). .env non e' stato toccato.")
        print(f"  token in {dove.relative_to(ROOT)}")
        print(f"  scope concessi da TikTok: {d.get('scope') or '(non dichiarati)'}")
        _verifica_prova(chiave, segreto, refresh)
        return 0

    _scrivi_env(refresh)
    print("\n✓ Autorizzato. Controllo che funzioni davvero:")
    _verifica()

    print("\n" + "─" * 68)
    print("RESTA UNA COSA, a mano perché l'API non la permette:")
    print("  il token va messo anche nei secret di GitHub, o i cicli che")
    print("  girano lì non potranno caricare nulla.\n")
    repo = env("GITHUB_REPO") or "TUO-UTENTE/TUO-REPO"
    print(f"  1. apri  https://github.com/{repo}/settings/secrets/actions")
    print("  2. crea TIKTOK_CLIENT_KEY, TIKTOK_CLIENT_SECRET, TIKTOK_REFRESH_TOKEN")
    print(f"  3. il refresh token è:\n\n     {refresh}\n")
    print("Il refresh token dura 365 giorni: questa procedura si rifà fra un anno.")
    return 0


def _scrivi_env(refresh: str) -> None:
    """Aggiorna .env da sé: ricopiare a mano è dove si sbaglia."""
    f = ROOT / ".env"
    if not f.exists():
        print("  (.env non trovato: mettilo tu)")
        return
    righe = f.read_text().splitlines()
    for i, r in enumerate(righe):
        if r.startswith("TIKTOK_REFRESH_TOKEN="):
            righe[i] = f"TIKTOK_REFRESH_TOKEN={refresh}"
            break
    else:
        righe.append(f"TIKTOK_REFRESH_TOKEN={refresh}")
    f.write_text("\n".join(righe) + "\n")
    print("  ✓ scritto in .env")


def _verifica_prova(chiave: str, segreto: str, refresh: str) -> None:
    """Chiede a TikTok cosa concede davvero al sandbox, invece di dedurlo.

    La domanda che conta e' una sola: `privacy_level_options` contiene
    PUBLIC_TO_EVERYONE oppure solo SELF_ONLY? La documentazione dice che un
    client non auditato e' confinato alla visibilita' privata, ma la
    documentazione descrive il caso generale e noi stiamo per pubblicare
    davvero. Meglio leggerlo dalla loro risposta.

    Non passa da engine.publish.tiktok perche' quel modulo prende le
    credenziali da .env, cioe' da produzione: userebbe l'app sbagliata e
    direbbe una verita' su un'altra applicazione.
    """
    print("\n  Controllo cosa concede il sandbox:")
    r = httpx.post(
        TOKEN,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={"client_key": chiave, "client_secret": segreto,
              "grant_type": "refresh_token", "refresh_token": refresh},
        timeout=60,
    )
    d = r.json()
    if "access_token" not in d:
        print(f"  ✗ rinnovo fallito: {str(d)[:200]}")
        return
    print("  ✓ il token si rinnova")

    r = httpx.post(
        "https://open.tiktokapis.com/v2/post/publish/creator_info/query/",
        headers={"Authorization": f"Bearer {d['access_token']}",
                 "Content-Type": "application/json; charset=UTF-8"},
        json={}, timeout=60,
    )
    c = r.json()
    errore = (c.get("error") or {}).get("code", "")
    if errore not in ("ok", "", None):
        print(f"  ✗ creator_info: {errore} — "
              f"{(c.get('error') or {}).get('message', '')[:160]}")
        return
    dati = c.get("data", {})
    opzioni = dati.get("privacy_level_options") or []
    print(f"  ✓ creator_info risponde")
    print(f"      profilo:  {dati.get('creator_username', '?')}")
    print(f"      privacy:  {opzioni or 'nessuna'}")
    print(f"      residui:  {dati.get('max_video_post_duration_sec', '?')}s max "
          f"per video")
    if "PUBLIC_TO_EVERYONE" in opzioni:
        print("\n  → il sandbox concede la visibilita' pubblica: `carica()` "
              "pubblicherebbe davvero.")
    else:
        print("\n  → niente PUBLIC_TO_EVERYONE, come da documentazione per i "
              "client non auditati.\n     E' il caso che `carica()` gestisce "
              "ripiegando sulla bozza.")


def _verifica() -> None:
    """Prova subito il rinnovo del token: se non va, si scopre adesso."""
    import importlib

    import engine.config as c
    importlib.reload(c)
    from engine.publish import tiktok
    importlib.reload(tiktok)
    try:
        tiktok._token_accesso()
        print("  ✓ il token risponde")
    except Exception as exc:
        print(f"  ✗ {str(exc)[:150]}")


if __name__ == "__main__":
    sys.exit(main())
