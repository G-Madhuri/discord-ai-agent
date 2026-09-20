# Single image, two entry points: the FastAPI service (default) and the bot.
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ ./backend/
WORKDIR /app/backend

# Cloud Run supplies PORT; default to 8080 for local `docker run`.
ENV PORT=8080
EXPOSE 8080

RUN useradd --create-home --uid 1000 appuser && chown -R appuser /app
USER appuser

CMD exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT}
