#!/usr/bin/env python3
"""Confronta i video TikTok prima e dopo uno stacco.

    python3 analytics/confronto.py 2026-09-14

Serve alle prove che si giudicano NEL TEMPO invece che fra bracci:
`esperimento.domanda` (stacco 6 settembre 2026) e
`esperimento.cta_anticipata` (stacco 14 settembre 2026). Le prove 2x2 su
lunghezza e apertura occupano gia' tutte le caselle, quindi quelle due si
leggono confrontando il periodo prima col periodo dopo.

I dati arrivano dall'API ufficiale di TikTok, che da' anche le
visualizzazioni. La lettura e' sempre di ADESSO: i conteggi di un video
crescono per giorni dopo la pubblicazione, quindi la finestra "dopo" va letta
a prova finita e non a meta'.
"""
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine import metriche_social  # noqa: E402
from engine.publish import tiktok as api  # noqa: E402


def tutti() -> list:
    """Ogni video del profilo, non solo quelli della settimana."""
    import httpx

    tok = api._token_accesso()
    testa = {"Authorization": f"Bearer {tok}", "Content-Type": "application/json"}
    video, cursore = [], None
    with httpx.Client(timeout=30) as c:
        for _ in range(20):
            corpo = {"max_count": 20}
            if cursore:
                corpo["cursor"] = cursore
            d = c.post("https://open.tiktokapis.com/v2/video/list/",
                       headers=testa, params={
                           "fields": "id,create_time,view_count,like_count,"
                                     "comment_count,share_count"},
                       json=corpo).json().get("data", {})
            video += d.get("videos", []) or []
            if not d.get("has_more"):
                break
            cursore = d.get("cursor")
    return video


def riga(nome: str, v: list) -> None:
    if not v:
        print(f"  {nome:<8} nessun video")
        return
    viste = sum(x.get("view_count", 0) for x in v)
    if not viste:
        print(f"  {nome:<8} {len(v)} video, ancora nessuna vista")
        return
    m = lambda k: 1000 * sum(x.get(k, 0) for x in v) / viste
    print(f"  {nome:<8} {len(v):>3} video  {viste:>6} viste   "
          f"like {m('like_count'):>5.1f}/1000   "
          f"commenti {m('comment_count'):>4.2f}/1000   "
          f"condivisioni {sum(x.get('share_count', 0) for x in v)}")


def main() -> None:
    stacco = sys.argv[1] if len(sys.argv) > 1 else "2026-09-14"
    y, mo, d = (int(x) for x in stacco.split("-"))
    soglia = dt.datetime(y, mo, d, tzinfo=dt.timezone.utc).timestamp()

    video = tutti()
    print(f"\n{len(video)} video letti dall'API · stacco {stacco}\n")
    riga("prima", [x for x in video if x.get("create_time", 0) < soglia])
    riga("dopo", [x for x in video if x.get("create_time", 0) >= soglia])

    p = metriche_social.tiktok(9999) or {}
    print(f"\n  follower: {p.get('follower', '?')}  — cumulativi. La prova sulla "
          f"CTA si giudica\n  sui follower ogni mille viste, quindi serve anche "
          f"la lettura del giorno\n  dello stacco: sta in config.yaml sotto "
          f"esperimento.cta_anticipata.")
    print("\n  ⚠️ i commenti sono troppo rari per decidere: a 0,57 ogni mille "
          "viste\n     l'attesa e' 1,7 a settimana, che oscilla fra 0 e 4 per "
          "caso.")


if __name__ == "__main__":
    main()
