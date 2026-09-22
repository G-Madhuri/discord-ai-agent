FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Copy dependency definitions and install
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
RUN python -m nltk.downloader punkt punkt_tab -d /usr/share/nltk_data

ENV NLTK_DATA=/usr/share/nltk_data

# Copy application source code
COPY backend /app/backend

WORKDIR /app/backend

ENV PORT=8080
EXPOSE 8080

RUN useradd --create-home --uid 1000 appuser && chown -R appuser /app
USER appuser

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
