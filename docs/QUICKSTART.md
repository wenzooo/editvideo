# Avvio rapido

Il modo più veloce per avere EditVideo in locale (Docker, identico al cloud):

```bash
cp .env.example .env        # imposta almeno ADMIN_PASSWORD
docker compose up -d --build
```

Apri **http://localhost:8000** ed entra con la password. Carica i video del giorno, scegli un **Format** (o lascia "— nessuno —"), lascia lavorare la coda, correggi i testi nei video *da controllare* e **scarica gli MP4** esportati. La prima trascrizione scarica il modello Whisper (~460 MB, una volta sola).

Senza Docker (sviluppo): `pip install -r backend/requirements.txt` e poi `python run.py` (serve `ffmpeg` nel PATH). Dettagli sviluppo in [`docs/SVILUPPO.md`](SVILUPPO.md), deploy in [`README.md`](../README.md), scorciatoie in [`docs/SCORCIATOIE.md`](SCORCIATOIE.md).
