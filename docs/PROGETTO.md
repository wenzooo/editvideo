# EditVideo — Piattaforma di editing batch per video verticali 9:16

Documento di progetto. Creato: 2026-07-07 · aggiornato: 2026-07-08 (stato di avanzamento in §10).

---

## 1. Analisi del progetto

**Problema.** Editare manualmente 30–40 video verticali al giorno (tagli + sottotitoli + export) è un collo di bottiglia. Le operazioni sono ripetitive e al 90% automatizzabili: l'unico passaggio che richiede davvero l'occhio umano è la *revisione* dei sottotitoli e dei tagli.

**Principio guida del prodotto.** La piattaforma non è un "editor video generico": è una **catena di montaggio con una stazione di controllo umano**. Tutto ciò che può girare senza di te (trascrizione, rendering, export) va in coda; tu intervieni solo nella fase "da controllare".

**Vincoli dichiarati dall'utente:**
- Deploy in cloud fin da subito.
- Costo il più vicino possibile a zero (nessuna API a pagamento per la trascrizione).
- Nessuna dipendenza da GPU NVIDIA → tutta la pipeline è **CPU-only**.
- Parlato in italiano.

**Assunzioni esplicite (dove mancavano dettagli):**
1. **Mono-utente** (tu). Niente registrazione/ruoli nell'MVP; c'è comunque una password di accesso perché l'app è esposta su internet.
2. Clip **brevi** (15 sec – 5 min tipici). La pipeline funziona anche oltre, ma il dimensionamento è tarato su questo.
3. Input **già prevalentemente verticali**; se un video non è 9:16 viene scalato e ritagliato al centro (crop cover) a 1080×1920.
4. I sottotitoli si generano sulla **timeline originale** del video; al momento dell'export i tempi vengono rimappati automaticamente sui tagli. Così puoi cambiare i tagli dopo la trascrizione senza rigenerare nulla.
5. "Cut semplici" = **rimozione di intervalli** interni (es. elimina 00:12–00:15), oltre a trim di inizio/fine.
6. Export standard unico: **MP4 H.264 + AAC, 1080×1920, sottotitoli impressi**, `faststart` per upload social.
7. Storage su **disco del server** (volume Docker). L'astrazione è pronta per passare a S3/R2 in futuro.
8. I file sorgente si eliminano manualmente dalla dashboard dopo l'export (retention automatica = funzione futura).

**Dimensionamento (capacity planning, CPU-only).** Su 4 vCPU (es. Oracle Free ARM):
- Trascrizione faster-whisper `small` int8: ~0,5–1× tempo reale → clip da 60s ≈ 40–90s.
- Export FFmpeg (x264 veryfast, 1080×1920): ≈ 1–1,5× tempo reale → clip da 60s ≈ 60–90s.
- **40 clip da ~60s ≈ 1–2 ore di elaborazione totale al giorno**, in coda, senza intervento umano. Ampiamente sostenibile: carichi al mattino, revisioni quando è comodo, la macchina lavora da sola.

**Il punto onesto su "cloud gratis".** Il video processing richiede CPU vera: i free tier di Render/Railway/Vercel non reggono (RAM/CPU/disco insufficienti). Le opzioni reali sono:

| Opzione | Costo | Note |
|---|---|---|
| **Hugging Face Spaces (free, 2 vCPU / 16 GB)** | **0 €** | **Deploy attuale** (Space privato `brushk/editvideo`, file in `deploy/hf-space/`). Regge la pipeline, con due caveat: disco effimero (gli export vanno scaricati in giornata) e sleep dopo ~48h di inattività. |
| **Oracle Cloud Always Free (ARM Ampere A1)** | **0 €** | Fino a 4 vCPU / 24 GB RAM / 200 GB disco, per sempre. Serve carta per la registrazione; la disponibilità di istanze ARM a volte richiede più tentativi. Tutto lo stack ha wheel ARM64: funziona. |
| VPS economico (Hetzner CX22/CPX21, Contabo…) | ~4–6 €/mese | Zero sorprese, disponibilità immediata. |
| Lo stesso stack in locale via Docker | 0 € | Identico al cloud: il deploy è una scelta, non una riscrittura. |

**Raccomandazione:** sviluppo/test con `docker compose up` ovunque. Oggi la produzione gira su **Hugging Face Spaces** (0 €, con i caveat sopra); quando servirà disco persistente il percorso resta **Oracle Free ARM** (0 €) o un VPS da 5 €: l'immagine Docker è la stessa.

---

## 2. Architettura consigliata

Monolite modulare + worker: la scelta giusta per un MVP mono-utente che deve essere solido e poi crescere. Niente microservizi, niente Redis finché non servono.

```
                    ┌────────────────────────── VM cloud (Docker) ──────────────────────────┐
                    │                                                                        │
 Browser (tu)       │  ┌─────────────┐   ┌──────────────────────┐    ┌───────────────────┐  │
 React SPA  ───────►│  │  Caddy      │──►│  web (FastAPI)       │───►│  Postgres         │  │
 upload/review      │  │  HTTPS      │   │  API REST + statici  │    │  video/subs/jobs  │  │
                    │  └─────────────┘   │  auth token firmato  │    └─────────▲─────────┘  │
                    │                    └──────────┬───────────┘              │            │
                    │                               │ scrive job (tabella)     │ claim job  │
                    │                    ┌──────────▼───────────┐              │            │
                    │                    │  worker (stesso      │──────────────┘            │
                    │                    │  codice, processo    │                           │
                    │                    │  separato)           │                           │
                    │                    │  • faster-whisper    │   ┌───────────────────┐   │
                    │                    │  • FFmpeg            │──►│ volume /media      │   │
                    │                    └──────────────────────┘   │ originals/ thumbs/ │   │
                    │                                               │ subs/ exports/     │   │
                    └───────────────────────────────────────────────┴───────────────────┴───┘
```

**Decisioni chiave e perché:**

- **Coda job = tabella nel DB** (polling + claim atomico), non Redis/Celery. Un solo consumatore basta per 40 video/giorno, elimina un intero servizio da gestire, e l'interfaccia (`jobs` table + worker loop) è identica a quella che avresti con Celery: quando servirà parallelismo vero si sostituisce solo il trasporto.
- **Worker sequenziale (concorrenza 1, configurabile).** Whisper e FFmpeg saturano da soli tutte le CPU: processare 2 video insieme su 4 vCPU è più lento, non più veloce. La coda dà comunque l'effetto "batch".
- **Sottotitoli sulla timeline originale + remapping all'export.** La trascrizione è l'operazione più costosa: non va mai ripetuta perché hai cambiato un taglio. La funzione di remapping (piecewise, gestisce sottotitoli a cavallo di un taglio) è pura e unit-testata.
- **Burn-in con ASS + libass** (filtro `ass` di FFmpeg): stili veri (bordo, box, posizione, dimensione) definiti come preset, resa identica su ogni piattaforma, un solo passaggio di encoding.
- **Stessa immagine Docker per web e worker** (comando diverso): build unica, zero drift.
- **SPA servita da FastAPI**: un solo processo esposto, nessun CORS, deploy banale. In dev il frontend gira con Vite + proxy.
- **Macchina a stati esplicita** per ogni video: `caricato → in elaborazione → da controllare → pronto → in export → esportato` (+ `errore`). È il cuore del flusso batch: la dashboard è una vista sugli stati.
- **Auth minimale ma reale**: password singola (env) → token HMAC firmato, che la SPA salva in `localStorage` e invia come `Authorization: Bearer` sulle API e come `?t=` sulle risorse media; il cookie HttpOnly resta come fallback. Così l'app funziona anche nell'iframe di Hugging Face, dove i cookie di terze parti sono bloccati. Sufficiente per mono-utente esposto su internet dietro HTTPS.

---

## 3. Stack tecnico consigliato

| Livello | Scelta | Perché | Alternative scartate |
|---|---|---|---|
| Backend | **Python 3.11 + FastAPI** | Ecosistema perfetto per faster-whisper e orchestrazione FFmpeg; API tipate con Pydantic; async dove serve | Node/Express (avrebbe bisogno di un servizio Python separato per Whisper) |
| Trascrizione | **faster-whisper (CTranslate2), modello `small`, int8, CPU** | Gratis, ottimo in italiano, 4× più veloce di openai-whisper a parità di modello, niente PyTorch, gira su x86 e ARM. Timestamp a livello di parola → caption in stile TikTok | Whisper API (a pagamento), whisper.cpp (binding meno comodi), Vosk (qualità inferiore in it) |
| Elaborazione video | **FFmpeg** (filter_complex: trim/concat, scale+crop 9:16, `ass` burn-in) | Lo standard. Un solo re-encode per export | MoviePy (lento, fragile), editly (Node, meno controllo) |
| Database | **PostgreSQL 16** in produzione, **SQLite** in dev/test (stesso codice via SQLAlchemy) | Postgres per claim atomici e crescita; SQLite = zero attrito in sviluppo | MongoDB (nessun bisogno di schema-less) |
| ORM | SQLAlchemy 2 | Standard, migrazione futura con Alembic | — |
| Coda | **Tabella `jobs` + worker polling** | Vedi architettura | Celery+Redis (overkill ora; percorso di upgrade documentato) |
| Frontend | **React 18 + Vite + TypeScript** | Editor con timeline e lista sottotitoli = UI a stato ricco; TS protegge l'evoluzione | HTMX (troppo poco per un editor), Next.js (SSR inutile qui) |
| Storage | Filesystem su volume Docker (`/media`) | Semplice, gratis; interfaccia isolata in `config`/servizi per futuro S3/R2 | S3 subito (costi/complessità senza beneficio mono-utente) |
| Reverse proxy | Caddy (2 righe di config, HTTPS automatico) | Let's Encrypt senza sbatti | Nginx (va benissimo, più config) |
| Deploy | **Docker**: oggi su Hugging Face Spaces (free); in alternativa Compose su Oracle Free ARM / VPS | Identico in locale e in cloud; ARM64 supportato da tutte le dipendenze | Kubernetes (no), PaaS free tier generici (non reggono video; HF Spaces è l'eccezione, con i caveat di §1) |

Costo totale di esercizio: **0 €** (oggi su Hugging Face Spaces; 0 € anche su Oracle Free, ~5 €/mese su VPS). Nessuna API a pagamento in tutta la pipeline.

---

## 4. Struttura delle cartelle

```
EditVideo/
├── docs/PROGETTO.md            ← questo documento
├── README.md                   ← guida operativa (avvio, deploy, config)
├── docker-compose.yml          ← db + web + worker (produzione/locale)
├── Dockerfile                  ← immagine unica web/worker (ffmpeg incluso)
├── .env.example                ← tutte le variabili di configurazione
├── run.py                      ← avvio dev in un comando (API + worker embedded)
│
├── backend/
│   ├── requirements.txt
│   ├── app/
│   │   ├── main.py             ← app FastAPI, statici SPA, lifespan (worker embedded)
│   │   ├── config.py           ← settings (env), percorsi media, default per OS
│   │   ├── db.py               ← engine SQLAlchemy (SQLite/Postgres), init
│   │   ├── models.py           ← Video, SubtitleSegment, Job + stati
│   │   ├── schemas.py          ← contratti Pydantic delle API
│   │   ├── auth.py             ← login a password, token/cookie HMAC, dependency
│   │   ├── worker.py           ← loop coda: claim job → transcribe/export
│   │   ├── routers/
│   │   │   ├── videos.py       ← upload multiplo, lista, patch trim/cuts, file/thumb/export
│   │   │   ├── subtitles.py    ← get/replace segmenti sottotitoli
│   │   │   ├── batch.py        ← trascrivi-tutti, esporta-pronti
│   │   │   └── jobs.py         ← stato job, stili, health
│   │   └── services/
│   │       ├── ffmpeg.py       ← probe, thumbnail, comando export (filter_complex)
│   │       ├── timeline.py     ← FUNZIONI PURE: keep-intervals, remap tempi (testate)
│   │       ├── captions.py     ← chunking parole → caption leggibili (testate)
│   │       ├── styles.py       ← preset stile → file .ass
│   │       └── transcribe.py   ← faster-whisper (lazy load, cache modello)
│   └── tests/
│       ├── test_units.py       ← timeline, captions, styles
│       └── test_smoke.py       ← end-to-end reale: upload→cuts→subs→export (con ffmpeg)
│
├── frontend/
│   ├── package.json  vite.config.ts  tsconfig.json  index.html
│   ├── dist/                   ← build committata: il server gira senza Node
│   └── src/
│       ├── main.tsx  App.tsx  api.ts  types.ts  format.ts  styles.css
│       ├── components/         ← StatusBadge, UploadZone, Timeline, StylePicker, JobsBar
│       └── pages/              ← Dashboard.tsx, Editor.tsx
│
├── media/   (runtime, gitignored)  originals/ thumbnails/ subs/ exports/
└── data/    (runtime, gitignored)  app.db (SQLite in dev), secret.key
```

Alla struttura MVP qui sopra le ondate successive (§10) hanno aggiunto: `backend/app/routers/templates.py` e `backend/app/services/formats.py` (Format), `services/silence.py` (taglia-silenzi), `services/retakes.py` (taglia-doppioni), `backend/app/assets/whoosh.wav` (suono dello zoom d'ingresso) e `deploy/hf-space/` (file dello Space Hugging Face).

Regia della struttura: **routers = HTTP, services = logica, worker = esecuzione**. Le funzioni critiche (remap tempi, chunking caption, costruzione ASS) sono pure e senza I/O → testabili e riusabili quando arriveranno template e automazioni.

---

## 5. Modello database

**videos**

| Campo | Tipo | Note |
|---|---|---|
| id | str (uuid) PK | |
| original_name | str | nome file caricato (solo display) |
| stored_path / thumbnail_path / exported_path | str | percorsi su /media |
| duration | float | secondi, da ffprobe |
| width, height | int | |
| fps | float | |
| size_bytes | int | |
| has_audio | bool | pilota il grafo FFmpeg |
| status | str | `uploaded · transcribing · review · ready · exporting · exported · error` |
| error_message | str? | |
| trim_start | float | default 0 |
| trim_end | float? | null = fine video |
| cuts | JSON | `[{start,end}]` intervalli **da rimuovere** (timeline originale) |
| subtitle_style | str | id preset (default `classic_white`) |
| created_at, updated_at | datetime | |

**subtitle_segments**

| Campo | Tipo | Note |
|---|---|---|
| id | int PK | |
| video_id | FK → videos (cascade) | |
| idx | int | ordine |
| start, end | float | secondi, **timeline originale** |
| text | str | editabile dall'utente |

**jobs**

| Campo | Tipo | Note |
|---|---|---|
| id | str (uuid) PK | |
| video_id | FK → videos | |
| type | str | `transcribe` · `export` |
| status | str | `queued · running · done · error` |
| progress | float | 0–1, aggiornato live (segmenti Whisper / out_time FFmpeg) |
| error | str? | |
| created_at, started_at, finished_at | datetime | |

Relazioni: 1 video → N segmenti, 1 video → N job. Migrazioni: `create_all` nell'MVP, Alembic quando lo schema comincerà a muoversi.

Aggiunte delle ondate successive (§10): su **videos** `tail_trim` (secondi tagliati dalla fine) e i flag `auto_silence`/`auto_retakes`/`auto_export`; su **subtitle_segments** `words` (timestamp per parola, base del karaoke); nuova tabella **templates** (i Format: trim, tail_trim, cuts, stile e automazioni riutilizzabili).

---

## 6. Flusso utente completo (giornata tipo)

Con i **Format** (v2+, §10) la giornata si riduce a: selezioni il format → trascini 30–40 video → le automazioni fanno tutto da sole (con 🚀 auto-export fino a *esportato*) → correggi i testi in *da controllare* → scarichi gli MP4. Il percorso manuale qui sotto resta valido ed è quello su cui si innestano le automazioni.

1. **Login** (password) → Dashboard.
2. **Upload**: trascini 30–40 file. Per ognuno il server salva l'originale, legge metadata (ffprobe) e genera la thumbnail → stato `caricato`.
3. **"Genera sottotitoli per tutti"**: un click → un job `transcribe` per ogni video `caricato`. Il worker li processa in coda; la dashboard mostra avanzamento live. A fine trascrizione ogni video passa a `da controllare`.
4. **Revisione** (l'unico passaggio umano): apri l'**Editor** su un video →
   - preview + timeline; imposti trim inizio/fine (maniglie o "= qui");
   - aggiungi eventuali tagli interni (segna A, segna B);
   - correggi il testo dei sottotitoli (la riga attiva si evidenzia durante il play, click su una riga = seek);
   - scegli lo stile (5 preset, incluso il karaoke a parola evidenziata; anteprima sovrimpressa sul player);
   - salva → **"Segna pronto"** (stato `pronto`). Oppure **"Salva ed esporta"** direttamente da qui (download automatico a fine job).
5. **"Esporta tutti i pronti"**: un click → job `export` in coda per ogni video `pronto`. Il worker: calcola gli intervalli da tenere → rimappa i tempi dei sottotitoli sui tagli → genera l'.ass con lo stile scelto → FFmpeg (trim/concat + scale/crop 1080×1920 + burn-in) → stato `esportato`.
6. **Download** dei singoli MP4 finali dalla dashboard (pronti per TikTok/Reels/Shorts), poi **elimina** i lavorati per liberare spazio.

In ogni momento il filtro per stato risponde alla domanda operativa: *"cosa devo controllare?"*, *"cosa è pronto da scaricare?"*.

---

## 7. Funzioni MVP (prima consegna — v1; le ondate successive sono in §10)

- Upload multiplo con metadata (nome, durata, formato, peso, data) e thumbnail.
- Dashboard: lista video, filtro per stato, azioni per riga (editor / genera sottotitoli / esporta / scarica / elimina), azioni batch, indicatore job attivi con progresso.
- Editor: preview HTML5, timeline visuale con maniglie di trim draggabili e seek, tagli interni multipli, salvataggio modifiche.
- Sottotitoli automatici in italiano (faster-whisper CPU, VAD, timestamp a parola, chunking in caption leggibili) + editing manuale completo (testo e tempi).
- 4 preset di stile: bianco/bordo nero, giallo/bordo nero, grande centrale stile TikTok, basso classico con box.
- Export MP4 1080×1920 H.264+AAC con tagli applicati e sottotitoli impressi; download dal browser.
- Coda di lavorazione con stati, progresso live e gestione errori per video.
- Auth a password, Docker Compose pronto per il cloud, test end-to-end della pipeline.

## 8. Funzioni future (e dove si agganciano)

| Funzione | Aggancio tecnico già predisposto |
|---|---|
| ✅ Template per profilo — **consegnato (v2)** come **Format** | tabella `templates` + apply all'upload/in batch; restano da fare i preset *grafici* (loghi, overlay) |
| Intro/outro automatiche | voci in più nel grafo `filter_complex` (concat di clip statiche prima/dopo i keep-intervals) |
| Musica di sottofondo | secondo input audio + `amix` in `ffmpeg.py` |
| B-roll / overlay | layer video aggiuntivi nel grafo; il modello `cuts` si estende a `tracks` |
| ✅ Rimozione silenzi — **consegnata (v3)** | `services/silence.py`: `silencedetect` di FFmpeg → `cuts` automatici; in più `services/retakes.py` taglia i doppioni dai word-timestamp |
| ✅ Karaoke / parola evidenziata — **consegnato (v4)** | stile `karaoke_word` in `services/styles.py`: un evento ASS per parola, parola attiva gialla |
| Retention automatica / pulizia disco | job schedulato nel worker loop |
| Storage S3/R2 | i percorsi passano tutti da `config`: si sostituisce il layer di storage |
| Multi-utente | tabella `users` + `owner_id` su videos; auth già isolata in `auth.py` |
| Più worker / GPU opzionale | coda già claim-based (`SKIP LOCKED` su Postgres); `WHISPER_DEVICE=auto` |
| Pubblicazione diretta sui social | API TikTok/Meta/YouTube: nuovo tipo di job `publish` |

## 9. Piano di sviluppo a step

| Step | Contenuto | Stato |
|---|---|---|
| 0 | Setup repo, config, Docker, DB, auth | ✅ in questa consegna |
| 1 | Upload multiplo + probe/thumbnail + dashboard con stati | ✅ |
| 2 | Coda job su DB + worker | ✅ |
| 3 | Trascrizione faster-whisper + chunking caption + editor sottotitoli | ✅ |
| 4 | Editor trim/cuts con timeline + remapping tempi | ✅ |
| 5 | Stili ASS + export burn-in 1080×1920 + download | ✅ |
| 6 | Batch (trascrivi tutti / esporta pronti) + progresso live | ✅ |
| 7 | Test end-to-end + hardening upload/errori | ✅ |
| 8 | Deploy in cloud | ✅ su **Hugging Face Spaces** (Space privato, piano free); Oracle Free ARM/VPS restano documentati nel README |
| 9 | Prime funzioni future in ordine di ROI: rimozione silenzi automatica → template per profilo → intro/outro | ✅ consegnate in più ondate (Format, taglia-silenzi, taglia-doppioni, zoom d'ingresso, karaoke, auto-export — dettaglio in §10); restano intro/outro e le altre voci di §8 |
| 10 | Rifinitura & upgrade: UX/GUI, animazioni, accessibilità, mobile, feedback, robustezza/perf/sicurezza, nuove funzioni (command palette, statistiche, impostazioni, waveform, annulla job), qualità/DevEx/deploy | ✅ 20 iterazioni (v1.1.0 → v1.20.0), riassunte in §10 |

---

## 10. Stato di avanzamento (changelog)

Aggiornato al 2026-07-08. Quattro ondate hanno portato al primo rilascio stabile
(**v1.0.0**); dopo di esso **20 iterazioni di upgrade** (v1.1.0 → v1.20.0) hanno rifinito
interfaccia, accessibilità, mobile, robustezza e aggiunto nuove funzioni — riassunte nella
sotto-sezione dedicata più sotto. Il dettaglio versione-per-versione è nel
[`CHANGELOG.md`](../CHANGELOG.md); le scorciatoie da tastiera in
[`docs/SCORCIATOIE.md`](SCORCIATOIE.md).

Quattro ondate fino alla v1.0.0:

- **v1 — MVP** (steps 0–7): upload multiplo con metadata e thumbnail, dashboard a stati con azioni batch e progresso live, editor con timeline (trim draggabile, tagli interni, seek), sottotitoli automatici faster-whisper con editing completo, 4 stili burn-in, export MP4 1080×1920, coda job su DB, auth a password, Docker Compose, test end-to-end.
- **v2 — Format e salva-ed-esporta**: template riutilizzabili (trim iniziale, coda "secondi dalla fine", tagli, stile sottotitoli, automazioni ⚡🔇🔁🔍🚀) applicati all'upload o in batch ai video già caricati; **"Salva ed esporta"** con download automatico e **"Salva come format"** dall'editor.
- **v3 — Editing automatico**: taglia-silenzi preciso (`silencedetect`: pause ≥ `SILENCE_MIN_DUR` a `SILENCE_NOISE_DB`, taglio al centro della pausa con respiro `SILENCE_LEAVE`, bordi a filo), anche on-demand dall'editor (🔇); taglia-doppioni/ripartenze dai word-timestamp di Whisper (`RETAKE_MIN_MATCH`/`RETAKE_WINDOW`/`RETAKE_MAX_CUT`, si tiene l'ultima ripresa, caption rigenerate senza i doppioni); zoom d'ingresso con whoosh (`INTRO_ZOOM_AMOUNT`/`INTRO_ZOOM_DURATION`, suono sostituibile); ✂ sulla riga sottotitolo per tagliare dal video la parola sbagliata.
- **v4 — Karaoke, auto-export, hardening**: stile **karaoke parola evidenziata** (parola attiva gialla; richiede caption generate automaticamente — quelle modificate a mano perdono i tempi per parola e tornano normali); 🚀 auto-export nei format; robustezza (watchdog sugli export FFmpeg, recovery al riavvio dei job rimasti `running` con errore esplicito, pulizia di `.ass` e output parziali, limite `MAX_UPLOAD_MB`, anti path-traversal sui file statici, `WORKER_CONCURRENCY` rispettato); auth a token (`Authorization: Bearer` + `?t=` sui media, cookie come fallback); **deploy su Hugging Face Spaces** (Space privato `brushk/editvideo`, free 2 vCPU/16 GB, disco effimero, sleep dopo ~48h; `SECRET_KEY` come secret per sessioni persistenti).

### Iterazioni di upgrade (v1.1.0 → v1.20.0)

Dopo la v1.0.0, venti iterazioni di rifinitura. Non hanno cambiato l'architettura né i
contratti dati (la validazione Pydantic è retro-compatibile): hanno reso il prodotto più
curato, accessibile, robusto e ricco. Raggruppate per tema — la versione tra parentesi rimanda
al [`CHANGELOG.md`](../CHANGELOG.md).

- **UX / GUI.** La GUI è stata riorganizzata attorno a un componente **Menu** a tendina
  accessibile, con più respiro (v1.1.0). Sono arrivate una **command palette** stile ctOS
  (`Ctrl+K`) per navigare ed eseguire azioni globali (v1.11.0), una **dashboard** che ora si
  ordina per colonna, si cerca per nome e si seleziona a più elementi con azioni batch
  (v1.12.0), un pannello **Impostazioni** (accento, densità, movimento; salvati in
  `localStorage`) aperto dall'ingranaggio ⚙ (v1.14.0) e un **onboarding** al primo avvio con
  tooltip sui controlli (v1.15.0).
- **Animazioni.** Micro-animazioni fluide stile **Watch Dogs / ctOS** (scanline, sweep, glow di
  stato, flusso del progresso), tutte **dietro `prefers-reduced-motion`** e disattivabili anche
  dalle Impostazioni (v1.2.0).
- **Accessibilità.** Skip-link, landmark, `focus-visible`, ARIA, **timeline pilotabile da
  tastiera**, **pannello scorciatoie** (`?`), contrasto **AA** e focus che segue il cambio di
  route (v1.7.0). Riepilogo dei tasti in [`docs/SCORCIATOIE.md`](SCORCIATOIE.md).
- **Mobile.** Revisione responsive completa: breakpoint 980/720/640/480, topbar compatta,
  editor al tocco, nessun overflow fino a 360 px, layout iframe-safe; la tabella della
  dashboard degrada a schede su schermo stretto (base in v1.1.0, passata completa in v1.9.0).
- **Feedback.** Sistema di **toast**, **skeleton** di caricamento e stati **vuoto/errore** con
  **riprova** (v1.4.0); un **error boundary** trasforma un crash di rendering in una schermata di
  recupero (v1.14.0).
- **Backend, robustezza, performance, sicurezza.** Validazione input Pydantic e suite di test
  backend estesa a 81 test (v1.3.0); **logging strutturato** con `contextvars`
  (`job_id`/`video_id`), `validate_runtime()` all'avvio e worker più robusto (v1.5.0); **indici
  DB** e correzione di un **N+1** nelle liste (v1.8.0); **rate limit del login** per IP → **429**
  con `Retry-After` e **header di sicurezza** iframe-compatibili (CSP `frame-ancestors *`,
  `X-Content-Type-Options`, `Referrer-Policy`; **niente** `X-Frame-Options`) (v1.10.0).
- **Nuove funzioni dell'editor.** Nei sottotitoli: **trova/sostituisci**, **unisci/dividi** riga
  e **annulla/ripristina** (v1.13.0); **waveform** audio sulla timeline, client-side con fallback
  sicuro (v1.16.0). Fuori dall'editor: pagina **Statistiche** (`/statistiche`, tutta
  client-side) (v1.17.0) e **annullamento dei job** dalla coda, `POST /api/jobs/{id}/cancel`
  (v1.18.0).
- **Qualità, DevEx, deploy.** `.editorconfig`, `.gitattributes`, Prettier opzionale, task runner
  `scripts/tasks.ps1` e guida `docs/SVILUPPO.md` (v1.6.0); **code-splitting** delle route con
  `React.lazy` (v1.17.0); **test frontend** con Vitest (20 test) e **scaffolding i18n** pronto per
  l'inglese (v1.19.0); igiene del deploy con `scripts/deploy-sync.ps1` e **drift di versione HF
  risolto** (v1.20.0).
