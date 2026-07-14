# Changelog

Tutte le modifiche rilevanti a EditVideo sono annotate in questo file.

Il formato segue [Keep a Changelog](https://keepachangelog.com/it/1.0.0/) e il
progetto aderisce al [Versionamento Semantico](https://semver.org/lang/it/)
(semver: `MAJOR.MINOR.PATCH`).

La versione è **unica** per backend e frontend (`backend/app/version.py` e
`frontend/src/version.ts`) e si aggiorna **solo** tramite
`scripts/bump-version.ps1`. Vedi `docs/REGOLE-ANTI-REGRESSIONE.md`.

---

## [Unreleased]

_Nessuna modifica non ancora rilasciata._

<!-- Aggiungere qui le prossime voci sotto Added / Changed / Fixed / Security.
     Al momento del rilascio: bump con scripts/bump-version.ps1 e spostare
     le voci in una nuova sezione [X.Y.Z] - AAAA-MM-GG. -->

---

## [1.46.0] - 2026-07-10

Passata di ingegneria trasversale: resilienza, osservabilità, performance,
sicurezza generica, CI e feature flag. Nessuna nuova dipendenza runtime; tutto
resta single-node e disattivabile via env. Decisioni motivate (cosa è stato
applicato e cosa RIFIUTATO, con i mental model) in [`docs/DECISIONS.md`](docs/DECISIONS.md);
modello di minaccia in [`docs/THREAT_MODEL.md`](docs/THREAT_MODEL.md); strategie di
rilascio in [`docs/DEPLOY_STRATEGIES.md`](docs/DEPLOY_STRATEGIES.md).

### Added
- **Resilienza dei job** (`services/resilience.py`): `retry_call` (retry con
  backoff **esponenziale**, `retry_on` selettivo, `sleeper` iniettabile per i
  test) e `CircuitBreaker` (soglia di fallimenti consecutivi + cooldown,
  `clock` iniettabile). Nel worker la trascrizione Whisper ora **degrada con
  grazia**: retry del modello principale, poi **fallback** al modello più
  leggero (`WHISPER_FALLBACK_MODEL`) protetto da un circuit breaker che, dopo
  fallimenti ripetuti del principale, salta subito al fallback; se anche questo
  fallisce e `EXPORT_ALLOW_WITHOUT_SUBS` è attivo l'export procede **senza
  sottotitoli** invece di finire in errore (meglio un export utile che nessuno).
  Nuove env: `JOB_MAX_RETRIES`, `JOB_RETRY_BACKOFF_SECONDS`,
  `WHISPER_FALLBACK_MODEL`, `EXPORT_ALLOW_WITHOUT_SUBS`.
- **Observability** (`services/metrics.py`, middleware in `main.py`):
  - **Request ID** di correlazione su ogni risposta (header `REQUEST_ID_HEADER`,
    default `X-Request-ID`): se il client ne fornisce uno lo si rispetta, altrimenti
    è generato; entra nel contesto di logging strutturato (riuso del meccanismo a
    contextvars già usato per job/video) così ogni riga è tracciabile.
  - **`/api/health` profondo** (autenticato, `HEALTH_DEEP` on di default): oltre a
    versione/binari verifica **DB (SELECT 1)**, **spazio disco** su MEDIA_ROOT/DATA_DIR
    e **coda** (job queued/running). Senza token resta il minimale `{"ok": true}`.
  - **`/api/metrics`** (autenticato, `METRICS_ENABLED` on di default, 404 se spento):
    contatori job/video per stato + durata media dei job completati, aggregati con
    query SQLAlchemy (nessuna dipendenza Prometheus).
- **Feature flags** (`services/flags.py`): un unico punto per accendere/spegnere
  funzionalità a runtime tramite la stringa `FEATURE_FLAGS` (`"nome=1,altro=0"`).
  `parse_flags` è una funzione pura testabile; default sicuro (flag sconosciuto =
  spento). Abilitano un canary/rollout controllato (vedi `docs/DEPLOY_STRATEGIES.md`).
- **Chaos tests** (`tests/test_chaos.py`) e **test di osservabilità**
  (`tests/test_observability.py`): fault injection deterministica (ffmpeg/ffprobe
  che fallisce o va in timeout, disco pieno, DB locked, input troncato, whisper
  che solleva) con assert sul **degrado con grazia** (errore tipizzato, nessun
  output parziale su disco, job in ERROR, worker vivo). Nuovi test frontend
  (`App`, `CommandPalette`, `Dashboard`, `Editor`).

### Changed
- **Performance / cache**:
  - **`ffprobe` memoizzato** (`services/ffmpeg.py`): cache **LRU** per path
    (chiave path+mtime+size, `PROBE_CACHE_SIZE`) — un file già analizzato non
    viene riletto finché non cambia.
  - **Media con `Cache-Control` + `ETag` debole** (`routers/videos.py`): max-age
    `MEDIA_CACHE_MAX_AGE`, `immutable` per gli export (nome derivato dall'id) e
    **304 Not Modified** su `If-None-Match` (niente ri-trasferimento del file).
- **CI**: nuovo/rafforzato workflow di **continuous integration**
  (`.github/workflows/ci.yml`) — typecheck + **ESLint bloccante** (`--max-warnings 0`)
  + vitest + build per il frontend; **ruff** (advisory, `continue-on-error`) +
  suite backend veloce; smoke E2E in job isolato (per via di `get_settings()`
  `@lru_cache`). Il deploy sullo Space resta un workflow a parte.
- **Login "mini-terminale"** (`App.tsx`): schermata di accesso in stile terminale
  con cursore e **tagline** che spiega cos'è l'app a chi arriva sullo Space;
  errori di rete mostrati con messaggio italiano generico ("Server non
  raggiungibile") invece della stringa tecnica inglese; stringhe passate da i18n.
- **Font unico** (`styles.css`): un solo font monospace di sistema (`--mono`) per
  tutta la UI (terminale coerente); i controlli form ereditano lo stesso font.

### Security
- **Rate limit generico sugli enqueue di massa** (`routers/batch.py`): freno
  **globale** (chiave costante, non per-IP) sulle rotte `/api/batch` — riusa il
  rate limiter delle scritture con soglie `UPLOAD_RATE_*`, risponde **429** con
  `Retry-After` e **non** è aggirabile ruotando `X-Forwarded-For` (spoofabile
  dietro il proxy HF). Nuove env: `UPLOAD_RATE_MAX`, `UPLOAD_RATE_WINDOW_SECONDS`.
- **Security header** su tutte le risposte (compresi i 411/413 anticipati),
  applicati dal middleware più esterno; CSP `frame-ancestors *` mantenuta come
  trade-off consapevole per l'iframe HF (vedi threat model).
- **Threat model** documentato ([`docs/THREAT_MODEL.md`](docs/THREAT_MODEL.md)):
  attori, asset (media/DB/`secret.key`/token), superfici (upload/login/health/iframe)
  e mitigazioni presenti + residui accettati.

---

## [1.45.0] - 2026-07-10

Ciclo di vita dei dati (retention/GC) e hardening di upload, export e avvio.

### Added
- **Retention/GC automatica** nel worker (`backend/app/services/retention.py`): gli export
  più vecchi di `RETENTION_EXPORTS_DAYS` (default 14 giorni, `0` = mai) vengono cancellati
  da disco e il video torna *pronto* (l'export è rigenerabile; gli **originali non si
  toccano mai**); i job terminati oltre `RETENTION_JOBS_DAYS` (default 30) vengono eliminati
  dalla coda. Passata periodica ogni `RETENTION_SWEEP_SECONDS` (default 1 ora).
- Nuove variabili di configurazione: `APP_ENV`, `RETENTION_EXPORTS_DAYS`,
  `RETENTION_JOBS_DAYS`, `RETENTION_SWEEP_SECONDS`, `MAX_UPLOAD_FILES`, `MAX_REQUEST_MB`
  (documentate in `.env.example` e nel README).

### Changed
- **Export**: il filtergraph FFmpeg viene passato via `-filter_complex_script` (file
  temporaneo, ripulito in ogni esito) invece che come singolo argomento: con centinaia di
  tagli (auto-silenzi su clip lunghe) il comando resta corto e non sfora `MAX_ARG_STRLEN`.
- **`/api/health` minimale senza auth**: senza token risponde solo `{"ok": true}`
  (uptime-check senza fingerprinting); il payload diagnostico completo — versione, modello
  whisper, presenza di ffmpeg **e ffprobe** — richiede un token valido.
- `run.py` verifica all'avvio anche `ffprobe` nel PATH (oltre a `ffmpeg`).
- `size_bytes` da `Integer` a **`BigInteger`** (con `ALTER` additivo nella mini-migrazione):
  niente overflow su Postgres per file ≥ 2 GiB.

### Security
- **Upload safety**: le richieste fuori misura vengono rifiutate con **413** sul
  `Content-Length` dichiarato **prima** che il body venga letto/spolato su disco (**411**
  se assente o non valido); tetto `MAX_UPLOAD_FILES` sul numero di file per richiesta e
  tetto separato sul body delle route non di upload.
- **`secret.key` hardening**: creazione con permessi **0600**, scrittura **atomica** e
  sicura in concorrenza (web/worker), file vuoto **rigenerato** invece di firmare con
  chiave vuota, chiave tenuta in **cache** in memoria (niente lettura da disco a ogni
  richiesta).
- **Avvio rifiutato** se `ADMIN_PASSWORD` è ancora `changeme` con `APP_ENV=prod`
  (in dev resta il warning); credenziali Postgres del compose parametrizzate via env.
- **Dipendenze frontend (dev) aggiornate**: vite 8, vitest 4, @vitejs/plugin-react 6 —
  `npm audit` senza vulnerabilità.

---

## [1.20.0] - 2026-07-08

Igiene del deploy: sincronizzazione ripetibile dello Space e drift di versione risolto.

### Added
- `scripts/deploy-sync.ps1`: allinea in modo **idempotente** `deploy/hf-space/` al
  codice canonico (`backend/app/` + `frontend/dist/`, incluso `version.py`), senza
  toccare i file specifici dello Space (`Dockerfile`, `README.md`, `.gitignore`) e
  senza eseguire git/commit/push né bump di versione.

### Fixed
- Risolto il **drift storico della versione** su Hugging Face: `deploy/hf-space/app/main.py`
  non resta più a `0.2.0` hardcoded ma riflette la versione reale, così l'endpoint health
  dello Space riporta la versione attesa.

---

## [1.19.0] - 2026-07-08

Test del frontend e basi per la localizzazione.

### Added
- Suite di test **frontend con Vitest** (20 test) su funzioni di formato, i18n e componenti
  (StatusBadge, Menu, Skeleton).
- **Scaffolding i18n** (`frontend/src/i18n.ts`): dizionario tipizzato con lingua unica `it`
  e struttura pronta ad accogliere l'inglese (`en`) senza toccare i componenti.

---

## [1.18.0] - 2026-07-08

Gestione della coda: i lavori si possono annullare.

### Added
- **Annullamento dei job** dalla barra dei lavori: `POST /api/jobs/{id}/cancel`. Un job in
  coda viene rimosso (dequeue logico, `queued → canceled`); uno in esecuzione riceve la
  richiesta (`running → canceling`) e il worker lo chiude in modo pulito al confine tra le
  fasi. L'operazione è idempotente sui job già terminati.

---

## [1.17.0] - 2026-07-08

Pagina Statistiche e bundle più leggero.

### Added
- Pagina **Statistiche** (`/statistiche`): totali, distribuzione per stato, durata
  totale/media, sottotitoli, produzione giornaliera (ultimi 14 giorni) e stima del tempo
  risparmiato. Tutto **client-side**, sulla stessa fonte della dashboard, senza nuovi endpoint.

### Changed
- **Code-splitting** delle route pesanti (Dashboard, Editor, Statistiche) con `React.lazy` +
  `Suspense`: il bundle iniziale (login + shell) resta leggero, ogni pagina finisce in un
  chunk separato caricato on-demand.

---

## [1.16.0] - 2026-07-08

Forma d'onda dell'audio nell'editor.

### Added
- **Waveform audio** sulla timeline dell'editor, disegnata client-side, con **fallback
  sicuro** quando l'audio non è presente o non è decodificabile.

---

## [1.15.0] - 2026-07-08

Primo avvio guidato.

### Added
- **Onboarding** al primo avvio (persistito in `localStorage`), riapribile on-demand dal
  pulsante "❔ Guida".
- **Tooltip** descrittivi sui controlli principali di dashboard ed editor.

---

## [1.14.0] - 2026-07-08

Personalizzazione del tema e resilienza dell'interfaccia.

### Added
- Pannello **Impostazioni** (ingranaggio ⚙ nella topbar): **accento** (teal/viola/ambra/rosso),
  **densità** (comodo/compatto) e **movimento** (auto/ridotto), applicati dal vivo e persistiti
  in `localStorage`.
- **Error boundary** attorno alle route: un errore di rendering mostra una schermata di
  recupero invece di una pagina bianca.

---

## [1.13.0] - 2026-07-08

Editor dei sottotitoli avanzato.

### Added
- **Trova e sostituisci** nel testo dei sottotitoli (navigazione tra le occorrenze, opzione
  maiuscole/minuscole, sostituisci singolo o tutti).
- **Unisci** righe adiacenti e **dividi** una riga (al cursore nel testo, o a metà) con tempi
  coerenti.
- **Annulla / Ripristina** (undo/redo) locale sulle modifiche ai sottotitoli, con accorpamento
  delle digitazioni ravvicinate in un unico passo (`Ctrl+Z`, `Ctrl+Shift+Z`, `Ctrl+Y`).

---

## [1.12.0] - 2026-07-08

Dashboard: ordina, cerca, seleziona.

### Added
- **Ordinamento** della tabella per colonna (nome, durata, stato, data) con intestazioni
  attivabili da tastiera e `aria-sort`.
- **Ricerca per nome** e **selezione multipla** con azioni batch sui video scelti (sottotitoli,
  export, elimina), inclusa la selezione dei soli video visibili.

### Changed
- I filtri di stato mostrano il conteggio per categoria.

---

## [1.11.0] - 2026-07-08

Palette dei comandi.

### Added
- **Command palette** stile ctOS, apribile con **`Ctrl+K` / `Cmd+K`**: ricerca fuzzy,
  navigazione da tastiera (`↑`/`↓`, `Home`/`End`, `Invio`) e comandi di navigazione e azioni
  globali (ricarica dati, copia link, apri scorciatoie, esci). Accessibile (pattern
  combobox+listbox, focus trap, focus ripristinato alla chiusura).

---

## [1.10.0] - 2026-07-08

Irrobustimento della sicurezza (compatibile con l'iframe HF).

### Security
- **Rate limit del login** per IP (finestra scorrevole): oltre la soglia di tentativi
  **falliti** l'endpoint risponde **429** con `Retry-After`; un login riuscito azzera il
  conteggio.
- **Header di sicurezza** iframe-compatibili: `X-Content-Type-Options: nosniff`,
  `Referrer-Policy`, CSP con `frame-ancestors *` e `object-src 'none'` — **senza**
  `X-Frame-Options`, che romperebbe l'embedding su Hugging Face.

---

## [1.9.0] - 2026-07-08

Revisione mobile e responsive completa.

### Changed
- Breakpoint 980 / 720 / 640 / 480 px, **topbar compatta**, editor usabile al **tocco**,
  nessun **overflow orizzontale** fino a 360 px, layout **iframe-safe**. Consolidata la resa a
  schede della dashboard su schermo stretto.

---

## [1.8.0] - 2026-07-08

Performance del backend.

### Changed
- **Indici di database** su `videos(status)`, `videos(created_at)`,
  `subtitle_segments(video_id, idx)`, `jobs(status, created_at)` e sulle foreign key.

### Fixed
- Eliminato un problema **N+1** nel caricamento delle liste (conteggio sottotitoli / job
  associati): meno query per richiesta.

---

## [1.7.0] - 2026-07-08

Accessibilità e uso da tastiera.

### Added
- **Skip-link** "Salta al contenuto", landmark e struttura semantica, `focus-visible`
  coerente, attributi **ARIA** sui componenti interattivi.
- **Timeline pilotabile da tastiera** e **pannello scorciatoie** richiamabile con **`?`**.

### Changed
- **Contrasto** colore portato al livello **AA**; al cambio di route il focus si sposta sul
  contenuto principale.

---

## [1.6.0] - 2026-07-08

Developer experience.

### Added
- `.editorconfig`, `.gitattributes` e configurazione **Prettier opzionale** (`.prettierrc.json`
  + `.prettierignore`, fuori da build e `package.json`).
- Task runner **`scripts/tasks.ps1`** (`install`/`dev`/`build`/`test`/`verify`/`bump`) e guida
  **`docs/SVILUPPO.md`**.

---

## [1.5.0] - 2026-07-08

Robustezza del worker e logging strutturato.

### Added
- **Logging strutturato** con `contextvars` (contesto `job_id` / `video_id`), corretto anche
  con più worker.
- `validate_runtime()` all'avvio: avvisi chiari su configurazione e ambiente.

### Changed
- Maggiore robustezza del worker nella gestione degli errori e del ciclo di vita dei job.

---

## [1.4.0] - 2026-07-08

Feedback dell'interfaccia.

### Added
- Sistema di **notifiche toast**, **skeleton** di caricamento e stati **vuoto/errore** con
  azione di **riprova**.

---

## [1.3.0] - 2026-07-08

Test e validazione degli input.

### Added
- Suite di **test backend** estesa (81 test).

### Changed
- **Validazione input** con Pydantic sui contratti API, **retro-compatibile**.

---

## [1.2.0] - 2026-07-08

Micro-animazioni dell'interfaccia.

### Added
- Micro-animazioni fluide stile **Watch Dogs / ctOS** (scanline, sweep, glow di stato, flusso
  del progresso), tutte **dietro `prefers-reduced-motion`**.

---

## [1.1.0] - 2026-07-08

Riorganizzazione della GUI.

### Added
- Componente **Menu** a tendina accessibile e layout riorganizzato con più respiro.
- Base **mobile**: su schermo stretto la tabella della dashboard diventa a schede.

### Changed
- Riorganizzazione generale della disposizione dei controlli.

---

## [1.0.0] - 2026-07-08

Prima versione stabile, messa sotto git (commit iniziale, tag `v1.0.0`, working
tree pulito). Consolida le quattro ondate di sviluppo (v1 MVP, v2 Format, v3
editing automatico, v4 karaoke/auto-export/hardening/deploy) in un unico
rilascio.

### Added

**Base / MVP (steps 0–7)**
- Upload multiplo di video con lettura metadata (nome, durata, formato, peso,
  data via `ffprobe`) e generazione thumbnail.
- Dashboard a stati (`caricato · in elaborazione · da controllare · pronto · in
  export · esportato · errore`) con filtro per stato, azioni per riga e azioni
  batch.
- Coda di lavorazione su tabella `jobs` nel DB con worker in polling, progresso
  live e gestione errori per singolo video.
- Editor: preview HTML5, timeline visuale con maniglie di trim draggabili e
  seek, tagli interni multipli (segna A / segna B), salvataggio modifiche.
- Sottotitoli automatici in italiano con faster-whisper (CPU, VAD, timestamp a
  parola, chunking in caption leggibili) ed editing manuale completo di testo e
  tempi. La trascrizione lavora sulla timeline originale; i tempi vengono
  rimappati sui tagli all'export.
- 4 preset di stile sottotitoli (bianco/bordo nero, giallo/bordo nero, grande
  centrale stile TikTok, basso classico con box) impressi via ASS + libass.
- Export MP4 1080×1920 H.264 + AAC con tagli applicati e sottotitoli impressi,
  `faststart`, download dal browser.
- Auth a password singola, Docker Compose (db + web + worker), test end-to-end
  della pipeline.

**Format e salva-ed-esporta (v2)**
- **Format**: template riutilizzabili (trim iniziale, coda "secondi dalla
  fine", tagli, stile sottotitoli e automazioni) applicabili all'upload oppure
  in batch ai video già caricati. Nuova tabella `templates`.
- **"Salva ed esporta"** dall'editor, con download automatico a fine job.
- **"Salva come format"** dall'editor, per capitalizzare una configurazione.

**Editing automatico (v3)**
- **Taglia-silenzi**: rilevamento pause con `silencedetect` (pause ≥
  `SILENCE_MIN_DUR` a `SILENCE_NOISE_DB`, taglio al centro della pausa con
  respiro `SILENCE_LEAVE`, bordi a filo) → `cuts` automatici; disponibile anche
  on-demand dall'editor.
- **Taglia-doppioni / ripartenze**: dai word-timestamp di Whisper
  (`RETAKE_MIN_MATCH` / `RETAKE_WINDOW` / `RETAKE_MAX_CUT`); si tiene l'ultima
  ripresa e le caption vengono rigenerate senza i doppioni.
- **Zoom d'ingresso con whoosh** (`INTRO_ZOOM_AMOUNT` / `INTRO_ZOOM_DURATION`,
  suono sostituibile).
- Taglio della **parola sbagliata** direttamente dalla riga sottotitolo (rimuove
  dal video l'intervallo corrispondente).

**Karaoke e auto-export (v4)**
- Stile **karaoke a parola evidenziata** (parola attiva in giallo). Richiede
  caption generate automaticamente: le caption modificate a mano perdono i tempi
  per-parola e tornano allo stile normale.
- **Auto-export** (🚀) nei Format: porta i video fino allo stato `esportato`
  senza intervento.

**Deploy**
- Pubblicazione su **Hugging Face Spaces** (Space privato `brushk/editvideo`,
  piano free 2 vCPU / 16 GB). File dello Space in `deploy/hf-space/`.
- Script `scripts/bump-version.ps1` per il bump della versione unica
  (`-Version X.Y.Z` oppure `-Part major|minor|patch`, con i flag `-Commit` e
  `-Tag`).

### Changed
- **Autenticazione a token**: dalla sola password/cookie si passa a token HMAC
  firmato inviato come `Authorization: Bearer` sulle API e come `?t=` sulle
  risorse media, con il cookie HttpOnly come fallback. Così l'app funziona anche
  nell'iframe di Hugging Face, dove i cookie di terze parti sono bloccati.
- La versione applicativa è centralizzata in `version.py` / `version.ts` ed è
  esposta nella UI (`frontend/src/App.tsx`), nell'endpoint health
  (`backend/app/routers/jobs.py`) e come versione FastAPI
  (`backend/app/main.py`).

### Fixed
- **Watchdog** sugli export FFmpeg: gli export bloccati non restano appesi.
- **Recovery al riavvio**: i job rimasti in stato `running` dopo un crash
  vengono chiusi con errore esplicito invece di restare zombie.
- **Pulizia** dei file `.ass` temporanei e degli output parziali dopo un errore.
- `WORKER_CONCURRENCY` viene ora rispettato dal worker.

### Security
- Token di sessione firmato HMAC (vedi *Changed*).
- Limite `MAX_UPLOAD_MB` sulla dimensione degli upload.
- Protezione **anti path-traversal** sui file statici / media serviti.

---

[Unreleased]: nessuna voce; confronto disponibile una volta configurato il remote git.
[1.1.0 → 1.20.0]: release consegnate il 2026-07-08 come iterazioni di upgrade dopo l'MVP,
ciascuna con bump della versione unica via `scripts/bump-version.ps1`. Il repo git è locale
(nessun remote configurato): i confronti diff saranno disponibili una volta pubblicato il remote.
[1.0.0]: corrisponde al tag git `v1.0.0` (nessun remote ancora configurato).
