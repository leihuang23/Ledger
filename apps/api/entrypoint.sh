#!/bin/sh
set -e

echo "Running database migrations and seed bootstrap..."
python -m app.bootstrap

echo "Starting API server..."
exec uvicorn app.main:app --host 0.0.0.0 --port 8000
