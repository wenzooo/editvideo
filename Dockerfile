# Immagine unica per web e worker (comando diverso in docker-compose).
# Compatibile x86_64 e ARM64 (Oracle Free Ampere).
FROM python:3.11-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/app ./app
COPY frontend/dist ./frontend/dist

ENV FRONTEND_DIST=/app/frontend/dist \
    MEDIA_ROOT=/media \
    DATA_DIR=/data \
    APP_ENV=prod \
    PYTHONUNBUFFERED=1

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
