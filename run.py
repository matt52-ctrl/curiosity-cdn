#!/usr/bin/env python3
"""Curiosity Engine — orchestratore.

    python3 run.py preview          # renderizza slide finte (nessuna API) — testa i template
    python3 run.py ideas            # genera e verifica un batch di curiosità
    python3 run.py build            # trasforma il prossimo fatto approvato in un post
    python3 run.py review           # legge le decisioni da Telegram
    python3 run.py publish          # pubblica i post approvati
    python3 run.py metrics          # raccoglie le insights
    python3 run.py report           # cosa sta funzionando
    python3 run.py cycle            # il ciclo completo — questo è ciò che va nel cron
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
import traceback
from pathlib import Path
from typing import List

from engine import allarme, analytics, ideas, render, review, visuals, writer
from engine.config import DATA_DIR, ROOT, cfg, env
from engine.db import (
    connect,
    quanti_liberi,
    get_post,
    insert_post,
    mark_published,
    next_approved_fact,
    posts_by_status,
    set_fact_status,
    set_post_status,
    set_post_urls,
)
from engine.hosting import upload
from engine.publish import instagram, tiktok


# ─── preview ──────────────────────────────────────────────────────────────────

SAMPLE = [
    {"kicker": "01", "headline": "Your brain edits your memories every time you recall them",
     "body": "", "image_query": "old photographs on wooden table", "image_kind": "concept"},
    {"kicker": "The finding", "headline": "Recall is a rewrite, not a replay",
     "body": "Each time a memory is retrieved it becomes briefly unstable and has to be stored again — and whatever you are feeling at that moment gets folded in.",
     "image_query": "faded family photograph album", "image_kind": "concept"},
    # Soggetto reale: l'esperimento esiste, quindi foto vera anche se la
    # generazione è attiva.
    {"kicker": "How we know", "headline": "Rats, a tone, and a missing fear",
     "body": "Nader and LeDoux blocked protein synthesis in the amygdala right after recall. The fear memory did not survive the reconsolidation. It was gone.",
     "image_query": "laboratory rat", "image_kind": "real_subject"},
    {"kicker": "The catch", "headline": "The memories you revisit most are the least accurate",
     "body": "Every retelling adds a layer. The story you are most certain about has been edited the most times.",
     "image_query": "handwritten letter crossed out", "image_kind": "concept"},
    {"kicker": "", "headline": "Your clearest memory is your most rewritten one",
     "body": "Follow for one quiet fact a day.",
     "image_query": "empty room afternoon light", "image_kind": "concept"},
]


def cmd_preview(args: argparse.Namespace) -> int:
    template = args.template or cfg.get("format.template", "editorial")
    slides = [dict(s) for s in SAMPLE]        # copia: SAMPLE è riusato fra i template

    if args.images:
        print("→ cerco immagini…")
        found = visuals.attach_images(slides)
        print(f"→ {found}/{len(slides)} slide con immagine")

    print(f"→ rendering template '{template}'…")
    paths = render.render_slides(slides, f"preview-{template}", template)
    for p in paths:
        print(f"  {p}")
    print(f"\nHTML ispezionabile: {paths[0].parent / 'preview.html'}")
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    """Dice cosa manca davvero, distinguendo "vuota" da "ancora il segnaposto".

    Esiste perché .env nasce come copia di .env.example, che contiene valori
    finti ma non vuoti: senza questo controllo sembrano configurati e l'errore
    arriva molto dopo, come rifiuto incomprensibile da parte della piattaforma.
    """
    from engine.config import is_placeholder

    # La chiave del motore dipende dal provider scelto: chiederle entrambe
    # farebbe segnalare come mancante una credenziale che non serve.
    provider = cfg.get("pipeline.provider", "anthropic")
    engine_key = (
        ("ANTHROPIC_API_KEY", "genera e verifica le curiosità", "console.anthropic.com")
        if provider == "anthropic"
        else ("GEMINI_API_KEY", "genera e verifica le curiosità", "aistudio.google.com/apikey")
    )

    required = [
        engine_key,
        ("IG_USER_ID", "id dell'account Instagram", "vedi README → Setup Instagram"),
        ("IG_ACCESS_TOKEN", "token Instagram", "vedi README → Setup Instagram"),
        ("GITHUB_TOKEN", "hosting immagini", "github.com → Developer settings"),
        ("GITHUB_REPO", "hosting immagini", "un repo pubblico, es. tuonome/curiosity-cdn"),
    ]
    optional = [
        ("TELEGRAM_BOT_TOKEN", "approvazione dei post da Telegram"),
        ("TELEGRAM_CHAT_ID", "approvazione dei post da Telegram"),
        ("PEXELS_API_KEY", "foto stock di qualità migliore"),
        ("CLOUDFLARE_API_TOKEN", "generazione FLUX gratuita (meglio di pollinations)"),
    ]

    missing = 0
    print("OBBLIGATORIE PER PUBBLICARE")
    for name, why, where in required:
        raw = env(name)
        if is_placeholder(raw):
            missing += 1
            state = "segnaposto" if raw.strip() else "vuota"
            print(f"  ✗ {name:20} [{state}] {why}")
            print(f"      → {where}")
        else:
            print(f"  ✓ {name:20} {why}")

    print("\nOPZIONALI")
    for name, why in optional:
        raw = env(name)
        print(f"  {'✓' if not is_placeholder(raw) else '·'} {name:22} {why}")

    print(f"\nCONFIGURAZIONE")
    for key in (
        "format.mode",
        "format.template",
        "format.use_images",
        "visuals.ai_provider",
        "review.require_approval",
    ):
        print(f"  {key:28} {cfg.get(key)}")

    handle = cfg.get("brand.handle", "")
    if handle == "@thequietfacts":
        print(f"\n  ⚠ brand.handle è ancora il segnaposto ({handle}) — mettilo vero")
        print("    in config.yaml, compare su ogni slide.")

    _db_sync_check()

    if args.live:
        print("\nVERIFICA DAL VIVO")
        _live_checks()

    if missing:
        print(f"\n{missing} credenziali mancanti. Finché ci sono, `publish` non parte.")
        print("Puoi comunque provare tutto il resto:  run.py preview --images")
    elif not args.live:
        print("\nTutte presenti. Ora verifica che funzionino:  run.py check --live")
    return 0


def _db_sync_check() -> None:
    """Avverte se il database locale è diverso da quello su GitHub.

    Con la pipeline che gira su Actions, il repo è la fonte di verità. Una
    modifica fatta in locale e non spinta viene sovrascritta al prossimo
    allineamento; una fatta su Actions e non tirata giù rende la copia locale
    ingannevole. È già successo due volte, entrambe silenziosamente.
    """
    import subprocess

    from engine.config import DATA_DIR, ROOT

    db = DATA_DIR / "engine.db"
    if not db.exists():
        return
    try:
        r = subprocess.run(
            ["git", "status", "--porcelain", "--", str(db.relative_to(ROOT))],
            cwd=ROOT, capture_output=True, text=True, timeout=15,
        )
        sporco = bool(r.stdout.strip())
        b = subprocess.run(
            ["git", "rev-list", "--left-right", "--count", "HEAD...origin/main"],
            cwd=ROOT, capture_output=True, text=True, timeout=15,
        )
        avanti, indietro = (b.stdout.split() + ["0", "0"])[:2]
    except Exception:
        return

    if sporco or indietro != "0":
        print("\n⚠️  DATABASE NON ALLINEATO CON GITHUB")
        if sporco:
            print("   modifiche locali non ancora spinte: verranno perse")
        if indietro != "0":
            print(f"   {indietro} commit su GitHub non ancora scaricati")
        print("   Su GitHub Actions vale la copia del repo, non questa.")
        print("   Allinea con:  git pull  (oppure git push, se le modifiche sono tue)")


def _live_checks() -> None:
    """Prova le credenziali contro le API vere.

    "Presente" e "funzionante" sono cose diverse: un token Instagram scaduto o
    un repo privato passano il controllo di presenza e falliscono al momento
    della pubblicazione, quando è più scomodo scoprirlo.
    """
    import httpx

    from engine.config import is_placeholder

    # ─ GitHub: il repo deve esistere, essere pubblico e accettare scritture ─
    token, repo = env("GITHUB_TOKEN"), env("GITHUB_REPO")
    if is_placeholder(token) or is_placeholder(repo):
        print("  · GitHub          non configurato")
    else:
        try:
            r = httpx.get(
                f"https://api.github.com/repos/{repo}",
                headers={"Authorization": f"Bearer {token}"},
                timeout=30,
            )
            if r.status_code == 404:
                print(f"  ✗ GitHub          repo '{repo}' non trovato, o il token non lo vede")
            elif r.status_code >= 400:
                print(f"  ✗ GitHub          {r.status_code} {r.text[:90]}")
            else:
                info = r.json()
                private = info.get("private", True)
                can_push = (info.get("permissions") or {}).get("push", False)
                if private:
                    print(f"  ✗ GitHub          '{repo}' è PRIVATO — Instagram non potrà scaricare le immagini")
                elif not can_push:
                    print(f"  ✗ GitHub          il token non ha permesso di scrittura su '{repo}'")
                else:
                    print(f"  ✓ GitHub          '{repo}' pubblico e scrivibile")
        except Exception as exc:
            print(f"  ✗ GitHub          {exc}")

    # ─ Instagram: risolve l'account e legge la quota di pubblicazione ─
    ig_id, ig_token = env("IG_USER_ID"), env("IG_ACCESS_TOKEN")
    if is_placeholder(ig_id) or is_placeholder(ig_token):
        print("  · Instagram       non configurato")
    else:
        try:
            r = httpx.get(
                f"https://graph.facebook.com/v21.0/{ig_id}",
                params={"fields": "username,name,followers_count", "access_token": ig_token},
                timeout=30,
            )
            if r.status_code >= 400:
                msg = r.json().get("error", {}).get("message", r.text)[:110]
                print(f"  ✗ Instagram       {msg}")
            else:
                d = r.json()
                print(
                    f"  ✓ Instagram       @{d.get('username','?')} "
                    f"({d.get('followers_count','?')} follower)"
                )
                used = instagram.quota_used()
                if used is not None:
                    print(f"  ✓ quota           {used}/25 post pubblicati nelle ultime 24h")
        except Exception as exc:
            print(f"  ✗ Instagram       {exc}")

    # ─ Scadenza dell'accesso ai dati Meta ─
    # Il token non scade mai, ma l'autorizzazione a leggere i dati sì: dopo
    # quella data ogni chiamata smette di rispondere e va rifatta a mano.
    # È l'unica scadenza di tutto il sistema che nessun automatismo può
    # rinnovare, quindi va vista prima e non il giorno che arriva.
    if not is_placeholder(ig_token):
        try:
            import datetime as _dt

            d = httpx.get("https://graph.facebook.com/debug_token",
                          params={"input_token": ig_token, "access_token": ig_token},
                          timeout=30).json().get("data", {})
            fine = d.get("data_access_expires_at", 0)
            if fine:
                giorni = (fine - time.time()) / 86400
                segno = "✓" if giorni > 21 else "⚠"
                print(f"  {segno} accesso Meta    scade il "
                      f"{_dt.datetime.fromtimestamp(fine):%d/%m/%Y} "
                      f"(fra {giorni:.0f} giorni)")
            mancanti = {"instagram_manage_insights"} - set(d.get("scopes", []))
            if mancanti:
                print(f"  · permessi Meta   manca {', '.join(mancanti)} "
                      f"— le statistiche restano a zero")
        except Exception as exc:
            print(f"  · accesso Meta    non verificabile: {exc}")

    # ─ Il motore che scrive: una chiamata vera, non solo la chiave presente ─
    provider = cfg.get("pipeline.provider", "anthropic")
    try:
        from engine.llm import ask_json

        r = ask_json("Reply in JSON.", "Return {\"ok\": true}.",
                     {"type": "object", "properties": {"ok": {"type": "boolean"}},
                      "required": ["ok"], "additionalProperties": False},
                     effort="low", max_tokens=2000)
        stato = "✓" if r.get("ok") is not None else "⚠"
        print(f"  {stato} motore testo    {provider}/{cfg.get('pipeline.model')} risponde")
    except Exception as exc:
        print(f"  ✗ motore testo    {provider}: {str(exc)[:96]}")

    # ─ YouTube: il refresh token è la cosa che scade più in fretta ─
    if cfg.get("publish.youtube.enabled", False):
        try:
            from engine.publish import youtube as _yt

            _yt.access_token()
            print("  ✓ YouTube         il token si rinnova")
            for etichetta, prova in (
                ("commenti YT", lambda: _yt.leggi_commenti("jib667XMAwQ", limite=1)),
                ("statistiche YT", lambda: _yt.ritenzione(giorni=7)),
            ):
                try:
                    prova()
                    print(f"  ✓ {etichetta:15} permesso presente")
                except _yt.PermessoMancante:
                    print(f"  · {etichetta:15} permesso mancante — rilancia "
                          f"python3 setup_youtube.py")
                except Exception:
                    pass
        except Exception as exc:
            print(f"  ✗ YouTube         {str(exc)[:96]}")

    # ─ Immagini e filmati: senza, i post escono su fondo pieno e i reel no ─
    if not is_placeholder(env("CLOUDFLARE_API_TOKEN")):
        try:
            from engine import visuals

            print("  ✓ immagini AI     Cloudflare configurato"
                  if visuals.generate("a single grey stone on white") else
                  "  ⚠ immagini AI     nessuna immagine restituita")
        except Exception as exc:
            print(f"  ✗ immagini AI     {str(exc)[:96]}")

    if not is_placeholder(env("PEXELS_API_KEY")):
        try:
            from engine import footage

            clip = footage.cerca("calm ocean at night", evita_usate=False)
            print(f"  {'✓' if clip else '⚠'} filmati         "
                  f"{'Pexels risponde' if clip else 'nessun filmato trovato'}")
        except Exception as exc:
            print(f"  ✗ filmati         {str(exc)[:96]}")

    # ─ Strumenti locali: su GitHub li installa il workflow, qui possono mancare ─
    import shutil as _sh

    print(f"  {'✓' if _sh.which('ffmpeg') else '✗'} ffmpeg          "
          f"{'presente' if _sh.which('ffmpeg') else 'assente — i reel non si montano'}")
    font = list((ROOT / "assets" / "fonts").glob("*.ttf"))
    musica = list((ROOT / "assets" / "music").rglob("*.mp3"))
    print(f"  {'✓' if font else '✗'} font            {len(font)} file"
          f"{'' if font else ' — lancia setup_fonts.py'}")
    print(f"  {'✓' if musica else '·'} musica          {len(musica)} tracce"
          f"{'' if musica else ' — lancia setup_music.py'}")


def _contenuti_da_sorvegliare(conn, ore: float) -> list:
    """Tutto ciò che è uscito di recente, su qualunque piattaforma.

    Prima guardava solo i caroselli. Ma i reel sono l'unica cosa che finora ha
    raccolto visualizzazioni, e i loro commenti non venivano letti da nessuno:
    il pezzo del sistema che serviva di più era spento.
    """
    limite = time.time() - ore * 3600
    fuori = []

    for p in conn.execute(
        """SELECT id, fact_id, ig_media_id, published_at FROM posts
           WHERE status='published' AND ig_media_id IS NOT NULL"""
    ).fetchall():
        if (p["published_at"] or 0) >= limite:
            fuori.append({"riga_id": p["id"], "fact_id": p["fact_id"],
                          "media": p["ig_media_id"], "platform": "instagram",
                          "source": "post", "etichetta": f"post #{p['id']}"})

    for r in conn.execute(
        """SELECT id, fact_id, line, caption, ig_media_id, youtube_id, published_at
           FROM reels WHERE status='published'"""
    ).fetchall():
        if (r["published_at"] or 0) < limite:
            continue
        base = {"riga_id": r["id"], "fact_id": r["fact_id"],
                "testo_hook": r["line"], "testo_fatto": r["caption"]}
        if r["ig_media_id"]:
            fuori.append({**base, "media": r["ig_media_id"],
                          "platform": "instagram", "source": "reel",
                          "etichetta": f"reel #{r['id']}"})
        if r["youtube_id"]:
            fuori.append({**base, "media": r["youtube_id"],
                          "platform": "youtube", "source": "reel",
                          "etichetta": f"YouTube #{r['id']}"})

    return fuori


def cmd_comments(args: argparse.Namespace) -> int:
    """Legge i commenti su ciò che è uscito di recente, redige le risposte e le
    manda — o le mette in approvazione, secondo `comments.require_approval`.

    Rispondere entro la prima ora è il segnale più forte dopo i salvataggi: per
    questo gira in coda a ogni ciclo di pubblicazione, non in un suo orario.
    """
    from engine import comments as cm

    conn = connect()
    cm.ensure_schema(conn)

    ore = float(cfg.get("comments.window_hours", 48))
    contenuti = _contenuti_da_sorvegliare(conn, ore)
    if not contenuti:
        print(f"Niente uscito nelle ultime {ore:.0f} ore: nessun commento da leggere.")
        return 1 if allarme.riepiloga("commenti") else 0
    print(f"→ {len(contenuti)} contenuti nella finestra di {ore:.0f} ore")

    nuovi = 0
    risposte = 0
    max_replies = int(cfg.get("comments.max_replies_per_run", 6))
    # Tetto sulle ANALISI, non solo sulle risposte. Ogni commento nuovo costa
    # una chiamata al modello, e il tetto sulle risposte scatta dopo: con un
    # video che gira, trecento commenti diventano trecento chiamate in un giro
    # solo. La quota e' la stessa che serve a generare le curiosita', quindi il
    # risultato non sarebbe qualche risposta in meno — sarebbe la pagina ferma.
    max_analisi = int(cfg.get("comments.max_drafts_per_run", 20))
    analizzati = 0
    # Il permesso YouTube può mancare (token rilasciato prima che questa parte
    # esistesse). Si segnala una volta e si smette di provarci, invece di
    # ripetere lo stesso errore per ogni video.
    yt_spento = ""

    for c in contenuti:
        if c["platform"] == "youtube" and yt_spento:
            continue

        # I reel portano il proprio testo; i caroselli lo prendono dal fatto.
        gancio = c.get("testo_hook") or ""
        fatto = c.get("testo_fatto") or ""
        if not gancio and c.get("fact_id"):
            f = conn.execute("SELECT hook, fact FROM facts WHERE id=?",
                             (c["fact_id"],)).fetchone()
            if f:
                gancio, fatto = f["hook"], f["fact"]

        try:
            if c["platform"] == "youtube":
                from engine.publish import youtube as yt
                trovati = yt.leggi_commenti(c["media"])
            else:
                trovati = cm.fetch_comments(c["media"])
        except Exception as exc:
            if c["platform"] == "youtube":
                yt_spento = str(exc)
                print(f"  · commenti YouTube saltati: {exc}")
                # Il permesso mancante NON fa fallire il giro. È uno stato di
                # configurazione noto, non un guasto: la pubblicazione
                # continua, manca solo una funzione secondaria. Farne partire
                # una mail a ogni giro — tre al giorno — trasformerebbe
                # l'unico canale d'allarme che abbiamo in rumore da ignorare,
                # ed è esattamente quando serve che verrebbe ignorato.
                from engine.publish.youtube import PermessoMancante
                if not isinstance(exc, PermessoMancante) and allarme.critico(exc):
                    allarme.segnala("commenti YouTube", exc)
            else:
                print(f"  · {c['etichetta']}: {exc}")
            continue

        for com in trovati:
            if cm.already_seen(conn, com["id"]):
                continue
            # Su YouTube sappiamo già se al commento è stato risposto: se ha
            # risposte, quasi sempre è la nostra di un giro precedente andata
            # persa prima di essere registrata.
            if com.get("reply_count"):
                continue
            if analizzati >= max_analisi:
                # I non analizzati non vengono segnati: al giro dopo sono
                # ancora nuovi e toccheranno a loro.
                continue
            analizzati += 1
            try:
                verdict = cm.draft_reply(
                    com.get("text", ""), gancio, fatto,
                    recent_replies=cm.recent_replies(conn),
                    commenter_history=cm.commenter_history(conn, com.get("username", "")),
                )
            except Exception as exc:
                print(f"  ✗ analisi fallita: {exc}")
                continue

            # Tetto alle risposte: un account che risponde a tutti, sempre, è
            # riconoscibile quanto uno che risponde male. Le correzioni non
            # rientrano nel tetto — quelle vanno sempre gestite.
            if (verdict["should_reply"] and verdict["category"] != "correction"
                    and risposte >= max_replies):
                verdict["should_reply"] = False
                verdict["reason"] = "tetto risposte per giro raggiunto"
            if verdict["should_reply"]:
                risposte += 1

            cm.record(conn, com, c["riga_id"], verdict,
                      platform=c["platform"], source=c["source"])
            nuovi += 1
            flag = "!" if verdict["needs_human"] else " "
            azione = "→ rispondere" if verdict["should_reply"] else "  ignorare"
            print(f"  {flag} [{verdict['category']:10}] {azione}  "
                  f"{c['etichetta']} @{com.get('username','?')}: {com.get('text','')[:40]}")

    if analizzati >= max_analisi:
        print(f"\n⚠ tetto di {max_analisi} analisi raggiunto: i restanti al prossimo giro")
    print(f"\n{nuovi} commenti nuovi")

    da_inviare = cm.pending(conn)
    if not da_inviare:
        return 1 if allarme.riepiloga("commenti") else 0

    if cfg.get("comments.require_approval", True):
        if review.enabled():
            for row in da_inviare:
                review.notify(
                    f"💬 <b>@{row['username']}</b> ({row['category']}) "
                    f"su {row['platform']}\n"
                    f"<i>{row['text'][:200]}</i>\n\n"
                    f"Risposta proposta:\n{row['draft']}\n\n"
                    f"<code>/reply {row['id']}</code> per inviarla · "
                    f"<code>/skip {row['id']}</code> per lasciar perdere"
                )
            print(f"{len(da_inviare)} risposte inviate su Telegram per approvazione")
        else:
            # Senza Telegram l'approvazione non ha chi la dia: le risposte
            # resterebbero in coda per sempre. Meglio dirlo che accumularle.
            print(f"\n⚠ {len(da_inviare)} risposte in attesa, ma non c'è nessuno che possa")
            print("  approvarle: comments.require_approval è true e Telegram non è")
            print("  configurato. Metti require_approval: false, o configura Telegram.")
            for row in da_inviare:
                print(f"  @{row['username']}: {row['text'][:56]}")
                print(f"    → {row['draft']}")
    else:
        # Pieno automatico: le correzioni restano comunque fuori. Se qualcuno
        # segnala un errore vero, una risposta automatica che difende il post
        # fa più danno di dieci risposte mancate.
        for row in da_inviare:
            if row["needs_human"]:
                cm.mark(conn, row["id"], "skipped")
                print(f"  · @{row['username']}: lasciato all'umano ({row['category']})")
                continue
            try:
                cm.invia_risposta(row, row["draft"])
                cm.mark(conn, row["id"], "replied")
                print(f"  ✓ risposto a @{row['username']} su {row['platform']}")
            except Exception as exc:
                print(f"  ✗ @{row['username']}: {exc}")
                if allarme.critico(exc):
                    allarme.segnala("risposta commenti", exc)

    return 1 if allarme.riepiloga("commenti") else 0




def _rifornisci(conn) -> None:
    """Genera curiosita' finche' entrambi i canali hanno scorte sufficienti.

    Il controllo e' per canale e non globale: Instagram e YouTube hanno
    registri separati, e un archivio pieno di curiosita' gia' uscite su
    Instagram non serve a YouTube. Prima si contava una cosa sola — le
    curiosita' mai diventate reel — e i caroselli non entravano nel conto.

    Il tetto sui giri esiste perche' la generazione e' l'unico punto del ciclo
    che puo' consumare quota senza un limite naturale: se il fact-check
    bocciasse tutto, senza tetto girerebbe finche' non finisce la quota
    giornaliera, lasciando a secco anche le didascalie e le risposte.
    """
    fabbisogno = {
        # Al giorno: 2 caroselli + 3 reel = 5 su Instagram; 3 video da 3
        # curiosita' = 9 su YouTube. Si tiene circa un giorno di margine.
        "instagram": int(cfg.get("pipeline.buffer_instagram", 10)),
        "youtube": int(cfg.get("pipeline.buffer_youtube", 12)),
    }
    tetto = int(cfg.get("pipeline.max_batches_per_run", 2))

    for giro in range(tetto + 1):
        stato = {c: quanti_liberi(conn, c) for c in fabbisogno}
        manca = [c for c, n in stato.items() if n < fabbisogno[c]]
        print("scorte: " + ", ".join(
            f"{c} {stato[c]}/{fabbisogno[c]}" + ("" if c not in manca else " ⚠")
            for c in fabbisogno))
        if not manca:
            return
        if giro >= tetto:
            print(f"  tetto di {tetto} generazioni per giro raggiunto, riprendo al prossimo")
            return
        print(f"→ genero per: {', '.join(manca)}")
        try:
            ideas.run_batch(conn, learnings=analytics.learning_brief(conn))
        except Exception as exc:
            print(f"generazione curiosita' fallita: {exc}")
            if allarme.critico(exc):
                allarme.segnala("generazione", exc)
            return


def _pubblica_youtube(conn, imparato: str = "") -> None:
    """Costruisce e pubblica un video YouTube con curiosita' tutte nuove.

    Indipendente da Instagram di proposito. Le due piattaforme hanno pubblici
    diversi e tempi diversi: la stessa curiosita' puo' uscire su entrambe — e'
    normale e voluto — ma il registro dei consumi e' separato, quindi su
    YouTube nessuna torna una seconda volta.

    Il tetto giornaliero non e' la quota API (sei caricamenti, ne servono tre):
    e' che oltre i due-tre Short al giorno la spinta per video cala, perche'
    ognuno viene provato su un piccolo pubblico prima di essere distribuito e
    pubblicarne troppi significa togliersi spazio da soli.
    """
    import json as _json

    from engine import lines, reel as _reel
    from engine.db import quanti_liberi, segna_uso_fatto
    from engine.publish import youtube

    quante = int(cfg.get("publish.youtube.facts_per_video", 3))
    tetto = int(cfg.get("publish.youtube.max_per_day", 3))

    usciti = conn.execute(
        """SELECT COUNT(*) n FROM fact_uses
           WHERE channel='youtube' AND used_at > ?""",
        (time.time() - 86400,),
    ).fetchone()["n"]
    if usciti >= tetto * quante:
        print(f"  · YouTube: gia' {usciti // quante} video nelle ultime 24 ore, tetto {tetto}")
        return

    # Scorte. Il video si costruisce solo se ci sono abbastanza curiosita'
    # mai uscite su YouTube: meglio saltare un giro che montare un video con
    # una curiosita' sola, o riprenderne una gia' vista.
    liberi = quanti_liberi(conn, "youtube")
    print(f"  · YouTube: {liberi} curiosita' mai uscite li'")
    if liberi < quante:
        print(f"    scorte sotto {quante}, genero")
        try:
            ideas.run_batch(conn, learnings=analytics.learning_brief(conn))
        except Exception as exc:
            print(f"    generazione fallita: {exc}")
        liberi = quanti_liberi(conn, "youtube")
    if liberi < quante:
        print(f"    ancora {liberi}: salto questo giro invece di ripetermi")
        return

    frasi = lines.generate(conn, quante, imparato=imparato, canale="youtube")
    frasi = [f for f in frasi if f.get("fact_id")][:quante]
    if len(frasi) < quante:
        print(f"    solo {len(frasi)} frasi utilizzabili su {quante}: salto")
        return

    # I filmati NON si cercano qui: ci pensa build_multi. Cercarli prima per
    # sapere se ci sono significa scaricarli due volte — quota Pexels doppia e
    # sei clip marcate come usate per un video che ne mostra tre.
    voci = [{"hook": f["hook"], "reveal": f["reveal"], "mood": f["mood"],
             "_frase": f} for f in frasi]

    import hashlib as _h
    nome = _h.sha1(frasi[0]["line"].encode()).hexdigest()[:10]
    video, montate = _reel.build_multi(voci, nome)
    if not video:
        print("    montaggio fallito")
        return

    # Solo le curiosita' davvero finite nel video: se un filmato mancava, quel
    # segmento e' saltato e annunciarla nella descrizione sarebbe una bugia.
    frasi = [v["_frase"] for v in montate]
    if not frasi:
        print("    nessun segmento montato")
        return

    meta = youtube.componi_metadati(
        frasi[0]["hook"], frasi[0]["reveal"], frasi[0].get("caption", ""),
        frasi[0].get("hashtags", []),
        altre=[f["line"] for f in frasi[1:]],
    )
    yt_id = youtube.publish(video, meta["title"], meta["description"],
                            tags=meta["tags"])

    # Si registra DOPO il caricamento riuscito: se YouTube rifiuta, le
    # curiosita' devono restare disponibili per il giro dopo invece di
    # risultare bruciate da un errore di rete.
    for f in frasi:
        segna_uso_fatto(conn, f["fact_id"], "youtube", f"yt-{yt_id}")
    print(f"  ✓ YouTube: youtu.be/{yt_id} — {len(frasi)} curiosita' nuove")


def cmd_reels(args: argparse.Namespace) -> int:
    """Ciclo dei reel, completamente separato da quello dei post.

    Separato per scelta: i due formati hanno cadenze diverse (3 reel al giorno
    contro 2 caroselli), tempi di lavorazione diversi, e soprattutto modi di
    rompersi diversi. Se ffmpeg o Pexels falliscono, i caroselli devono
    continuare a uscire come se nulla fosse — e viceversa.
    """
    import json as _json

    from engine import footage, lines, reel
    from engine.db import (
        insert_reel,
        mark_reel_published,
        reel_lines_used,
        reels_by_status,
        set_reel_status,
        set_reel_url,
    )

    conn = connect()

    # 0a. Statistiche YouTube, prima di generare qualsiasi cosa: sono l'unico
    #     ritorno misurabile che questo sistema riceva. Su Instagram al token
    #     manca `instagram_manage_insights`, quindi copertura e salvataggi
    #     restano invisibili; su YouTube la percentuale di visione si legge, ed
    #     e' proprio il numero che decide se uno Short viene rilanciato.
    try:
        analytics.raccogli_youtube(conn)
    except Exception as exc:
        print(f"statistiche YouTube saltate: {exc}")
    imparato = analytics.brief_youtube(conn)
    if imparato:
        print("→ genero tenendo conto di cosa ha trattenuto di piu'")

    # 0. I reel consumano curiosita' ma non ne producono: solo il ciclo dei
    #    caroselli genera fatti nuovi, e lo fa in base alle SUE scorte. Senza
    #    questo controllo i reel si esauriscono in circa una settimana e il
    #    ciclo resta verde producendo zero.
    _rifornisci(conn)

    # 1. Magazzino: se restano meno di 2 reel pronti, se ne producono altri.
    pronti = reels_by_status(conn, "approved")
    print(f"reel pronti: {len(pronti)}")

    # La coda si tiene volutamente corta. Un magazzino grande sembra prudente
    # ma invecchia: ogni volta che il generatore migliora, i reel gia' pronti
    # restano fermi alla versione precedente e usciranno peggiori di quelli
    # che sapremmo fare oggi. Meglio produrre poco e spesso.
    if len(pronti) < int(cfg.get("reel.min_queue", 1)):
        quanti = int(cfg.get("reel.batch", 3))
        print(f"→ genero {quanti} frasi")
        try:
            usate = set(reel_lines_used(conn))
            nuove = [l for l in lines.generate(conn, quanti + 2, imparato=imparato)
                     if l["line"] not in usate]
        except Exception as exc:
            print(f"generazione frasi fallita: {exc}")
            nuove = []

        for l in nuove[:quanti]:
            print(f"  [{l['mood']}] {l['line'][:58]}")
            try:
                sfondo = footage.per_frase(l["mood"], l["line"])
                if not sfondo:
                    print("    nessun filmato disponibile, salto")
                    continue
                # Alterna i due formati: una parte dei reel resta a frase
                # singola. Un profilo dove ogni video ha lo stesso schema
                # smette di far aspettare la seconda parte, e la struttura
                # perde proprio l'effetto per cui esiste.
                singolo = random.random() < float(cfg.get("reel.single_ratio", 0.4))
                if singolo:
                    testo, risposta = l["line"], ""
                else:
                    testo, risposta = l.get("hook") or l["line"], l.get("reveal", "")

                # Nome stabile: hash() in Python e' salato per processo, quindi
                # lo stesso reel finiva in cartelle diverse a ogni esecuzione,
                # accumulando copie orfane e rendendo impossibile ricostruirlo.
                import hashlib as _h
                nome = _h.sha1(l["line"].encode()).hexdigest()[:10]

                video = reel.build_line(
                    testo,
                    sfondo,
                    nome,
                    mood=l["mood"],
                    reveal=risposta,
                )
                print(f"    formato: {'frase singola' if singolo else 'due tempi'}")
                l["video_path"] = video
                rid = insert_reel(conn, l)
                # Caricamento immediato, come per i post: il reel costruito
                # oggi può essere pubblicato da un'altra macchina domani, e su
                # GitHub Actions il disco non sopravvive fra un giro e l'altro.
                try:
                    url = upload([video], prefix=f"reel-{rid}")[0]
                    set_reel_url(conn, rid, url)
                    print(f"    ✓ reel #{rid} pronto ({video.stat().st_size//1024} KB)")
                except Exception as exc:
                    print(f"    ⚠ caricamento rimandato: {exc}")
            except Exception as exc:
                print(f"    ✗ {exc}")

        pronti = reels_by_status(conn, "approved")

    # 2. Pubblicazione: uno per giro.
    if not pronti:
        print("nessun reel da pubblicare")
        return 0

    # In prova si costruisce e basta. Pubblicare un reel di collaudo lo mette
    # davanti al pubblico prima che qualcuno l'abbia guardato, e su Instagram
    # non si sostituisce: si puo solo cancellare, lasciando un buco nel profilo.
    if getattr(args, "no_publish", False):
        print(f"\n--no-publish: {len(pronti)} reel pronti, nessuno pubblicato")
        for x in pronti:
            print(f"  #{x['id']}  {x['video_path']}")
        return 0

    r = pronti[0]
    url = r["video_url"]
    try:
        if not url:
            video = Path(r["video_path"])
            if not video.exists():
                raise FileNotFoundError(
                    f"video non trovato: {video}. Costruito su un'altra macchina "
                    f"e mai caricato — il reel va rigenerato."
                )
            url = upload([video], prefix=f"reel-{r['id']}")[0]
            set_reel_url(conn, r["id"], url)

        caption = r["caption"]
        tags = _json.loads(r["hashtags"] or "[]")
        if tags:
            caption = f"{caption}\n\n" + " ".join("#" + t for t in tags)

        # Copertina esplicita, se il montaggio l'ha estratta. Senza, Instagram
        # sceglie un fotogramma a caso e nel feed il reel appare come una clip
        # di stock senza testo.
        cover_url = ""
        cover = Path(r["video_path"]).parent / "cover.jpg"
        if cover.exists():
            try:
                cover_url = upload([cover], prefix=f"reel-{r['id']}-cover")[0]
            except Exception as exc:
                print(f"  ⚠ copertina non caricata: {exc}")

        media_id = instagram.publish_reel(url, caption, cover_url=cover_url)
        mark_reel_published(conn, r["id"], media_id)
        print(f"✓ reel #{r['id']} pubblicato: {media_id}")

        # YouTube. Sta DOPO Instagram e in un blocco proprio perche' un
        # problema qui non deve far risultare fallito un reel gia' uscito su
        # Instagram — sono due destinazioni indipendenti, e trattarle come una
        # sola farebbe ripubblicare.
        #
        # Il video YouTube non e' piu' una rielaborazione del reel appena
        # uscito: e' costruito da curiosita' proprie, mai andate su YouTube.
        # Prima riciclava le gia' pubblicate come riempitivo, perche' Instagram
        # produce tre curiosita' al giorno e ogni video ne vuole tre — la
        # richiesta era il triplo dell'offerta e il riciclo era obbligato. Ora
        # la generazione si alza quando le scorte scendono, quindi ogni video
        # esce con materiale nuovo e nessuna curiosita' torna due volte.
        if cfg.get("publish.youtube.enabled", False):
            try:
                _pubblica_youtube(conn, imparato)
            except Exception as exc:
                print(f"  ⚠ YouTube saltato: {exc}")
                # Un singhiozzo di rete non merita una mail; un token scaduto
                # si', perche' da li' in poi su YouTube non esce piu' nulla.
                if allarme.critico(exc):
                    allarme.segnala("YouTube", exc)

        # Come per i post: video e copertina hanno esaurito il loro scopo.
        # Senza questa potatura i reel aggiungono ~9 MB al giorno al repo,
        # piu' dei caroselli.
        try:
            from engine.hosting import elimina
            n = elimina([f"reel-{r['id']}", f"reel-{r['id']}-cover"])
            if n:
                print(f"  · {n} file rimossi dal CDN")
        except Exception:
            pass
    except Exception as exc:
        set_reel_status(conn, r["id"], "failed")
        # La curiosita' torna disponibile: il reel resta segnato come fallito
        # ma il suo fact_id lo escluderebbe per sempre dalle generazioni
        # future, bruciando una curiosita' verificata a ogni errore di rete.
        conn.execute("UPDATE reels SET fact_id=NULL WHERE id=?", (r["id"],))
        conn.commit()
        print(f"✗ reel #{r['id']} fallito: {exc}")
        review.notify(f"⚠️ Reel #{r['id']} fallito:\n<code>{exc}</code>")
        if allarme.critico(exc):
            allarme.segnala("Instagram reel", exc)

    return 1 if allarme.riepiloga("reel") else 0


def cmd_prune(args: argparse.Namespace) -> int:
    """Rimuove dal CDN i media dei contenuti gia' pubblicati.

    Instagram scarica il file una volta sola, quando crea il contenitore: dopo
    la pubblicazione l'URL non viene piu' interrogato. Tenerli fa crescere il
    repo di ~21 MB al giorno.

    Tocca SOLO i contenuti in stato `published`. Quelli ancora in coda hanno
    bisogno del loro URL per essere pubblicati, e cancellarli li romperebbe.
    """
    from engine.db import alleggerisci_slides
    from engine.hosting import elimina

    conn = connect()

    # Il peso vero non e' sul CDN ma nel database, che sta nel repo: ogni post
    # si porta dentro i propri PNG in base64. Si toglie qui perche' e' lo
    # stesso lavoro — liberare spazio da cio' che ha gia' fatto il suo giro.
    if not getattr(args, "dry", False):
        liberati = alleggerisci_slides(conn)
        if liberati:
            print(f"database alleggerito di {liberati/1_048_576:.1f} MB")

    prefissi = []
    for r in conn.execute("SELECT id FROM posts WHERE status='published'").fetchall():
        prefissi.append(str(r["id"]))
    for r in conn.execute("SELECT id FROM reels WHERE status='published'").fetchall():
        prefissi += [f"reel-{r['id']}", f"reel-{r['id']}-cover"]

    # Anche i contenuti archiviati o falliti: il loro media non servira' mai
    # a nessuno, ma resta sul CDN a occupare spazio per sempre.
    for r in conn.execute(
        "SELECT id FROM posts WHERE status IN ('superseded','failed','rejected')"
    ).fetchall():
        prefissi.append(str(r["id"]))
    for r in conn.execute(
        "SELECT id FROM reels WHERE status IN ('superseded','failed')"
    ).fetchall():
        prefissi += [f"reel-{r['id']}", f"reel-{r['id']}-cover"]

    if not prefissi:
        print("Niente da potare.")
        return 0

    print(f"→ {len(prefissi)} cartelle da contenuti gia' pubblicati")
    if args.dry:
        for x in prefissi:
            print(f"    posts/{x}")
        print("\n--dry: nulla e' stato rimosso")
        return 0

    n = elimina(prefissi)
    print(f"✓ {n} file rimossi dal CDN")
    print("  Nota: libera la copia di lavoro, non la storia di git.")
    return 0


def _ripristina_immagini(slides: list) -> int:
    """Rimette le foto dentro le slide lette dal database.

    Prima si prova la cache su disco, che e' il caso normale in locale; se il
    file non c'e' piu' — succede su una macchina diversa, o dopo una pulizia —
    si riscarica dall'indirizzo originale. Se non riesce nessuna delle due, la
    slide esce su fondo pieno: brutta ma non rotta, che e' meglio di un post
    che non si ri-renderizza affatto.
    """
    from engine.visuals import as_data_uri

    rimesse = 0
    for s in slides:
        if s.get("image"):
            continue
        percorso = s.get("image_file", "")
        if percorso and Path(percorso).exists():
            s["image"] = as_data_uri(Path(percorso))
            rimesse += 1
            continue
        # Le immagini generate hanno un `image_src` che punta alla cache
        # locale: se il file non c'è più, non c'è niente da riscaricare — la
        # stessa richiesta a FLUX darebbe un'altra immagine, non quella.
        src = s.get("image_src", "")
        if not src.startswith(("http://", "https://")):
            continue
        try:
            import httpx
            r = httpx.get(src, timeout=45, follow_redirects=True)
            r.raise_for_status()
            # Sempre nella cache di questa macchina: `image_file` è il percorso
            # di dove il post fu costruito, che su un computer diverso può
            # essere una cartella che non esiste o su cui non si scrive.
            import hashlib as _h
            nome = _h.sha1(src.encode()).hexdigest()[:16]
            dest = DATA_DIR / "imgcache" / f"{nome}{Path(src).suffix[:5] or '.jpg'}"
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(r.content)
            s["image"] = as_data_uri(dest)
            rimesse += 1
        except Exception as exc:
            print(f"    ⚠ foto non recuperata ({src[:44]}): {exc}")
    return rimesse


def cmd_rerender(args: argparse.Namespace) -> int:
    """Ri-renderizza un post già costruito, senza chiamare nessuna API.

    Le immagini e il testo delle slide sono già salvati: serve a riapplicare
    modifiche ai template su post esistenti, o a cambiare template, senza
    consumare quota.
    """
    conn = connect()
    targets = (
        [get_post(conn, args.post_id)]
        if args.post_id
        else posts_by_status(conn, "pending_review") + posts_by_status(conn, "approved")
    )
    targets = [t for t in targets if t]
    if not targets:
        print("Nessun post da ri-renderizzare.")
        return 1

    done = 0
    for post in targets:
        slides = json.loads(post["slides"] or "[]")
        if not slides:
            print(f"  · #{post['id']} saltato: costruito prima che il testo venisse salvato")
            continue

        # Le foto non stanno nel database — ci starebbero a un megabyte l'una,
        # in un file versionato nel repo. Si rimettono ora, dalla cache locale
        # se c'e' ancora, altrimenti riscaricandole dall'originale.
        rimesse = _ripristina_immagini(slides)
        if rimesse:
            print(f"  · #{post['id']}: {rimesse} foto rimesse")

        # Le immagini stanno nella cartella del post: si rileggono da lì invece
        # di riscaricarle.
        old = [Path(p) for p in json.loads(post["image_paths"])]
        folder = old[0].parent if old else None

        paths = render.render_slides(
            slides, folder.name if folder else f"post-{post['id']}", args.template
        )
        conn.execute(
            "UPDATE posts SET image_paths=?, image_urls='[]' WHERE id=?",
            (json.dumps([str(p) for p in paths]), post["id"]),
        )
        conn.commit()
        done += 1
        print(f"  ✓ #{post['id']} → {paths[0].parent}")

    print(f"\n{done} post ri-renderizzati")
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    """Prepara un post per la pubblicazione manuale.

    Serve finché l'accesso API non è disponibile — tipicamente perché l'account
    Facebook è troppo nuovo per ottenere l'abilitazione sviluppatore. Il post è
    già pronto: mancano solo le credenziali per spedirlo. Nel frattempo si
    pubblica a mano senza perdere giorni di pubblicazione.
    """
    conn = connect()

    if args.post_id:
        post = get_post(conn, args.post_id)
    else:
        pending = posts_by_status(conn, "pending_review") or posts_by_status(
            conn, "approved"
        )
        post = pending[0] if pending else None

    if not post:
        print("Nessun post da esportare. Costruiscine uno:  run.py build")
        return 1

    paths = [Path(p) for p in json.loads(post["image_paths"])]
    if not paths:
        print(f"Il post #{post['id']} non ha immagini.")
        return 1

    dest = paths[0].parent
    caption_file = dest / "caption.txt"
    caption_file.write_text(post["caption"], encoding="utf-8")

    print(f"Post #{post['id']} — {len(paths)} slide\n")
    print(f"  Cartella:  {dest}")
    print(f"  Caption:   {caption_file.name}\n")

    # La caption negli appunti: è la parte che si sbaglia a ricopiare a mano.
    try:
        import subprocess

        subprocess.run(["pbcopy"], input=post["caption"].encode(), check=True)
        print("  ✓ caption copiata negli appunti")
    except Exception:
        pass

    print("\nPer pubblicare:")
    print(f"  1. Apri la cartella:  open '{dest}'")
    print("  2. Mandale al telefono (AirDrop) nell'ordine 01 → 05")
    print("  3. Instagram → nuovo post → seleziona multiplo → incolla la caption")
    print(f"\nPoi segnalo come pubblicato:  run.py published {post['id']}")

    if args.open:
        import subprocess

        subprocess.run(["open", str(dest)])

    return 0


def cmd_published(args: argparse.Namespace) -> int:
    """Segna un post come pubblicato a mano, così non ricompare in coda e il
    suo fatto non viene rigenerato."""
    conn = connect()
    mark_published(conn, args.post_id)
    print(f"post #{args.post_id} segnato come pubblicato")
    return 0


def cmd_igtoken(args: argparse.Namespace) -> int:
    """Scambia il token breve del Graph Explorer con uno da 60 giorni, trova
    l'IG_USER_ID e scrive tutto in .env.

    Esiste perché è il passaggio dove ci si perde: il token dell'Explorer dura
    un'ora, l'id dell'account non è quello della Pagina, e ogni valore va
    copiato a mano fra tre schermate diverse.
    """
    import re

    import httpx

    from engine.config import ROOT

    GRAPH = "https://graph.facebook.com/v21.0"

    with httpx.Client(timeout=60) as client:
        # 1. Token breve → token a lunga scadenza (60 giorni)
        print("→ scambio il token con uno a lunga scadenza…")
        r = client.get(
            f"{GRAPH}/oauth/access_token",
            params={
                "grant_type": "fb_exchange_token",
                "client_id": args.app_id,
                "client_secret": args.app_secret,
                "fb_exchange_token": args.token,
            },
        )
        if r.status_code >= 400:
            msg = r.json().get("error", {}).get("message", r.text)
            print(f"✗ scambio fallito: {msg}")
            print("\n  Cause tipiche: App ID o Secret sbagliati, oppure il token")
            print("  breve è già scaduto (dura un'ora — rigeneralo nell'Explorer).")
            return 1
        long_token = r.json()["access_token"]
        expires = r.json().get("expires_in", 0)
        print(f"  ✓ ottenuto, valido {expires // 86400 if expires else 60} giorni")

        # 2. Pagine dell'utente → account Instagram collegato
        print("→ cerco l'account Instagram collegato…")
        r = client.get(f"{GRAPH}/me/accounts", params={"access_token": long_token})
        if r.status_code >= 400:
            print(f"✗ {r.json().get('error', {}).get('message', r.text)}")
            return 1
        pages = r.json().get("data", [])
        if not pages:
            print("✗ Nessuna Pagina Facebook trovata su questo account.")
            print("  Serve una Pagina (non un profilo) collegata all'account Instagram.")
            return 1

        ig_id = None
        for page in pages:
            r = client.get(
                f"{GRAPH}/{page['id']}",
                params={"fields": "instagram_business_account", "access_token": long_token},
            )
            linked = r.json().get("instagram_business_account")
            label = f"  · Pagina '{page.get('name', '?')}'"
            if linked:
                ig_id = linked["id"]
                print(f"{label} → Instagram {ig_id}")
                break
            print(f"{label} → nessun Instagram collegato")

        if not ig_id:
            print("\n✗ Nessuna Pagina ha un account Instagram professionale collegato.")
            print("  Instagram → Impostazioni → Condivisione su altre app → Facebook")
            return 1

        # 3. Conferma leggendo l'handle
        r = client.get(
            f"{GRAPH}/{ig_id}",
            params={"fields": "username,followers_count", "access_token": long_token},
        )
        handle = r.json().get("username", "?")
        followers = r.json().get("followers_count", "?")
        print(f"  ✓ @{handle} ({followers} follower)")

    # 4. Scrittura in .env, preservando il resto del file
    env_path = ROOT / ".env"
    content = env_path.read_text(encoding="utf-8")
    content = re.sub(r"^IG_USER_ID=.*$", f"IG_USER_ID={ig_id}", content, flags=re.M)
    content = re.sub(
        r"^IG_ACCESS_TOKEN=.*$", f"IG_ACCESS_TOKEN={long_token}", content, flags=re.M
    )
    env_path.write_text(content, encoding="utf-8")

    print(f"\n✓ scritti IG_USER_ID e IG_ACCESS_TOKEN in .env")
    print("  Verifica con:  run.py check --live")
    print(f"\n⚠️  Il token scade fra ~60 giorni. Mettiti un promemoria a 55.")
    return 0


def cmd_testimage(args: argparse.Namespace) -> int:
    """Verifica la generazione AI isolata dal resto: un prompt, un'immagine,
    un messaggio chiaro su cosa è andato storto."""
    provider = args.provider or cfg.get("visuals.ai_provider", "none")
    if provider in ("none", "", None):
        print(
            "visuals.ai_provider è \"none\" in config.yaml.\n"
            "Mettilo a imagen | gemini | openai, oppure usa --provider."
        )
        return 1

    keys = {
        "imagen": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
        "gemini": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
        "openai": ("OPENAI_API_KEY",),
        "cloudflare": ("CLOUDFLARE_API_TOKEN",),
        "pollinations": (),          # nessuna chiave richiesta
    }.get(provider, ())
    if keys and not any(env(k) for k in keys):
        print(f"Nessuna chiave per '{provider}'. Serve una fra: {', '.join(keys)}")
        print("Mettila in .env — la crei su https://aistudio.google.com/apikey")
        return 1

    # L'override vive solo per questo processo: non tocca config.yaml.
    cfg.raw.setdefault("visuals", {})["ai_provider"] = provider
    if args.model:
        cfg.raw["visuals"]["ai_model"] = args.model

    subject = args.prompt
    model = cfg.get("visuals.ai_model") or visuals._AI_MODELS.get(provider, "")
    print(f"→ provider: {provider}  modello: {model}")
    print(f"→ soggetto: {subject}")
    print(f"→ stile:    {cfg.get('visuals.ai_style', '').strip()[:110]}…\n")

    image = visuals.generate(subject)
    if not image or not image.path:
        print("\n✗ nessuna immagine generata. Come leggere l'errore qui sopra:")
        print("  429 quota / limit: 0")
        print("      I modelli immagine Gemini NON hanno free tier. Serve la")
        print("      fatturazione attiva su console.cloud.google.com/billing.")
        print("      Un abbonamento Gemini Advanced non conta: è un altro prodotto.")
        print("  404 no longer available to new users")
        print("      Modello dismesso. Gli imagen-4.* lo sono tutti; usa")
        print("      --provider gemini, che punta a gemini-3.1-flash-lite-image.")
        print("  401 / 403")
        print("      Chiave non valida o API Generative Language non abilitata.")
        print("\n  Senza generazione il progetto funziona lo stesso: con")
        print("  visuals.ai_provider: \"none\" le immagini arrivano da Wikimedia.")
        return 1

    size = image.path.stat().st_size // 1024
    print(f"\n✓ generata: {image.path}  ({size} KB)")

    if args.render:
        slides = [
            {
                "kicker": "Test",
                "headline": subject[:60],
                "body": "Immagine generata, trattamento duotone applicato dal template.",
                "image": visuals.as_data_uri(image.path),
                "credit": image.credit,
            }
        ]
        paths = render.render_slides(slides, "test-ai", args.template or "photo")
        print(f"✓ slide renderizzata: {paths[0]}")

    return 0


# ─── ideas ────────────────────────────────────────────────────────────────────

def cmd_ideas(args: argparse.Namespace) -> int:
    conn = connect()
    learnings = analytics.learning_brief(conn)
    if learnings:
        print("→ inietto i pattern dei post migliori nella generazione")
    stats = ideas.run_batch(conn, args.count, learnings)
    print(
        f"\n{stats['approved']} approvate / {stats['kept']} verificate "
        f"/ {stats['generated']} generate"
    )
    if stats["approved"] == 0:
        print(
            "\nNessuna idea ha passato la verifica. Se succede spesso: la soglia "
            "pipeline.min_confidence è alta di proposito, ma una nicchia troppo "
            "stretta o troppo esoterica la rende difficile da superare."
        )
    return 0


# ─── build ────────────────────────────────────────────────────────────────────

def _choose_format() -> int:
    """Numero di slide. In modalità mix alterna secondo mix_ratio_carousel."""
    mode = cfg.get("format.mode", "mix")
    slides = int(cfg.get("format.carousel_slides", 5))
    if mode == "single":
        return 1
    if mode == "carousel":
        return slides
    return slides if random.random() < float(cfg.get("format.mix_ratio_carousel", 0.6)) else 1


def cmd_build(args: argparse.Namespace) -> int:
    conn = connect()
    fact = next_approved_fact(conn)
    if not fact:
        print("Nessun fatto approvato in coda. Lancia prima:  python3 run.py ideas")
        return 1

    slide_count = _choose_format()
    fmt = "carousel" if slide_count > 1 else "single"
    print(f"→ [{fact['id']}] {fact['hook']}")
    print(f"→ formato: {fmt} ({slide_count} slide)")

    copy = writer.write_copy(fact, slide_count)

    ai_images = False
    if cfg.get("format.use_images", False):
        print("→ cerco immagini…")
        found = visuals.attach_images(copy["slides"])
        print(f"→ {found}/{len(copy['slides'])} slide con immagine")
        ai_images = any(
            "AI-generated" in s.get("credit", "") for s in copy["slides"]
        )

    # La caption si compone dopo le immagini: la dichiarazione va aggiunta solo
    # se qualcuna è davvero generata.
    caption = writer.full_caption(copy, has_ai_images=ai_images)

    paths = render.render_slides(copy["slides"], f"{fact['id']}-{fact['hook']}")
    print(f"→ {len(paths)} immagini in {paths[0].parent}")

    # In modalità veto il post nasce già approvato: il controllo umano avviene
    # al momento della pubblicazione, non della costruzione.
    mode = cfg.get("review.mode", "auto")
    needs_approval = mode == "approval" or (
        mode not in ("auto", "veto") and cfg.get("review.require_approval", True)
    )
    status = "pending_review" if needs_approval else "approved"
    post_id = insert_post(
        conn,
        fact["id"],
        fmt,
        caption,
        copy["hashtags"],
        [str(p) for p in paths],
        status,
        slides=copy["slides"],
    )
    conn.execute(
        "UPDATE posts SET alt_text=? WHERE id=?", (copy.get("alt_text", ""), post_id)
    )
    conn.commit()
    set_fact_status(conn, fact["id"], "rendered")

    # Upload immediato, non al momento della pubblicazione. Il ciclo costruisce
    # un post in un'esecuzione e lo pubblica in quella successiva: su GitHub
    # Actions ogni esecuzione parte da un disco vuoto, quindi rimandare
    # l'upload significherebbe cercare file che non esistono più. Con gli URL
    # salvati, il post è pubblicabile da qualunque macchina.
    try:
        urls = upload(paths, prefix=str(post_id))
        set_post_urls(conn, post_id, urls)
        print(f"→ {len(urls)} immagini caricate sul CDN")
    except Exception as exc:
        # Non è fatale: se il post viene pubblicato dalla stessa macchina che
        # l'ha costruito, l'upload può ancora avvenire più tardi.
        print(f"⚠ upload rimandato ({exc})")

    if status == "pending_review" and review.enabled():
        review.send_for_review(post_id, paths, caption)
        print(f"→ post #{post_id} inviato su Telegram per approvazione")
    elif status == "pending_review":
        print(
            f"→ post #{post_id} in attesa di approvazione, ma Telegram non è "
            f"configurato. Approva a mano:  python3 run.py approve {post_id}"
        )
    else:
        print(f"→ post #{post_id} pronto per la pubblicazione")

    return 0


def cmd_approve(args: argparse.Namespace) -> int:
    conn = connect()
    set_post_status(conn, args.post_id, "approved")
    print(f"post #{args.post_id} approvato")
    return 0


# ─── review ───────────────────────────────────────────────────────────────────

def cmd_review(args: argparse.Namespace) -> int:
    conn = connect()
    if not review.enabled():
        print("Telegram non configurato (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID).")
        pending = posts_by_status(conn, "pending_review")
        for p in pending:
            print(f"  #{p['id']}  {p['format']}  {p['caption'][:70]}…")
        return 0
    for post_id, status in review.poll_decisions(conn):
        print(f"post #{post_id} → {status}")
    return 0


# ─── publish ──────────────────────────────────────────────────────────────────

def _veto_window(conn, post) -> bool:
    """Mostra il post su Telegram e attende. True se è stato bloccato.

    Il silenzio vale come consenso: il post esce da solo. L'attesa avviene qui
    dentro perché con due esecuzioni al giorno rimandare al ciclo successivo
    significherebbe farlo uscire sette ore dopo.
    """
    import time as _time

    minutes = int(cfg.get("review.veto_minutes", 10))
    if not review.enabled():
        print("  ⚠ mode veto ma Telegram non configurato: pubblico senza attendere")
        return False

    paths = [Path(p) for p in json.loads(post["image_paths"])]
    review.send_for_veto(post["id"], paths, post["caption"], minutes)
    print(f"  → inviato su Telegram, attendo {minutes} min prima di pubblicare")

    deadline = _time.time() + minutes * 60
    while _time.time() < deadline:
        _time.sleep(30)
        if review.vetoed(conn, post["id"]):
            print(f"  ✋ post #{post['id']} bloccato da te")
            review.notify(f"Post #{post['id']} bloccato, non verrà pubblicato.")
            return True
    return False


def _publish_one(conn, post) -> bool:
    post_id = post["id"]

    mode = cfg.get("review.mode", "auto")
    if mode == "veto" and _veto_window(conn, post):
        return False

    paths = [Path(p) for p in json.loads(post["image_paths"])]
    urls: List[str] = json.loads(post["image_urls"] or "[]")

    try:
        if not urls:
            # Un post costruito su una macchina e pubblicato da un'altra (es.
            # costruito sul Mac, pubblicato da GitHub Actions) porta con sé
            # percorsi che altrove non esistono, perché output/ non è
            # versionata. Meglio dirlo che fallire su un errore di file.
            missing = [p for p in paths if not p.exists()]
            if missing:
                raise FileNotFoundError(
                    f"{len(missing)} immagini non trovate su questa macchina "
                    f"(prima mancante: {missing[0]}). Il post è stato costruito "
                    f"altrove e non ha ancora URL pubblici. Rilancia `build` qui, "
                    f"oppure carica le immagini dalla macchina che le ha generate."
                )
            print(f"→ upload immagini post #{post_id}…")
            urls = upload(paths, prefix=str(post_id))
            set_post_urls(conn, post_id, urls)

        ig_media_id = None
        if cfg.get("publish.instagram.enabled", True):
            ig_media_id = instagram.publish(
                urls, post["caption"], alt_text=post["alt_text"] or ""
            )
            print(f"  ✓ Instagram: {ig_media_id}")

        tiktok_id = None
        if cfg.get("publish.tiktok.enabled", False):
            try:
                title = post["caption"].split("\n")[0][:90]
                tiktok_id = tiktok.publish_photos(urls, title, post["caption"])
                mode = cfg.get("publish.tiktok.mode", "inbox")
                note = " (bozza in inbox — pubblica dall'app)" if mode == "inbox" else ""
                print(f"  ✓ TikTok: {tiktok_id}{note}")
            except tiktok.TikTokError as exc:
                # TikTok che fallisce non deve far fallire un post IG riuscito.
                print(f"  ⚠ TikTok saltato: {exc}")

        mark_published(conn, post_id, ig_media_id, tiktok_id)

        # I file sul CDN hanno esaurito il loro scopo: Instagram li ha gia'
        # scaricati. Tenerli fa crescere il repo di ~6 MB a carosello.
        try:
            from engine.hosting import elimina
            n = elimina([str(post_id)])
            if n:
                print(f"  · {n} file rimossi dal CDN")
        except Exception:
            pass

        return True

    except Exception as exc:
        set_post_status(conn, post_id, "failed")
        # Il fatto era passato a "rendered" alla costruzione: senza rimetterlo
        # in coda resterebbe consumato per sempre, e una curiosità verificata
        # andrebbe persa a ogni post fallito.
        set_fact_status(conn, post["fact_id"], "approved")
        print(f"  ✗ post #{post_id} fallito: {exc}")
        review.notify(f"⚠️ Post #{post_id} fallito:\n<code>{exc}</code>")
        if cfg.get("debug", False):
            traceback.print_exc()
        return False


def cmd_publish(args: argparse.Namespace) -> int:
    conn = connect()
    queue = posts_by_status(conn, "approved")
    if not queue:
        print("Nessun post approvato in coda.")
        return 0

    used = instagram.quota_used()
    if used is not None:
        print(f"→ quota Instagram usata nelle ultime 24h: {used}")
        if used >= 25:
            print("  quota prudenziale raggiunta (25/24h) — mi fermo")
            return 0

    limit = args.limit or 1
    published = 0
    for post in queue[:limit]:
        if _publish_one(conn, post):
            published += 1

    print(f"\n{published} post pubblicati")
    return 0


# ─── metrics / report ─────────────────────────────────────────────────────────

def cmd_metrics(args: argparse.Namespace) -> int:
    conn = connect()
    n = analytics.collect(conn)
    print(f"metriche aggiornate per {n} post")
    # YouTube è l'unica piattaforma da cui arrivino numeri: su Instagram al
    # token manca `instagram_manage_insights`, quindi `collect` qui sopra
    # restituisce zero e continuerà a farlo finché il permesso non c'è.
    m = analytics.raccogli_youtube(conn)
    print(f"metriche aggiornate per {m} reel su YouTube")
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    """Rapporto sui contenuti pubblicati.

    Legge i campi diretti del contenuto (like, commenti) invece delle insights:
    quelle richiedono `instagram_manage_insights`, che il token attuale non ha.
    Visualizzazioni, copertura, salvataggi e condivisioni restano quindi
    invisibili — sono proprio le metriche che contano di più, ma meglio
    mostrare quel poco che si vede che non mostrare niente.
    """
    import httpx

    from engine.db import connect as _c

    conn = _c()
    tok, uid = env("IG_ACCESS_TOKEN"), env("IG_USER_ID")

    try:
        prof = httpx.get(
            f"https://graph.facebook.com/v21.0/{uid}",
            params={
                "fields": "username,followers_count,media_count",
                "access_token": tok,
            },
            timeout=40,
        ).json()
        print(
            f"@{prof.get('username','?')} — "
            f"{prof.get('followers_count','?')} follower, "
            f"{prof.get('media_count','?')} contenuti\n"
        )

        r = httpx.get(
            f"https://graph.facebook.com/v21.0/{uid}/media",
            params={
                "fields": "id,media_type,timestamp,like_count,comments_count,caption",
                "limit": args.limit,
                "access_token": tok,
            },
            timeout=40,
        )
        media = r.json().get("data", [])
    except Exception as exc:
        print(f"lettura fallita: {exc}")
        return 1

    print(f"{'quando':17} {'tipo':9} {'like':>5} {'comm':>5}  prima riga")
    print("─" * 76)
    for m in media:
        tipo = "REEL" if m["media_type"] == "VIDEO" else "carosello"
        prima = (m.get("caption") or "").split("\n")[0][:34]
        print(
            f"{m['timestamp'][:16]:17} {tipo:9} "
            f"{m.get('like_count', 0):>5} {m.get('comments_count', 0):>5}  {prima}"
        )

    # Le metriche mancanti vanno dette, non lasciate intuire da colonne assenti.
    print(
        "\n⚠️  Su Instagram visualizzazioni, copertura, salvataggi e condivisioni\n"
        "    non sono leggibili: manca il permesso instagram_manage_insights."
    )

    # YouTube invece i numeri li dà, ed è da lì che il sistema impara.
    print("\n" + "═" * 76)
    print("YOUTUBE")
    try:
        analytics.raccogli_youtube(conn)
    except Exception as exc:
        print(f"  aggiornamento saltato: {exc}")
    print(analytics.rapporto_youtube(conn))

    imparato = analytics.brief_youtube(conn)
    if imparato:
        print("\n" + "═" * 76)
        print("COSA STA IMPARANDO (finisce dentro la generazione delle prossime frasi)")
        print()
        print(imparato)
    else:
        print("\n  Ancora troppo pochi video con dati per imparare qualcosa:")
        print("  servono almeno 3 Short con 25+ visualizzazioni ciascuno.")
    return 0


# ─── cycle ────────────────────────────────────────────────────────────────────

def cmd_cycle(args: argparse.Namespace) -> int:
    """Un giro completo. Questo è il comando da mettere nello scheduler.

    Idempotente e difensivo: se un pezzo fallisce, gli altri proseguono.
    """
    conn = connect()
    problems: List[str] = []

    # 1. Raccogli le decisioni umane arrivate da Telegram.
    try:
        for post_id, status in review.poll_decisions(conn):
            print(f"review: post #{post_id} → {status}")
    except Exception as exc:
        print(f"review saltata: {exc}")
        problems.append(f"review: {exc}")

    # 2. Tieni la dispensa piena: se restano meno di 3 fatti approvati, genera.
    stock = conn.execute(
        "SELECT COUNT(*) c FROM facts WHERE status='approved'"
    ).fetchone()["c"]
    print(f"fatti approvati in magazzino: {stock}")
    if stock < 3:
        try:
            ideas.run_batch(conn, learnings=analytics.learning_brief(conn))
        except Exception as exc:
            print(f"generazione fallita: {exc}")
            problems.append(f"generazione: {exc}")

    # 3. Assicurati che ci sia almeno un post pronto o in revisione.
    ready = len(posts_by_status(conn, "approved")) + len(
        posts_by_status(conn, "pending_review")
    )
    if ready < 2:
        try:
            cmd_build(argparse.Namespace())
        except Exception as exc:
            print(f"build fallito: {exc}")
            problems.append(f"build: {exc}")

    # 4. Pubblica al massimo un post per giro.
    published_before = conn.execute(
        "SELECT COUNT(*) c FROM posts WHERE status='published'"
    ).fetchone()["c"]
    try:
        cmd_publish(argparse.Namespace(limit=1))
    except Exception as exc:
        print(f"pubblicazione fallita: {exc}")
        problems.append(f"pubblicazione: {exc}")
    published_after = conn.execute(
        "SELECT COUNT(*) c FROM posts WHERE status='published'"
    ).fetchone()["c"]

    # 5. Commenti: leggerli e preparare le risposte. Rispondere entro la prima
    #    ora è il segnale più forte dopo i salvataggi, quindi vale la pena
    #    farlo a ogni giro e non solo su richiesta.
    try:
        cmd_comments(argparse.Namespace())
    except Exception as exc:
        print(f"commenti saltati: {exc}")

    # 6. Scadenza del token Meta. Quando scade tutto si ferma senza errori
    #    visibili: il ciclo continua a girare e a risultare verde, ma nessuna
    #    pubblicazione va a buon fine. E' il guasto piu' insidioso del sistema,
    #    quindi va annunciato con settimane di anticipo.
    try:
        giorni = instagram.token_days_left()
        if giorni is not None and giorni <= 14:
            msg = (
                f"Il token Instagram scade fra {giorni} giorni. "
                f"Quando scade la pagina smette di pubblicare senza segnalare "
                f"nulla. Rigeneralo dal Graph API Explorer e aggiorna il "
                f"secret IG_ACCESS_TOKEN su GitHub."
            )
            print(f"\n⚠️  {msg}")
            problems.append(msg)
            review.notify(f"⚠️ {msg}")
    except Exception:
        pass

    # 7. Aggiorna le metriche.
    try:
        analytics.collect(conn)
    except Exception as exc:
        print(f"metriche saltate: {exc}")

    # 7. Scadenza dell'accesso Meta. Va controllata a ogni giro perché quando
    #    scade non c'è nessun altro segnale: le chiamate iniziano a fallire e
    #    il profilo si ferma senza che nessuno se ne accorga per settimane.
    giorni = instagram.token_days_left()
    if giorni is not None:
        if giorni <= 0:
            msg = "🔴 Accesso Instagram SCADUTO: la pubblicazione è ferma. Rigenera il token."
        elif giorni <= 14:
            msg = f"⚠️ L'accesso Instagram scade fra {giorni} giorni. Rigenera il token."
        else:
            msg = ""
        if msg:
            print(f"\n{msg}")
            review.notify(msg)
        else:
            print(f"accesso Instagram valido ancora {giorni} giorni")

    # 6. Un ciclo che non ha prodotto nulla e ha accumulato errori è la morte
    #    silenziosa tipica di queste pipeline: il token scade, il cron continua
    #    a girare, e te ne accorgi settimane dopo guardando il profilo fermo.
    #    Meglio un messaggio in chat.
    # Stallo silenzioso: post pronti ma bloccati in revisione senza che esista
    # un canale per approvarli. Non genera eccezioni, quindi senza questo
    # controllo il ciclo resterebbe verde all'infinito pubblicando zero.
    stuck = len(posts_by_status(conn, "pending_review"))
    if (
        published_after == published_before
        and stuck > 0
        and cfg.get("review.require_approval", True)
        and not review.enabled()
    ):
        problems.append(
            f"{stuck} post fermi in attesa di approvazione, ma Telegram non è "
            f"configurato: nessuno può approvarli. Metti "
            f"review.require_approval a false, oppure configura Telegram."
        )
        print(f"\n⚠️  {problems[-1]}")

    if published_after == published_before and problems:
        review.notify(
            "⚠️ Ciclo senza pubblicazioni.\n\n"
            + "\n".join(f"• <code>{p[:180]}</code>" for p in problems)
        )
        # Un giro che non pubblica nulla E ha incontrato problemi è il caso in
        # cui il sistema si sta spegnendo. Va segnalato anche senza Telegram:
        # `review.notify` qui sopra non manda niente se non è configurato, e
        # finora era l'unico avviso previsto.
        allarme.segnala("ciclo", f"nessuna pubblicazione — {problems[0]}")

    return 1 if allarme.riepiloga("caroselli") else 0


# ─── CLI ──────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        prog="run.py", description="Curiosity Engine — pagina di curiosità autonoma"
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("check", help="dice cosa manca prima di poter pubblicare")
    p.add_argument(
        "--live",
        action="store_true",
        help="prova le credenziali contro le API vere, non solo la presenza",
    )
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("preview", help="renderizza slide di prova (nessuna chiamata API)")
    p.add_argument("--template", help="editorial | bold | mono | photo | split | frame")
    p.add_argument(
        "--images",
        action="store_true",
        help="scarica immagini reali (Wikimedia senza chiave, Pexels se configurato)",
    )
    p.set_defaults(func=cmd_preview)

    p = sub.add_parser("prune", help="rimuove dal CDN i media gia' pubblicati")
    p.add_argument("--dry", action="store_true", help="mostra soltanto cosa toglierebbe")
    p.set_defaults(func=cmd_prune)

    p = sub.add_parser("reels", help="ciclo dei reel, indipendente dai post")
    p.add_argument(
        "--no-publish",
        action="store_true",
        help="costruisce i reel ma non li pubblica — da usare per le prove",
    )
    p.set_defaults(func=cmd_reels)

    p = sub.add_parser("comments", help="leggi i commenti e prepara le risposte")
    p.set_defaults(func=cmd_comments)

    p = sub.add_parser(
        "rerender", help="ri-renderizza i post esistenti senza chiamare le API"
    )
    p.add_argument("post_id", type=int, nargs="?", help="default: tutti quelli in coda")
    p.add_argument("--template", help="cambia template: editorial | bold | photo | split | frame")
    p.set_defaults(func=cmd_rerender)

    p = sub.add_parser("export", help="prepara un post per la pubblicazione manuale")
    p.add_argument("post_id", type=int, nargs="?", help="default: il prossimo in coda")
    p.add_argument("--open", action="store_true", help="apri la cartella nel Finder")
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("published", help="segna un post come pubblicato a mano")
    p.add_argument("post_id", type=int)
    p.set_defaults(func=cmd_published)

    p = sub.add_parser(
        "igtoken", help="scambia il token Instagram e trova l'IG_USER_ID"
    )
    p.add_argument("--app-id", required=True, help="App ID (Meta → Impostazioni → Di base)")
    p.add_argument("--app-secret", required=True, help="App Secret, stessa schermata")
    p.add_argument("--token", required=True, help="token breve dal Graph API Explorer")
    p.set_defaults(func=cmd_igtoken)

    p = sub.add_parser("testimage", help="prova la generazione AI di un'immagine")
    p.add_argument(
        "prompt",
        nargs="?",
        default="an empty desk at night lit by a single lamp",
        help="soggetto da generare",
    )
    p.add_argument("--provider", help="imagen | gemini | openai")
    p.add_argument("--model", help="forza un modello specifico")
    p.add_argument("--render", action="store_true", help="renderizza anche la slide")
    p.add_argument("--template", help="template per --render (default: photo)")
    p.set_defaults(func=cmd_testimage)

    p = sub.add_parser("ideas", help="genera e verifica un batch di curiosità")
    p.add_argument("--count", type=int, help="quante idee generare")
    p.set_defaults(func=cmd_ideas)

    p = sub.add_parser("build", help="costruisci un post dal prossimo fatto approvato")
    p.set_defaults(func=cmd_build)

    p = sub.add_parser("approve", help="approva un post a mano")
    p.add_argument("post_id", type=int)
    p.set_defaults(func=cmd_approve)

    p = sub.add_parser("review", help="leggi le decisioni da Telegram")
    p.set_defaults(func=cmd_review)

    p = sub.add_parser("publish", help="pubblica i post approvati")
    p.add_argument("--limit", type=int, default=1)
    p.set_defaults(func=cmd_publish)

    p = sub.add_parser("metrics", help="raccogli le insights dei post pubblicati")
    p.set_defaults(func=cmd_metrics)

    p = sub.add_parser("report", help="cosa sta funzionando")
    p.add_argument("--limit", type=int, default=10)
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("cycle", help="ciclo completo — da usare nello scheduler")
    p.set_defaults(func=cmd_cycle)

    args = parser.parse_args()
    try:
        return args.func(args)
    except RuntimeError as exc:
        # Quota esaurita, credenziali mancanti, rifiuto del modello: sono
        # condizioni previste, non bug. Un traceback qui nasconde il messaggio
        # utile sotto venti righe di stack.
        print(f"\n✗ {exc}")
        return 1
    except KeyboardInterrupt:
        print("\ninterrotto")
        return 130


if __name__ == "__main__":
    sys.exit(main())
