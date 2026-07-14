# Regole anti-regressione

Checklist operativa per **non rompere un deploy funzionante**. Tono asciutto:
sono cose da fare, in ordine, ogni volta. Se hai fretta e salti un passaggio,
salta i punti 3–5, mai il punto 1.

Contesto: mono-utente, deploy su Hugging Face Space **privato**
`brushk/editvideo` (piano free), versione unica in `backend/app/version.py` e
`frontend/src/version.ts`. Il repo git è locale (tag `v1.0.0`), **senza remote**:
i tuoi punti di ripristino sono i commit/tag locali e gli zip su Desktop.

---

## 1. Regola d'oro: punto di ripristino PRIMA di toccare qualsiasi cosa

Prima di **qualsiasi** modifica devi avere due reti di sicurezza:

1. **Working tree git pulito** e un commit di partenza noto.
   ```powershell
   cd C:\Users\brsan\Desktop\EditVideo
   git status          # deve essere "nothing to commit, working tree clean"
   git log --oneline -1 # annota da dove parti (es. tag v1.0.0)
   ```
   Se ci sono modifiche pendenti: committale o mettile da parte (`git stash`)
   prima di iniziare qualcosa di nuovo. Non lavorare mai sopra a un working tree
   sporco di cui non sai lo stato.

2. **Backup zip su Desktop**, che esclude le cartelle pesanti/rigenerabili.
   Nome: `EditVideo_backup_v<versione>_<data>.zip`.
   ```powershell
   $ver   = "1.0.0"                      # = APP_VERSION corrente in version.py
   $data  = Get-Date -Format "yyyy-MM-dd"
   $src   = "C:\Users\brsan\Desktop\EditVideo"
   $stage = "$env:TEMP\EditVideo_stage"
   $zip   = "$env:USERPROFILE\Desktop\EditVideo_backup_v${ver}_${data}.zip"

   robocopy $src $stage /E /XD node_modules media data __pycache__ .pytest_cache .git | Out-Null
   Compress-Archive -Path "$stage\*" -DestinationPath $zip -Force
   Remove-Item $stage -Recurse -Force
   ```
   Escludi sempre: `node_modules`, `media`, `data`, `__pycache__`,
   `.pytest_cache`, `.git`.

> Con questi due punti fatti, qualsiasi cosa vada storta si torna indietro in un
> minuto (vedi §6). Senza, no.

---

## 2. Versione: solo con lo script, mai a mano

- **Non editare mai a mano** `backend/app/version.py` o
  `frontend/src/version.ts`. Se li tocchi a mano, backend e frontend vanno in
  drift e la UI/health mostrano versioni diverse.
- Usa **solo** `scripts/bump-version.ps1`, che allinea entrambi i file in un
  colpo solo:
  ```powershell
  .\scripts\bump-version.ps1 -Part patch          # 1.0.0 -> 1.0.1 (solo file)
  .\scripts\bump-version.ps1 -Part minor          # 1.0.0 -> 1.1.0
  .\scripts\bump-version.ps1 -Version 2.0.0 -Tag  # imposta 2.0.0, committa e tagga v2.0.0
  ```
  Flag: `-Commit` crea il commit `vX.Y.Z`; `-Tag` crea anche il tag annotato
  (e implica `-Commit`).
- **Semver**, per decidere quale parte incrementare:
  - `patch` → correzioni di bug, nessun cambio di comportamento visibile.
  - `minor` → nuove funzioni **retro-compatibili**.
  - `major` → modifiche **breaking** (API, formato dati, comportamento).
- Dopo il bump la versione **non è ancora nel deploy**: serve rebuild del
  frontend (§3) e redeploy (§4).

---

## 3. Prima del deploy — checklist pre-volo

Da fare in locale, in quest'ordine. Se un passo fallisce, **ci si ferma**.

- [ ] **Build frontend senza errori.** I sorgenti in `frontend/src` NON bastano:
      il server serve `frontend/dist`, quindi la build va **rifatta**.
      ```powershell
      cd C:\Users\brsan\Desktop\EditVideo\frontend
      npm run build          # deve finire senza errori -> rigenera frontend/dist
      ```
- [ ] **Test backend verdi** se hai toccato il backend.
      ```powershell
      cd C:\Users\brsan\Desktop\EditVideo\backend
      pytest                 # test_units.py (funzioni pure) + test_smoke.py (end-to-end con ffmpeg)
      ```
      `test_units.py` copre timeline/captions/styles; `test_smoke.py` fa il giro
      reale upload→cuts→subs→export e richiede `ffmpeg` installato.
- [ ] **L'app parte in locale.**
      ```powershell
      cd C:\Users\brsan\Desktop\EditVideo
      python run.py          # API + worker embedded; apri il browser e fai login
      ```
- [ ] **`dist` rigenerata e committata.** La build è committata di proposito
      (il server gira senza Node): se hai fatto `npm run build`, aggiungi e
      committa `frontend/dist`.
      ```powershell
      git add frontend/dist
      git status             # verifica che dist aggiornata sia in stage
      git commit -m "build frontend"
      ```

---

## 4. Sincronizzazione deploy (Hugging Face)

I file dello Space stanno in `deploy/hf-space/` e vanno **allineati al codice
canonico** prima del redeploy:

- [ ] Allinea `deploy/hf-space/` a `backend/app` + `frontend/dist` (il codice
      buono è quello canonico nella root, non la copia nello Space).
- [ ] **DRIFT NOTO da correggere a ogni deploy:**
      `deploy/hf-space/app/main.py` ha la versione **hardcoded** e **non**
      importa da `version.py`. Oggi vale:
      ```python
      app = FastAPI(title="EditVideo", version="0.2.0", lifespan=lifespan)
      ```
      Va riportata alla versione reale (`APP_VERSION` corrente), altrimenti
      l'health dello Space riporterà una versione sbagliata. Fix minimo: allinea
      la stringa. Fix definitivo: importare da `version.py` come fa il backend
      canonico.
- [ ] Verifica che `SECRET_KEY` sia impostato come **secret** dello Space (serve
      per sessioni/login persistenti tra i riavvii).

---

## 5. Checklist post-deploy

Dopo il redeploy, verifica che sia davvero tutto su:

- [ ] Lo Space torna in stato **"Running"** (non "Building"/"Error").
- [ ] **Login** funziona (l'auth a token gira anche nell'iframe HF).
- [ ] L'endpoint **health** riporta la **versione attesa** (quella del bump, non
      la 0.2.0 hardcoded — se vedi la versione vecchia, hai saltato il §4).
- [ ] Un **video di prova gira end-to-end**: upload → trascrizione → export →
      download dell'MP4.

Se uno di questi fallisce, non "aggiusto in produzione": rollback (§6) e si
riparte da un punto pulito.

---

## 6. Rollback — come tornare indietro

In ordine di preferenza:

1. **Torna al tag/commit buono con git** (working tree pulito richiesto).
   ```powershell
   git checkout v1.0.0          # stato esatto della 1.0.0 (detached HEAD)
   ```
   Per ripartire a lavorare da lì crea un branch: `git switch -c ripristino`.

2. **Annulla un commit specifico senza riscrivere la storia.**
   ```powershell
   git revert <hash-del-commit-che-ha-rotto>
   ```
   `revert` è più sicuro di `reset --hard` perché non butta via nulla.

3. **Se git non basta** (o hai perso il repo), **ripristina dallo zip di
   backup** su Desktop: scompatta `EditVideo_backup_v<versione>_<data>.zip` in
   una cartella pulita e riparti da lì. Ricorda che lo zip **non** contiene
   `media/`, `data/`, `node_modules/`, `.git`: reinstalla le dipendenze e
   rifai la build.

Dopo qualsiasi rollback, ripeti §3–§5 prima di ri-deployare.

---

## 7. Cosa NON committare mai

Già coperto da `.gitignore`, ma tienilo a mente: se compaiono nel `git status`,
qualcosa è sbagliato.

- `media/` — file caricati, thumbnail, export (runtime, pesante).
- `data/` — DB SQLite di dev, `secret.key`.
- `.env` — segreti e configurazione locale.
- `node_modules/` — dipendenze frontend (si rigenerano con `npm install`).
- `__pycache__/`, `.pytest_cache/` — cache Python.
- Gli `.mp4` sparsi in root e gli zip di backup.

**Eccezione voluta:** `frontend/dist/` **va committata** (il server la serve
senza Node). Vedi §3.

---

## 8. Caveat Hugging Face Spaces (e implicazioni operative)

Il free tier ha due limiti che vanno gestiti, non subiti:

- **Disco effimero.** Lo storage dello Space non è persistente: gli export e i
  file caricati **non sopravvivono** a un riavvio/rebuild.
  → **Scarica gli MP4 in giornata**; non usare lo Space come archivio. Il DB
  SQLite e i media sono usa-e-getta lato cloud.
- **Sleep dopo ~48h di inattività.** Lo Space va in pausa se non lo usi.
  → Al primo accesso dopo lo sleep serve qualche secondo per il risveglio; è
  normale, non è un errore. Non confondere "in risveglio" con "rotto".
- Piano free **2 vCPU / 16 GB**: sufficiente per la pipeline CPU-only, ma il
  worker gira in concorrenza 1 — i batch grossi si smaltiscono in coda, non in
  parallelo.

Quando serviranno **disco persistente** e assenza di sleep, il percorso già
documentato (README) è Oracle Free ARM o un piccolo VPS: stessa immagine
Docker, nessuna riscrittura.
