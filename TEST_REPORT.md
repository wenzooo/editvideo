# TEST_REPORT — baseline QA di EditVideo

**Sessione:** prima ricognizione + test di baseline (branch `test/qa-baseline`).
**Obiettivo:** fotografare lo stato reale del progetto con test completi e mirati, **senza cambiare il comportamento del codice di produzione**. I test che rivelano un bug **restano rossi** e il bug è documentato qui: sono i guardrail per la sessione di correzione.

**Data:** 2026-07-09 · **Commit di partenza:** `5073955` (v1.43.0)

---

## 1. Mappa del progetto

### Stack
- **Backend:** Python 3.11 · FastAPI · SQLAlchemy 2 · Pydantic 2 · Uvicorn. DB **SQLite** di default (`data/`), **Postgres** in docker-compose. Media processing via **ffmpeg/ffprobe** (subprocess, arg-list, no shell). Trascrizione via **faster-whisper** (CPU). Dipendenze in `backend/requirements.txt` (versioni a range, non pinnate).
- **Frontend:** React 18 · Vite 5 · TypeScript 5 · React-Router 6. Test con **Vitest 2** + Testing-Library + jsdom. Buildato in `frontend/dist/` (versionato: il backend serve la SPA senza Node).
- **Deploy:** Hugging Face Space privato (`brushk/editvideo`, disco **effimero**) via `.github/workflows/deploy.yml`; anche Docker/VPS. Auth a password unica con token HMAC.

### Struttura e chi chiama chi
```
frontend/src (SPA)  ──HTTP──▶  backend/app/main.py (FastAPI + SPA statica + security headers)
                                   │
        ┌──────────────────────────┼───────────────────────────────┐
     routers/                   auth.py + security.py            db.py + models.py
     ├ videos.py  (upload, patch trim/cuts, autocut, retakes,   (token HMAC,          (SQLite/PG,
     │            delete, file/thumb/export download, azioni)    rate-limit login)     Video/Job/
     ├ jobs.py    (lista, cancel, styles, health)                                      Template/
     ├ subtitles.py (GET/PUT, preserva word-timestamp karaoke)                         SubtitleSegment)
     ├ batch.py   (auto, apply-template, transcribe, export-reviewed)
     └ templates.py (CRUD Format)
                                   │  crea Job in tabella `jobs`
                                   ▼
                      worker.py  (claim ottimistico UPDATE...WHERE status='queued';
                                  embedded o processo dedicato; recovery all'avvio)
                                   │  handler:
              run_transcribe ──────┤──────── run_export
                 │                              │
       services/silence.py (silencedetect)   services/ffmpeg.py (1 comando: trim/concat keep,
       services/transcribe.py (whisper)        scale/crop 9:16, zoompan, burn-in .ass, x264+aac)
       services/retakes.py (doppioni)         services/styles.py (preset → .ass, karaoke per-parola)
       services/captions.py (chunking)        services/timeline.py (FUNZIONI PURE: cuts→keeps→plan,
       services/formats.py (apply Format)                          remap sottotitoli, speedup)
```

**Funzioni pure (cuore testabile senza I/O):** `timeline.py`, `captions.py`, `retakes.py`, gran parte di `styles.py` e `ffmpeg.build_export_cmd`/`_atempo_chain`.

### Flussi critici individuati (verificati sul codice)
1. **Import/upload media** — `POST /api/videos/upload`: estensioni ammesse, probe metadati, thumbnail, limite dimensione, nomi con spazi/unicode.
2. **Trascrizione + automazioni** — worker `run_transcribe`: silenzi → whisper → doppioni → caption; auto-export opzionale.
3. **Operazioni timeline** — trim/cuts/speedup: `timeline.keep_intervals`/`apply_speedups`, PATCH `/videos/{id}`.
4. **Sottotitoli & karaoke** — chunking (`captions`), preservazione word-timestamp (`subtitles PUT`), rendering `.ass` (`styles`).
5. **Export/render** — `run_export` → `ffmpeg.build_export_cmd`/`export_video` (progress, watchdog, cleanup parziali).
6. **Coda job & cancel** — claim ottimistico, `cancel_job`, recovery al riavvio.
7. **Auth** — login/token/rate-limit, `require_auth`, servizio media protetto.
8. **Gestione errori** — file mancanti/corrotti, formati non supportati, job interrotti.

---

## 2. Stato di build / lint / typecheck / suite esistente

| Verifica | Comando | Esito |
|---|---|---|
| Typecheck FE | `npx tsc --noEmit` | ✅ exit 0 |
| Test FE (pre-esistenti) | `npx vitest run` | ✅ **20/20** |
| Build FE | `npm run build` | ✅ ok |
| Lint | — | ⚠️ nessun linter configurato (solo Prettier `.prettierrc.json`, non in CI) |
| Test BE (come CI/COLLABORAZIONE) | `pytest tests/ -q --ignore=tests/test_smoke.py` | ✅ **169 passed, 4 skipped** |
| Smoke E2E BE | `pytest tests/test_smoke.py` (isolato, con ffmpeg) | ✅ **4 passed** |

**Note di ambiente / trappole confermate:**
- **`get_settings()` è `@lru_cache`**: il primo modulo importato congela le Settings. `test_smoke.py` va eseguito in un **processo pytest separato** (in suite completa i suoi env var sono ignorati → login 401). Per questo la CI/COLLABORAZIONE usa `--ignore=tests/test_smoke.py`. **Non è un bug del codice**, ma una trappola di test documentata.
- **ffmpeg/ffprobe** non erano nel container: installati con `apt-get update && apt-get install -y ffmpeg` (l'`update` è necessario). `run.py` esce se ffmpeg manca.
- La **CI attuale (`deploy.yml`) non esegue alcun test backend** (solo `tsc`+`vitest`+`build` del frontend come gate prima del deploy). L'intera suite pytest è quindi un gate solo manuale/documentale. → vedi PR `chore/repo-foundations` (`ci.yml`).

---

## 3. Test aggiunti in questa sessione

**+177 test** in 11 nuovi file (9 backend pytest, 2 frontend vitest), scritti col framework già presente, senza modificare configurazione né codice di produzione. **9 test sono rossi per design** (documentano bug confermati, sezione §4).

| File | Test | Verdi | Rossi | Copre |
|---|---:|---:|---:|---|
| `backend/tests/test_qa_ffmpeg_cmd.py` | 26 | 26 | — | `build_export_cmd` (concat, afade, setpts/atempo, zoompan+whoosh, ass dopo scale/crop, clamp fps), `_atempo_chain`, `escape_filter_path`, `probe`, `export_video` (progress/errori/output vuoto) — tutto offline con `_run` mockato |
| `backend/tests/test_qa_timeline_plan.py` | 14 | 13 | 1 | invarianti del piano render, `map_time_plan` (monotonia), `remap_segments_detailed_plan` — **QA-01** |
| `backend/tests/test_qa_silence_parsing.py` | 12 | 12 | — | parsing `silencedetect`, `silences_to_cuts_and_speedups` (rami non coperti), `auto_cuts_for` wiring; characterization QA-07 |
| `backend/tests/test_qa_captions_ass.py` | 20 | 18 | 2 | chunking caption, `_ass_time`, `_escape_text`, `_karaoke_events`, `build_ass` — **QA-03, QA-04** |
| `backend/tests/test_qa_upload_api.py` | 18 | 16 | 2 | upload (estensioni/unicode/probe fallito+cleanup/template 404/multiplo), listing/delete/autocut, health/styles — **QA-08, QA-19** |
| `backend/tests/test_qa_worker_recovery.py` | 8 | 8 | — | recovery all'avvio, `run_export` (file mancante/felice/cleanup .ass), `run_transcribe`+auto_export, `_set_progress` throttle; characterization QA-14 |
| `backend/tests/test_qa_subtitles_preserve.py` | 11 | 11 | — | preservazione word-timestamp karaoke su PUT; characterization collisione chiave |
| `backend/tests/test_qa_auth_tokens.py` | 19 | 18 | 1 | `make_token`/`verify_token`, `_extract_token` (priorità Bearer>cookie>?t=), login/me/logout — **QA-02** |
| `backend/tests/test_qa_spa_static.py` | 15 | 15 | — | fallback SPA, 404 api/asset, anti path-traversal (4 encoding), Cache-Control, security headers, health |
| `frontend/src/format.qa.test.ts` | 17 | 14 | 3 | `fmtTime`/`parseTime`/`fmtSize`/`fmtDate` — **QA-05 (×2), QA-06** |
| `frontend/src/api.qa.test.ts` | 17 | 17 | — | `req`/`AuthError`/`ApiError`, header/token, login/logout, url media con `?t=` |

**Suite completa dopo l'aggiunta:**
- Backend (no smoke): **306 passed, 6 failed** (i 6 rossi-per-design) — era 169.
- Frontend: **51 passed, 3 failed** (i 3 rossi-per-design) — era 20.

> ⚠️ La suite di `test/qa-baseline` è **volutamente rossa** su questi 9 test: fotografa i bug. Diventeranno verdi nella sessione di correzione (`refine/quality-pass-1`), quando si corregge la causa. I test *characterization* (verdi) fotografano invece comportamenti attuali discutibili ma non necessariamente da cambiare.

---

## 4. Bug trovati — ordinati per gravità

Legenda: **🔴 rosso** = c'è un test che fallisce e lo dimostra · **🟡 characterization** = c'è un test verde che fotografa il comportamento (potenziale bug o scelta da confermare).

### ALTA

**QA-02 🔴 — Login con password non-ASCII → HTTP 500 e non conteggiato dal rate limiter**
`backend/app/auth.py:76` · test: `test_qa_auth_tokens.py::test_login_with_non_ascii_password_returns_401`
`hmac.compare_digest(body.password, settings.admin_password)` su `str` con caratteri non-ASCII solleva `TypeError` → 500 invece di 401. Il fallimento avviene **prima** di `limiter.record(ip)`, quindi non viene contato (canale di brute-force/log-flood invisibile). Inoltre se `ADMIN_PASSWORD` stessa è non-ASCII, **ogni** login crolla (self-lockout).
Ripro: `POST /api/auth/login {"password":"pässword"}` con `TestClient(app, raise_server_exceptions=False)` → 500.
Causa: confronto su `str` invece che su `bytes`. Fix naturale: `.encode()` su entrambi gli operandi. *(Approfondito in `SECURITY_REPORT.md`, critico #1.)*

**QA-08 🔴 — PATCH `trim_start` non confrontato col `trim_end` già salvato → stato incoerente**
`backend/app/routers/videos.py:149-152` · test: `test_qa_upload_api.py::test_patch_trim_start_beyond_saved_trim_end_rejected`
Con `trim_end=10` salvato, una PATCH `{"trim_start": 20}` (durata 30) passa la validazione (controlla solo `trim_start < duration`), salvando `trim_start > trim_end`. All'export `timeline.keep_intervals` alza `ValueError("Intervallo di trim vuoto")`: il video va in errore per un input che l'API aveva accettato come valido.
Ripro: crea video (duration 30), PATCH trim_end=10, poi PATCH trim_start=20 → 200 (atteso 422).
Causa: manca il confronto incrociato col `trim_end` corrente quando si aggiorna solo `trim_start`.

### MEDIA

**QA-01 🔴 — `apply_speedups`: span di velocizzazione sovrapposti generano segmenti di piano sovrapposti**
`backend/app/services/timeline.py:124-131` · test: `test_qa_timeline_plan.py::test_apply_speedups_overlapping_spans_must_not_overlap_in_plan`
Con keep `(0,10)` e speedups `[(1,5,×2),(4,8,×3)]` il piano risulta `[(0,1,1),(1,5,2),(4,8,3),(8,10,1)]`: il tratto **4–5 è renderizzato due volte**. `a2` è clampato solo su `ks` (`max(ks,a)`) e mai su `cursor`; il guard `b2 <= cursor` scarta solo gli span interamente consumati.
Impatto reale: il flusso auto-silenzi produce span **disgiunti** (non lo innesca), ma speedup provenienti da API/manuali sì. Fix naturale: `a2 = max(a2, cursor)`.

**QA-06 🔴 — `parseTime` accetta componenti negative/spazzatura**
`frontend/src/format.ts:16-21` · test: `format.qa.test.ts::parseTime … '1:-30'`
`parseTime("1:-30")` → `30` (1×60 + (−30)); `"1:99"` → 159; `"1abc"` → 1 (parseFloat permissivo). Usato negli input di trim e dei tempi sottotitoli: un valore ambiguo diventa un numero sbagliato senza errore.
Fix naturale: rifiutare componenti fuori range e input non interamente numerici.

**QA-03 🔴 — Caption sovrapposte per `end` forzato a `start+0.2`**
`backend/app/services/captions.py:36` · test: `test_qa_captions_ass.py::test_chunks_never_overlap_even_with_min_duration_padding`
`end = max(cur_end, cur_start + 0.2)` può far sì che una caption molto breve finisca **dopo** l'inizio della successiva → due `Dialogue` simultanei nell'`.ass` (testo impilato). Con words `[(0.0,0.1,'Ciao.'),(0.15,0.5,'mondo'),…]` la prima caption diventa `{0.0, 0.2}` e si sovrappone alla seconda (0.15).
Fix naturale: clampare l'`end` al `min` con lo `start` della caption seguente.

### BASSA

**QA-05 🔴 — `fmtTime` al bordo del minuto → "0:60.0"**
`frontend/src/format.ts:7` · test: `format.qa.test.ts::fmtTime — bordo del minuto`
`fmtTime(59.99)` → `"0:60.0"` invece di `"1:00.0"`; `fmtTime(119.97)` → `"1:60.0"`. `toFixed(1)` arrotonda i secondi a 60 senza riporto sul minuto. Solo visualizzazione.
Fix naturale: arrotondare prima di scomporre minuti/secondi.

**QA-04 🔴 — `_ass_time(59.999)` → "0:00:60.00" (timestamp non canonico)**
`backend/app/services/styles.py:119-124` · test: `test_qa_captions_ass.py::test_ass_time_rounds_up_into_minutes_canonically`
`f"{59.999:05.2f}"` = `"60.00"` → secondi = 60, non canonico (i secondi ASS devono essere < 60). libass lo tollera; altri parser/editor `.ass` possono rifiutarlo.
Fix naturale: riportare il resto sui minuti dopo l'arrotondamento.

**QA-19 🔴 — `GET /api/jobs?limit=-1` non validato**
`backend/app/routers/jobs.py:28-31` · test: `test_qa_upload_api.py::test_jobs_negative_limit_rejected`
`limit` ha solo `min(limit, 200)` (nessun lower bound). `?limit=-1` → `LIMIT -1`: su SQLite = nessun limite (bypass del cap); su **Postgres = errore SQL 500**.
Fix naturale: `Query(ge=1, le=200)`.

### Comportamenti fotografati (🟡 characterization — verdi, da valutare)

| ID | File:riga | Cosa |
|---|---|---|
| QA-07 | `silence.py:28` | `detect_silences` non controlla il `returncode`: se ffmpeg fallisce ritorna `[]`, indistinguibile da "nessun silenzio" (auto-edit fallisce in silenzio). Se il binario **manca**, `FileNotFoundError` non gestita propaga. |
| QA-09 | `ffmpeg.py:71-75, 169` | Doppio escaping in conflitto col quoting: un `ass_path` con apice chiude la quote anzitempo (comando malformato); con `:` il backslash resta letterale dentro le quote. Non sfruttabile come injection (path interni UUID, arg-list). |
| QA-11 | `auth.py:89-92` | `logout` cancella solo il cookie: un token Bearer già emesso resta valido fino a scadenza (30gg), nessuna revoca server-side. |
| QA-14 | `worker.py:234-240` | Un export annullato (`canceling`) **completa comunque** l'export e setta `EXPORTED`/`exported_path`, poi il job è chiuso `canceled`: job canceled ma lavoro eseguito. |
| — | `subtitles.py:38-41` | Collisione di chiave `round(start,2),round(end,2)`: due segmenti con tempi che collidono e stesso testo → uno **perde le proprie words** (perdita silenziosa del karaoke). |
| — | `subtitles.py:43-46` | Una PUT di soli segmenti degeneri (`end<=start`) o vuoti **svuota** i sottotitoli rispondendo 200, senza segnale d'errore. |
| — | `videos.py:120` | `GET /api/videos?status=<qualsiasi>` non valida lo stato: 200 con lista vuota invece di 422 (un typo del client fallisce in silenzio). |
| — | `ffmpeg.py:33` | `probe()` non incapsula `json.JSONDecodeError`: stdout non-JSON con returncode 0 → eccezione grezza invece di `FFmpegError`. |
| — | `ffmpeg.py:107-148` | `build_export_cmd` con `keeps=[]` produce `concat=n=0` (filtergraph invalido) senza sollevare: difesa solo a monte. |
| — | `ffmpeg.py:235-236` | `export_video` non clampa il progresso a `>=0`: `out_time_ms` negativi a inizio encoding → `progress_cb` con valore negativo. |
| — | `styles.py:127-130` | `_escape_text` non neutralizza il backslash: `\N`/`\h` letterali nel testo utente diventano a-capo/spazi ASS involontari. |
| — | `worker.py:56-62` | `_set_progress` scrive senza guardia sullo stato del job: può sovrascrivere il progress di un job già chiuso. |
| — | `format.ts:30-34` | `fmtDate` su data invalida → `"Invalid Date Invalid Date"` (nessuna guardia `isNaN`). |
| — | `api.ts:40` | Risposta di errore JSON **senza** `detail` → `ApiError.message` diventa `undefined` (perde il fallback `statusText`). |

> **Sicurezza e scalabilità:** ulteriori bug ad alto impatto (bypass rate-limit via `X-Forwarded-For`, DoS disco upload, `size_bytes` int32 overflow su Postgres, media senza garbage-collection, `db.py` `BOOLEAN DEFAULT` non valido su Postgres, recovery worker non-scoped con `WORKER_CONCURRENCY>1`, JSON `cuts/speedups` senza `MutableList`) sono documentati in dettaglio nei report gemelli **`SECURITY_REPORT.md`** (branch `audit/security`) e **`SCALING_REPORT.md`** (branch `audit/scaling-report`), per non duplicarli qui.

---

## 5. Zone di codice più a rischio

1. **`services/ffmpeg.py` — `build_export_cmd`** (~100 righe, filtergraph costruito per concatenazione di stringhe): nessuna difesa su input degeneri (`keeps=[]`, speed 0/negativo → `_atempo_chain` loop infinito), escaping/quoting fragile (QA-09), progresso non clampato. È il cuore dell'export: ogni video ci passa. Fino a questa sessione **zero test**; ora coperta con 26 test offline.
2. **`services/timeline.py` — `apply_speedups`** (partizionamento con cursor): il bug QA-01 vive esattamente qui; il contratto "piano contiguo" non è rispettato ai bordi (sliver < `MIN_INTERVAL`).
3. **`worker.py` — concorrenza e cancellazione**: recovery all'avvio rieseguito da ogni thread (`WORKER_CONCURRENCY>1`), export senza checkpoint di cancel (QA-14), `_set_progress`/chiusura job con UPDATE non condizionati allo stato.
4. **`routers/videos.py` — upload e PATCH**: validazioni parziali (QA-08, status non validato), limite dimensione applicato dopo lo spooling del body, ordine unlink-file/commit-DB in `delete`.
5. **`routers/subtitles.py` — preservazione karaoke**: logica sottile a chiave arrotondata (collisione, drift, svuotamento silenzioso). Ben coperta ora, ma delicata.
6. **`format.ts`** (client): parsing/formattazione tempi senza validazione dei bordi, usati negli input dell'editor.

---

## 6. Interventi consigliati (prossimi passi)

1. **Correggere i 5 bug a impatto utente diretto** (in ordine): QA-02 (login 500/rate-limit), QA-08 (trim incoerente → export fallito), QA-01 (doppio render), QA-03 (caption sovrapposte), QA-06 (parseTime). Ognuno ha già il test rosso: correggere la causa → suite verde. *(È l'oggetto della sessione `refine/quality-pass-1`.)*
2. **Sistematizzare la gestione errori nei flussi ffmpeg/whisper**: incapsulare `FileNotFoundError`/`JSONDecodeError`/`TimeoutExpired` in `FFmpegError` con messaggio chiaro; controllare il `returncode` in `detect_silences` (QA-07); difendere `build_export_cmd` da `keeps=[]` e speed ≤ 0. Evita crash silenziosi e stati incoerenti.
3. **Attivare una CI che esegua i test** (backend veloce + frontend + smoke E2E con ffmpeg in job separato): oggi il deploy non li lancia. Pronta nella PR `chore/repo-foundations` (`ci.yml`).
4. **Rendere robuste le operazioni interrotte a metà**: checkpoint di cancellazione in `run_export` (QA-14), recovery worker idempotente e scoped, unlink-file **dopo** il commit DB in `delete_video`.
5. **Dare priorità ai critici di sicurezza/scalabilità** dai report gemelli: `.encode()` nel confronto password, non fidarsi di `X-Forwarded-For` dietro il rate-limit, limite hard sul body upload, `size_bytes` → `BigInteger`, garbage-collection dei media.

---

## 7. Cosa NON è stato fatto (limiti di questa sessione)
- Nessuna modifica al codice di produzione (vincolo della sessione): i bug restano rossi.
- `test_smoke.py` resta escluso dalla suite unica (trappola `lru_cache`, non un bug).
- I flussi che richiedono il modello Whisper reale (trascrizione end-to-end) non sono esercitati con il modello: `run_transcribe` è testato con `transcribe_words` mockato.
- Nessun test su Postgres reale: la suite gira su SQLite, quindi i bug specifici di Postgres (`size_bytes`, `LIMIT -1`, `BOOLEAN DEFAULT`) sono documentati ma non riproducibili qui.

---

## 8. Aggiornamento — sessione di affinamento (`refine/quality-pass-1`)

Branch `refine/quality-pass-1` (da `test/qa-baseline`). Ogni bug corretto nella **causa**, un commit per bug, suite verde dopo ognuno. **API pubbliche e formati di file invariati.**

### Risolti — gli 8 bug confermati (i test rossi sono ora verdi)
| ID | Fix | File |
|---|---|---|
| QA-02 | confronto password su `bytes` UTF-8 (niente più 500 con non-ASCII, fallimento ora conteggiato) | `auth.py` |
| QA-08 | PATCH `trim_start` validato contro il `trim_end` salvato → 422 se creerebbe finestra vuota | `videos.py` |
| QA-01 | `apply_speedups`: `a2 = max(ks, cursor, a)` → nessun tratto renderizzato due volte | `timeline.py` |
| QA-06 | `parseTime` rifiuta componenti negative (permissività `parseFloat` lasciata invariata) | `format.ts` |
| QA-03 | caption clampate allo `start` della successiva → nessuna sovrapposizione | `captions.py` |
| QA-05 | `fmtTime` arrotonda ai decimi prima di scomporre → rollover del minuto corretto | `format.ts` |
| QA-04 | `_ass_time` arrotonda a centisecondi e riporta sui minuti → timestamp canonico | `styles.py` |
| QA-19 | `GET /api/jobs` valida `limit >= 1` (`Query(ge=1)`) | `jobs.py` |

### Robustezza aggiunta (Fase 2 — nessun comportamento valido alterato)
- `_atempo_chain`: fattore ≤ 0 → `FFmpegError` invece di **loop infinito** (worker bloccato).
- `probe`: `json.JSONDecodeError` incapsulato in `FFmpegError` (messaggio chiaro).
- `build_export_cmd`: piano vuoto → `FFmpegError` invece di `concat=n=0` invalido (difesa in profondità).
- `export_video`: progresso clampato a `[0, 0.99]` (ffmpeg può emettere `out_time_ms` negativi).
- Aggiornato il solo test QA di `keeps=[]` all'esito difeso (ora asserisce che solleva).

### Stato suite dopo l'affinamento
- Backend: **312 passed** (era 306 + 6 rossi) · Frontend: **54 passed** (era 51 + 3 rossi) · Smoke E2E (ffmpeg): **4 passed**. Zero regressioni.

### Non affrontati in questa sessione (per scelta motivata)
- **Pulizia mirata**: nessuna. Il codice morto individuato (`retakes.py:115`) è fuori dalle zone a rischio prioritarie; toccarlo darebbe rischio > beneficio ora.
- **Performance**: nessuna. Il report non segnala una lentezza **misurabile** nel codice: le voci di performance (RAM Whisper, filtergraph) sono strutturali e stanno in `SCALING_REPORT.md`. Come da vincolo "niente ottimizzazioni a occhio", non ho fatto micro-ottimizzazioni non misurate.
- **Sicurezza/scalabilità ad alto impatto**: nei report/PR gemelli (`audit/security`, `audit/scaling-report`), fuori dallo scopo di questo pass.

---

## 9. Decisioni di design da confermare (comportamenti ambigui — NON decisi in autonomia)

Questi comportamenti sono *characterization* (test verdi che li fotografano): sistemarli cambierebbe un contratto, quindi servono una scelta.

| ID / punto | Comportamento attuale | Opzione A | Opzione B |
|---|---|---|---|
| **QA-07** `silence.py:28` | `detect_silences` con ffmpeg fallito (`returncode≠0`) ritorna `[]` (silenzio == "nessuna pausa") | **Sollevare** `FFmpegError` → l'autocut dà 500 chiaro. *Pro:* nessun fallimento silenzioso. *Contro:* cambia il contratto dell'endpoint |
| **QA-09** `ffmpeg.py:71-75,169` | escaping+quoting del path `.ass` fragile (apice/`:`) | **Non escapare** e affidarsi solo alle quote singole ffmpeg. *Pro:* corretto per tutti i path. *Contro:* verificare i path Windows. Non sfruttabile (path UUID interni) → priorità bassa |
| **QA-11** `auth.py:89-92` | logout non revoca il token Bearer (valido 30gg) | **Blacklist/rotazione secret** server-side. *Pro:* logout reale. *Contro:* stato server, complessità. *(vedi `SECURITY_REPORT.md`)* |
| **QA-14** `worker.py:234-240` | export annullato completa comunque e setta `EXPORTED` | **Checkpoint di cancel** prima/dopo l'encoding. *Pro:* stato coerente. *Contro:* lavoro ffmpeg già speso da interrompere |
| `subtitles.py:43-46` | una PUT di soli segmenti degeneri **svuota** i sottotitoli con 200 | **422** se dopo il filtro non resta nulla e ne erano stati inviati. *Pro:* niente perdite silenziose. *Contro:* blocca il caso legittimo "cancella tutto" |
| `subtitles.py:38-41` | collisione chiave `round(_,2)` → un segmento eredita le words dell'altro | **Chiave a `round(_,3)`** o per `idx`. *Pro:* nessuna perdita karaoke. *Contro:* casistica rara |
| `videos.py:120` | `GET /api/videos?status=<qualsiasi>` → 200 lista vuota | **Validare** con enum/`Literal` → 422 sul typo. *Pro:* errori evidenti. *Contro:* micro-breaking per client tolleranti |
| `format.ts` `parseTime("1abc")` | `parseFloat` permissivo → `1` | **Regex stretta** che rifiuta i suffissi. *Pro:* input più sicuro. *Contro:* rompe copia-incolla tolleranti |
| `format.ts` `fmtSize(400)` → "0 KB" · `fmtDate` invalida → "Invalid Date…" | resa fuorviante ai bordi | mostrare "<1 KB" / guardia `isNaN` con "-". *Pro:* estetica. *Contro:* puramente cosmetico |

