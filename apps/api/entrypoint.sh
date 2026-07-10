#!/bin/sh
set -e

MODE="${1:-api}"
PORT="${PORT:-8000}"

if [ "$MODE" = "worker" ]; then
  echo "Starting Celery worker..."
  exec celery -A app.celery_app.celery_app worker --loglevel=info --concurrency=2
fi

echo "Running database migrations and seed bootstrap..."
python -m app.bootstrap

echo "Starting API server..."
exec uvicorn app.main:app --host 0.0.0.0 --port "$PORT"
