#!/usr/bin/env bash
# One command to bring up the whole Stage 1 POC: Postgres + backend + frontend,
# each in its own container.
#
#   ./scripts/dev.sh            # up everything (idempotent); Ctrl-C stops backend+frontend
#   SEED=1 ./scripts/dev.sh     # also (re)run the seed script after migrating
#
# Requires: docker (with compose). Python/uv and node/pnpm live inside the
# images, so nothing has to be installed on the host — which is also why the
# "find a CPython in [3.10, 3.13]" dance this script used to do is gone: the
# backend image pins 3.12, an interpreter the deps have wheels for.
set -euo pipefail
cd "$(dirname "$0")/.."

# Read by docker-compose.yml and passed into the backend container. The Postgres
# host is the compose service name, not localhost, because the backend now dials
# it across the compose network.
export DATABASE_URL="${DATABASE_URL:-postgresql+asyncpg://agentopt:agentopt@db:5432/agentopt}"
export SYNC_DATABASE_URL="${SYNC_DATABASE_URL:-postgresql+psycopg://agentopt:agentopt@db:5432/agentopt}"
export FAKE_USER_SUBJECT="${FAKE_USER_SUBJECT:-alice}"

echo "==> Building backend + frontend images (deps via uv / pnpm)"
docker compose build

echo "==> Starting Postgres (docker compose)"
docker compose up -d db

echo "==> Waiting for Postgres to be ready"
for i in $(seq 1 30); do
  if docker exec agentopt_db pg_isready -U agentopt -d agentopt >/dev/null 2>&1; then
    echo "    ready"; break
  fi
  sleep 1
done

echo "==> Alembic migrate"
docker compose run --rm --no-deps backend alembic upgrade head

if [ "${SEED:-0}" = "1" ]; then
  echo "==> Seeding fake data"
  docker compose run --rm --no-deps backend python -m app.seed
fi

echo "==> Starting backend (uvicorn :8000) + frontend dev server (:5173)"
docker compose up -d backend frontend

trap 'echo; echo "Stopping…"; docker compose stop backend frontend >/dev/null 2>&1 || true' INT TERM
echo
echo "Backend:  http://localhost:8000   (docs at /docs)"
echo "Frontend: http://localhost:5173"
echo "Postgres stays up; run 'docker compose down' to stop it."
echo "Press Ctrl-C to stop backend + frontend."
echo
# Foreground log stream, so Ctrl-C lands in the trap above and stops only the
# two app containers — Postgres keeps running, exactly as before.
docker compose logs -f --tail=100 backend frontend
