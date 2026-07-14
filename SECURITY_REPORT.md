# SECURITY_REPORT — EditVideo

Audit di sicurezza "occhio da attaccante" sul backend FastAPI (`backend/app/`),
sul frontend React/Vite (`frontend/`) e sulla configurazione di deploy.
Nessun codice di produzione e' stato modificato: sono stati aggiunti solo questo
report e i test dimostrativi in `backend/tests/test_security_audit.py`.

**Stack:** FastAPI + Starlette, SQLAlchemy (SQLite/Postgres), ffmpeg/ffprobe via
`subprocess` (lista argomenti, **no** `shell=True`), faster-whisper, deploy su
Hugging Face Space (privato, dietro iframe). Auth mono-utente: password unica
(`ADMIN_PASSWORD`) + token HMAC-SHA256 con scadenza.

## Sintesi per gravita'

| # | Gravita' | Titolo | File:riga | Test |
|---|----------|--------|-----------|------|
| 1 | ~~CRITICO~~ **RISOLTA** | Password non-ASCII -> HTTP 500 + fallimento non conteggiato + self-lockout admin — **corretta in master (QA-02, commit 6e63a9b): confronto su bytes** | `auth.py:76-77` | si (asserisce il fix) |
| 2 | ~~CRITICO~~ **RISOLTA** | Rate-limit login aggirabile via `X-Forwarded-For` + crescita illimitata chiavi (DoS memoria) — **chiuso: contatore GLOBALE + tetto `max_keys`** | `security.py` | si (asserisce il fix) |
| 3 | **ALTO** (parz. RISOLTA) | ~~logout senza revoca~~ + ~~cookie senza `Secure`~~ **corretti** (revoca via generazione + flag `Secure`); token in `?t=` resta per i media (trade-off documentato) | `auth.py` | si (asserisce il fix) |
| 4 | ~~ALTO/MEDIO~~ **RISOLTA** | DoS disco in upload: limite applicato dopo lo spool — **chiuso: middleware 413 su `Content-Length` PRIMA di leggere il body (411 se assente) + tetto `max_upload_files`** | `main.py`, `videos.py` | si (asserisce il fix) |
| 5 | ~~MEDIO~~ **RISOLTA** | `resolved_secret()` fragile — **chiuso: file 0600, scrittura atomica (tmp + `os.link`/`os.replace`), file vuoto rigenerato, chiave cache-ata in memoria** | `config.py` | si (asserisce il fix) |
| 6 | ~~MEDIO~~ **RISOLTA** | Credenziali di default — **chiuso: avvio RIFIUTATO con `changeme` se `APP_ENV=prod`; credenziali Postgres del compose parametrizzate via env** | `main.py`, `docker-compose.yml` | documentato |
| 7 | ~~MEDIO~~ **RISOLTA** | Dipendenze frontend (dev) vulnerabili — **chiuso: bump a vite 8 / vitest 4 / @vitejs/plugin-react 6 → `npm audit`: 0 vulnerabilita'** | `frontend/package.json` | npm audit |
| 8 | ~~BASSO~~ **RISOLTA** | `/api/health` non autenticato: info disclosure — **chiuso: senza token risponde solo `{"ok": true}`; diagnostica completa solo autenticati** | `jobs.py` | si (asserisce il fix) |
| 9 | **BASSO** | CSP `frame-ancestors *`: trade-off clickjacking (accettato per l'iframe HF) | `security.py:40` | documentato |
| 10 | ~~BASSO~~ **RISOLTA** | `size_bytes` colonna `Integer`: overflow su Postgres — **chiuso: `BigInteger` + `ALTER ... TYPE BIGINT` additivo in `db.py`** | `models.py`, `db.py` | documentato |

**Difese verificate che REGGONO** (vedi in fondo): niente shell injection; filtergraph
injection non sfruttabile (path interni uuid); path traversal SPA bloccato; schemi
Pydantic rifiutano Inf/NaN; nessun segreto nella history git; `pip-audit` pulito;
nessun middleware CORS permissivo.

---

## ~~CRITICO~~ RISOLTA #1 — Password non-ASCII: HTTP 500 e rate limiter aggirato

> **STATO: RISOLTA in `master`** (quality pass QA-02, commit `6e63a9b`). `auth.py`
> ora confronta la password su **bytes** (`encode`), quindi una password errata
> non-ASCII torna **401** ed e' **conteggiata** dal rate limiter; niente piu'
> self-lockout dell'admin. I test di questo blocco sono stati aggiornati per
> asserire il comportamento sicuro. Il resto della sezione documenta la falla
> originaria (pre-fix) a scopo storico.

**Dove:** `backend/app/auth.py:76-77`
```python
if not hmac.compare_digest(body.password, settings.admin_password):
    limiter.record(ip)  # conta solo i fallimenti
    raise HTTPException(status_code=401, detail="Password errata")
```

**Problema.** `hmac.compare_digest`, quando riceve due `str`, richiede che siano
**ASCII puri**; con caratteri non-ASCII solleva
`TypeError: comparing strings with non-ASCII characters is not supported`.
Verificato empiricamente:
```
POST /api/auth/login {"password": "pässwörd"}  ->  HTTP 500
```
Conseguenze concatenate:
1. **500 invece di 401** su una banale password errata (semantica sbagliata,
   stacktrace nei log, superficie di enumerazione/errore).
2. **Bypass della contabilita' anti-brute-force:** l'eccezione esplode *prima* di
   `limiter.record(ip)` (riga 77), quindi il tentativo **non viene contato**. Un
   attaccante puo' inviare richieste illimitate con password non-ASCII senza mai
   incappare nel 429 (rumore/log-flood, e un canale che sfugge del tutto al
   rate limiter).
3. **Self-lockout / availability:** se l'amministratore imposta una
   `ADMIN_PASSWORD` con caratteri non-ASCII (es. `pässwort`, un'emoji, accenti),
   **ogni** login -- anche con la password giusta -- crolla con TypeError -> 500.
   L'app diventa completamente non-loginabile.

**Scenario d'attacco.** Un bot che sbatte sull'endpoint di login con payload tipo
`{"password":"ééé"}` genera 500 illimitati (log flooding, carico) e non lascia
traccia nel rate limiter. In parallelo, l'utente legittimo che sceglie una
password "robusta" con caratteri accentati si auto-esclude dall'app.

**Fix proposto.**
- *Quick win:* confrontare su **bytes** con encoding esplicito:
  `hmac.compare_digest(body.password.encode("utf-8"), settings.admin_password.encode("utf-8"))`.
  `compare_digest` su `bytes` gestisce qualsiasi valore ed e' constant-time.
- *Strutturale:* spostare `limiter.record(ip)` in modo che ogni tentativo fallito
  (inclusi input malformati) venga contato, e validare/normalizzare l'input di
  login. Considerare l'hashing della password (Argon2/bcrypt) invece del confronto
  in chiaro della password di admin in memoria.

**Test:** `test_vuln_nonascii_password_returns_500_not_401`,
`test_vuln_nonascii_password_failure_is_not_rate_limited`,
`test_vuln_nonascii_admin_password_would_lock_out_all_logins` (verdi, documentano
la falla) + `test_secure_nonascii_password_should_return_401` (xfail = rosso-per-design).

---

## ~~CRITICO~~ RISOLTA #2 — Rate limit aggirabile via `X-Forwarded-For` + DoS memoria

> **STATO: RISOLTA** (branch `fix/security-2-3`). `RateLimiter` ora ha (a) un
> **contatore GLOBALE** (`login_global_max_attempts`, default 20): anche ruotando
> l'header spoofabile i tentativi confluiscono qui e oltre soglia scatta il 429 →
> bypass chiuso; (b) un **tetto alle chiavi** (`rate_limit_max_keys`, default 4096)
> con evizione delle piu' vecchie → niente crescita illimitata del dict (DoS
> memoria). I test asseriscono il nuovo comportamento. La sezione sotto descrive
> la falla originaria.

**Dove:** `backend/app/security.py:65-69` (estrazione IP) e `:91` (dict `_hits`)
```python
xff = request.headers.get("x-forwarded-for")
if xff:
    first = xff.split(",")[0].strip()
    if first:
        return first          # <-- fidato ciecamente, nessun proxy fidato
```

**Problema.** `client_ip` prende il **primo** valore di `X-Forwarded-For` (o
`X-Real-IP`) **senza** validare una whitelist di proxy fidati. Poiche' il rate
limiter del login e' *per IP* (`security.py:104-117`), l'attaccante controlla la
propria chiave:
1. **Bypass del 429:** cambiando l'header a ogni richiesta, ogni tentativo di
   brute-force finisce in un secchiello diverso -> la soglia (default 10) non
   scatta mai. Verificato: 30 tentativi errati con XFF rotante -> **tutte 401,
   zero 429**.
2. **DoS memoria:** `self._hits` (`defaultdict(deque)`) accumula una chiave per
   ogni IP visto. Un flusso di IP fittizi sempre nuovi (spoofati via XFF) fa
   crescere il dict **senza tetto** (le chiavi con 1 hit vengono purgate solo se
   quell'IP ritorna dopo la finestra). Verificato: 400 IP fittizi -> 400 chiavi
   residenti, nessuna evizione. Milioni di IP fittizi -> memoria esaurita.

**Scenario d'attacco.** Uno script non autenticato invia
`X-Forwarded-For: <IP casuale>` a `/api/auth/login`: brute-forcia la password
unica senza limiti E, con IP sempre nuovi, gonfia la RAM del processo fino
all'OOM del container (su HF Space la memoria e' limitata).

> Nota: dietro il proxy di HF il commento nel codice riconosce che l'header e'
> "spoofabile" e accetta il trade-off. Ma il trade-off vale solo se ci si fida
> del proxy: qui manca sia il pinning al peer reale del proxy sia un tetto alle
> chiavi. Entrambe le mitigazioni sono indipendenti dall'iframe.

**Fix proposto.**
- *Quick win:* mettere un **tetto** al numero di chiavi in `_hits` (es. LRU con
  cap, o purge globale periodico) per chiudere il DoS memoria.
- *Strutturale:* fidarsi di `X-Forwarded-For` **solo** se il peer del socket
  (`request.client.host`) e' un proxy noto/fidato (es. rete interna HF), e in tal
  caso prendere l'IP corretto della catena; altrimenti usare il peer. In
  alternativa, aggiungere un limite **globale** per-endpoint (non solo per-IP) sui
  tentativi di login.

**Test:** `test_vuln_login_rate_limit_bypassed_by_rotating_xff`,
`test_vuln_ratelimiter_unbounded_memory_growth_via_spoofed_xff` (verdi) +
`test_secure_login_should_block_bruteforce_despite_xff_rotation` (xfail).

---

## ALTO #3 (parz. RISOLTA) — Token in URL, logout non revoca, cookie senza `Secure`

> **STATO: parzialmente RISOLTA** (branch `fix/security-2-3`). Corretti i due punti
> piu' gravi: (2) il **logout ora revoca** davvero — il token include una
> "generazione" firmata che il logout incrementa, invalidando ogni token gia'
> emesso (Bearer incluso); (3) il **cookie di sessione ha il flag `Secure`**
> (`cookie_secure`, default True). RESTA come trade-off documentato il punto (1),
> token completo in `?t=` per i media nell'iframe: la mitigazione strutturale
> (URL firmati a scadenza breve) e' un lavoro a parte; nel frattempo la revoca al
> logout ne riduce molto l'impatto. I test asseriscono revoca + `Secure`.

**Dove:** `backend/app/auth.py:46-53` (`_extract_token`), `:81-92` (login/logout),
`:29-31` (`make_token`, scadenza `session_days` = 30 gg di default).

**Problemi (concatenati sul ciclo di vita del token).**
1. **Token via query string** (`auth.py:53`: `request.query_params.get("t")`).
   Serve per i media nell'iframe (`<video src=...?t=TOKEN>`), ma il token completo
   finisce negli **access log** di proxy/uvicorn, nella **history del browser** e
   nell'header **Referer** verso terze parti -> esfiltrazione del segreto di
   sessione. Verificato: `GET /api/auth/me?t=<token>` -> `{"authenticated": true}`.
2. **Logout senza revoca server-side** (`auth.py:89-92`): `delete_cookie` cancella
   solo il cookie del browser. Il token HMAC resta **valido fino a scadenza** (30
   giorni) perche' la verifica e' puramente stateless (`verify_token`). Un token
   trapelato (vedi punto 1) resta usabile anche dopo il "logout". Verificato:
   dopo `POST /api/auth/logout`, `me?t=<token>` -> ancora `authenticated: true`.
3. **Cookie senza flag `Secure`** (`auth.py:81-85`): il `Set-Cookie` porta
   `HttpOnly; SameSite=Lax` ma **non** `Secure`, quindi puo' essere inviato in
   chiaro su HTTP e intercettato. Verificato nell'header `set-cookie`.
4. **Validita' lunga (30 gg)** senza rotazione ne' `jti`/denylist: la finestra di
   abuso di un token rubato e' enorme.

**Scenario d'attacco.** Un URL media con `?t=<token>` condiviso/loggato (o un
Referer verso una CDN esterna) espone il token; l'attaccante lo riusa per 30
giorni, e il "logout" della vittima non lo ferma.

**Fix proposto.**
- *Quick win:* impostare `secure=True` sul cookie in produzione (dietro HTTPS);
  ridurre `session_days`.
- *Strutturale:* usare token con `jti` + versione/`token_version` server-side (o
  una denylist) cosi' che il logout (o un "logout ovunque") **revochi** davvero;
  per i media, sostituire `?t=<token>` con **URL firmati a scadenza breve**
  (query param dedicato, non il token di sessione completo).

**Test:** `test_vuln_token_accepted_in_query_string`,
`test_vuln_logout_does_not_revoke_token`, `test_vuln_login_cookie_missing_secure_flag`
(verdi) + `test_secure_logout_should_revoke_token`,
`test_secure_login_cookie_should_have_secure_flag` (xfail).

---

## ~~ALTO/MEDIO~~ RISOLTA #4 — DoS disco nell'upload: limite applicato troppo tardi

> **STATO: RISOLTA.** Il middleware `request_size_guard` (`main.py`) valida il
> `Content-Length` dichiarato e risponde **413 PRIMA** che il body venga
> letto/spolato su disco (tetto `resolved_max_request_bytes()`; senza
> `Content-Length`, es. chunked, risponde **411**: il limite deve essere
> verificabile in anticipo). Le route non di upload hanno un tetto separato e
> molto piu' piccolo (`max_json_body_kb`). In piu' l'endpoint rifiuta con 413 le
> richieste con piu' di `max_upload_files` file (default 10). Test in
> `tests/test_upload_limits.py`. La sezione sotto descrive la falla originaria.

**Dove:** `backend/app/routers/videos.py:74-84`
```python
max_bytes = settings.max_upload_mb * 1024 * 1024
written = 0
with open(dst, "wb") as out:
    while True:
        chunk = f.file.read(1024 * 1024)
        ...
        if written > max_bytes:
            raise ValueError(...)
```

**Problema.** Quando l'handler gira, `f.file` (lo `SpooledTemporaryFile` di
Starlette) contiene **gia' l'intero body**: FastAPI esegue `await request.form()`
e materializza la UploadFile *prima* di entrare nella funzione. Con file oltre la
soglia di spool (default 1 MB) Starlette ha gia' scritto **tutto** su un file
temporaneo su disco. Quindi `max_upload_mb`:
- non impedisce l'esaurimento del disco (il file e' gia' atterrato in temp);
- causa comunque una **doppia scrittura** su disco (temp Starlette + copia in
  `originals_dir`).

Manca inoltre un controllo di `Content-Length` a monte e un tetto sul numero/somma
dei file per richiesta (`files: list[UploadFile]`).

> L'upload richiede autenticazione (`dependencies=[Depends(require_auth)]`): l'auth
> risponde 401 *prima* di consumare il body. Il DoS e' quindi **autenticato** — ma
> con password unica condivisa la superficie e' realistica, e su HF Space il disco
> e' limitato. La protezione dichiarata (`max_upload_mb`) e' comunque **illusoria**
> per lo scopo per cui esiste.

**Fix proposto.**
- *Quick win:* rifiutare in base a `Content-Length` prima di leggere il body;
  impostare un limite di dimensione a livello ASGI/Starlette (max request size).
- *Strutturale:* streaming diretto verso lo storage con enforcement del limite
  durante la ricezione (senza spool completo), tetto su numero e somma dei file.

---

## ~~MEDIO~~ RISOLTA #5 — `resolved_secret()`: gestione fragile della chiave di firma

> **STATO: RISOLTA.** `resolved_secret()` ora crea `data/secret.key` con
> **permessi 0600** e **scrittura atomica** (temp + `os.link`/`os.replace`; la
> creazione concorrente web/worker e' risolta da `os.link`, atomico: vince il
> primo, l'altro rilegge la sua chiave); un file **vuoto** (write interrotto,
> disco pieno) viene **rigenerato** invece di firmare con chiave vuota; la
> chiave e' **cache-ata in memoria**, quindi niente `read_text()` (e TOCTOU) a
> ogni richiesta autenticata. I test di questo blocco asseriscono il fix
> (`test_security_audit.py`). La sezione sotto descrive la falla originaria.

**Dove:** `backend/app/config.py:230-239`
```python
def resolved_secret(self) -> str:
    if self.secret_key:
        return self.secret_key
    key_file = self.data_dir / "secret.key"
    if key_file.exists():
        return key_file.read_text().strip()
    key = secrets.token_hex(32)
    self.data_dir.mkdir(parents=True, exist_ok=True)
    key_file.write_text(key)     # nessun chmod 0600, scrittura non atomica
    return key
```

**Problemi.**
1. **Permessi non ristretti:** `write_text` crea `data/secret.key` con i permessi
   dettati dall'umask (tipicamente `0644`). Su host multi-utente la **chiave HMAC
   di firma** e' leggibile da altri utenti locali -> **forgiatura di token** validi.
   Verificato (umask permissivo): file leggibile da gruppo/altri.
2. **File vuoto -> chiave vuota:** se `secret.key` esiste ma e' vuoto (write
   interrotto, disco pieno, race), `read_text().strip()` restituisce `""` e da quel
   momento i token vengono firmati/verificati con **chiave vuota** -> banalmente
   forgiabili. Verificato.
3. **Race web/worker:** in `docker-compose.yml` i servizi `web` e `worker` sono
   container separati che condividono il volume `appdata:/data` **senza** `SECRET_KEY`
   in env (default vuoto). Al primo avvio possono generare/leggere `secret.key`
   in concorrenza (chiavi divergenti o lettura di un file parzialmente scritto).
4. **Riletto da disco a ogni richiesta:** `require_auth` -> `resolved_secret()`
   fa una `read_text()` per **ogni** richiesta autenticata (I/O e TOCTOU inutili).

**Fix proposto.**
- *Quick win:* dopo la creazione, `os.chmod(key_file, 0o600)`; scrittura atomica
  (temp + `os.replace`); rifiutare/rigenerare se il file letto e' vuoto.
- *Strutturale:* generare la chiave **una sola volta** all'avvio (o esigere
  `SECRET_KEY` in produzione, gia' fatto in HF via secret) e tenerla in memoria;
  in compose impostare esplicitamente un `SECRET_KEY` condiviso.

**Test:** `test_vuln_empty_secret_key_file_signs_with_empty_key`,
`test_vuln_secret_key_file_written_world_readable` (verdi) +
`test_secure_secret_key_file_should_be_owner_only` (xfail).

---

## ~~MEDIO~~ RISOLTA #6 — Credenziali di default

> **STATO: RISOLTA.** Con `APP_ENV=prod` l'avvio viene **rifiutato**
> (`RuntimeError` in `main.py`) se `ADMIN_PASSWORD` e' ancora `changeme`; in dev
> resta il warning. Le credenziali Postgres del compose non sono piu' hardcoded:
> `POSTGRES_USER/PASSWORD/DB` sono **parametrizzate via env** (override
> consigliato da `.env` non committato; i default valgono solo per il primo
> avvio locale). La sezione sotto descrive la falla originaria.

**Dove:** `config.py:22` (`admin_password: str = "changeme"`), `.env.example:2`
(`ADMIN_PASSWORD=changeme`), `docker-compose.yml:23` (`ADMIN_PASSWORD:-changeme`),
`docker-compose.yml:5-7` (`POSTGRES_USER/PASSWORD/DB = editvideo` hardcoded).

**Problema.** Il default `changeme` e' un invito a esporre l'app senza cambiare la
password (c'e' un warning a runtime in `main.py:39-40`, ma non blocca l'avvio). Le
credenziali Postgres sono in chiaro nel compose e identiche a prodotto/utente.

**Fix proposto.** In produzione: **rifiutare l'avvio** (o disabilitare il login) se
`ADMIN_PASSWORD == "changeme"`; leggere le credenziali DB da secret/env non
committati; documentare la rotazione.

---

## ~~MEDIO~~ RISOLTA #7 — Dipendenze frontend (dev) vulnerabili

> **STATO: RISOLTA.** Toolchain dev aggiornata in `frontend/package.json`:
> `vite ^8.1.4`, `vitest ^4.1.10`, `@vitejs/plugin-react ^6.0.3` (esbuild e i
> transitivi `@vitest/mocker`/`vite-node` seguono) → `npm audit`:
> **0 vulnerabilita'**. La tabella sotto fotografa la situazione pre-fix.

**`npm audit` (frontend, con `package-lock.json`):** 5 vulnerabilita' — **1
critical, 1 high, 3 moderate**, tutte in tooling **dev-only**:

| Pacchetto | Sev | Nota |
|-----------|-----|------|
| `vitest` (<=3.2.5) | critical | Vitest UI server: lettura/esecuzione file arbitrari se in ascolto |
| `vite` (<=6.4.2) | high | path traversal `.map`, bypass `server.fs.deny`, ecc. |
| `esbuild` (<=0.24.2) | moderate | dev server accetta richieste da qualsiasi sito |
| `@vitest/mocker`, `vite-node` | moderate | transitive di vite/vitest |

**Impatto reale:** basso in produzione — l'artefatto deployato e' la **dist
statica** + backend Python; vite/vitest/esbuild non girano in prod. Contano solo
se il **dev server** o la **Vitest UI** vengono esposti in rete. Da aggiornare
comunque (supply chain / sicurezza in sviluppo).

**`pip-audit -r backend/requirements.txt`:** **nessuna vulnerabilita' nota**.
Versioni risolte in questo ambiente: fastapi 0.139.0, starlette 1.3.1,
pydantic 2.13.4, sqlalchemy 2.0.51, python-multipart 0.0.32.

**Nota pinning:** `backend/requirements.txt` usa range (`>=`), non pin esatti:
build riproducibili non garantite (una futura release vulnerabile entrerebbe
silenziosamente). Consigliato un lockfile (pip-tools/uv) per il backend.

---

## BASSO #8-10 (#8 e #10 RISOLTE)

- **#8 `/api/health` non autenticato** — **RISOLTA:** l'endpoint ora e' a
  risposta "graduata" (`jobs.py`): senza token valido risponde solo
  `{"ok": true}` (resta usabile come uptime-check, niente fingerprinting);
  il payload diagnostico completo (versione, modello whisper, lingua, presenza
  ffmpeg **e ffprobe**) e' riservato alle richieste autenticate. Test in
  `tests/test_health_endpoint.py`. *(Originale: esponeva versione app, modello
  whisper, lingua e presenza di ffmpeg a chiunque.)*
- **#9 CSP `frame-ancestors *`** (`security.py:40`): scelta deliberata per l'iframe
  HF, ma consente il **clickjacking** (chiunque puo' inquadrare l'app). Trade-off
  accettabile solo perche' l'app e' dietro auth; valutare un allowlist di ancestor
  (il dominio HF) invece di `*`.
- **#10 `size_bytes` `Integer`** — **RISOLTA:** colonna passata a `BigInteger`
  (`models.py`) con `ALTER TABLE videos ALTER COLUMN size_bytes TYPE BIGINT`
  additivo nella mini-migrazione (`db.py`); su SQLite e' un no-op (INTEGER e'
  gia' a 64 bit). *(Originale: su Postgres `INTEGER` e' a 32 bit, max ~2.147 GB,
  e il default `max_upload_mb=2048` = 2 GiB lo supera: overflow in scrittura al
  primo upload alla dimensione massima.)*

---

## Difese verificate che REGGONO (nessuna azione richiesta)

- **Nessuna shell injection.** Tutti i comandi esterni (`ffmpeg`, `ffprobe`) sono
  eseguiti come **lista di argomenti** con `subprocess.run/Popen`, **mai** con
  `shell=True` (`services/ffmpeg.py:22-23,226`, `services/silence.py:28`). Nessun
  `os.system`.
- **Filtergraph injection non sfruttabile.** I path che entrano nel
  `filter_complex` (`ass='...'`) sono **generati internamente** con UUID
  (`videos.py:71-72`: `dst = originals_dir / f"{video.id}{ext}"`; l'`.ass` e'
  `subs_dir / f"{vid}.ass"`), **non** derivano dal nome file utente
  (`original_name` e' usato solo come nome di download, sanificato a
  `videos.py:306`). In piu' `escape_filter_path` (`ffmpeg.py:71-75`) escapa `:` e
  `'`, e il testo dei sottotitoli e' neutralizzato da `_escape_text`
  (`styles.py:127-130`, che rimuove `{`/`}` e i tag override ASS).
- **Path traversal SPA bloccato.** La guardia `resolve()` + prefix-check
  (`main.py:90-93`) fa ricadere `/../../etc/passwd`, `%2e%2e`, `..%2f`, `....//`
  su `index.html`: **nessun** leak di `/etc/passwd`. Verificato con 4 encoding
  (`test_safe_spa_path_traversal_is_blocked`).
- **Schemi Pydantic rifiutano Inf/NaN** (`schemas.py`: `allow_inf_nan=False` su
  `CutRange`, `VideoPatch`, `SubtitleSegmentIn`, `TemplateIn`), impedendo che
  valori degeneri finiscano nella matematica della timeline.
- **Nessun segreto nella history git.** `git log`/`git grep` mirati: solo
  `.env.example` con `changeme`; `HF_TOKEN` e' un secret di GitHub Actions
  (`deploy.yml:58`), non committato. `.gitignore` esclude `data/`, `media/`, `.env`.
- **Nessun CORS permissivo.** In `main.py` non c'e' `CORSMiddleware`: nessun
  `Access-Control-Allow-Origin` -> le richieste cross-origin autenticate restano
  bloccate dalla same-origin policy del browser.
- **`pip-audit` pulito** sulle dipendenze backend.

---

## Metodo e riproducibilita'

- Test dimostrativi: `backend/tests/test_security_audit.py`
  (esegui: `cd backend && python -m pytest tests/test_security_audit.py -v`).
  Esito: **14 passed** (verdi = documentano la falla reale) + **5 xfailed**
  (rossi-per-design: asseriscono il comportamento sicuro atteso, oggi fallito ->
  proveranno il fix diventando `xpass`).
- Suite completa invariata: `cd backend && python -m pytest tests/ -q --ignore=tests/test_smoke.py`
  -> **183 passed, 5 xfailed** (i 5 xfailed sono i soli "rossi-per-design"
  dichiarati; nessuna regressione della suite preesistente, 169 -> 183 passed).
- Dipendenze: `pip-audit -r backend/requirements.txt` (pulito),
  `npm audit` in `frontend/` (5 vuln dev-only).
