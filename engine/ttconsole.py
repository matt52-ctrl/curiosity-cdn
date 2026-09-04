"""La schermata di pubblicazione TikTok, che esiste per una ragione sola: l'audit.

Il sistema pubblica da solo e non ha bisogno di nessuna interfaccia. Ma per
togliere il limite `unaudited_client_can_only_post_to_private_accounts` — quello
che oggi costringe ogni video in bozza — TikTok chiede un audit, e l'audit si
chiede allegando un video che mostri la schermata di pubblicazione al lavoro.

Le Content Sharing Guidelines elencano cosa quella schermata deve avere, e non
sono consigli: sono la lista su cui il revisore mette le spunte.

  · il nickname del creator, perche' si veda su quale account si sta per uscire
  · il menu della privacy costruito su `privacy_level_options`, SENZA
    preselezione — dev'essere l'utente a scegliere
  · commenti, duetti e stitch spenti di default, e disattivati e grigi se il
    creator li ha chiusi nelle sue impostazioni
  · il contenuto commerciale su un interruttore spento, con la scelta fra
    "Your Brand" e "Branded Content" e le due diciture relative
  · con "Branded Content" la privacy privata va disabilitata, perche' TikTok
    non ammette contenuti brandizzati in visibilita' privata
  · la riga sulla Music Usage Confirmation prima di pubblicare
  · l'anteprima del video e un consenso esplicito prima che parta il caricamento

Gira in locale, su un indirizzo che non e' esposto a nessuno: serve a Mattia
per registrare il filmato e, dopo, resta come via manuale per pubblicare un
singolo video fuori dal ciclo automatico.
"""
from __future__ import annotations

import html
import http.server
import json
import socketserver
import urllib.parse
from pathlib import Path
from typing import Dict, List

from .config import OUTPUT_DIR

PORTA = 8723


def _video_pronti() -> List[Path]:
    cartella = OUTPUT_DIR / "tiktok"
    if not cartella.exists():
        return []
    return sorted(cartella.glob("*.mp4"))


def _didascalie() -> Dict[str, str]:
    try:
        lotto = json.loads((OUTPUT_DIR / "tiktok" / "lotto.json").read_text())
        return {v["file"]: v.get("didascalia", "") for v in lotto}
    except Exception:
        return {}


PAGINA = """<!doctype html><meta charset=utf-8>
<title>Oddly Wired — post to TikTok</title>
<style>
 body{{font:15px/1.55 -apple-system,system-ui,sans-serif;max-width:760px;
      margin:32px auto;padding:0 20px;color:#151311;background:#fbfaf8}}
 h1{{font-size:20px;margin:0 0 4px}}
 .chi{{display:flex;align-items:center;gap:12px;padding:12px;border:1px solid #e3ded6;
      border-radius:10px;background:#fff;margin:16px 0}}
 .chi img{{width:44px;height:44px;border-radius:50%}}
 fieldset{{border:1px solid #e3ded6;border-radius:10px;margin:16px 0;padding:14px 16px;
      background:#fff}}
 legend{{font-weight:600;padding:0 6px}}
 label{{display:block;margin:7px 0}}
 select,button{{font:inherit;padding:8px 10px;border-radius:8px;border:1px solid #cdc6bb}}
 button{{background:#151311;color:#fff;border:0;padding:11px 18px;cursor:pointer}}
 video{{width:260px;border-radius:10px;background:#000}}
 .spento{{color:#9a9086}}
 .nota{{font-size:13px;color:#6b6359;margin-top:10px}}
 .fila{{display:flex;gap:18px;align-items:flex-start}}
</style>
<h1>Post to TikTok</h1>
<div class=chi>
  <img src="{avatar}" alt="">
  <div><b>{nickname}</b><br><span class=spento>@{username}</span></div>
</div>
<form method=post action="/pubblica">
<fieldset><legend>Video</legend>
 <div class=fila>
   <video src="/file/{primo}" controls preload=metadata></video>
   <div>
     <label>File
       <select name=file>{opzioni_file}</select>
     </label>
     <div class=nota>Caption: {didascalia}</div>
   </div>
 </div>
</fieldset>

<fieldset><legend>Who can see this video</legend>
 <select name=privacy required>
   <option value="" selected disabled>Select an option</option>
   {opzioni_privacy}
 </select>
 <div class=nota id=notaprivacy></div>
</fieldset>

<fieldset><legend>Allow users to</legend>
 <label><input type=checkbox name=comment {comment_off}> Comment</label>
 <label><input type=checkbox name=duet {duet_off}> Duet</label>
 <label><input type=checkbox name=stitch {stitch_off}> Stitch</label>
</fieldset>

<fieldset><legend>Disclose video content</legend>
 <label><input type=checkbox id=commerciale name=commerciale>
   Turn on to disclose that this video promotes goods or services.</label>
 <div id=marche style="display:none;padding-left:22px">
   <label><input type=checkbox name=your_brand id=yourbrand> Your brand —
     you are promoting yourself or your own business.</label>
   <label><input type=checkbox name=branded id=branded> Branded content —
     you are promoting another brand or a third party.</label>
   <div class=nota id=etichetta></div>
 </div>
</fieldset>

<p class=nota id=musica>By posting, you agree to TikTok's
  <a href="https://www.tiktok.com/legal/page/global/music-usage-confirmation/en"
     target=_blank>Music Usage Confirmation</a>.</p>

<label><input type=checkbox name=conferma required> I have reviewed the video
  above and I want to post it now.</label>
<p><button type=submit>Post to TikTok</button></p>
</form>
<script>
 var c=document.getElementById('commerciale'), m=document.getElementById('marche'),
     yb=document.getElementById('yourbrand'), bc=document.getElementById('branded'),
     et=document.getElementById('etichetta'), mus=document.getElementById('musica'),
     pv=document.querySelector('[name=privacy]'), np=document.getElementById('notaprivacy');
 function stato() {{
   m.style.display = c.checked ? 'block' : 'none';
   if (!c.checked) {{ yb.checked = false; bc.checked = false; }}
   // Con "Branded content" la privacy privata non e' ammessa da TikTok.
   var op = pv.querySelector('option[value=SELF_ONLY]');
   if (op) {{
     op.disabled = bc.checked;
     if (bc.checked && pv.value === 'SELF_ONLY') pv.value = '';
   }}
   np.textContent = bc.checked
     ? 'Branded content cannot be set to private.' : '';
   et.textContent = bc.checked && yb.checked ? 'Your video will be labeled as "Paid partnership".'
     : bc.checked ? 'Your video will be labeled as "Paid partnership".'
     : yb.checked ? 'Your video will be labeled as "Promotional content".' : '';
   mus.innerHTML = bc.checked
     ? 'By posting, you agree to TikTok\\'s <a target=_blank '
       + 'href="https://www.tiktok.com/legal/page/global/bc-policy/en">Branded Content Policy</a> '
       + 'and <a target=_blank '
       + 'href="https://www.tiktok.com/legal/page/global/music-usage-confirmation/en">Music Usage Confirmation</a>.'
     : 'By posting, you agree to TikTok\\'s <a target=_blank '
       + 'href="https://www.tiktok.com/legal/page/global/music-usage-confirmation/en">Music Usage Confirmation</a>.';
 }}
 [c,yb,bc].forEach(function(e){{ e.addEventListener('change', stato); }});
 document.querySelector('[name=file]').addEventListener('change', function(e){{
   document.querySelector('video').src = '/file/' + e.target.value;
 }});
 stato();
</script>
"""


def _pagina() -> str:
    from .publish.tiktok import info_creatore

    info = info_creatore()
    video = _video_pronti()
    if not video:
        return ("<p>Nessun video pronto in <code>output/tiktok</code>. "
                "Montane uno prima:<br><code>python run.py tiktok --quanti 1"
                "</code></p>")
    cap = _didascalie()
    etichette = {
        "PUBLIC_TO_EVERYONE": "Everyone",
        "MUTUAL_FOLLOW_FRIENDS": "Friends",
        "SELF_ONLY": "Only you",
    }
    return PAGINA.format(
        avatar=html.escape(info.get("creator_avatar_url", "")),
        nickname=html.escape(info.get("creator_nickname", "")),
        username=html.escape(info.get("creator_username", "")),
        primo=urllib.parse.quote(video[0].name),
        opzioni_file="".join(
            f'<option value="{html.escape(v.name)}">{html.escape(v.name)}</option>'
            for v in video),
        didascalia=html.escape((cap.get(video[0].name) or "—")[:180]),
        opzioni_privacy="".join(
            f'<option value="{p}">{etichette.get(p, p)}</option>'
            for p in info.get("privacy_level_options", [])),
        # Spenti di default come chiede la guida, e disattivati quando il
        # creator li ha chiusi nelle proprie impostazioni.
        comment_off="disabled" if info.get("comment_disabled") else "",
        duet_off="disabled" if info.get("duet_disabled") else "",
        stitch_off="disabled" if info.get("stitch_disabled") else "",
    )


class _Server(http.server.BaseHTTPRequestHandler):
    def _manda(self, corpo: str, tipo: str = "text/html; charset=utf-8") -> None:
        dati = corpo.encode() if isinstance(corpo, str) else corpo
        self.send_response(200)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(dati)))
        self.end_headers()
        self.wfile.write(dati)

    def log_message(self, *a):        # niente rumore nel terminale
        pass

    def do_GET(self):  # noqa: N802
        if self.path.startswith("/file/"):
            nome = urllib.parse.unquote(self.path[6:])
            # Solo i file della cartella dei video, e solo per nome: il
            # percorso non si compone con cio' che arriva dalla richiesta.
            f = next((v for v in _video_pronti() if v.name == nome), None)
            if not f:
                self.send_error(404)
                return
            dati = f.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Content-Length", str(len(dati)))
            self.end_headers()
            self.wfile.write(dati)
            return
        try:
            self._manda(_pagina())
        except Exception as exc:
            self._manda(f"<p>Errore: {html.escape(str(exc))}</p>")

    def do_POST(self):  # noqa: N802
        lung = int(self.headers.get("Content-Length", 0))
        campi = urllib.parse.parse_qs(self.rfile.read(lung).decode())
        prendi = lambda k: (campi.get(k) or [""])[0]

        nome = prendi("file")
        video = next((v for v in _video_pronti() if v.name == nome), None)
        privacy = prendi("privacy")
        if not (video and privacy):
            self._manda("<p>Manca il video o il livello di privacy. "
                        "<a href='/'>Torna indietro</a></p>")
            return

        from .publish.tiktok import pubblica_diretto
        opzioni = {
            "privacy_level": privacy,
            # La guida ragiona per permessi ("allow users to"), l'API per
            # divieti: la spunta accesa vuol dire consentito, quindi qui si
            # rovescia. Sbagliare il verso pubblicherebbe video con i commenti
            # chiusi, cioe' l'opposto di cio' che si e' scelto a schermo.
            "disable_comment": not prendi("comment"),
            "disable_duet": not prendi("duet"),
            "disable_stitch": not prendi("stitch"),
            "brand_content_toggle": bool(prendi("branded")),
            "brand_organic_toggle": bool(prendi("your_brand")),
        }
        try:
            pid = pubblica_diretto(video, _didascalie().get(nome, ""),
                                   opzioni=opzioni)
            self._manda(
                f"<p>Pubblicato. publish_id <code>{html.escape(pid)}</code>"
                f"<br>Il video compare sul profilo entro qualche minuto.</p>"
                f"<p><a href='/'>Pubblicane un altro</a></p>")
        except Exception as exc:
            self._manda(f"<p>TikTok ha rifiutato: {html.escape(str(exc)[:400])}"
                        f"</p><p><a href='/'>Torna indietro</a></p>")


def avvia(porta: int = PORTA) -> None:
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("127.0.0.1", porta), _Server) as srv:
        print(f"Schermata di pubblicazione TikTok su  http://localhost:{porta}")
        print("Ctrl-C per chiudere.")
        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            print("\nchiusa.")
