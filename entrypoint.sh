#!/bin/sh
set -e

echo "[Entrypoint] Running database migrations..."
alembic upgrade head

echo "[Entrypoint] Starting server..."
exec uvicorn main:app --host 0.0.0.0 --port 8000
