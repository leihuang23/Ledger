#!/bin/sh
set -e

echo "Running database migrations..."
alembic upgrade head

echo "Checking whether seed data is required..."
python - <<'PY'
from app.db.session import SessionLocal
from app.seed import ensure_seeded_if_empty

with SessionLocal() as session:
    result = ensure_seeded_if_empty(session)
    if result:
        print(f"Seeded empty database (fingerprint={result.fingerprint})")
    else:
        print("Seed data already present; skipping reseed")
PY

echo "Starting API server..."
exec uvicorn app.main:app --host 0.0.0.0 --port 8000
