# EditVideo — il tuo progetto completo

Questo archivio contiene **tutto il sorgente** di EditVideo (backend FastAPI, frontend
React/Vite, test, CI, deploy), non solo l'artefatto deployato dello Space. Da qui puoi
continuare lo sviluppo in autonomia e ospitarlo dove vuoi.

## Cosa c'è dentro
- `backend/` — API FastAPI + worker + services + **test** (`backend/tests/`)
- `frontend/` — SPA React/Vite (`frontend/src/`) + `dist/` già buildata
- `deploy/hf-space/` — copia pronta per Hugging Face Space (Docker)
- `.github/workflows/` — CI (`ci.yml`) e deploy (`deploy.yml`)
- `docs/`, `CLAUDE.md`, `SCALING_REPORT.md`, `SECURITY_REPORT.md`, `TEST_REPORT.md`,
  `OPTIMIZATION_REPORT.md`
- `_pending-patches/` — due miglioramenti pronti da applicare (vedi sotto)
- `.git/` — **storia completa** dei commit

## Farlo girare in locale
Serve `ffmpeg` nel PATH, Python 3.11, Node 20.
```bash
# backend
pip install -r backend/requirements.txt pytest httpx
python run.py            # -> http://localhost:8000

# frontend (in un altro terminale)
cd frontend && npm ci && npm run dev
```
Test: `cd backend && python -m pytest tests/ -q --ignore=tests/test_smoke.py`
e `cd frontend && npx vitest run`.

## Renderlo TUO su GitHub
La storia git qui punta ancora al repo originale. Per averlo sul tuo account:
```bash
# 1) crea un repo VUOTO sul tuo GitHub (es. wenzooo/editvideo), senza README
# 2) ri-punta il remote e pusha
git remote set-url origin https://github.com/<TUO-UTENTE>/editvideo.git
git push -u origin master
```
(Se preferisci ripartire senza storia: cancella la cartella `.git`, poi
`git init && git add -A && git commit -m "EditVideo" && git remote add origin ... && git push`.)

## Deployarlo sul TUO Hugging Face
Lo Space è **Docker**, e sul piano free HF ora richiede **PRO** (~9$/mese) per gli Space
Docker/Gradio. Opzioni:
- **HF PRO**: attivi PRO, crei uno Space Docker `<tuo-utente>/editvideo` e carichi il
  contenuto di `deploy/hf-space/`.
- **VPS/Docker** (spesso più adatto a un editor video, storage persistente): c'è già
  `Dockerfile` e `docker-compose.yml` nella root — `docker compose up` e sei online con
  Postgres e disco persistente (niente più dati che si azzerano ai riavvii come su HF free).
- **Render/Railway/Fly.io**: build da `Dockerfile`, impostando i secret (sotto).

### Secret/variabili minime (Settings del deploy)
- `ADMIN_PASSWORD` — password di accesso all'app (obbligatoria)
- `SECRET_KEY` — stringa casuale lunga (se vuota viene generata; su disco effimero
  conviene fissarla per non invalidare le sessioni ai riavvii)
- `APP_ENV=prod` — rifiuta l'avvio se `ADMIN_PASSWORD` è ancora `changeme`
- Opzionali: `DATABASE_URL` (Postgres), `WHISPER_MODEL`, `MAX_UPLOAD_MB`, ecc.
  (elenco completo in `.env.example`)

## Miglioramenti pronti (`_pending-patches/`)
Due batch di ottimizzazioni/fix già scritti e testati (suite verde) ma non ancora nel
master del repo originale. Per applicarli:
```bash
git apply _pending-patches/perf-quick-wins-1.patch     # performance (backend+frontend)
git apply _pending-patches/fix-section9.patch          # decisioni §9 (sottotitoli, status, format)
cd frontend && npm run build                           # rigenera dist
```
Dettagli in `OPTIMIZATION_REPORT.md` (§1 fatto, §2 rimandato) e nei commenti delle patch.
