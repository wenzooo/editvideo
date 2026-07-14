# EditVideo su Hugging Face Spaces (Docker SDK, CPU basic free: 2 vCPU / 16 GB)
# Nota Spaces: il container gira con utente uid 1000 e disco EFFIMERO.
FROM python:3.11-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

RUN useradd -m -u 1000 user
USER user
ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH \
    MEDIA_ROOT=/home/user/media \
    DATA_DIR=/home/user/data \
    HF_HOME=/home/user/.cache \
    FRONTEND_DIST=/app/frontend/dist \
    EMBEDDED_WORKER=1 \
    WHISPER_MODEL=medium \
    WHISPER_LANGUAGE=it \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY --chown=user requirements.txt .
RUN pip install --user --no-cache-dir -r requirements.txt

COPY --chown=user app ./app
COPY --chown=user frontend/dist ./frontend/dist

EXPOSE 8000
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
