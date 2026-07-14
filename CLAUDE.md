# CLAUDE.md — EditVideo

Batch editor per video verticali 9:16: upload, taglia-silenzi automatico, sottotitoli
Whisper, karaoke, export MP4. Coda job asincrona; deploy come Hugging Face Space (Docker).
Backend FastAPI + SQLAlchemy; frontend React/Vite SPA servita statica dal backend.

## Architettura
- Frontend SPA (`frontend/src`, React+Router) → chiama l'API FastAPI (`backend/app/main.py`).
- Router (`backend/app/routers/`): `videos`, `jobs`, `subtitles`, `batch`, `templates` (+ `auth`).
- Coda = tabella `jobs` (`models.py`); `worker.py` fa claim ottimistico (`UPDATE ... WHERE status='queued'`),
  gira embedded (thread) in dev o come processo dedicato (`python -m app.worker`) in compose.
- Services (`backend/app/services/`): `ffmpeg` (probe/encode), `silence`, `retakes`, `captions`,
  `styles` (build ASS), `transcribe` (faster-whisper), `timeline`, `formats`.
- Funzioni pure (testabili senza env/ffmpeg): `timeline.py`, `captions.py`, `retakes.py`, `styles.py`.
- DB: SQLite (default, in `data/app.db`) o Postgres via `DATABASE_URL`; `db.py` + `models.py`.
- Config centralizzata: `config.py` (pydantic-settings, tutto da env/`.env`).
- Auth: token HMAC firmato (`auth.py`) + header di sicurezza e rate-limit login (`security.py`).

## Comandi
- Install: frontend `cd frontend && npm ci`; backend `pip install -r backend/requirements.txt pytest httpx`.
- Dev: backend `python run.py` (→ http://localhost:8000, richiede ffmpeg nel PATH); frontend `cd frontend && npm run dev`.
- Build frontend: `cd frontend && npm run build` (= `tsc && vite build`; output in `frontend/dist`, versionato).
- Typecheck frontend: `cd frontend && npx tsc --noEmit`.
- Test frontend: `cd frontend && npx vitest run` (jsdom + testing-library).
- Test backend (veloce, no ffmpeg): `cd backend && python -m pytest tests/ -q --ignore=tests/test_smoke.py`.
- Smoke E2E (isolato, richiede ffmpeg): `cd backend && python -m pytest tests/test_smoke.py -q`.

## Convenzioni
- Frontend: Prettier (`.prettierrc.json`: printWidth 100, 2 spazi, semi, double-quote, trailing all);
  componenti in `src/components`, pagine in `src/pages`, chiamate API in `src/api.ts`.
- Backend: docstring e commenti in italiano; ogni percorso su disco passa da `config.py`.
- Test pytest degli endpoint: impostare gli env (`os.environ.setdefault("ADMIN_PASSWORD"/"SECRET_KEY"/
  "DATA_DIR"/"MEDIA_ROOT"/"DATABASE_URL"/"EMBEDDED_WORKER"...)`) PRIMA di importare l'app, perché
  `get_settings()` è `@lru_cache`. I service puri si testano senza env.
- Test frontend: file `*.test.ts(x)` accanto al sorgente; setup in `src/setupTests.ts`.

## Trappole note
- Dipendenze native: `ffmpeg`/`ffprobe` devono stare nel PATH (richiesto anche da `run.py`,
  che esce se ffmpeg manca).
- `get_settings()` è cache-ata (`@lru_cache`): in una suite pytest completa gli env impostati tardi
  vengono ignorati. Perciò `test_smoke.py` va eseguito ISOLATO (processo pytest separato): dentro la
  suite intera fallirebbe con 401. In CI è un job a parte.
- Colonne JSON `cuts`/`speedups`/`words` (`models.py`) non usano MutableList: per modificarle
  RIASSEGNARE l'intera lista (`video.cuts = [...]`), non fare mutazioni in-place.
- `frontend/dist` è versionato di proposito (il backend serve la SPA prebuildata; lo Space gira senza Node).
  Non toglierlo dal tracking senza conferma.
- `deploy/hf-space/` contiene una copia di `backend/app` + `frontend/dist` per lo Space; è riallineata
  dalla CI di deploy (`.github/workflows/deploy.yml`, push su master → upload HF). Non modificarla a mano.
- Disco effimero su HF: al riavvio dello Space media e DB si azzerano.
