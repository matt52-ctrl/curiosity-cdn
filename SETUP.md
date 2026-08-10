# Messa in produzione — guida passo passo

Quattro fasi. Dopo ognuna c'è un comando che verifica se è andata a buon fine,
così non arrivi in fondo scoprendo che il secondo passo era sbagliato.

Tempo realistico: **~40 minuti**, quasi tutti nella fase 2.

---

## Fase 1 — GitHub, hosting delle immagini (5 min)

Instagram non accetta immagini caricate: le **scarica** da un URL pubblico. E
rifiuta gli URL con query string, quindi servono link puliti. Un repo GitHub
pubblico è il modo più semplice e gratuito.

### 1.1 Crea il repo

1. [github.com/new](https://github.com/new)
2. Nome: `curiosity-cdn` (o quello che vuoi)
3. **Public** — obbligatorio. Se è privato, `raw.githubusercontent.com`
   richiede autenticazione e Instagram vede un 404.
4. Spunta "Add a README file" così il repo non nasce vuoto
5. *Create repository*

### 1.2 Crea il token

1. [github.com/settings/tokens?type=beta](https://github.com/settings/tokens?type=beta)
   → *Generate new token*
2. Nome: `curiosity-engine`
3. Expiration: **No expiration** (o 1 anno — segnati la scadenza)
4. Repository access: **Only select repositories** → scegli `curiosity-cdn`
5. Permissions → Repository permissions → **Contents: Read and write**
   (è l'unico permesso che serve)
6. *Generate token* e copialo subito — non lo rivedrai più

### 1.3 Mettilo in `.env`

```
GITHUB_TOKEN=github_pat_...
GITHUB_REPO=tuo-username/curiosity-cdn
```

`GITHUB_REPO` va nel formato `utente/repo`, senza `https://` e senza `.git`.

### 1.4 Verifica

```bash
.venv/bin/python run.py check --live
```

Deve dire: `✓ GitHub  'tuo-username/curiosity-cdn' pubblico e scrivibile`.

---

## Fase 2 — Instagram (30 min)

Questa è la parte noiosa. Una volta sola, poi non ci pensi più per 60 giorni.

> **Tutto questo si fa dal browser.** L'unica eccezione possibile è il passo
> 2.1: Instagram a volte nasconde il cambio di tipo account sul web. Se non
> trovi la voce, quello è l'unico passaggio da fare dall'app.

### 2.1 Account Instagram professionale

L'account personale non può pubblicare via API.

[instagram.com](https://instagram.com) → *Altro* (in basso a sinistra) →
**Impostazioni → Tipo di account e strumenti → Passa a un account
professionale** → **Creator** o **Business** (indifferente).

### 2.2 Pagina Facebook collegata

Serve una **Pagina**, non un profilo personale. Se non ce l'hai, creane una
vuota: [facebook.com/pages/create](https://facebook.com/pages/create) —
categoria qualsiasi, nessun contenuto necessario.

### 2.3 Collega Instagram alla Pagina

Dal browser conviene farlo da Meta Business Suite:
[business.facebook.com/settings](https://business.facebook.com/settings) →
*Account* → *Account Instagram* → **Aggiungi** → accedi con Instagram e
associa la Pagina.

È il punto in cui il collegamento riesce più spesso, e ti conferma subito
l'esito. In alternativa: Instagram → Impostazioni → Condivisione su altre
app → Facebook.

### 2.3 App Meta per sviluppatori

1. [developers.facebook.com/apps](https://developers.facebook.com/apps) →
   *Crea app*
2. Caso d'uso: **Altro** → tipo **Business**
3. Nome app: qualsiasi (es. `curiosity-engine`)
4. Nella dashboard dell'app → *Aggiungi prodotto* → **Instagram Graph API**
   → Configura

### 2.4 Genera il token

1. [developers.facebook.com/tools/explorer](https://developers.facebook.com/tools/explorer)
2. In alto a destra scegli la **tua app**
3. *Generate Access Token* e concedi questi permessi:

```
instagram_basic
instagram_content_publish
pages_show_list
pages_read_engagement
business_management
```

4. Autorizza scegliendo la Pagina e l'account Instagram giusti

### 2.5 App ID e App Secret

Dashboard dell'app → *Impostazioni → Di base*. Il Secret è nascosto, c'è un
tasto **Mostra**.

### 2.6 Un comando fa il resto

Con App ID, App Secret e il token breve dell'Explorer:

```bash
.venv/bin/python run.py igtoken --app-id "APP_ID" --app-secret "APP_SECRET" --token "TOKEN_BREVE"
```

Il comando scambia il token con uno da 60 giorni, trova la Pagina, ne ricava
l'`IG_USER_ID`, verifica leggendo il tuo handle e scrive tutto in `.env`.

Sostituisce i due passaggi dove ci si perde: comporre a mano il `curl` di
scambio, e capire che **l'IG_USER_ID non è l'id della Pagina** ma quello
dell'account Instagram collegato — due chiamate in sequenza nell'Explorer che
si sbagliano facilmente.

> ⚠️ Il token breve dell'Explorer dura **un'ora**. Generalo quando sei pronto
> a lanciare subito il comando, non prima.

### 2.7 Verifica

```bash
.venv/bin/python run.py check --live
```

Deve dire: `✓ Instagram  @tuohandle (N follower)`.
Se vedi il tuo handle vero, la parte difficile è finita.

> ⚠️ **Segnati un promemoria a 55 giorni** per rigenerare il token. È la
> causa numero uno di pipeline che smettono di funzionare in silenzio.

---

## Fase 3 — Telegram, approvazione dei post (3 min, consigliato)

Serve a leggere i post prima che escano. Con il fact-check su Gemini è
particolarmente consigliato, perché è più indulgente di quanto vorresti.

1. Scrivi a [@BotFather](https://t.me/BotFather) → `/newbot` → segui le
   istruzioni → copia il token
2. **Scrivi un messaggio qualsiasi al tuo bot** (senza questo il passo 3 non
   restituisce nulla)
3. Apri nel browser: `https://api.telegram.org/bot<IL_TUO_TOKEN>/getUpdates`
   e cerca `"chat":{"id":123456789` — quello è il tuo chat id

```
TELEGRAM_BOT_TOKEN=123456:ABC-DEF...
TELEGRAM_CHAT_ID=123456789
```

Da lì approvi con `/ok 12` e scarti con `/no 12`.

---

## Fase 4 — Il primo post

### 4.1 Metti l'handle vero

In `config.yaml`, sostituisci il segnaposto — compare su **ogni slide**:

```yaml
brand:
  handle: "@iltuohandle"
  name: "Il Tuo Nome"
  watermark: "@iltuohandle"
  contact: "https://instagram.com/iltuohandle"
```

### 4.2 Genera e costruisci

```bash
.venv/bin/python run.py ideas --count 5
.venv/bin/python run.py build
```

Guarda le immagini in `output/`. Se ti piacciono:

```bash
.venv/bin/python run.py approve 1     # o rispondi /ok 1 su Telegram
.venv/bin/python run.py publish
```

Il primo `publish` è il momento della verità: carica le immagini su GitHub,
crea i container su Instagram e pubblica. Se qualcosa non va, l'errore dice
quale dei tre passi è fallito.

---

## Fase 5 — Automazione

Quando il primo post manuale è uscito bene, attiva lo scheduler.

### Sul Mac

```bash
cp deploy/com.curiosity.engine.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.curiosity.engine.plist
```

Gira alle 13:05 e alle 20:05, **solo a Mac acceso e connesso**. Un giro perso
per Mac spento non viene recuperato.

Per fermarlo:

```bash
launchctl unload ~/Library/LaunchAgents/com.curiosity.engine.plist
```

### Su un VPS

Se vuoi costanza vera (l'algoritmo la premia), un Hetzner CX22 costa ~4 €/mese.
Vedi `deploy/crontab.example` e la sezione Automazione del README.

---

## Ordine di attivazione consigliato

Non accendere tutto insieme.

1. **Settimana 1-2** — `require_approval: true`, un post al giorno, approvi
   tutto a mano. Serve a capire cosa sbaglia il generatore.
2. **Settimana 3-4** — se meno di un post su dieci ti fa storcere il naso,
   passa a 2 post al giorno.
3. **Poi** — solo se la qualità regge, valuta `require_approval: false`.

Il motivo non è prudenza generica: sia Instagram che TikTok declassano i
contenuti percepiti come prodotti in serie, e un occhio umano per due secondi
al giorno è la difesa più economica che esista.
