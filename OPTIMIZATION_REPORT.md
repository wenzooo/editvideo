# OPTIMIZATION_REPORT — performance & qualità del codice EditVideo

> Sessione di **ottimizzazione**: analisi + implementazione del primo scaglione di
> quick win (branch `perf/quick-wins-1`). Focus su ciò che NON era già coperto dai
> report esistenti (`SCALING_REPORT` = data-lifecycle, `SECURITY_REPORT` = sicurezza,
> `TEST_REPORT` = baseline test). Tutti i riferimenti sono `file:riga` sul codice
> verificato. Contesto deploy: **Hugging Face Space free** (2 vCPU / 16 GB, CPU-only,
> disco effimero).

---

## 0. Sintesi esecutiva

Il progetto è già sano dove di solito fa male: **bundle frontend snello** (~61 KB gzip
il chunk principale, code-splitting per route già presente, nessuna libreria pesante,
nessun leak di `URL.createObjectURL`), data-lifecycle e sicurezza già auditati. Le
ottimizzazioni residue sono di due tipi: **CPU sprecata nel processing** lato server e
**disciplina di render/polling** lato client — entrambe si sentono sul HF free a 2 vCPU.

**Implementato in questo batch (7 interventi, suite verde):** vedi §1. In sintesi: stop
alla decodifica video inutile in silencedetect, stop alla ricompressione dei media,
media job in SQL bounded, sessione DB ridondante rimossa, listener keydown registrati
una volta sola, polling gated sulla visibilità, guardia anti-race nel caricamento editor.

**Rimandato a batch successivi (più invasivo, §2):** alleggerire il payload di
`/api/videos` (array `cuts` → conteggio), non tenere la sessione DB aperta durante ffmpeg,
`zoompan` solo sull'intro, spezzare `Editor.tsx` (monolite 1677 righe) con `SegmentRow`
memoizzato + key stabile, ridimensionare la Waveform.

---

## 1. Implementato in questo batch — `perf/quick-wins-1`

### 1.1 `silencedetect` non decodifica più il video (`-vn`)
**File:** `backend/app/services/silence.py:25-33`
**Prima:** `ffmpeg -i <path> -af silencedetect ... -f null -` — il muxer `null` mappa
anche lo stream video, quindi ffmpeg **decodifica l'intero H.264 1080×1920** solo per
buttarlo. L'auto-silenzi è ON di default e gira su ogni `run_transcribe` e ogni `autocut`.
**Dopo:** aggiunto `-vn` → il video non viene nemmeno decodificato.
**Misura (benchmark reale su questa macchina):** su una clip 9s a tinta unita (decodifica
banale) **0,350s → 0,071s per run, −80%**. Su un video reale complesso il divario è molto
più ampio (lì la decodifica H.264 pesa davvero). Impatto: **Alto** · Effort: **S**.

### 1.2 I media non vengono più ricompressi da GZip
**File:** `backend/app/routers/videos.py:325-341` (`_serve_media`) + `backend/app/main.py:55-59`
**Prima:** `GZipMiddleware` app-wide comprimeva **qualsiasi** risposta, inclusi
`/file`, `/thumbnail`, `/export/file|download` — cioè MP4 (fino a 2 GB) e JPEG **già
compressi**: ~0% di guadagno, 100% di CPU, e Range/seek del `<video>` degradati.
**Dopo:** `_serve_media` imposta `Content-Encoding: identity` sui media → Starlette 1.0
salta la compressione e lascia intatto lo streaming Range (un solo punto, copre tutti e 4
gli endpoint). In più `compresslevel` del gzip 9→6 (stesso rapporto sul JSON, meno CPU).
Impatto: **Alto** · Effort: **S**.

### 1.3 Media dei tempi job in SQL bounded (non più tutta la tabella in Python)
**File:** `backend/app/services/metrics.py:27-53`
**Prima:** `_avg_done_job_seconds` faceva `SELECT` di **tutte** le righe `done` (con la
retention a 30 giorni: migliaia) e mediava in Python; invocata dall'health profondo (ON di
default) e da `/api/metrics`.
**Dopo:** `ORDER BY finished_at DESC LIMIT 500` → scan bounded, aritmetica date ancora
backend-agnostica, media "recente" più utile. Impatto: **Medio** · Effort: **S**.

### 1.4 Rimossa la terza sessione DB in export
**File:** `backend/app/worker.py:238-280`
**Prima:** `run_export` apriva una sessione extra solo per rileggere `has_audio` (già
presente sull'oggetto `video` della prima sessione).
**Dopo:** `has_audio` catturato nel primo blocco; sessione ridondante eliminata.
Impatto: **Basso** · Effort: **S**.

### 1.5 Listener keydown registrati una sola volta (Editor)
**File:** `frontend/src/pages/Editor.tsx` (effetti scorciatoie e undo/redo)
**Prima:** due `useEffect` di `keydown` **senza dependency array** → `add/removeEventListener`
su `window` a **ogni** render; durante il playback `setCurrent` (~4 Hz) causava churn
continuo di listener.
**Dopo:** pattern *latest-ref* — l'handler è tenuto in un `useRef` sempre aggiornato e il
listener `window` è registrato una sola volta (`[]`). Comportamento identico, zero churn.
Impatto: **Alto** · Effort: **S**.

### 1.6 Polling gated sulla visibilità + backoff a riposo (Dashboard)
**File:** `frontend/src/pages/Dashboard.tsx` (nuovo hook `useVisiblePolling`)
**Prima:** due `setInterval` (job 2,5s; video 2,5/10s) **senza** gating: continuavano a
pollare all'infinito anche con la tab/iframe in background → richieste a vuoto sul backend
HF free.
**Dopo:** hook `useVisiblePolling` che ferma i timer su `document.hidden` e riparte (con
refresh immediato) al ritorno; job con backoff a riposo (2,5s attivo / 8s idle).
Impatto: **Alto** · Effort: **S/M**.

### 1.7 Guardia anti-race nel caricamento dell'editor
**File:** `frontend/src/pages/Editor.tsx:113-155` (`load` + `loadSeqRef`)
**Prima:** cambiando video rapidamente (Precedente/Successivo), la risposta di un `load()`
precedente poteva arrivare dopo e **sovrascrivere** lo stato del video corrente.
**Dopo:** numero di sequenza per ogni `load()`; le risposte superate vengono scartate.
Impatto: **Medio/Alto** · Effort: **S**.

**Verifica batch:** backend **439** test veloci + **4** smoke E2E verdi; frontend
`tsc` OK, `eslint --max-warnings 0` OK, **67** test verdi, build OK. `frontend/dist`
rigenerata (è versionata di proposito).

---

## 2. Rimandato — candidati per i prossimi batch (più invasivi)

Ordinati per impatto × probabilità. NON implementati qui perché richiedono modifiche
strutturali e/o sign-off (comportamento visibile), da fare con calma e test dedicati.

### Backend
1. **`/api/videos` rispedisce l'intero array `cuts` ad ogni poll** — `routers/videos.py:127-143`
   + `schemas.py:52`. Su clip lunghe i tagli auto superano il migliaio; la Dashboard usa
   solo `cuts.length`. Introdurre `VideoListOut` con `cut_count: int` (il get singolo
   dell'editor continua a restituire i cuts completi). **Medio-Alto · M.**
2. **Sessione/connessione DB tenuta aperta durante subprocess ffmpeg** — `routers/videos.py:216-235`
   (`autocut`, `silencedetect` timeout 600s) e `72-118` (`upload`). Su SQLite la transazione
   di lettura lunga impedisce il checkpoint del WAL e lega una connessione del pool. Leggere,
   chiudere la sessione, lanciare ffmpeg, riaprire una sessione breve per l'esito. **Medio · M.**
3. **`zoompan` su tutto il video per un intro-zoom di 0,9s** (default ON) — `services/ffmpeg.py:219-232`.
   Applicarlo solo al segmento iniziale (`split`+`zoompan`+`concat`) quando non c'è
   `smooth_zoom`. Richiede verifica visiva. **Medio · M/L.**
4. **Cache modello Whisper a slot singolo** — `services/transcribe.py:18-48`. Ricarica ~1,5 GB
   quando alterna `medium`↔`small` (fallback). Trasformare in `dict[key→model]` (tetto 2).
   Solo su path d'errore. **Basso-Medio · S.**
5. **Guardrail `worker_concurrency>1` con CPU** — `worker.py:416-423`. Oversubscription su
   2 vCPU: avvisare in `validate_runtime`. **Basso · S.**
6. **Doppio commit per file in upload** — `routers/videos.py:112-117`. Un fsync per video +
   uno per job; accorpare. **Basso · S.**
7. **Qualità/DRY**: duplicazione normalizzazione `plan` (`ffmpeg.py:166,320`); `build_export_graph`
   ~100 righe da spezzare; `remap_segments_detailed` vs `_plan` quasi identiche
   (`timeline.py:162-217`); `silences_to_cuts` delegabile a `_and_speedups` (`silence.py`);
   `_get_video` duplicato (`videos.py:28`, `subtitles.py:16`). **Basso · S ciascuno.**

### Frontend
1. **`Editor.tsx` monolite (1677 righe, ~40 `useState`)** — è la radice dei problemi di
   render. Estrarre `usePlayer`, `useJobPolling`, `useSegmentHistory`, `<SubtitleList>`,
   `<TrimControls>`, `<AutomationsPanel>`. **Medio (abilita gli altri) · L.**
2. **Lista sottotitoli: re-render ~4 Hz + `key={i}` su lista editabile** — `Editor.tsx:1500-1590`
   (key ~1502). Estrarre `SegmentRow` come `React.memo` con **id stabile** come key (esiste
   già `Segment.id?` in `types.ts:37`); memoizzare `activeSegment`. Corregge anche i bug di
   focus su split/merge/delete. **Alto · M.**
3. **Waveform scarica l'intero video in RAM (fino a 180 MB) per una decorazione** —
   `Waveform.tsx:30,149-160`. Abbassare `MAX_BYTES`, disattivare su viewport strette/mobile,
   o calcolare i picchi lato backend e servirli come JSON. **Alto · M.**
4. **Tabella Dashboard non memoizzata** — `Dashboard.tsx` (`shown.map`). Ogni poll rirenderizza
   tutte le righe (ognuna con un `<Menu>`). Estrarre `VideoRow` come `React.memo`;
   valutare virtualizzazione su librerie grandi. **Medio · M.**
5. **Karaoke word-highlight guidato da stato React a ~4 Hz** — `Editor.tsx` (~975). Guidarlo via
   `requestAnimationFrame` leggendo `video.currentTime` (come già fa lo zoom d'ingresso),
   senza `setCurrent`. Migliora la fluidità dell'anteprima. **Basso/Medio · M.**
6. **DRY frontend**: `flash`+`setTimeout` e traduzione errori duplicati tra `Editor` e
   `Dashboard`; `setTimeout` di `flash` non ripulito su unmount; `api.req` senza `signal`
   (precondizione per abortire le fetch). **Basso · S ciascuno.**

---

## 3. TOP priorità per il prossimo batch

1. **`SegmentRow` memoizzato + key stabile** (frontend) — taglia il re-render di centinaia
   di `<textarea>` 4 volte/sec e corregge i bug di focus. *Alto/M.*
2. **`/api/videos` → `cut_count`** (backend) — toglie decine di migliaia di validazioni
   `CutRange` ad ogni poll. *Medio-Alto/M.*
3. **Waveform ridimensionata** (frontend) — evita picchi di RAM da 180 MB nell'iframe. *Alto/M.*
4. **Sessione DB fuori dai subprocess ffmpeg** (backend) — WAL e pool più sani. *Medio/M.*
5. **Spezzare `Editor.tsx`** (frontend) — abilita 1 e 5 e riduce il rischio di regressioni. *L.*
