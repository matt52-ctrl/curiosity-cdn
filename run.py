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

from engine import analytics, ideas, render, review, visuals, writer
from engine.config import cfg, env
from engine.db import (
    connect,
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

    # ─ Anthropic o Gemini, a seconda del provider configurato ─
    provider = cfg.get("pipeline.provider", "anthropic")
    print(f"  · motore testo    provider={provider} model={cfg.get('pipeline.model')}")


def cmd_comments(args: argparse.Namespace) -> int:
    """Legge i commenti sui post pubblicati, redige le risposte, le manda in
    approvazione. Rispondere entro la prima ora è il segnale più forte dopo i
    salvataggi — per questo va nello scheduler con cadenza ravvicinata."""
    from engine import comments as cm
    from engine.db import published_posts

    conn = connect()
    cm.ensure_schema(conn)

    posts = published_posts(conn)
    if not posts:
        print("Nessun post pubblicato: non ci sono commenti da leggere.")
        return 0

    # Solo i post recenti: sui vecchi i commenti arrivano col contagocce e
    # interrogarli tutti brucia il rate limit per niente.
    horizon = time.time() - float(cfg.get("comments.window_hours", 48)) * 3600
    recent = [p for p in posts if (p["published_at"] or 0) >= horizon]
    print(f"→ {len(recent)} post nella finestra, su {len(posts)} pubblicati")

    nuovi = 0
    risposte = 0
    max_replies = int(cfg.get("comments.max_replies_per_run", 6))

    for post in recent:
        fact = conn.execute(
            "SELECT hook, fact FROM facts WHERE id=?", (post["fact_id"],)
        ).fetchone()

        for c in cm.fetch_comments(post["ig_media_id"]):
            if cm.already_seen(conn, c["id"]):
                continue
            try:
                verdict = cm.draft_reply(
                    c.get("text", ""),
                    fact["hook"],
                    fact["fact"],
                    recent_replies=cm.recent_replies(conn),
                    commenter_history=cm.commenter_history(
                        conn, c.get("username", "")
                    ),
                )
            except Exception as exc:
                print(f"  ✗ analisi fallita: {exc}")
                continue

            # Tetto alle risposte: un account che risponde a tutti, sempre, è
            # riconoscibile quanto uno che risponde male. Le correzioni non
            # rientrano nel tetto — quelle vanno sempre gestite.
            if (
                verdict["should_reply"]
                and verdict["category"] != "correction"
                and risposte >= max_replies
            ):
                verdict["should_reply"] = False
                verdict["reason"] = "tetto risposte per giro raggiunto"
            if verdict["should_reply"]:
                risposte += 1

            cm.record(conn, c, post["id"], verdict)
            nuovi += 1
            flag = "!" if verdict["needs_human"] else " "
            action = "→ rispondere" if verdict["should_reply"] else "  ignorare"
            print(f"  {flag} [{verdict['category']:10}] {action}  @{c.get('username','?')}: {c.get('text','')[:46]}")

    print(f"\n{nuovi} commenti nuovi")

    da_inviare = cm.pending(conn)
    if not da_inviare:
        return 0

    if cfg.get("comments.require_approval", True):
        if review.enabled():
            for row in da_inviare:
                review.notify(
                    f"💬 <b>@{row['username']}</b> ({row['category']})\n"
                    f"<i>{row['text'][:200]}</i>\n\n"
                    f"Risposta proposta:\n{row['draft']}\n\n"
                    f"<code>/reply {row['id']}</code> per inviarla · "
                    f"<code>/skip {row['id']}</code> per lasciar perdere"
                )
            print(f"{len(da_inviare)} risposte inviate su Telegram per approvazione")
        else:
            print(f"\n{len(da_inviare)} risposte in attesa (Telegram non configurato):")
            for row in da_inviare:
                print(f"  @{row['username']}: {row['text'][:60]}")
                print(f"    → {row['draft']}")
    else:
        # Pieno automatico: le correzioni restano comunque all'umano.
        for row in da_inviare:
            if row["needs_human"]:
                continue
            try:
                cm.post_reply(row["id"], row["draft"])
                cm.mark(conn, row["id"], "replied")
                print(f"  ✓ risposto a @{row['username']}")
            except Exception as exc:
                print(f"  ✗ @{row['username']}: {exc}")

    return 0


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

    # 1. Magazzino: se restano meno di 2 reel pronti, se ne producono altri.
    pronti = reels_by_status(conn, "approved")
    print(f"reel pronti: {len(pronti)}")

    if len(pronti) < int(cfg.get("reel.min_queue", 2)):
        quanti = int(cfg.get("reel.batch", 3))
        print(f"→ genero {quanti} frasi")
        try:
            usate = set(reel_lines_used(conn))
            nuove = [l for l in lines.generate(conn, quanti + 2) if l["line"] not in usate]
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
                video = reel.build_line(
                    l["line"], sfondo, f"{abs(hash(l['line'])) % 10**8}", mood=l["mood"]
                )
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

        media_id = instagram.publish_reel(url, caption)
        mark_reel_published(conn, r["id"], media_id)
        print(f"✓ reel #{r['id']} pubblicato: {media_id}")
    except Exception as exc:
        set_reel_status(conn, r["id"], "failed")
        print(f"✗ reel #{r['id']} fallito: {exc}")
        review.notify(f"⚠️ Reel #{r['id']} fallito:\n<code>{exc}</code>")

    return 0


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
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    conn = connect()
    print(analytics.report(conn, args.limit))
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

    # 6. Aggiorna le metriche.
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

    return 0


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

    p = sub.add_parser("reels", help="ciclo dei reel, indipendente dai post")
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
