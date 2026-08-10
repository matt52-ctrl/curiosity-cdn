# Curiosity Engine

Pagina di curiosità in inglese per Instagram (+ TikTok), interamente gestita da IA:
genera le curiosità, le **verifica cercando sul web**, le impagina come caroselli,
le pubblica e legge i risultati per capire cosa funziona.

```
┌──────────┐   ┌───────────┐   ┌────────┐   ┌────────┐   ┌───────────┐   ┌──────────┐
│  ideas   │──▶│  verify   │──▶│ writer │──▶│ render │──▶│  publish  │──▶│ metrics  │
│ (Claude) │   │ (+ web    │   │(slide+ │   │(HTML→  │   │ (IG API)  │   │(insights)│
│          │   │  search)  │   │caption)│   │  PNG)  │   │           │   │          │
└──────────┘   └───────────┘   └────────┘   └────────┘   └───────────┘   └────┬─────┘
                                                                              │
                              i post migliori rientrano nella generazione ◀───┘
```

---

## Cosa è automatico e cosa no — leggi questo per primo

| | Instagram | TikTok |
|---|---|---|
| Pubblicazione automatica | ✅ completa, subito | ⚠️ solo dopo audit |
| Cosa serve | account Business + Pagina FB + app Meta | app approvata + dominio verificato |
| Senza i requisiti | — | carica come **bozza** nella tua inbox, premi tu "Post" |
| Tempi di attivazione | ~30 minuti | 2-4 settimane di audit |

**Traduzione pratica:** parti su Instagram in automatico completo. TikTok è fase 2 —
il codice c'è, ma finché non passi l'audit di TikTok le foto arrivano come bozza e
ti costano 10 secondi al giorno sul telefono. Non è un limite del progetto, è come
funziona la loro API.

Un terzo punto che vale entrambe le piattaforme: **contenuti percepiti come non
originali o prodotti in serie vengono declassati.** Per questo `review.require_approval`
è `true` di default: due secondi al giorno di occhio umano, via Telegram. Puoi
spegnerlo, ma nelle prime settimane è il modo più economico di scoprire cosa
sbaglia il generatore prima che lo veda il pubblico.

---

## Setup

### 1. Dipendenze

```bash
cd ~/Desktop/curiosity_engine
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/playwright install chromium
.venv/bin/python setup_fonts.py
```

### 2. Prova il rendering (nessuna API, nessun costo)

```bash
.venv/bin/python run.py preview --template editorial
```

Trovi i PNG in `output/preview-editorial/`. Prova anche `bold` e `mono`.
Se il look non ti convince, si cambia in `engine/templates/*.css` — non serve
toccare Python.

### 3. Chiavi

```bash
cp .env.example .env
```

Poi compila. Sotto c'è come ottenere ciascuna.

---

## Come ottenere i token

### Anthropic (obbligatorio)

[console.anthropic.com](https://console.anthropic.com) → API Keys → Create Key.
Ricarica almeno 5-10 $ di credito.

### Instagram (obbligatorio)

Questa è la parte noiosa. Una volta sola, poi non ci pensi più.

1. **L'account IG deve essere Business o Creator.**
   App IG → Impostazioni → Tipo di account → Passa a un account professionale.

2. **Collega l'account IG a una Pagina Facebook.**
   Serve una Pagina, non un profilo. Creane una vuota se non ce l'hai:
   [facebook.com/pages/create](https://facebook.com/pages/create).
   Poi da IG: Impostazioni → Condivisione su altre app → Facebook → collega.

3. **Crea un'app Meta.**
   [developers.facebook.com/apps](https://developers.facebook.com/apps) →
   Crea app → tipo **Business** → aggiungi il prodotto **Instagram Graph API**.

4. **Genera un token con i permessi giusti.**
   [Graph API Explorer](https://developers.facebook.com/tools/explorer) →
   seleziona la tua app → Genera token utente con questi permessi:
   ```
   instagram_basic
   instagram_content_publish
   pages_show_list
   pages_read_engagement
   business_management
   ```

5. **Trova il tuo `IG_USER_ID`.**
   Nel Graph API Explorer:
   ```
   GET /me/accounts
   ```
   prendi l'`id` della tua Pagina, poi:
   ```
   GET /{page-id}?fields=instagram_business_account
   ```
   Il valore restituito è il tuo `IG_USER_ID`.

6. **Trasforma il token in uno a lunga scadenza.**
   Il token dell'Explorer scade in un'ora. Quello lungo dura 60 giorni:
   ```bash
   curl "https://graph.facebook.com/v21.0/oauth/access_token?grant_type=fb_exchange_token&client_id=APP_ID&client_secret=APP_SECRET&fb_exchange_token=TOKEN_CORTO"
   ```
   Metti il risultato in `IG_ACCESS_TOKEN`. **Segnati un promemoria a 55 giorni**
   per rigenerarlo — è la causa numero uno di pipeline che smettono di funzionare
   senza spiegazione.

### Hosting immagini (obbligatorio)

Instagram scarica le immagini da un URL pubblico e **rifiuta gli URL con query
string** — quindi gli URL firmati di S3/R2 non vanno. Serve un link pulito.

**Opzione consigliata (gratis): repo GitHub pubblico.**

1. Crea un repo pubblico vuoto, es. `curiosity-cdn`.
2. Genera un Personal Access Token: GitHub → Settings → Developer settings →
   Fine-grained tokens → accesso al solo repo `curiosity-cdn`, permesso
   **Contents: Read and write**.
3. Nel `.env`:
   ```
   IMAGE_HOST_BACKEND=github
   GITHUB_TOKEN=github_pat_...
   GITHUB_REPO=tuo-username/curiosity-cdn
   ```

Le immagini finiscono su `raw.githubusercontent.com/...` — URL pulite, servite
da CDN, gratis.

**Alternativa: Cloudinary.** Necessaria se poi vuoi TikTok, perché lì serve un
dominio di cui puoi dimostrare la proprietà (e `raw.githubusercontent.com` non è
tuo). Free tier 25 GB.

### Telegram (opzionale ma consigliato)

1. Scrivi a [@BotFather](https://t.me/BotFather) → `/newbot` → ottieni il token.
2. Scrivi un messaggio qualsiasi al tuo bot.
3. Apri `https://api.telegram.org/bot<TOKEN>/getUpdates` e leggi `chat.id`.

Da lì approvi i post con `/ok 12` e li scarti con `/no 12`.

### TikTok (fase 2)

[developers.tiktok.com](https://developers.tiktok.com) → crea app → richiedi
**Content Posting API** con gli scope `video.upload` (bozza) o `video.publish`
(pubblicazione diretta, richiede audit). Vedi i commenti in
[`engine/publish/tiktok.py`](engine/publish/tiktok.py) per i dettagli sui due muri.

---

## Uso

```bash
# genera e verifica un batch di curiosità (le mette in magazzino)
.venv/bin/python run.py ideas

# costruisci il prossimo post: slide + caption + immagini
.venv/bin/python run.py build

# leggi le decisioni arrivate da Telegram
.venv/bin/python run.py review

# pubblica i post approvati
.venv/bin/python run.py publish

# raccogli le metriche e guarda cosa funziona
.venv/bin/python run.py metrics
.venv/bin/python run.py report

# tutto insieme — questo va nello scheduler
.venv/bin/python run.py cycle
```

`cycle` è idempotente e difensivo: se un pezzo fallisce, gli altri proseguono.
Tiene il magazzino pieno (rigenera quando restano meno di 3 fatti verificati),
costruisce se serve, pubblica al massimo un post per giro.

---

## Automazione

### Mac (launchd)

```bash
cp deploy/com.curiosity.engine.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.curiosity.engine.plist
```

Gira solo a Mac acceso e connesso. Va bene per iniziare.

### VPS (cron) — consigliato

Un Hetzner CX22 costa ~4 €/mese e gira sempre. La costanza di pubblicazione è
uno dei pochi fattori che l'algoritmo premia in modo misurabile.

```bash
sudo apt install -y python3-venv fonts-liberation
git clone <il-tuo-repo> && cd curiosity_engine
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/playwright install --with-deps chromium
.venv/bin/python setup_fonts.py
crontab deploy/crontab.example   # controlla i path prima
```

---

## Immagini

Sei template: tre di sola tipografia (`editorial`, `bold`, `mono`) e tre che
usano una foto pertinente al fatto (`photo`, `split`, `frame`). Si attivano con
`format.use_images: true`.

### Da dove arrivano

| Fonte | Chiave | Quando si usa |
|---|---|---|
| **Wikimedia Commons** | nessuna | fallback sempre disponibile, ottima sui soggetti concreti e storici |
| **Pexels** | gratuita, 200/ora | primario se configurata, resa migliore sui soggetti generici |
| **Generazione AI** | a consumo | concetti astratti, dove la ricerca stock restituisce solo repertorio |

Openverse è deliberatamente assente: anonimo dà 5 richieste/ora, che si
esauriscono al primo carosello.

### La regola di instradamento

Ogni slide viene classificata da Claude come `concept` o `real_subject`, e la
distinzione non è estetica:

- **`real_subject`** — il soggetto esiste davvero (un esperimento, un reperto,
  una specie, un luogo). Si cerca una **fotografia vera** e non si genera mai.
  Su una pagina di fatti, l'immagine inventata di una cosa reale è un documento
  falso, per quanto bella venga.
- **`concept`** — una scena ordinaria che sta per un'idea (una scrivania di
  notte, un binario affollato). Nessuno sta documentando nulla, quindi si può
  generare.

Nel dubbio si sceglie `real_subject`: il costo di un'immagine mancante è un
fondo pieno, il costo di una inventata è la credibilità della pagina.

### Generazione AI

**Claude non genera immagini** — serve un provider dedicato, configurato in
`visuals.ai_provider`:

| Provider | Modello | Costo | Setup |
|---|---|---|---|
| `pollinations` | FLUX | **gratis** | nessuno |
| `cloudflare` | FLUX.1-schnell | **gratis** (10k neuroni/giorno) | account gratuito |
| `openai` | gpt-image-2 (`low`) | ~0,005-0,02 $/img | carta |
| `gemini` | gemini-3.1-flash-lite-image | a consumo | carta |
| `imagen` | ⚠️ dismesso | — | — |

**Verificato l'8 agosto 2026** interrogando l'API con una chiave AI Studio reale:

- Tutti gli `imagen-4.*` rispondono **404 "no longer available to new users"**.
- Tutti i modelli immagine Gemini rispondono **429 con
  `generate_content_free_tier_requests limit: 0`** — la generazione immagini
  **non ha free tier**, serve la fatturazione attiva sul progetto Google.
- Imagen accetta solo aspect ratio `1:1, 9:16, 16:9, 4:3, 3:4` — **non** `4:5`.
  I modelli Gemini image accettano `4:5`, che è il formato esatto del post.

⚠️ **Un abbonamento Gemini Advanced / Google One AI Premium non dà accesso
all'API**, e non sblocca Nano Banana via API: è un altro prodotto. Non esiste
un modo legittimo di pilotare l'app da uno script.

⚠️ `gpt-image-1` viene ritirato il 23 ottobre 2026 — non costruirci sopra.

**Per partire senza carta di credito:** `ai_provider: "pollinations"` funziona
subito. Se poi vuoi risoluzione piena e qualità FLUX, `cloudflare` costa solo
la creazione di un account gratuito.

Due accorgimenti che decidono se il risultato sembra una pagina o un collage:

- **`visuals.ai_style`** viene accodato a ogni prompt. È la ragione per cui
  venti immagini generate in giorni diversi si somigliano. Cambiarlo cambia il
  carattere visivo dell'account.
- **Il trattamento duotone** nei CSS (desaturazione + velatura di colore in
  `mix-blend-mode`) uniforma sorgenti diverse. Vale anche per le foto cercate:
  è ciò che permette di mescolare Wikimedia, Pexels e AI senza che si veda.

Le immagini reperite sono filtrate per licenza — solo pubblico dominio, CC0,
CC BY e CC BY-SA, con il credito stampato sulla slide. NonCommercial e
NoDerivatives vengono scartate perché su un account che può monetizzare non
sono utilizzabili. Le immagini generate sono etichettate come illustrazione:
sia Instagram che TikTok richiedono di dichiarare i contenuti sintetici.

---

## Configurazione

Tutto quello che definisce la pagina sta in [`config.yaml`](config.yaml):
nicchia, voce editoriale, formato, ritmo, soglie di qualità.

Le due manopole che contano di più:

**`niche`** — più è stretta, più cresci. Una pagina "curiosità varie" non
permette all'algoritmo di profilare il pubblico, e la crescita si ferma.
Il default è psicologia/comportamento umano; per cambiarla riscrivi `brief` e
`avoid`, e il generatore si adatta senza toccare il codice.

**`pipeline.min_confidence`** — default `0.85`, severo di proposito. È la soglia
sotto cui un fatto viene scartato dopo il fact-check. Abbassarla aumenta la resa
del batch e abbassa l'affidabilità della pagina; una pagina di curiosità che
posta una bufala viene smontata nei commenti e non recupera.

---

## Perché la verifica è un passaggio separato

Le curiosità generate da un LLM senza controllo sono vere circa l'80% delle volte.
Il 20% restante non è rumore innocuo: sono esattamente i "fatti" più condivisibili,
quelli che circolano da anni in versione distorta.

Il secondo passaggio (`engine/ideas.py`) usa un prompt deliberatamente ostile e
la **ricerca web reale** — non un secondo parere dello stesso modello — e cerca in
particolare: studi non replicati, effect size gonfiati, numeri derivati nel
passaparola, citazioni a fonti che non esistono. Assegna un verdetto e una
confidenza; sotto soglia il fatto viene buttato. Quando il problema è solo
un'esagerazione recuperabile, riscrive la formulazione invece di scartare l'idea.

È il passaggio che costa di più e l'unico che non va tolto.

---

## Costi

| Voce | Stima |
|---|---|
| Claude API | ~0,10-0,25 $ per post pubblicato (generazione + fact-check con ricerca) |
| Hosting immagini | 0 € (GitHub) |
| VPS | ~4-5 €/mese (opzionale) |
| **Totale a 2 post/giorno** | **~10-18 $/mese** |

Il fact-check con ricerca web è la voce dominante. È anche la ragione per cui la
pagina resta credibile.

---

## Struttura

```
config.yaml               nicchia, voce, formato, soglie — la pagina si definisce qui
run.py                    CLI e orchestrazione
setup_fonts.py            scarica i font usati dai template
engine/
  llm.py                  wrapper Claude: JSON, prompt caching, ricerca web
  ideas.py                generazione → deduplica → fact-check ostile
  writer.py               slide, caption, hashtag
  render.py               HTML/CSS → PNG (Chromium headless)
  templates/              editorial · bold · mono — il look sta qui, non in Python
  hosting.py              upload immagini (GitHub / Cloudinary)
  publish/instagram.py    Graph API: immagine singola e carosello
  publish/tiktok.py       Content Posting API (photo mode)
  review.py               coda di approvazione via Telegram
  analytics.py            insights + reinserimento nel loop di generazione
  db.py                   SQLite: fatti, post, metriche
```

---

## Problemi comuni

**`The access token could not be decrypted`** — il token IG è scaduto (60 giorni).
Rigeneralo con il comando `fb_exchange_token` sopra.

**`Media URL cannot be fetched` / errore generico sul container** — l'URL non è
pubblica, non è raggiungibile, o contiene una query string. Aprila in incognito:
se non vedi l'immagine, non la vede nemmeno Instagram.

**Nessuna idea passa la verifica** — la nicchia è troppo esoterica, oppure
`min_confidence` è troppo alto per l'argomento. Controlla il campo `verify_note`:
```bash
sqlite3 data/engine.db "SELECT verdict, confidence, verify_note FROM facts ORDER BY id DESC LIMIT 10"
```

**I font sembrano sbagliati sul VPS** — `setup_fonts.py` non è stato eseguito, o
Chromium non ha le dipendenze di sistema: `playwright install --with-deps chromium`.
