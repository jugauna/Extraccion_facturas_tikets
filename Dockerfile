# Raíz del repo — Cloud Build / Cloud Run buscan Dockerfile aquí por defecto.
# Construye el servicio FastAPI multi-ticket (carpeta backend/).
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8080

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends poppler-utils \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/app ./app
COPY backend/prompts ./prompts
COPY backend/data ./data

RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8080

CMD sh -c "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}"
