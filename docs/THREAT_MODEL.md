# THREAT_MODEL — EditVideo

Modello di minaccia sintetico del backend FastAPI (`backend/app/`), del frontend
React/Vite (`frontend/`) e del deploy su Hugging Face Space. Complementare
all'audit dettagliato in [`../SECURITY_REPORT.md`](../SECURITY_REPORT.md): qui la
vista d'insieme (attori → asset → superfici → mitigazioni → residui); là le
singole vulnerabilità con test e fix.

**Contesto.** App **mono-utente** (una sola password `ADMIN_PASSWORD` + token
HMAC-SHA256 con scadenza), **single-node**, deployata su uno **Space privato**
dietro l'**iframe** di Hugging Face, con **disco effimero** (un riavvio azzera
media e DB). Comandi esterni (`ffmpeg`/`ffprobe`) eseguiti come **lista di
argomenti**, mai con `shell=True`.

---

## Attori (chi)

- **Anonimo su Internet** — trova l'URL dello Space (privato ma indirizzabile); nessuna credenziale.
- **Bot / scanner automatico** — brute-force sul login, fuzzing degli endpoint, upload/DoS.
- **Utente legittimo abusante o credenziale trapelata** — la password unica è condivisa: chi la ottiene ha pieno accesso (upload, batch, export, delete).
- **Sito ostile che inquadra l'app** — l'iframe è aperto (`frame-ancestors *`): rischio clickjacking.
- **Vicino sull'host** (solo self-host multi-utente) — altro utente locale che prova a leggere `secret.key`/DB.
- **Rete/intermediari** — intercettazione se non su HTTPS; header `X-Forwarded-*` spoofabili.

## Asset (cosa proteggere)

- **Media** — originali caricati (non rigenerabili) ed export MP4 (rigenerabili). Riservatezza + disponibilità.
- **Database** — metadati video, coda `jobs`, sottotitoli, template. Integrità + disponibilità.
- **`data/secret.key`** — chiave di firma HMAC: chi la legge **forgia token** validi. Riservatezza massima.
- **Token di sessione** — Bearer in `localStorage` + `?t=` sui media: se trapela, accesso pieno fino a scadenza.
- **`ADMIN_PASSWORD`** — unica barriera d'accesso.
- **Risorse del nodo** — CPU (whisper/ffmpeg costosi) e **disco effimero** (limitato): bersaglio DoS.

## Superfici di attacco (dove)

- **Login** `POST /api/auth/login` — non autenticato: brute-force, DoS di memoria via body enorme.
- **Upload** `POST /api/videos/upload` — autenticato: DoS disco (file enormi / troppi file), doppia scrittura.
- **Endpoint mutanti / batch** `/api/batch/*`, `PATCH /api/videos/{id}` — enqueue di massa (flood costoso).
- **Health / metrics** `/api/health`, `/api/metrics` — information disclosure / fingerprinting.
- **Media & download** `?t=<token>` — token completo in URL (log/Referer/history).
- **SPA catch-all** `GET /{path}` — path traversal verso il filesystem.
- **Iframe HF** — clickjacking, cookie di terze parti bloccati (da cui il token in `localStorage`).
- **Pipeline ffmpeg** — filtergraph/shell injection tramite path o testo dei sottotitoli.
- **Dipendenze** — supply chain (frontend dev + backend runtime).

## Mitigazioni presenti

| Superficie | Mitigazione | Dove |
|---|---|---|
| Login brute-force | Rate limit per-IP **+ globale**; confronto su bytes (constant-time); avvio rifiutato con `changeme` in prod | `security.py`, `auth.py`, `main.py` |
| DoS memoria (body login) | Tetto `max_json_body_kb` sul body delle route non-upload, verificato sul `Content-Length` | `main.py` (`request_size_guard`) |
| DoS disco (upload) | **413** sul `Content-Length` **prima** di leggere il body (**411** se assente); tetto `MAX_UPLOAD_FILES` e `resolved_max_request_bytes()` | `main.py`, `routers/videos.py` |
| Flood enqueue di massa | Rate limit **globale** a chiave costante su `/api/batch` (non aggirabile via XFF), 429 + `Retry-After` | `routers/batch.py` |
| Fingerprinting health | `/api/health` graduato: `{"ok": true}` anonimo, diagnostica solo autenticato | `routers/jobs.py` |
| Forgiatura token | `secret.key` a **0600**, scrittura atomica, file vuoto rigenerato, chiave in memoria | `config.py` |
| Furto token/sessione | Cookie `Secure`+`HttpOnly`+`SameSite=Lax`; **logout revoca** (generazione firmata); token stateless con scadenza | `auth.py` |
| Path traversal SPA | `resolve()` + prefix-check → ricade su `index.html` | `main.py` (`spa`) |
| Injection ffmpeg | Argomenti come lista (no `shell=True`); path interni con UUID; escape del filtergraph e del testo ASS | `services/ffmpeg.py`, `services/styles.py` |
| Input degenere | Pydantic `allow_inf_nan=False` sugli schemi numerici | `schemas.py` |
| Header di sicurezza | CSP/nosniff/referrer-policy su ogni risposta (anche 411/413) | `security.py`, `main.py` |
| Overflow dimensione | `size_bytes` in `BigInteger` (no overflow Postgres su file ≥ 2 GiB) | `models.py`, `db.py` |
| Resilienza/DoS applicativo | Retry+circuit-breaker whisper, degradazione graziosa, recovery job al riavvio, watchdog ffmpeg | `worker.py`, `services/resilience.py` |
| Correlazione/forense | Request ID nei log; logging strutturato; `/api/metrics` per anomalie di coda | `main.py`, `services/metrics.py` |
| Supply chain | `npm audit` pulito (vite 8/vitest 4), `pip-audit` pulito; CI con ruff/eslint/tsc | `frontend/package.json`, `.github/workflows/ci.yml` |

## Residui accettati (rischi consapevoli)

- **CSP `frame-ancestors *`** — l'app è inquadrabile da qualsiasi sito → **clickjacking**. Accettato: serve per l'iframe HF e l'app è comunque dietro auth. Valutabile un allowlist del dominio HF (`SECURITY_REPORT` #9).
- **Token completo in `?t=`** sui media — finisce in access log/history/Referer. Mitigato dalla revoca al logout; la soluzione strutturale (URL firmati a scadenza breve) è lavoro a parte (`SECURITY_REPORT` #3).
- **Password unica condivisa** — nessun multi-utenza/RBAC: chi ha la password ha tutto. Coerente con un'app personale mono-utente.
- **`X-Forwarded-For` spoofabile** dietro il proxy HF — mitigato dai contatori **globali** (login e batch) invece del pinning al peer del proxy.
- **Disco effimero** — media e DB si azzerano al riavvio: **non è un backup**. Contromisura operativa: scaricare gli export in giornata (README); per persistenza reale usare Docker/VPS con volume.
- **DoS autenticato residuo** — un client autenticato può comunque saturare CPU con lavoro legittimo; frenato (rate limit batch/upload, `WORKER_CONCURRENCY`) ma non eliminato su single-node.
- **`SECRET_KEY` non impostata su HF** — se assente, le sessioni non sopravvivono al riavvio (usabilità, non falla); impostarla come secret dello Space.

## Fuori scope

Attacchi fisici all'infrastruttura HF, compromissione della piattaforma HF stessa,
attacchi DDoS volumetrici di rete (competenza del provider), sicurezza di rete
multi-tenant (l'app è single-node). Il modello si concentra sul livello
applicativo, che è ciò su cui EditVideo può realmente agire.
