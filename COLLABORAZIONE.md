# Collaborare su EditVideo

Guida rapida per lavorare in due (o più) sul progetto. Tre pezzi distinti:

- **Codice → GitHub** (`github.com/BrushRLX/editorvideo`): dove si scrive e si versiona il codice.
- **Pubblicazione → Hugging Face** (Space `brushk/editvideo`): dove l'app viene messa online.
- **Uso dell'app → il browser**: gli utenti aprono lo Space e lavorano lì.

GitHub **non** pubblica l'app: tiene solo il codice. La pubblicazione è un passo separato (vedi §3).

---

## 1. Setup iniziale (una volta per persona)

**Chi possiede il repo** (già fatto): repo privato creato, codice caricato, collaboratori invitati da
GitHub → *Settings → Collaborators → Add people*.

**La collaboratrice** (dopo aver accettato l'invito):

```bash
git clone https://github.com/BrushRLX/editorvideo.git
cd editorvideo
```

Per lavorare in locale servono:
- **Node.js** (per il frontend): `cd frontend && npm install`
- **Python 3.11** (per il backend/test): `cd backend && pip install -r ../requirements.txt`

---

## 2. Il giro di collaborazione (loop quotidiano)

1. Aggiornati con l'ultima versione:
   ```bash
   git pull
   ```
2. Crea un ramo per la tua modifica:
   ```bash
   git checkout -b nome-modifica
   ```
3. Lavora, poi salva e carica:
   ```bash
   git add -A
   git commit -m "descrizione della modifica"
   git push -u origin nome-modifica
   ```
4. Su GitHub apri una **Pull Request** dal tuo ramo verso `master`.
5. Chi possiede il repo **revisiona** e fa **Merge**.
6. Tutti tornano allineati con `git pull` sul proprio `master`.

Regola d'oro: non lavorare direttamente su `master`; usa sempre un ramo + PR. Così si evita di
pestarsi i piedi e si può revisionare prima di unire.

---

## 3. Build, test e pubblicazione (deploy)

> **Deploy AUTOMATICO.** Ad ogni merge/push su `master`, la GitHub Action
> `.github/workflows/deploy.yml` builda e testa il frontend e — se è verde —
> pubblica da sola sullo Space. Non serve fare nulla a mano. La vedi nel tab
> **Actions** del repo (o la lanci manualmente da lì). Richiede il secret di
> repo **`HF_TOKEN`** (token Hugging Face con permesso *Write* sullo Space),
> impostato una volta in *Settings → Secrets and variables → Actions*.
>
> I passi manuali qui sotto restano validi come alternativa/fallback.

Prima di pubblicare (o di aprire una PR), verifica che tutto sia verde:

```bash
# frontend
cd frontend
npm run build          # deve compilare senza errori
npx vitest run         # test frontend

# backend
cd ../backend
python -m pytest tests/ -q --ignore=tests/test_smoke.py
```

**Pubblicare sullo Space Hugging Face** (serve un account HF con accesso allo Space
`brushk/editvideo` — aggiungibile da HF → Space → *Settings → Collaborators*):

1. Rigenera la build del frontend (`npm run build`).
2. Allinea la cartella di deploy: `scripts/deploy-sync.ps1`.
3. Carica sullo Space (login HF già fatto con `huggingface-cli login`):
   ```
   huggingface-cli upload brushk/editvideo deploy/hf-space . --repo-type=space
   ```
   (oppure lo script/flusso `upload_folder` già in uso).

Il numero di versione si alza **solo** con `scripts/bump-version.ps1` (tiene allineati
backend e frontend). Vedi `docs/REGOLE-ANTI-REGRESSIONE.md` per la checklist completa.

> In pratica: **codice su GitHub → build/test → upload allo Space HF**. Sono due account/servizi
> diversi. Il deploy può restare in mano a una persona sola: le altre mandano solo Pull Request.

---

## 4. Dove usano l'app gli utenti

L'app gira sullo Space, nel browser:

**https://huggingface.co/spaces/brushk/editvideo**

Si apre, si fa login con la password, si caricano i video, si edita, si esporta. Nessuna
installazione.

Da tenere presente:
- Lo Space è **privato**: per aprirlo bisogna essere collaboratori HF (oppure renderlo pubblico —
  resta comunque protetto dalla password di login).
- **Login unico e condiviso** (una sola password): chi entra vede e modifica lo **stesso** spazio di
  lavoro. Non sono account separati.
- **Disco effimero**: se lo Space si riavvia, i video caricati e il database si azzerano. È uno
  strumento di lavoro, non un archivio.

---

## Comandi rapidi

| Cosa | Comando |
|------|---------|
| Aggiornarsi | `git pull` |
| Nuovo ramo | `git checkout -b nome-modifica` |
| Salvare e caricare | `git add -A && git commit -m "..." && git push` |
| Build frontend | `cd frontend && npm run build` |
| Test frontend | `npx vitest run` |
| Test backend | `cd backend && python -m pytest tests/ -q --ignore=tests/test_smoke.py` |
| Allineare deploy | `scripts/deploy-sync.ps1` |
