# EditVideo — batch editor per video verticali 9:16

Piattaforma self-hosted per lavorare 30–40 video verticali al giorno: upload multiplo, tagli, sottotitoli automatici in italiano (faster-whisper, gratis, CPU-only), stili burn-in (incluso il karaoke a parola evidenziata), export MP4 1080×1920 pronto per TikTok/Reels/Shorts, tutto gestito da una coda di lavorazione con stati. I **Format** riutilizzabili automatizzano la catena: taglia-silenzi, sottotitoli, taglia-doppioni, zoom d'ingresso, fino all'auto-export.

Design completo in [`docs/PROGETTO.md`](docs/PROGETTO.md).

## Novità dell'interfaccia (v1.1 → v1.20)

Dopo l'MVP la piattaforma ha ricevuto 20 iterazioni di rifinitura (dettaglio in [`CHANGELOG.md`](CHANGELOG.md) e in [`docs/PROGETTO.md`](docs/PROGETTO.md) §10):

- **Interfaccia** — menu a tendina accessibili, micro-animazioni ctOS (dietro `prefers-reduced-motion`), pannello **Impostazioni** (accento, densità, movimento; salvati nel browser), onboarding al primo avvio con tooltip.
- **Produttività** — **command palette** (`Ctrl+K`), dashboard con ordinamento colonne, ricerca e selezione multipla, editor sottotitoli con **trova/sostituisci**, **unisci/dividi** riga e **annulla/ripristina**, **waveform** audio sulla timeline, pagina **Statistiche**, **annullamento** dei job dalla coda.
- **Accessibilità** — navigazione completa da tastiera, skip-link, ARIA, `focus-visible`, contrasto **AA**, pannello scorciatoie (`?`). Riferimento tasti: [`docs/SCORCIATOIE.md`](docs/SCORCIATOIE.md).
- **Mobile** — layout responsive rivisto (tabella→schede, editor touch, iframe-safe, nessun overflow fino a 360 px).
- **Robustezza e qualità** — validazione input, logging strutturato, indici DB, **rate limit** del login (`429`), header di sicurezza iframe-compatibili, test backend e frontend.

Per sviluppare in locale: [`docs/SVILUPPO.md`](docs/SVILUPPO.md). Prima di ogni deploy segui [`docs/REGOLE-ANTI-REGRESSIONE.md`](docs/REGOLE-ANTI-REGRESSIONE.md).

---

## Avvio rapido (Docker — consigliato, identico in locale e in cloud)

Prerequisito: Docker + Docker Compose.

```bash
cp .env.example .env        # imposta almeno ADMIN_PASSWORD
docker compose up -d --build
```

Apri **http://localhost:8000**, entra con la password. Fine.

Note:
- il primo job di trascrizione scarica il modello Whisper (~460 MB per `small`) e viene messo in cache in un volume: succede una volta sola;
- il frontend è già buildato in `frontend/dist`: non serve Node per usare l'app.

## Avvio in sviluppo (senza Docker)

Prerequisiti: Python 3.10–3.12, FFmpeg nel PATH (Windows: `winget install Gyan.FFmpeg`), Node 20+ solo se tocchi il frontend.

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows   (Linux/mac: source .venv/bin/activate)
pip install -r backend/requirements.txt
python run.py                     # API + worker embedded su http://localhost:8000 (SQLite)
```

Frontend in modalità dev (hot reload, opzionale):

```bash
cd frontend
npm install
npm run dev                       # http://localhost:5173 con proxy verso :8000
npm run build                     # rigenera frontend/dist servito dal backend
```

Test:

```bash
pip install pytest
pytest backend/tests -q
```

## Deploy attuale: Hugging Face Spaces

L'istanza in produzione gira su uno **Space privato** (`brushk/editvideo`, piano free: 2 vCPU / 16 GB RAM); i file dello Space sono in `deploy/hf-space/` (con il suo README dedicato). Caveat del piano free:

- **disco effimero**: un riavvio o rebuild cancella media e database → **scarica gli MP4 esportati in giornata**;
- **sleep dopo ~48h di inattività**: al primo accesso successivo lo Space riparte da solo (attesa di qualche minuto);
- imposta come secret dello Space `ADMIN_PASSWORD` e **`SECRET_KEY`**: con la chiave fissa le sessioni sopravvivono ai riavvii.

### Aggiornare lo Space (workflow)

I file in `deploy/hf-space/` sono una **copia** del codice canonico (`backend/app` + `frontend/dist`) e vanno riallineati prima di ogni redeploy, altrimenti vanno in drift (storicamente la versione nello Space restava indietro, `0.2.0` hardcoded in `main.py`). Procedura:

1. **Build del frontend** — solo se hai toccato `frontend/`:
   ```powershell
   .\scripts\tasks.ps1 -Task build      # rigenera frontend/dist
   ```
2. **Sync del deploy** — allinea `deploy/hf-space/` al codice canonico:
   ```powershell
   .\scripts\deploy-sync.ps1
   ```
   Copia in modo **idempotente** `backend/app/` (incluso `version.py`) e `frontend/dist/` dentro `deploy/hf-space/`, allinea `requirements.txt` e verifica che `app/main.py` prenda la versione da `version.py` (niente più `0.2.0` hardcoded). **Non** tocca i file specifici dello Space (`Dockerfile`, `README.md` con frontmatter HF, `.gitignore`) e **non** esegue git/commit/push né bump di versione.
3. **Pubblica lo Space** `brushk/editvideo` dai file in `deploy/hf-space/`: commit + push verso il remote git dello Space **oppure** upload manuale via UI di Hugging Face (*Files → Add file → Upload*).

Checklist completa pre/post deploy (punto di ripristino, test, verifica della versione nell'endpoint health): **[`docs/REGOLE-ANTI-REGRESSIONE.md`](docs/REGOLE-ANTI-REGRESSIONE.md)**.

Il login rilascia un **token** salvato in `localStorage` e inviato come `Authorization: Bearer` su ogni chiamata API e come `?t=` sulle risorse media (video, thumbnail, download): così l'app funziona anche dentro l'iframe di HF, dove i cookie di terze parti sono bloccati; il cookie firmato resta come fallback. Il progetto rimane comunque deployabile via Docker/VPS come descritto sotto.

## Deploy in cloud (~0 €)

Per un'installazione con disco persistente (senza i caveat di HF) il target consigliato è **Oracle Cloud Always Free** (VM ARM Ampere: fino a 4 vCPU / 24 GB RAM, gratuita per sempre — serve una carta in fase di registrazione). In alternativa qualsiasi VPS da ~5 €/mese (Hetzner, ecc.). La procedura è identica:

1. Crea la VM (Ubuntu 22.04/24.04, anche ARM: lo stack è compatibile) e apri le porte 80/443.
2. Installa Docker: `curl -fsSL https://get.docker.com | sh`
3. Copia il progetto sulla VM (git o scp), poi:
   ```bash
   cp .env.example .env   # ADMIN_PASSWORD forte + SECRET_KEY casuale
   docker compose up -d --build
   ```
4. HTTPS con Caddy (consigliato se hai un dominio):
   ```bash
   sudo apt install caddy
   # /etc/caddy/Caddyfile:
   #   video.tuodominio.it {
   #       reverse_proxy localhost:8000
   #   }
   sudo systemctl reload caddy
   ```
   Senza dominio puoi usare un tunnel (es. Tailscale/Cloudflare Tunnel) o esporre la porta 8000 limitandola al tuo IP.

## Configurazione (variabili `.env`)

| Variabile | Default | Descrizione |
|---|---|---|
| `ADMIN_PASSWORD` | `changeme` | password di accesso — **cambiala** |
| `APP_ENV` | `dev` | con `prod` l'avvio viene rifiutato se `ADMIN_PASSWORD` è ancora `changeme` |
| `SECRET_KEY` | autogenerata | firma di token e cookie di sessione; su HF Spaces impostala come secret per far sopravvivere le sessioni ai riavvii |
| `DATABASE_URL` | SQLite in `data/` | in compose: Postgres |
| `MEDIA_ROOT` | `./media` | originali, thumbnail, ass, export |
| `MAX_UPLOAD_MB` | `2048` | dimensione massima per singolo file caricato |
| `MAX_UPLOAD_FILES` | `10` | numero massimo di file per singola richiesta di upload |
| `MAX_REQUEST_MB` | `0` (auto) | tetto sull'intera richiesta di upload: oltre, rifiuto 413 sul `Content-Length` prima di leggere il body |
| `WHISPER_MODEL` | `small` | `base` = più veloce, `medium` = più accurato |
| `WHISPER_DEVICE` | `cpu` | CPU-only di default |
| `WHISPER_LANGUAGE` | `it` | vuoto = auto-detect |
| `SUB_MAX_CHARS` | `42` | lunghezza massima di una caption |
| `SUB_FONT` | Arial (Win) / DejaVu Sans (Linux) | font del burn-in |
| `SILENCE_MIN_DUR` | `0.4` | taglia-silenzi: durata minima (s) di una pausa perché venga tagliata |
| `SILENCE_LEAVE` | `0.24` | taglia-silenzi: respiro (s) lasciato al posto di ogni pausa |
| `SILENCE_NOISE_DB` | `-35` | taglia-silenzi: soglia (dB) sotto cui l'audio è silenzio |
| `RETAKE_MIN_MATCH` | `3` | taglia-doppioni: parole ripetute per riconoscere una ripartenza |
| `RETAKE_WINDOW` | `10` | taglia-doppioni: la ripresa deve iniziare entro N secondi |
| `RETAKE_MAX_CUT` | `20` | taglia-doppioni: durata massima (s) di un singolo taglio |
| `INTRO_ZOOM_AMOUNT` | `0.12` | zoom d'ingresso: entità del punch-in (12%) |
| `INTRO_ZOOM_DURATION` | `0.9` | zoom d'ingresso: durata (s) |
| `INTRO_SOUND` | whoosh incluso | zoom d'ingresso: percorso di un suono alternativo |
| `INTRO_SOUND_VOLUME` | `0.85` | zoom d'ingresso: volume del suono |
| `EXPORT_CRF` | `20` | qualità export (più basso = migliore) |
| `EMBEDDED_WORKER` | `1` | `0` quando il worker gira come processo separato (compose) |
| `WORKER_CONCURRENCY` | `1` | job in parallelo (lascia 1 su 2–4 vCPU) |
| `RETENTION_EXPORTS_DAYS` | `14` | export più vecchi di N giorni cancellati automaticamente (`0` = mai); gli originali non si toccano |
| `RETENTION_JOBS_DAYS` | `30` | job terminati eliminati dalla coda dopo N giorni (`0` = mai) |
| `RETENTION_SWEEP_SECONDS` | `3600` | intervallo (s) tra due passate di pulizia del worker |
| `JOB_MAX_RETRIES` | `2` | tentativi extra di un job fallito prima dell'errore (`0` = nessun retry) |
| `WHISPER_FALLBACK_MODEL` | `small` | modello più leggero usato se il principale non carica (vuoto = nessun fallback) |
| `EXPORT_ALLOW_WITHOUT_SUBS` | `1` | se la trascrizione fallisce, esporta comunque senza sottotitoli |
| `HEALTH_DEEP` | `1` | `/api/health` autenticato verifica anche db/disco/coda |
| `METRICS_ENABLED` | `1` | espone `/api/metrics` (`0` = 404) |
| `FEATURE_FLAGS` | vuoto | interruttori runtime `"nome=1,altro=0"` letti da `services/flags.py` |

Le altre variabili nuove di questa release (`JOB_RETRY_BACKOFF_SECONDS`,
`REQUEST_ID_HEADER`, `MEDIA_CACHE_MAX_AGE`, `PROBE_CACHE_SIZE`, `UPLOAD_RATE_MAX`,
`UPLOAD_RATE_WINDOW_SECONDS`) sono documentate con i default in
[`.env.example`](.env.example). I **feature flag** (`FEATURE_FLAGS`) sono il modo
consigliato per un rollout controllato/canary di funzionalità sensibili: vedi
[`docs/DEPLOY_STRATEGIES.md`](docs/DEPLOY_STRATEGIES.md).

## Flusso operativo

1. Seleziona il **Format** (schema riutilizzabile: trim iniziale, coda "secondi dalla fine", tagli, stile sottotitoli, automazioni) e fai **upload** di tutti i file del giorno; un format si applica anche in batch ai video già caricati.
2. Le automazioni del format lavorano da sole: **🔇 taglia-silenzi** → **⚡ sottotitoli** → **🔁 taglia-doppioni** (dai timestamp della trascrizione) → **🔍 zoom d'ingresso** → stato *da controllare*. Con **🚀 auto-export** la catena arriva direttamente a *esportato* senza toccare nulla.
3. Per ogni video: **Editor** → correggi i testi (✂ su una caption taglia dal video la parola sbagliata), ritocca i tagli, 🔇 taglia-silenzi on-demand → **Salva ed esporta** (download automatico) oppure **Segna pronto**. Le impostazioni correnti si riusano con **"Salva come format"**.
4. **Esporta tutti i pronti** → download degli MP4 finali → elimina i lavorati.

Stati: `caricato → in elaborazione → da controllare → pronto → in export → esportato` (+ `errore` con messaggio).

### Funzioni di editing automatico

- **🔇 Taglia silenzi** (editor o automatico via format): rileva le pause con `silencedetect` di FFmpeg (durata ≥ `SILENCE_MIN_DUR`, default 0,4s, sotto `SILENCE_NOISE_DB`, −35dB) e taglia il centro di ogni pausa lasciando `SILENCE_LEAVE` (0,24s) di respiro; l'aria morta a inizio/fine video viene tagliata a filo.
- **🔁 Taglia doppioni/ripartenze** (automatico via format): usa i timestamp a parola di Whisper; se almeno `RETAKE_MIN_MATCH` (3) parole si ripetono entro `RETAKE_WINDOW` (10s), il primo tentativo viene tagliato e si tiene l'ultima ripresa (taglio massimo `RETAKE_MAX_CUT`, 20s); le caption vengono rigenerate senza i doppioni.
- **🔍 Zoom d'ingresso con suono**: punch-in del 12% con curva morbida nei primi 0,9s + whoosh mixato sull'audio. Tarabile con `INTRO_ZOOM_AMOUNT`, `INTRO_ZOOM_DURATION`; suono sostituibile (`INTRO_SOUND`/`INTRO_SOUND_VOLUME` o rimpiazza `backend/app/assets/whoosh.wav`).
- **✂ sulla riga sottotitolo**: aggiunge un taglio esattamente sull'intervallo di quella caption — il modo più rapido per eliminare una parola sbagliata.

### Stili sottotitoli

5 preset: bianco/bordo nero, giallo/bordo nero, TikTok grande centrale, basso classico con box e **karaoke parola evidenziata** (la parola attiva si colora di giallo). Il karaoke usa i timestamp a parola, quindi richiede sottotitoli generati automaticamente: le caption modificate a mano perdono i tempi per parola e vengono rese come sottotitoli normali.

## Risoluzione problemi

- **"ffmpeg non trovato"** — installa FFmpeg e riavvia il terminale (`ffmpeg -version` deve rispondere). In Docker è già incluso.
- **Prima trascrizione lenta/bloccata su "in coda"** — sta scaricando il modello (una tantum, ~460 MB). Guarda i log: `docker compose logs -f worker`.
- **Trascrizione troppo lenta sul tuo hardware** — `WHISPER_MODEL=base` (qualità comunque buona in italiano) o riduci `EXPORT_CRF`.
- **Sottotitoli senza il font atteso** — imposta `SUB_FONT` con un font installato sul server; l'immagine Docker include DejaVu Sans.
- **Upload di file molto grandi rifiutato (413)** — il limite applicativo è `MAX_UPLOAD_MB` (default 2048); dietro reverse proxy alza anche `client_max_body_size`/equivalente (Caddy non ha limite di default).
- **Job rimasto "in elaborazione" dopo un riavvio** — al boot il recovery marca in errore (con messaggio esplicito) i job interrotti e ripulisce i file parziali: rilancia trascrizione o export dal video. Gli export FFmpeg hanno inoltre un watchdog che termina i processi bloccati.
- **Su Hugging Face gli export spariscono / lo Space non risponde** — è il piano free: disco effimero (scarica in giornata) e sleep dopo ~48h di inattività (riapri la pagina e attendi il riavvio).

## API (riassunto)

`POST /api/auth/login` (rilascia il token Bearer) · `POST /api/videos/upload` · `GET /api/videos?status=` · `PATCH /api/videos/{id}` (trim/cuts/stile/stato) · `POST /api/videos/{id}/autocut` (taglia-silenzi) · `POST /api/videos/{id}/transcribe` · `GET|PUT /api/videos/{id}/subtitles` · `POST /api/videos/{id}/export` · `GET /api/videos/{id}/export/download` · `GET|POST /api/templates` · `DELETE /api/templates/{id}` (Format) · `POST /api/videos/{id}/apply-template` · `POST /api/batch/apply-template` · `POST /api/batch/transcribe` · `POST /api/batch/export` · `GET /api/jobs?active=1` · `POST /api/jobs/{id}/cancel` (annulla job) · `GET /api/styles` · `GET /api/health` (autenticato: diagnostica profonda db/disco/coda) · `GET /api/metrics` (contatori job/video, autenticato)
