"""Genera il sito statico: una pagina per curiosità, più l'indice.

Perché un sito, in un progetto fatto di social: è l'unico prodotto che si
accumula. Un reel muore in due giorni, una pagina resta indicizzata per anni e
continua a portare persone. Ed è l'unico che nessuno può spegnere — Instagram e
YouTube possono cambiare regole domani, questo no.

Ma la ragione vera è un'altra. La pipeline fa un fact-check ostile — pretende
la citazione, l'obiezione più forte, un livello di confidenza — e poi di tutto
quel lavoro pubblica due righe di didascalia. Il resto veniva buttato. Qui
trova la sua casa: ogni pagina mostra lo studio, l'anno, cosa è stato misurato
e cosa il fact-check ha obiettato. È ciò che distingue questa pagina da mille
account che dicono numeri a caso, e finora non era visibile da nessuna parte.

Sta tutto in `docs/`, che è la cartella che GitHub Pages serve senza chiedere
nulla: niente hosting, niente dominio, niente da pagare.
"""
from __future__ import annotations

import html
import json
import re
import sqlite3
from pathlib import Path
from typing import Dict, List, Optional

from .config import ROOT, cfg

DOCS = ROOT / "docs"

# Il segno del marchio, lo stesso del profilo Instagram e della copertina
# YouTube. Ridisegnato in vettoriale perché su sfondo trasparente il PNG
# originale mostrerebbe il suo quadrato nero.
MARCHIO = """<svg viewBox="0 0 1080 1080" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
<circle cx="547" cy="550" r="300" stroke="currentColor" stroke-width="23"/>
<circle cx="540" cy="535" r="180" stroke="currentColor" stroke-width="35" opacity=".85"/>
<rect x="532" y="193" width="17" height="205" fill="currentColor"/></svg>"""


def _slug(testo: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", testo.lower()).strip("-")
    return s[:70] or "fatto"


def _e(x: Optional[str]) -> str:
    return html.escape(x or "", quote=True)


def _base_url() -> str:
    """L'indirizzo pubblico del sito, senza barra finale."""
    u = (cfg.get("sito.url", "") or "").rstrip("/")
    if u:
        return u
    # Ricavato dal repo: è dove GitHub Pages pubblica di default.
    from .config import env

    repo = env("GITHUB_REPO") or ""
    if "/" in repo:
        utente, nome = repo.split("/", 1)
        return f"https://{utente}.github.io/{nome}"
    return ""


CSS = """
:root {
  --fondo:#0d0c0b; --carta:#151311; --testo:#fdfbf7; --tenue:#a49c90;
  --oro:#e8c07a; --bordo:#2a2622;
}
* { margin:0; padding:0; box-sizing:border-box; }
html { scroll-behavior:smooth; }
body {
  background:var(--fondo); color:var(--testo);
  font:17px/1.65 -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
  -webkit-font-smoothing:antialiased;
}
a { color:inherit; }
.guscio { max-width:44rem; margin:0 auto; padding:0 1.4rem; }

/* Intestazione: il segno del marchio è lo stesso di Instagram e YouTube. Chi
   arriva qui da uno Short deve riconoscere dove è finito, senza leggere. */
header { border-bottom:1px solid var(--bordo); }
header .guscio { display:flex; align-items:center; gap:.85rem; padding-top:1.5rem;
                 padding-bottom:1.5rem; }
header svg { width:34px; height:34px; color:var(--oro); flex:none; }
header .nome { font-family:'Instrument Serif',Georgia,serif; font-size:1.5rem;
               letter-spacing:-.02em; text-decoration:none; }
header nav { margin-left:auto; display:flex; gap:1.1rem; font-size:.86rem; }
header nav a { color:var(--tenue); text-decoration:none; }
header nav a:hover { color:var(--oro); }

h1 { font-family:'Instrument Serif',Georgia,serif; font-weight:400;
     font-size:clamp(2rem,5.2vw,3rem); line-height:1.08; letter-spacing:-.025em;
     margin-bottom:1.1rem; }
.occhiello { color:var(--oro); font-size:.79rem; letter-spacing:.15em;
             text-transform:uppercase; margin-bottom:.9rem; }

main { padding:3.2rem 0 4.5rem; }
.corpo p { margin-bottom:1.2rem; }
.corpo p.guida { font-size:1.16rem; color:#e4ded4; }

/* La scheda della verifica. È il pezzo per cui il sito esiste: mostra che
   dietro alla frase c'è uno studio con un nome e un anno, e cosa il controllo
   ha obiettato. Sta in evidenza, non in fondo in grigio chiaro. */
.verifica { background:var(--carta); border:1px solid var(--bordo);
            border-radius:12px; padding:1.4rem 1.5rem; margin:2.2rem 0; }
.verifica h2 { font-size:.79rem; letter-spacing:.15em; text-transform:uppercase;
               color:var(--oro); margin-bottom:1rem; font-weight:600; }
.verifica dt { font-size:.78rem; color:var(--tenue); text-transform:uppercase;
               letter-spacing:.08em; margin-top:1rem; }
.verifica dt:first-of-type { margin-top:0; }
.verifica dd { margin-top:.28rem; font-size:.96rem; }
.fonte { font-family:'Instrument Serif',Georgia,serif; font-size:1.12rem; color:var(--oro); }

.etichette { display:flex; flex-wrap:wrap; gap:.5rem; margin-top:2rem; }
.etichette span { font-size:.8rem; color:var(--tenue); border:1px solid var(--bordo);
                  padding:.28rem .75rem; border-radius:999px; }

/* La morale, in chiusura. Sta dopo la spiegazione perché è una conclusione:
   messa in cima faceva concorrenza al titolo e si leggeva due volte. */
.morale { font-family:'Instrument Serif',Georgia,serif; font-size:1.5rem;
          line-height:1.3; color:var(--oro); border-left:2px solid var(--oro);
          padding-left:1.2rem; margin:2.4rem 0 0; letter-spacing:-.01em; }

/* Indice */
.elenco { list-style:none; }
.elenco li { border-bottom:1px solid var(--bordo); }
.elenco a { display:block; padding:1.45rem 0; text-decoration:none; }
.elenco a:hover .titolo { color:var(--oro); }
.elenco .titolo { font-family:'Instrument Serif',Georgia,serif; font-size:1.42rem;
                  line-height:1.22; letter-spacing:-.015em; }
.elenco .sotto { color:var(--tenue); font-size:.93rem; margin-top:.4rem; }

footer { border-top:1px solid var(--bordo); padding:2.2rem 0 3rem;
         color:var(--tenue); font-size:.87rem; }
footer a { color:var(--oro); }
footer p { margin-bottom:.6rem; }

.altrove { display:flex; gap:.7rem; flex-wrap:wrap; margin:2.6rem 0 0; }
.altrove a { flex:1; min-width:11rem; text-align:center; padding:.85rem 1rem;
             border:1px solid var(--bordo); border-radius:10px; text-decoration:none;
             font-size:.92rem; }
.altrove a:hover { border-color:var(--oro); color:var(--oro); }

.indietro { display:inline-block; margin-bottom:2rem; color:var(--tenue);
            text-decoration:none; font-size:.9rem; }
.indietro:hover { color:var(--oro); }

.fonti .citazione { font-family:'Instrument Serif',Georgia,serif; font-size:1.18rem;
                    color:var(--oro); line-height:1.35; }
.fonti .sotto { margin-top:.3rem; }

/* La curiosità del giorno: uguale per tutti, cambia a mezzanotte. È il motivo
   per tornare che al sito mancava del tutto — chi l'aveva già visto non aveva
   nessuna ragione di riaprirlo. */
.oggi { background:var(--carta); border:1px solid var(--bordo); border-radius:14px;
        padding:1.8rem 1.7rem; margin:2.6rem 0 0; }
.oggi .occhiello { margin-bottom:.7rem; }
.oggi h2 { font-family:'Instrument Serif',Georgia,serif; font-weight:400;
           font-size:1.75rem; line-height:1.18; letter-spacing:-.02em; }
.oggi p { color:var(--tenue); margin-top:.7rem; font-size:.97rem; }
.oggi a { text-decoration:none; display:block; }
.oggi a:hover h2 { color:var(--oro); }

.correlate { margin-top:3rem; padding-top:2rem; border-top:1px solid var(--bordo); }
.correlate h2 { font-size:.79rem; letter-spacing:.15em; text-transform:uppercase;
                color:var(--tenue); margin-bottom:.5rem; }
.correlate .titolo { font-size:1.2rem; }
.correlate .elenco a { padding:1.05rem 0; }

/* Ricerca e caso: le due funzioni per chi arriva senza sapere cosa cerca. */
.strumenti { display:flex; gap:.7rem; margin:2.2rem 0 1rem; }
#cerca { flex:1; background:var(--carta); border:1px solid var(--bordo);
         border-radius:10px; padding:.8rem 1rem; color:var(--testo); font:inherit;
         font-size:.98rem; }
#cerca:focus { outline:none; border-color:var(--oro); }
#cerca::placeholder { color:var(--tenue); }
#caso { background:var(--carta); border:1px solid var(--bordo); border-radius:10px;
        padding:.8rem 1.15rem; color:var(--testo); font:inherit; font-size:.94rem;
        cursor:pointer; white-space:nowrap; }
#caso:hover { border-color:var(--oro); color:var(--oro); }
.vuoto { color:var(--tenue); padding:2rem 0; display:none; }
"""


def _radice() -> str:
    """Il percorso assoluto della radice del sito, con barra finale.

    Su GitHub Pages un sito di progetto vive sotto /nome-repo/, non alla
    radice del dominio. Costruire i collegamenti con percorsi relativi sembra
    piu' semplice ma si rompe appena una pagina cambia profondita': il logo in
    alto puntava a "./", che dentro /f/qualcosa/ e' la pagina stessa.
    """
    base = _base_url()
    if not base:
        return "/"
    resto = base.split("//", 1)[-1]
    return ("/" + resto.split("/", 1)[1].strip("/") + "/") if "/" in resto else "/"


def _pagina(titolo: str, descrizione: str, corpo: str, percorso: str,
            jsonld: Optional[Dict] = None) -> str:
    """Lo scheletro comune. Le meta servono a due cose diverse: `description`
    è quello che Google mostra sotto al titolo, le `og:` sono quello che
    compare quando qualcuno incolla il link in una chat."""
    base = _base_url()
    url = f"{base}/{percorso}".rstrip("/") if base else ""
    marchio = MARCHIO
    ig = (cfg.get("brand.handle", "") or "").lstrip("@")
    yt = (cfg.get("brand.youtube", "") or "").lstrip("@")
    nome = _e(cfg.get("brand.name", "Oddly Wired"))

    dati = f'<script type="application/ld+json">{json.dumps(jsonld)}</script>' if jsonld else ""
    return f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_e(titolo)}</title>
<meta name="description" content="{_e(descrizione)}">
{f'<link rel="canonical" href="{url}">' if url else ''}
<meta property="og:type" content="article">
<meta property="og:title" content="{_e(titolo)}">
<meta property="og:description" content="{_e(descrizione)}">
{f'<meta property="og:url" content="{url}">' if url else ''}
<meta property="og:site_name" content="{nome}">
<meta name="twitter:card" content="summary">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Instrument+Serif&display=swap" rel="stylesheet">
<link rel="stylesheet" href="{_radice()}style.css">
<link rel="alternate" type="application/rss+xml" title="{nome}" href="{_radice()}feed.xml">
{dati}
</head><body>
<header><div class="guscio">
  {marchio}
  <a class="nome" href="{_radice()}">{nome}</a>
  <nav>
    <a href="{_radice()}sources/">Sources</a>
    {f'<a href="https://instagram.com/{ig}" rel="me">Instagram</a>' if ig else ''}
    {f'<a href="https://youtube.com/@{yt}" rel="me">YouTube</a>' if yt else ''}
  </nav>
</div></header>
<main><div class="guscio">
{corpo}
</div></main>
<footer><div class="guscio">
  <p>One checked fact a day. Every claim here names the study behind it.</p>
  <p>{f'<a href="https://instagram.com/{ig}">@{ig}</a> · ' if ig else ''}{f'<a href="https://youtube.com/@{yt}">YouTube</a>' if yt else ''}</p>
</div></footer>
</body></html>"""


def _scheda_verifica(f: sqlite3.Row) -> str:
    """La parte che giustifica l'esistenza del sito."""
    voci = []
    if f["source_hint"]:
        voci.append(("Study", f'<span class="fonte">{_e(f["source_hint"])}</span>'))
    if f["verify_note"]:
        voci.append(("What the check found", _e(f["verify_note"])))
    if f["confidence"] is not None:
        # La confidenza si mostra a parole e non come numero: "0.92" non
        # significa niente per chi legge, e dare tre decimali a un giudizio
        # qualitativo è una precisione finta.
        c = float(f["confidence"] or 0)
        etichetta = ("Well replicated" if c >= 0.9 else
                     "Solid, with limits" if c >= 0.75 else
                     "Contested")
        voci.append(("Confidence", etichetta))
    if not voci:
        return ""
    righe = "".join(f"<dt>{k}</dt><dd>{v}</dd>" for k, v in voci)
    return f'<section class="verifica"><h2>Where this comes from</h2><dl>{righe}</dl></section>'


def genera(conn: sqlite3.Connection) -> int:
    """Scrive tutto il sito. Ritorna quante pagine di curiosità ha prodotto."""
    DOCS.mkdir(parents=True, exist_ok=True)
    (DOCS / "style.css").write_text(CSS, encoding="utf-8")
    # Senza questo file GitHub Pages passa le pagine da Jekyll, che ignora le
    # cartelle che iniziano per underscore e a volte rimaneggia l'HTML.
    (DOCS / ".nojekyll").write_text("", encoding="utf-8")

    # Tutte le verificate, non solo quelle gia' uscite sui social. Il sito e'
    # un archivio, non un feed: razionare le pagine non ha alcun vantaggio, e
    # la pipeline verifica piu' in fretta di quanto i social consumino —
    # quel surplus altrimenti non vedrebbe mai la luce. Le scartate restano
    # fuori: sono state bocciate dal fact-check, ed e' il punto.
    fatti = conn.execute(
        """SELECT * FROM facts
           WHERE status IN ('published','rendered','approved') AND hook != ''
             AND COALESCE(verdict,'') != 'refuted'
           ORDER BY created_at DESC"""
    ).fetchall()

    base = _base_url()
    ig = (cfg.get("brand.handle", "") or "").lstrip("@")
    yt = (cfg.get("brand.youtube", "") or "").lstrip("@")

    # Primo passaggio: si contano gli argomenti. Serve prima di scrivere le
    # pagine perche' ognuna deve sapere quali delle sue etichette avranno una
    # pagina d'argomento e quali no — un collegamento a una pagina che non
    # esiste e' peggio di nessun collegamento.
    conteggio: Dict[str, int] = {}
    for f in fatti:
        try:
            for k in json.loads(f["keywords"] or "[]")[:4]:
                k = k.strip().lower()
                conteggio[k] = conteggio.get(k, 0) + 1
        except json.JSONDecodeError:
            pass
    MIN_PER_ARGOMENTO = 3
    argomenti_validi = {k for k, n in conteggio.items() if n >= MIN_PER_ARGOMENTO}

    # Serve l'elenco completo con gli slug PRIMA di scrivere le pagine, o le
    # correlate potrebbero puntare solo all'indietro.
    indice_fatti = [(_slug(f["hook"]), f) for f in fatti]

    voci = []
    for f in fatti:
        slug = _slug(f["hook"])
        voci.append((slug, f))

        jsonld = {
            "@context": "https://schema.org",
            "@type": "Article",
            "headline": f["hook"][:110],
            "description": (f["fact"] or "")[:300],
            "author": {"@type": "Organization", "name": cfg.get("brand.name")},
        }
        if f["source_hint"]:
            jsonld["citation"] = f["source_hint"]


        # L'occhiello prende la prima parola chiave, non il `kicker`:
        # quest'ultimo e' una frase intera, e in maiuscolo spaziato occupava
        # due righe sopra al titolo facendogli concorrenza. Come chiusura
        # invece funziona — e' la morale, e le morali stanno in fondo.
        try:
            chiavi = json.loads(f["keywords"] or "[]")
        except json.JSONDecodeError:
            chiavi = []
        occhiello = (chiavi[0] if chiavi else "Psychology").replace("-", " ")
        # Le etichette diventano collegamenti solo se la pagina d'argomento
        # esiste davvero: un collegamento a una pagina inesistente e' peggio
        # di nessun collegamento.
        etichette = ""
        if chiavi:
            pezzi = []
            for k in chiavi[:5]:
                if k.strip().lower() in argomenti_validi:
                    pezzi.append(f'<a href="{_radice()}t/{_slug(k)}/">{_e(k)}</a>')
                else:
                    pezzi.append(f"<span>{_e(k)}</span>")
            etichette = '<div class="etichette">' + "".join(pezzi) + "</div>"

        # Correlate: e' la leva piu' forte contro l'uscita dopo una pagina
        # sola. Chi arriva da un link nella bio ha gia' dimostrato interesse
        # per un argomento; offrirgliene altri tre dello stesso tema costa
        # nulla e cambia completamente quanto resta.
        vicine = []
        if chiavi:
            insieme = {k.strip().lower() for k in chiavi}
            punteggi = []
            for s2, f2 in indice_fatti:
                if f2["id"] == f["id"]:
                    continue
                try:
                    c2 = {k.strip().lower() for k in json.loads(f2["keywords"] or "[]")}
                except json.JSONDecodeError:
                    continue
                comuni = len(insieme & c2)
                if comuni:
                    punteggi.append((comuni, s2, f2))
            punteggi.sort(key=lambda x: -x[0])
            vicine = punteggi[:3]
        correlate = ""
        if vicine:
            righe_v = "".join(
                f'<li><a href="{_radice()}f/{s2}/"><div class="titolo">{_e(f2["hook"])}</div></a></li>'
                for _, s2, f2 in vicine
            )
            correlate = ('<section class="correlate"><h2>Related</h2>'
                         f'<ul class="elenco">{righe_v}</ul></section>')

        corpo = f"""<a class="indietro" href="{_radice()}">← all facts</a>
<div class="occhiello">{_e(occhiello)}</div>
<h1>{_e(f["hook"])}</h1>
<div class="corpo">
  <p class="guida">{_e(f["fact"])}</p>
  {f'<p>{_e(f["detail"])}</p>' if f["detail"] else ''}
</div>
{f'<p class="morale">{_e(f["kicker"])}</p>' if f["kicker"] else ''}
{_scheda_verifica(f)}
{etichette}
{correlate}
<div class="altrove">
  {f'<a href="https://instagram.com/{ig}">One a day on Instagram</a>' if ig else ''}
  {f'<a href="https://youtube.com/@{yt}">Watch on YouTube</a>' if yt else ''}
</div>"""

        cartella = DOCS / "f" / slug
        cartella.mkdir(parents=True, exist_ok=True)
        (cartella / "index.html").write_text(
            _pagina(f["hook"], (f["fact"] or "")[:180], corpo, f"f/{slug}/", jsonld),
            encoding="utf-8",
        )

    # ── pagine per argomento ──
    #
    # Servono a due cose diverse. Per chi legge, sono il modo di continuare
    # dopo la prima pagina invece di uscire. Per i motori di ricerca, sono
    # pagine che rispondono a una domanda larga ("psychology of memory")
    # mentre le singole rispondono a una stretta: senza, il sito compete solo
    # sulle code lunghe e non ha nulla al centro.
    per_argomento: Dict[str, list] = {}
    for s, f in voci:
        try:
            for k in json.loads(f["keywords"] or "[]")[:4]:
                per_argomento.setdefault(k.strip().lower(), []).append((s, f))
        except json.JSONDecodeError:
            pass

    # Sotto le tre curiosita' una pagina d'argomento e' piu' vuota che utile,
    # e Google la tratta come contenuto sottile.
    argomenti = {k: v for k, v in per_argomento.items() if k in argomenti_validi}

    for arg, elenco_a in sorted(argomenti.items()):
        sa = _slug(arg)
        righe = "".join(
            f'<li><a href="{_radice()}f/{s}/"><div class="titolo">{_e(f["hook"])}</div>'
            f'<div class="sotto">{_e((f["fact"] or "")[:118])}…</div></a></li>'
            for s, f in elenco_a
        )
        titolo_a = arg[0].upper() + arg[1:]
        corpo_a = f"""<a class="indietro" href="{_radice()}">← all facts</a>
<div class="occhiello">{len(elenco_a)} checked facts</div>
<h1>{_e(titolo_a)}</h1>
<div class="corpo"><p class="guida">Everything on this site about
{_e(arg)} — each one naming the study behind it.</p></div>
<ul class="elenco" style="margin-top:2.5rem">{righe}</ul>"""
        cartella_a = DOCS / "t" / sa
        cartella_a.mkdir(parents=True, exist_ok=True)
        (cartella_a / "index.html").write_text(
            _pagina(f"{titolo_a} — {len(elenco_a)} checked psychology facts",
                    f"Checked facts about {arg}, each naming the study behind it.",
                    corpo_a, f"t/{sa}/"),
            encoding="utf-8",
        )

    # ── la curiosità del giorno ──
    #
    # Scelta dalla data, non a caso: così è la stessa per tutti e cambia a
    # mezzanotte. Sceglierla a caso a ogni ricarica la renderebbe solo un
    # secondo bottone «a caso», e soprattutto toglierebbe il motivo per
    # tornare domani — che è l'unica ragione per cui esiste.
    #
    # Si genera qui, staticamente: la data è quella della rigenerazione, e
    # visto che i cicli girano cinque volte al giorno il sito non resta mai
    # indietro. Farlo in JavaScript avrebbe significato ricalcolarlo a ogni
    # visita per lo stesso risultato.
    oggi_html = ""
    if voci:
        import datetime as _dt

        giorno = _dt.date.today()
        i = (giorno.toordinal() * 2654435761) % len(voci)   # sparpaglia i giorni vicini
        s_o, f_o = voci[i]
        oggi_html = f'''<div class="oggi">
  <a href="{_radice()}f/{s_o}/">
    <div class="occhiello">Today&rsquo;s fact</div>
    <h2>{_e(f_o["hook"])}</h2>
    <p>{_e((f_o["fact"] or "")[:150])}&hellip;</p>
  </a>
</div>'''

    # ── indice ──
    elenco = "".join(
        f'<li><a href="{_radice()}f/{s}/"><div class="titolo">{_e(f["hook"])}</div>'
        f'<div class="sotto">{_e((f["fact"] or "")[:118])}…</div></a></li>'
        for s, f in voci
    )
    corpo_indice = f"""<div class="occhiello">Psychology &amp; human behaviour</div>
<h1>Why people do what they do.</h1>
<div class="corpo">
  <p class="guida">One checked fact a day. Every claim on this site names the
  study behind it — the authors, the year, and what the check found. If a
  finding is contested, it says so.</p>
</div>
{oggi_html}
<div class="altrove">
  {f'<a href="https://instagram.com/{ig}">Follow on Instagram</a>' if ig else ''}
  {f'<a href="https://youtube.com/@{yt}">Watch on YouTube</a>' if yt else ''}
</div>
<div class="strumenti">
  <input id="cerca" type="search" placeholder="Search {len(voci)} checked facts…"
         autocomplete="off" aria-label="Search">
  <button id="caso" type="button">Surprise me</button>
</div>
<p class="vuoto" id="vuoto">Nothing matches that. Try a single word — memory, regret, effort.</p>
<ul class="elenco" id="elenco">{elenco}</ul>
<script>
// Ricerca lato browser: nessun server, nessuna chiamata, funziona anche
// offline. Con qualche centinaio di pagine il filtro su testo già presente
// nella pagina è istantaneo, e costa zero da mantenere.
(function () {{
  var campo = document.getElementById('cerca');
  var lista = document.getElementById('elenco');
  var vuoto = document.getElementById('vuoto');
  var voci = [].slice.call(lista.children);
  voci.forEach(function (v) {{ v.dataset.t = v.textContent.toLowerCase(); }});

  campo.addEventListener('input', function () {{
    var q = campo.value.trim().toLowerCase();
    var visti = 0;
    voci.forEach(function (v) {{
      var ok = !q || v.dataset.t.indexOf(q) !== -1;
      v.style.display = ok ? '' : 'none';
      if (ok) visti++;
    }});
    vuoto.style.display = visti ? 'none' : 'block';
  }});

  // "A caso" è il modo di navigare di chi arriva da un video: non cerca
  // niente di preciso, vuole un'altra cosa interessante.
  document.getElementById('caso').addEventListener('click', function () {{
    var v = voci[Math.floor(Math.random() * voci.length)];
    var a = v.querySelector('a');
    if (a) location.href = a.getAttribute('href');
  }});
}})();
</script>"""
    (DOCS / "index.html").write_text(
        _pagina(
            f"{cfg.get('brand.name')} — why people do what they do",
            "One checked psychology fact a day. Every claim names the study behind it.",
            corpo_indice, "",
        ),
        encoding="utf-8",
    )

    # ── pagina delle fonti ──
    #
    # È la cosa che questo progetto ha e che nessun altro nella nicchia può
    # copiare senza rifare il lavoro. Chiunque può aprire una pagina di
    # curiosità di psicologia; una bibliografia verificabile no. Sta in una
    # pagina sola, non solo in fondo a ciascuna, perché il messaggio è
    # cumulativo: sessanta paper insieme dicono una cosa che sessanta
    # citazioni sparse non dicono.
    import re as _re

    fonti = []
    for s, f in voci:
        sh = (f["source_hint"] or "").strip()
        if not sh:
            continue
        anno = _re.search(r"\b(19|20)\d{2}\b", sh)
        fonti.append((int(anno.group(0)) if anno else 0, sh, s, f["hook"]))
    fonti.sort(key=lambda x: (-x[0], x[1]))

    if fonti:
        righe_f = "".join(
            f'<li><a href="{_radice()}f/{s}/">'
            f'<div class="citazione">{_e(sh)}</div>'
            f'<div class="sotto">{_e(h)}</div></a></li>'
            for _, sh, s, h in fonti
        )
        anni_v = [a for a, _, _, _ in fonti if a]
        arco = f"{min(anni_v)}-{max(anni_v)}" if anni_v else ""
        corpo_f = f"""<a class="indietro" href="{_radice()}">back to all facts</a>
<div class="occhiello">{len(fonti)} studies{f' &middot; {arco}' if arco else ''}</div>
<h1>Every study behind every claim.</h1>
<div class="corpo"><p class="guida">Nothing on this site is published without
a named source. This is the full list &mdash; authors, year, journal &mdash;
with the finding each one supports. If a claim is contested, its page says so.</p></div>
<ul class="elenco fonti" style="margin-top:2.5rem">{righe_f}</ul>"""
        (DOCS / "sources").mkdir(parents=True, exist_ok=True)
        (DOCS / "sources" / "index.html").write_text(
            _pagina(f"Sources - {len(fonti)} studies behind every claim",
                    f"The {len(fonti)} studies cited on this site, "
                    f"with the finding each one supports.",
                    corpo_f, "sources/"),
            encoding="utf-8",
        )

    # ── feed RSS ──
    #
    # È la newsletter senza la newsletter. Chi vuole gli aggiornamenti si
    # iscrive dal proprio lettore, e non c'è nessun indirizzo email da
    # raccogliere: niente modulo, niente banca dati, niente consenso da
    # registrare, niente obblighi da titolare del trattamento. Su un progetto
    # in Europa quella differenza non è burocratica, è sostanziale.
    #
    # E non è un vicolo cieco: quando ci sarà un pubblico vero, i servizi di
    # newsletter sanno leggere un RSS e trasformarlo in email da soli. Questo
    # feed è la fondazione di quella, costruita adesso a costo zero.
    if base:
        import email.utils as _eu

        elementi = []
        for s, f in voci[:50]:
            data = _eu.formatdate(f["created_at"] or 0, usegmt=True)
            elementi.append(
                "<item>"
                f"<title>{_e(f['hook'])}</title>"
                f"<link>{base}/f/{s}/</link>"
                f"<guid isPermaLink=\"true\">{base}/f/{s}/</guid>"
                f"<pubDate>{data}</pubDate>"
                f"<description>{_e((f['fact'] or '')[:400])}</description>"
                "</item>"
            )
        nome_marchio = _e(cfg.get("brand.name", "Oddly Wired"))
        (DOCS / "feed.xml").write_text(
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom"><channel>'
            f"<title>{nome_marchio}</title>"
            f"<link>{base}/</link>"
            "<description>One checked psychology fact a day. "
            "Every claim names the study behind it.</description>"
            "<language>en</language>"
            f'<atom:link href="{base}/feed.xml" rel="self" type="application/rss+xml"/>'
            + "".join(elementi) +
            "</channel></rss>",
            encoding="utf-8",
        )

    # ── sitemap: è ciò che dice a Google che le pagine esistono senza
    #    aspettare che le trovi seguendo i link ──
    if base:
        url = ([f"<url><loc>{base}/</loc></url>"]
               + [f"<url><loc>{base}/f/{s}/</loc></url>" for s, _ in voci]
               + [f"<url><loc>{base}/t/{_slug(a)}/</loc></url>" for a in sorted(argomenti)]
               + ([f"<url><loc>{base}/sources/</loc></url>"] if fonti else []))
        (DOCS / "sitemap.xml").write_text(
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
            + "\n".join(url) + "\n</urlset>",
            encoding="utf-8",
        )
        (DOCS / "robots.txt").write_text(
            f"User-agent: *\nAllow: /\nSitemap: {base}/sitemap.xml\n", encoding="utf-8"
        )
    return len(voci)
