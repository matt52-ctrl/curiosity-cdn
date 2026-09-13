# Metriche TikTok

**Dall'API ufficiale, non dalla pagina.** Il 13 settembre 2026 si è riprovato
`video/list` con lo stesso token che usiamo per pubblicare: risponde **200**.
La nota di agosto che lo dava per negato (`401 scope_not_authorized`) era
vecchia. Quindi non serve nessun browser e nessun passaggio a mano.

    /v2/user/info/    follower_count, likes_count, video_count
    /v2/video/list/   id, create_time, view_count, like_count,
                      comment_count, share_count      (paginata, 20 per volta)

In codice: `engine/metriche_social.py` → `tiktok(giorni)`. Il riepilogo
settimanale su Telegram la usa da solo.

## Confronto prima/dopo uno stacco

    python3 analytics/confronto.py 2026-09-14

Serve alle prove che si giudicano nel tempo — `esperimento.domanda` (stacco
6 settembre) e `esperimento.cta_anticipata` (stacco 14 settembre).

## La via dalla pagina, per memoria

C'era anche un modo senza API: ogni pagina video incorpora i conteggi in
`<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__">`, e basta un GET con un
`User-Agent` da browser per leggerli. Funziona ancora e non chiede
credenziali, ma manca l'elenco degli id — la griglia del profilo arriva solo
da JavaScript, e un browser headless ci finisce dentro il CAPTCHA a
scorrimento. Serve solo se un giorno il token TikTok muore.
