#!/usr/bin/env bash
# One command to bring up the whole Stage 1 POC: Postgres + backend + frontend.
#
#   ./scripts/dev.sh            # up everything (idempotent); Ctrl-C stops backend+frontend
#   SEED=1 ./scripts/dev.sh     # also (re)run the seed script after migrating
#
# Requires: docker (compose), python3, node/npm.
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$(pwd)"

export DATABASE_URL="${DATABASE_URL:-postgresql+asyncpg://agentopt:agentopt@localhost:5432/agentopt}"
export SYNC_DATABASE_URL="${SYNC_DATABASE_URL:-postgresql+psycopg://agentopt:agentopt@localhost:5432/agentopt}"
export FAKE_USER_SUBJECT="${FAKE_USER_SUBJECT:-alice}"

echo "==> Starting Postgres (docker compose)"
docker compose up -d

echo "==> Waiting for Postgres to be ready"
for i in $(seq 1 30); do
  if docker exec agentopt_db pg_isready -U agentopt -d agentopt >/dev/null 2>&1; then
    echo "    ready"; break
  fi
  sleep 1
done

echo "==> Backend deps"
cd "$ROOT/backend"
if [ ! -d .venv ]; then python3 -m venv .venv; fi
. .venv/bin/activate
pip install -q -r requirements.txt

echo "==> Alembic migrate"
alembic upgrade head

if [ "${SEED:-0}" = "1" ]; then
  echo "==> Seeding fake data"
  python -m app.seed
fi

echo "==> Starting backend (uvicorn :8000)"
uvicorn app.main:app --host 127.0.0.1 --port 8000 &
BACKEND_PID=$!

echo "==> Frontend deps + dev server (:5173)"
cd "$ROOT/frontend"
if [ ! -d node_modules ]; then npm install; fi
npm run dev &
FRONTEND_PID=$!

trap 'echo; echo "Stopping…"; kill $BACKEND_PID $FRONTEND_PID 2>/dev/null || true' INT TERM
echo
echo "Backend:  http://localhost:8000   (docs at /docs)"
echo "Frontend: http://localhost:5173"
echo "Postgres stays up; run 'docker compose down' to stop it."
echo "Press Ctrl-C to stop backend + frontend."
wait
