# SCALING_REPORT — Audit storage & scalabilità EditVideo

> Sessione di sola **analisi**: nessuna modifica al codice di produzione. Tutti i
> riferimenti sono nella forma `file:riga` sul codice verificato in questo branch.
> Contesto deploy primario: **Hugging Face Space free** (2 vCPU / 16 GB RAM, **disco
> effimero** — al riavvio/rebuild media e DB si azzerano, cfr. `README.md:65-68`).
> In secondo piano: Docker Compose (Postgres) e VPS/Oracle con disco persistente.

---

## 0. Sintesi esecutiva (TL;DR)

Il sistema è funzionalmente solido (worker con recovery, watchdog ffmpeg, export in
streaming, indici DB corretti) ma **non ha nessun meccanismo di ciclo di vita dei
dati**: media e righe `jobs` crescono **all'infinito** e l'unica pulizia è la DELETE
manuale del singolo video. Su disco persistente questo porta a **disco pieno in
giorni**; su HF il problema è mascherato solo perché il disco effimero si azzera ai
riavvii (che a loro volta cancellano il lavoro dell'utente).

**I 3 interventi da fare subito** (dettaglio in §5):

1. **GC automatico dei media + limite hard sul body dell'upload** (crescita illimitata + riempimento disco in import).
2. **`size_bytes` da `Integer` a `BigInteger`** (overflow certo su Postgres per file ≥ 2 GiB — è il default `MAX_UPLOAD_MB=2048`).
3. **Retention/pruning della tabella `jobs`** (cresce senza limite, mai potata).

---

## 1. FASE 1 — Dove vivono i dati oggi

### 1.1 Mappa dello storage (tabella)

Legenda "Chi pulisce": 🟥 = **nessuno** (crescita illimitata), 🟨 = parziale/manuale, 🟩 = automatico.

| Dato | Percorso / servizio | Formato | Chi scrive | Chi pulisce |
|---|---|---|---|---|
| Video originali | `media/originals/<id>.<ext>` (`config.py:119-120`) | mp4/mov/mkv/webm/avi (`videos.py:25`) | `upload()` `videos.py:76-84` | 🟨 solo `DELETE /api/videos/{id}` `videos.py:263-265` — **nessun TTL/GC** |
| Thumbnail | `media/thumbnails/<id>.jpg` (`config.py:122-124`) | JPEG 270×480 (`ffmpeg.py:61-68`) | `make_thumbnail` `videos.py:94-97` | 🟨 solo con la DELETE del video `videos.py:263-265` |
| Export finali | `media/exports/<id>.mp4` (`config.py:130-132`) | MP4 1080×1920 H264/AAC | `run_export` `worker.py:217,224` | 🟥 **nessuno** finché il video esiste (solo DELETE del video li rimuove; nessun "svuota esportati") |
| Sottotitoli `.ass` | `media/subs/<id>.ass` (`config.py:126-128`) | ASS temporaneo | `run_export` `worker.py:211-215` | 🟩 `unlink` nel `finally` `worker.py:230-232` (+ best-effort su DELETE `videos.py:266`) |
| DB applicativo | `data/app.db` (SQLite, `config.py:215-218`) o Postgres (compose) | tabelle `videos/jobs/templates/subtitle_segments` | tutta l'app | 🟨 cascade su delete video; **`jobs` mai potata** |
| Segmenti sottotitoli | tabella `subtitle_segments`, colonna `words` JSON (`models.py:124-144`) | JSON `[[start,end,"parola"],…]` per caption | `run_transcribe` `worker.py:150-152` | 🟩 cascade `ondelete=CASCADE` + `delete-orphan` (`models.py:93-96,135`) |
| Righe coda | tabella `jobs` (`models.py:147-168`) | 1 riga per transcribe/export | `enqueue_job`/batch (`videos.py:42-45`, `batch.py:34`) | 🟥 **nessuno** — cresce all'infinito (solo cascade se si elimina il video) |
| `secret.key` | `data/secret.key` (`config.py:230-239`) | hex 64 char | `resolved_secret()` una tantum | 🟩 stabile (1 file). Su HF effimero **si rigenera a ogni riavvio → invalida tutte le sessioni** |
| Cache modelli Whisper | `~/.cache/huggingface` (default lib); in compose volume `whisper_cache:/root/.cache` (`docker-compose.yml:44,52`) | pesi CTranslate2 | `faster_whisper.WhisperModel` `transcribe.py:31-38` | 🟩 volume persistente in compose; 🟥 **su HF si riscarica a ogni riavvio** (`medium` ≈ 1,5 GB, `small` ≈ 460 MB) |
| Frontend buildato | `frontend/dist/` (in git, `Dockerfile:16`) | JS/CSS statici (≈ 348 KB) | build Vite (committato) | 🟩 versionato |
| Temp multipart upload | tempdir di sistema (`$TMPDIR`/`/tmp`) | `SpooledTemporaryFile` Starlette | Starlette prima dell'endpoint (§2, punto 4) | 🟩 chiuso a fine request, ma **occupa disco durante l'upload** |

### 1.2 Stima di crescita

**Un video tipico** (verticale 9:16, 1–3 min, workflow README 30–40/giorno):

| Componente | Dimensione tipica | Note |
|---|---|---|
| Originale | ~150–300 MB | fino a **2 GiB** consentiti (`config.py:107`, `MAX_UPLOAD_MB=2048`) |
| Export MP4 | ~40–80 MB | crf20 veryfast (`config.py:62-65`) |
| Thumbnail | ~20–50 KB | trascurabile |
| DB (segmenti + words JSON + cuts) | ~100–500 KB | ~40–80 caption/2min, ognuna con `words` JSON |
| `.ass` | transitorio | ripulito nel `finally` |
| **Totale trattenuto / video** | **~250–350 MB** | **mai liberato** senza DELETE manuale |

- **40 video/giorno** ⇒ **~12–14 GB/giorno**.
- **100 video/giorno** ⇒ **~30–35 GB/giorno**.
- **Righe `jobs`**: ≥ 2/video (transcribe + export, workflow auto) ⇒ ~80–100 righe/giorno, **mai potate**. Righe piccole (~200 B) ma monotone: ~30k righe/anno a 40/giorno.

### 1.3 Limiti impliciti

- **Disco HF effimero ~50 GB**: a ~12 GB/giorno si riempie in **~4 giorni** di uso reale se lo Space non riavvia prima; nella pratica il riavvio (sleep dopo ~48h, `README.md:168`) azzera tutto **prima** che il disco si saturi — quindi il vero danno su HF è la **perdita dati**, non il disco pieno.
- **Disco persistente (Oracle/VPS 24–50 GB)**: nessun reset ⇒ **saturazione in giorni/settimane garantita** (nessun GC).
- **DB SQLite**: robusto ma monofile; con `words` JSON su migliaia di video e `jobs` mai potata cresce indefinitamente (WAL su disco effimero).
- **Nessun rate limit sulle risorse pesanti**: solo il login è limitato (`security.py:94-133`); upload/transcribe/export non hanno tetto di concorrenza oltre `worker_concurrency=1` (`config.py:111`).

---

## 2. FASE 2 — Punti di rottura (analisi dal codice)

### (A) Disco pieno a metà operazione
- **Import multipart**: FastAPI usa `UploadFile` di Starlette = `SpooledTemporaryFile` (spill su disco oltre ~1 MB). **L'intero body multipart viene già scritto su disco temporaneo PRIMA che l'endpoint giri**; il controllo `MAX_UPLOAD_MB` è applicato solo dopo, in lettura a chunk (`videos.py:74-84`). Non esiste middleware che rifiuti per `Content-Length`, né limite su `max_files`. **Manifestazione**: un body enorme (o molti file in un colpo) riempie il tempdir prima ancora del check; con disco pieno lo spool fallisce con `OSError: No space left`. In più, per ogni file si **scrive due volte** (spool temp + copia in `originals/`).
  - Cleanup del parziale: buono lato originals (`videos.py:111-114`: `rollback` + `dst.unlink`), ma non copre lo spool temp Starlette.
- **Render ffmpeg su `exports_dir`**: se il disco si riempie durante l'encoding, ffmpeg esce ≠ 0 e `run_export` fa `Path(dst).unlink(missing_ok=True)` (`worker.py:227-229`) → **nessun output parziale**, bene. Il video va in ERROR (`worker.py:250-264`).
- **Scrittura DB**: con SQLite su disco pieno, il commit fallisce (`disk I/O error`/`database or disk is full`); l'eccezione è catturata e il job va ERROR. Rischio residuo: WAL non checkpointabile.

### (B) File molto grandi (4K/8K, ore)
- **Whisper**: `faster-whisper` **decodifica l'intero audio in RAM** (array float32 16 kHz mono) prima della trascrizione (`transcribe.py:52-60`). ~230 MB/ora di audio; `medium` + `beam_size=5` (`config.py:41,45`) aggiunge memoria e **tempo CPU**: su 2 vCPU una clip lunga può durare svariati minuti/ore e, con più upload, saturare i 16 GB. **Manifestazione**: lentezza estrema, possibile OOM-kill del worker su clip di ore.
- **ffmpeg export**: è **streaming a passata singola** (`ffmpeg.py:186-195`, `-progress pipe:1`) → buono, memoria costante col crescere della durata. Watchdog `deadline=max(900, total*12)` uccide i processi impiantati (`ffmpeg.py:218-227,244-246`).
- **Rischio 4K/8K**: `scale/crop` a 1080×1920 (`ffmpeg.py:151-153`) è ok, ma il **decode** di sorgenti 4K/8K è CPU-bound su 2 vCPU → export molto lenti (rischio watchdog kill su video lunghi).

### (C) Progetti con molti clip/tagli/segmenti
- **Nessun tetto sul numero di `cuts`** (`schemas.py:79` — `list[CutRange]` senza `max_length`) né sui `segments` (`schemas.py:149`). L'auto-silenzi può generare **centinaia di tagli** (`silence.py`).
- **Export**: `build_export_cmd` costruisce **una catena `trim`+`concat` per ogni keep-segment** e le concatena in **un unico `-filter_complex`** passato come **singolo argomento CLI** (`ffmpeg.py:118-148,189`). Con molti tagli il filtergraph diventa enorme: rischio di superare il limite del **singolo argomento** (`MAX_ARG_STRLEN`, ~128 KB su Linux) → ffmpeg non parte, oppure filtergraph lentissimo/instabile. **Manifestazione**: export che fallisce con errore ffmpeg poco chiaro o si impianta (poi ucciso dal watchdog).
- **Timeline JSON**: `cuts`/`speedups` sono colonne JSON nella riga `videos` (`models.py:71-73`); molti tagli = riga grande riletta/riscritta a ogni PATCH.
- **N+1 / caricamento segmenti**: la relationship `segments` è `lazy="selectin"` (`models.py:93-96`). Le liste calde **sopprimono** correttamente il caricamento con `noload` (`videos.py:125`, `batch.py:25,63`) e il conteggio è un'unica aggregata (`videos.py:131-134`) → **niente N+1 nelle liste**. Ma `GET /api/videos/{id}` e l'export caricano **tutti** i segmenti con `words` (`worker.py:186-189`): su video molto lunghi il payload cresce.

### (D) Processo interrotto a metà scrittura
- **Recovery all'avvio del worker** (`worker.py:308-322`): job `running`→`error`, `canceling`→`canceled`, video `BUSY`→`error`. Buono: nessun loop di crash.
- **SQLite WAL** attivo (`db.py:24-26`) + `busy_timeout=30000` → resistente a interruzioni; export parziale rimosso (`worker.py:227-229`).
- **Rischio**: su HF il riavvio **cancella anche il DB e i media** (disco effimero) → la recovery gira su un DB vuoto; il lavoro è perso a prescindere (per design del piano free).

### (E) Dipendenze native
- `ffmpeg`/`ffprobe`: **non pinnate** (installate via `apt` in `Dockerfile:6`; su dev solo `shutil.which("ffmpeg")` in `run.py:15-19` e `/api/health` `jobs.py:96`). Se mancano/cambiano versione: probe/thumbnail/export falliscono con `FFmpegError`. `run.py` blocca l'avvio dev se manca ffmpeg, ma **non** controlla `ffprobe`.
- `faster-whisper>=1.0` pinnato al minor (`requirements.txt:8`); il resto con range larghi (`fastapi>=0.110,<1`, ecc.). **Nessun lock file Python** (no `requirements.lock`/hash) → build non riproducibili nel tempo.

### (F) Repo: asset binari pesanti versionati
- **Nessun file > 10 MB in git** (max: `frontend/dist/assets/index-*.js` ≈ 186 KB; `whoosh.wav` ≈ 104 KB). **Nessun rischio limite hard GitHub (100 MB).** Git LFS non configurato e **non serve oggi**.
- **`frontend/dist/` è committata di proposito** (`.gitignore:13-14`, `Dockerfile:16`): ok per far girare il server senza Node, ma i bundle buildati sporcano la history a ogni build.
- **Duplicazione integrale sotto `deploy/hf-space/`** (41 file: copia di `app/` e `frontend/dist/`, es. `deploy/hf-space/app/worker.py` = `backend/app/worker.py`). Non è un problema di **storage** (pochi KB) ma di **drift/manutenzione**: due sorgenti da tenere allineate a mano.

---

## 3. Punti di rottura ordinati per **probabilità × danno**

| # | Rottura | Prob. | Danno | Quando/Come si manifesta | Rif. |
|---|---|---|---|---|---|
| 1 | **Media senza GC/TTL** → disco pieno | 🔴 Alta | 🔴 Alto | Su VPS/Oracle: saturazione in giorni; nuovi upload/export falliscono (`No space left`), DB in errore | `videos.py:258-269`, `config.py:130-137` |
| 2 | **`size_bytes` int4 overflow su Postgres** | 🔴 Alta (con Postgres) | 🟠 Medio-alto | Upload di file ≥ 2 GiB (= default): `INSERT` fallisce `integer out of range`, video **non salvato** anche se il file è a metà su disco | `models.py:63`, `config.py:107` |
| 3 | **Upload: intero body su disco temp prima del limite** | 🟠 Media | 🔴 Alto | Body enorme/molti file: riempie il tempdir prima del check; `OSError`, doppia scrittura | `videos.py:74-84` |
| 4 | **Tabella `jobs` mai potata** | 🔴 Alta | 🟡 Medio | Crescita monotona; DB gonfio, listing/scan sempre più lenti nel tempo | `models.py:147-168`, tutta l'app |
| 5 | **Filtergraph ffmpeg illimitato con molti tagli** | 🟠 Media | 🟠 Medio-alto | Auto-silenzi su clip lunghe → centinaia di segmenti → `-filter_complex` oltre `MAX_ARG_STRLEN`: export fallisce/impianta | `ffmpeg.py:118-148,189`, `schemas.py:79` |
| 6 | **Whisper: audio intero in RAM + `medium`/beam5 su 2 vCPU** | 🟠 Media | 🟠 Medio | Clip di ore: lentezza estrema, possibile OOM del worker | `transcribe.py:52-60`, `config.py:41,45` |
| 7 | **Cache Whisper riscaricata su HF a ogni riavvio** | 🔴 Alta (su HF) | 🟡 Medio | Primo job dopo il riavvio scarica ~1,5 GB (`medium`) → latenza + banda | `transcribe.py:31-38` |
| 8 | **`secret.key` rigenerata su disco effimero** | 🔴 Alta (su HF) | 🟡 Medio | Ogni riavvio invalida tutte le sessioni (logout forzato) | `config.py:230-239` |
| 9 | **ffmpeg/ffprobe non pinnati, no lock Python** | 🟡 Bassa | 🟠 Medio | Cambio versione base image → comportamento ffmpeg diverso; build non riproducibili | `Dockerfile:6`, `requirements.txt` |
| 10 | **Duplicazione `deploy/hf-space/`** | 🟡 Bassa | 🟡 Basso | Drift silenzioso tra le due copie del codice | `deploy/hf-space/**` |

---

## 4. Mitigazioni: QUICK WIN vs STRUTTURALE

### #1 — Media senza GC (disco pieno)
- **QUICK WIN**: task periodico (thread nel worker o cron/`APScheduler`) che, a soglia disco o per età, cancella `exports/` più vecchi di N giorni e i media dei video in stato `EXPORTED` già scaricati; esporre un endpoint "svuota esportati" (oggi assente: `batch.py` non ha delete). Aggiungere `du`-based guard prima dell'export.
  - **FATTO:** `services/retention.py` (`gc_old_exports`): gli export più vecchi di `RETENTION_EXPORTS_DAYS` (default 14, `0` = mai) vengono cancellati da disco e il video torna `ready` (l'export è rigenerabile); file orfani in `exports/` inclusi, **originali mai toccati**. Sweep periodico nel worker (`run_retention_sweep`, ogni `RETENTION_SWEEP_SECONDS`, default 1h). Test: `tests/test_retention.py`.
- **STRUTTURALE**: politica di **retention/TTL** esplicita per stato (es. originali eliminati dopo export+download, export dopo N giorni) + **object storage** (S3/R2) con lifecycle rules, tenendo `config.py` come unico layer di percorsi (già predisposto, cfr. commento `config.py:3-4`). Upload **presigned** diretto al bucket (bypassa il disco dello Space).

### #2 — `size_bytes` overflow
- **QUICK WIN**: `size_bytes: Mapped[int] = mapped_column(BigInteger, ...)` (`models.py:63`) + `ALTER TABLE` additivo nella mini-migrazione (`db.py:41-69`). Su SQLite è no-op (INTEGER è a 64 bit); su Postgres risolve l'overflow.
  - **FATTO:** colonna `BigInteger` in `models.py` + `ALTER TABLE videos ALTER COLUMN size_bytes TYPE BIGINT` additivo in `db.py`.
- **STRUTTURALE**: introdurre **Alembic** (oggi assente: solo `create_all` + ALTER best-effort in `db.py`) per migrazioni versionate e tipizzate.

### #3 — Upload spola tutto su disco
- **QUICK WIN**: middleware ASGI che rifiuta con **413** se `Content-Length` > `MAX_UPLOAD_MB` **prima** di leggere il body; validare anche il numero di file.
  - **FATTO:** middleware `request_size_guard` in `main.py`: **413** su `Content-Length` oltre il tetto (`MAX_REQUEST_MB`, `0` = auto) **prima** che il body venga letto/spolato, **411** se il Content-Length manca (chunked) o non è valido; tetto `MAX_UPLOAD_FILES` (default 10) sul numero di file in `videos.py`. Test: `tests/test_upload_limits.py`.
- **STRUTTURALE**: **upload presigned** verso object storage con enforcement lato bucket (dimensione/tipo), streaming reale senza mai toccare il disco del backend.

### #4 — `jobs` mai potata
- **QUICK WIN**: nella recovery/loop del worker, `DELETE FROM jobs WHERE status IN ('done','error','canceled') AND finished_at < now()-interval` (retention es. 7–30 gg). L'indice `ix_jobs_status_created_at` (`models.py:153-155`) supporta la query.
  - **FATTO:** `services/retention.py` (`prune_finished_jobs`): i job terminati (done/error/canceled) con `finished_at` oltre `RETENTION_JOBS_DAYS` (default 30, `0` = mai) vengono eliminati; eseguito dallo stesso sweep periodico del worker. Test: `tests/test_retention.py`.
- **STRUTTURALE**: retention configurabile + eventuale partizionamento/archiviazione; separare la "history" dei job dalla coda attiva.

### #5 — Filtergraph illimitato
- **QUICK WIN**: cap sul numero di `cuts` accettati (`schemas.py:79`, es. `max_length`) e fusione dei tagli adiacenti (già presente `normalize_cuts`, ma senza tetto finale). Passare il filtergraph via `-filter_complex_script <file>` invece che come singolo argomento (aggira `MAX_ARG_STRLEN`).
  - **FATTO:** `export_video` scrive **sempre** il graph su file temporaneo e lo passa via `-filter_complex_script` (`ffmpeg.py`, con pulizia nel `finally` in ogni esito): il comando resta corto anche con centinaia/migliaia di segmenti. Il cap sui `cuts` è stato **deliberatamente scartato**: la pipeline (auto-silenzi + retakes) genera liste anche > 1000 tagli che la SPA rimanda intere in PATCH/"Salva come Format" — un `max_length` bloccherebbe salvataggio ed export di quei video (motivazione in `schemas.py`); con lo script il tetto non serve più. Test: `tests/test_filter_script.py`.
- **STRUTTURALE**: per progetti con moltissimi tagli, **render in due fasi** (segment+concat demuxer con file di lista) o pre-taglio in file intermedi, evitando un filtergraph monolitico.

### #6 — Whisper in RAM / lento
- **QUICK WIN**: default `WHISPER_MODEL=small` (compose già lo fa, `docker-compose.yml:24`, ma `config.py:41` default è `medium`) e `beam=1` per clip lunghe; limite di durata oltre cui declassare il modello.
- **STRUTTURALE**: trascrizione a **finestre/chunk** in streaming (non tutto l'audio in RAM), o offload a servizio GPU/API dedicato.

### #7/#8 — Cache Whisper & secret.key effimeri (HF)
- **QUICK WIN**: impostare `SECRET_KEY` via env (evita rigenerazione, `config.py:231`) e puntare la cache Whisper a un percorso persistente se disponibile; documentare "scarica in giornata" (già in README).
- **STRUTTURALE**: montare storage persistente (HF Persistent Storage a pagamento, o migrare a VPS) e pre-warmare la cache in build.

### #9 — Dipendenze native
- **QUICK WIN**: verificare anche `ffprobe` in `run.py`/health; pinnare la base image e la versione ffmpeg apt.
  - **FATTO (prima metà):** `run.py` ora blocca l'avvio dev anche se manca `ffprobe` e `/api/health` (autenticato) riporta la presenza di `ffprobe` oltre a `ffmpeg`. Il pinning di base image/ffmpeg resta da fare.
- **STRUTTURALE**: `requirements.lock` con hash (pip-tools/uv), immagine ffmpeg a versione fissa.

### #10 — Duplicazione deploy
- **QUICK WIN**: script che rigenera `deploy/hf-space/` da `backend/app` + `frontend/dist` (evita edit manuali su due copie).
- **STRUTTURALE**: build/CI che produce l'artefatto HF, togliendo la copia dalla history git.

---

## 5. I 3 interventi da fare SUBITO (e perché)

1. **GC/TTL automatico dei media + limite hard sul body upload** — *(rottura #1 + #3, prob.×danno più alto)*
   È l'unico problema che porta a **fermo totale** (disco pieno → ogni scrittura fallisce) su disco persistente, ed è oggi **completamente scoperto**: nessun TTL, nessun "svuota esportati", e l'upload può riempire il disco temp **prima ancora** del controllo di dimensione (`videos.py:74-84`). Quick win: job di pulizia per età/soglia + middleware 413 su `Content-Length`. Sblocca l'uso continuativo senza intervento manuale.
   **FATTO:** GC export in `services/retention.py` + sweep nel worker, e middleware 413/411 su `Content-Length` in `main.py` (dettagli in §4 #1 e #3).

2. **`size_bytes` → `BigInteger`** — *(rottura #2)*
   Bug **latente ma deterministico**: con Postgres (compose/VPS) un file ≥ 2 GiB — cioè **il valore di default `MAX_UPLOAD_MB=2048`** — fa fallire l'`INSERT` per overflow int4 (`models.py:63`, `config.py:107`). Fix di poche righe (colonna + ALTER additivo in `db.py`), rischio zero su SQLite, elimina una corruzione/errore silenzioso al primo file grande.
   **FATTO:** `BigInteger` in `models.py` + ALTER additivo in `db.py` (dettagli in §4 #2).

3. **Pruning della tabella `jobs`** — *(rottura #4)*
   La coda è anche lo storico e **non viene mai potata**: crescita monotona che gonfia il DB e degrada listing/scan nel tempo. Una `DELETE` retention-based nel worker (job terminati oltre N giorni), supportata dall'indice esistente `ix_jobs_status_created_at`, è a basso rischio e mette un tetto strutturale alla crescita del DB.
   **FATTO:** `prune_finished_jobs` in `services/retention.py`, eseguita dallo sweep periodico del worker (dettagli in §4 #4).

> Nota: gli interventi #7/#8 (cache Whisper e `secret.key` effimeri su HF) hanno **alta
> probabilità** ma danno limitato/atteso per il piano free; vanno chiusi impostando
> `SECRET_KEY` e valutando storage persistente, ma dopo i tre sopra.

---

*Fine report — analisi statica del codice al branch `audit/scaling-report`. Nessuna
modifica al codice di produzione è stata effettuata.*
