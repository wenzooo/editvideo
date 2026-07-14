---
title: EditVideo
emoji: 🎬
colorFrom: blue
colorTo: indigo
sdk: docker
app_port: 8000
pinned: false
---

# EditVideo — batch editor per video verticali 9:16

Piattaforma self-hosted: upload multiplo, tagli, sottotitoli automatici in italiano
(faster-whisper), stili burn-in, export MP4 1080×1920 per TikTok/Reels/Shorts.

**Config obbligatoria:** in *Settings → Variables and secrets* aggiungi il secret
`ADMIN_PASSWORD` (password di accesso all'app).

**Nota (piano free):** lo storage dello Space è **effimero**: a ogni riavvio/pausa
si perdono video caricati, database ed export. Usalo come banco di lavoro
giornaliero: carica → sottotitola → correggi → esporta → **scarica gli MP4 in giornata**.
Lo Space va in pausa dopo ~48h di inattività: il primo accesso del giorno può
richiedere 1–2 minuti di avvio (+ download del modello Whisper alla prima trascrizione).
