# Sviluppo — guida rapida

Come mettere in piedi EditVideo in locale, buildare, testare e bumpare la
versione. Tono asciutto: i comandi essenziali, in ordine.

Per il processo da seguire **prima di un deploy** (punti di ripristino, checklist
pre-volo, sincronizzazione Hugging Face, rollback) il riferimento resta
**[docs/REGOLE-ANTI-REGRESSIONE.md](REGOLE-ANTI-REGRESSIONE.md)**: questa pagina
serve solo a lavorare in locale, non sostituisce quel processo.

---

## 1. Prerequisiti

| Strumento | Versione | Note |
|---|---|---|
| **Node.js** | 18+ (LTS) | serve `npm` per installare e buildare il frontend |
| **Python** | **3.11** | il backend gira su 3.11; i test usano l'interprete 3.11 dedicato |
| **ffmpeg** | recente, nel `PATH` | usato da `python run.py` e dall'export; su Windows: `winget install Gyan.FFmpeg` (poi riapri il terminale) |

Verifica veloce:

```powershell
node --version
python --version        # atteso: Python 3.11.x
ffmpeg -version
```

---

## 2. Task runner (il modo consigliato)

Quasi tutte le operazioni ricorrenti sono incapsulate in **`scripts/tasks.ps1`**,
che richiama i comandi canonici del progetto (non li reinventa). Dalla root del
repo:

```powershell
cd C:\Users\brsan\Desktop\EditVideo
.\scripts\tasks.ps1 -Task help
```

| Task | Cosa fa | Comando incapsulato |
|---|---|---|
| `install` | Installa le dipendenze del frontend | `npm install` in `frontend/` |
| `dev` | Avvia l'app in locale (API + worker embedded) | `python run.py` dalla root |
| `build` | Ricostruisce `frontend/dist` | `npm run build` in `frontend/` |
| `test` | Test backend delle funzioni pure e degli schemi | `pytest tests/test_units.py tests/test_validation.py` (Python 3.11) |
| `verify` | Check pre-deploy rapido | `build` + `test` in sequenza |
| `bump` | Cambia la versione unica | inoltra i flag a `scripts/bump-version.ps1` |
| `help` | Stampa l'elenco dei task | — |

Senza `-Task` (o con un task sconosciuto) lo script stampa l'aiuto.

> Il task runner è **additivo**: puoi sempre lanciare i comandi a mano (sezioni
> sotto). Non c'è nessun hook che blocca commit o build.

---

## 3. Installazione

```powershell
# dipendenze frontend
.\scripts\tasks.ps1 -Task install
#   equivale a:  cd frontend; npm install

# dipendenze backend (una tantum, nell'ambiente Python 3.11)
cd backend
pip install -r requirements.txt
```

---

## 4. Avvio in locale

```powershell
.\scripts\tasks.ps1 -Task dev
#   equivale a:  python run.py
```

Serve API + worker embedded su <http://localhost:8000>. Richiede `ffmpeg` nel
`PATH` (lo script `run.py` si ferma con un messaggio chiaro se non lo trova).
Apri il browser, fai login con la password configurata e usa la dashboard.

In alternativa, per lavorare sul frontend con hot-reload (Vite + proxy):

```powershell
cd frontend
npm run dev
```

---

## 5. Build del frontend

Il server serve `frontend/dist`, **non** i sorgenti: dopo ogni modifica al
frontend la build va rifatta.

```powershell
.\scripts\tasks.ps1 -Task build
#   equivale a:  cd frontend; npm run build   (tsc && vite build)
```

La build deve finire **senza errori**. `frontend/dist` è versionata di proposito
(il server gira senza Node) — vedi REGOLE-ANTI-REGRESSIONE §3.

---

## 6. Test

```powershell
.\scripts\tasks.ps1 -Task test
```

Esegue `test_units.py` (timeline, captions, styles) e `test_validation.py`
(casi limite delle funzioni pure + validazione degli schemi Pydantic). Sono test
**offline**: non richiedono ffmpeg né whisper.

Dettaglio di cosa fa il task, se ti serve lanciarlo a mano:

```powershell
cd backend
$env:PYTHONHOME = ''    # azzerato: evita che punti ad un'altra installazione
& "C:\Users\brsan\AppData\Local\Programs\Python\Python311\python.exe" -m pytest tests/test_units.py tests/test_validation.py
```

> `test_smoke.py` (giro end-to-end reale con ffmpeg) **non** è incluso nel task:
> lanciatelo a parte quando serve la verifica completa della pipeline.

---

## 7. Bump versione

La versione è **unica** e vive in due file (`backend/app/version.py` e
`frontend/src/version.ts`). Si cambia **solo** con lo script dedicato, mai a mano
(altrimenti backend e frontend vanno in drift).

```powershell
.\scripts\tasks.ps1 -Task bump -Part patch          # 1.0.0 -> 1.0.1 (solo file)
.\scripts\tasks.ps1 -Task bump -Part minor          # 1.0.0 -> 1.1.0
.\scripts\tasks.ps1 -Task bump -Version 2.0.0 -Tag  # imposta 2.0.0, commit + tag
```

I flag vengono inoltrati tali e quali a `scripts/bump-version.ps1`:
`-Part major|minor|patch`, `-Version X.Y.Z`, `-Commit`, `-Tag`. Semver: `patch`
= bugfix, `minor` = funzioni retro-compatibili, `major` = breaking. Dopo il bump
la versione **non è ancora nel deploy**: serve rebuild del frontend e redeploy.

---

## 8. Prima di un deploy

Il check rapido locale è:

```powershell
.\scripts\tasks.ps1 -Task verify   # build frontend + test backend
```

Ma **non basta**: il processo completo (punto di ripristino prima di toccare
qualsiasi cosa, checklist pre-volo, allineamento di `deploy/hf-space/`, verifica
post-deploy, rollback) è in
**[docs/REGOLE-ANTI-REGRESSIONE.md](REGOLE-ANTI-REGRESSIONE.md)**. Seguilo ogni
volta: `verify` è solo il primo dei suoi passi.

---

## 9. Convenzioni dell'editor (opzionali)

- **`.editorconfig`** — encoding UTF-8, fine riga LF (CRLF per gli `.ps1`),
  newline finale, 2 spazi per frontend/config, 4 per Python. Lo leggono gli
  editor che supportano EditorConfig; non c'è nulla da installare.
- **`.gitattributes`** — normalizza gli EOL nel repo e marca i binari (mp4, png,
  wav, ico, ...) come binary.
- **Prettier** — c'è un `.prettierrc.json` minimale con un `.prettierignore`, per
  chi vuole formattare il frontend a mano (`npx prettier --write frontend/src`).
  È **opzionale**: non è in `package.json` né in nessuna build, non blocca niente.
