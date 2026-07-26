#!/usr/bin/env bash
# One command to bring up the whole Stage 1 POC: Postgres + backend + frontend.
#
#   ./scripts/dev.sh            # up everything (idempotent); Ctrl-C stops backend+frontend
#   SEED=1 ./scripts/dev.sh     # also (re)run the seed script after migrating
#
# Requires: docker (compose), Python 3.10-3.13, node/npm.
# (The pinned deps have no wheels for Python 3.14 yet and can't build from source
#  against it — pyo3<=0.22 caps at 3.13 — so we build the venv with a supported
#  interpreter. Override with PYTHON_BIN=/path/to/python if you want a specific one.)
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$(pwd)"

# Pick a CPython in [3.10, 3.13]. Honors PYTHON_BIN if it points at a supported one.
py_minor() { "$1" -c 'import sys; print(sys.version_info[1])' 2>/dev/null || echo 99; }
py_ok() { local m; m="$(py_minor "$1")"; [ "$m" != 99 ] && [ "$m" -ge 10 ] && [ "$m" -le 13 ]; }
pick_python() {
  if [ -n "${PYTHON_BIN:-}" ] && py_ok "$PYTHON_BIN"; then echo "$PYTHON_BIN"; return 0; fi
  for cand in python3.12 python3.11 python3.13 python3.10 python3; do
    if command -v "$cand" >/dev/null 2>&1 && py_ok "$cand"; then command -v "$cand"; return 0; fi
  done
  return 1
}

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
# Recreate the venv if it is missing or was built with an unsupported Python
# (e.g. a system default of 3.14) — otherwise pip tries to compile pydantic-core
# from source and fails.
if [ -d .venv ] && ! py_ok .venv/bin/python; then
  echo "    existing .venv uses an unsupported Python (3.$(py_minor .venv/bin/python)); recreating"
  rm -rf .venv
fi
if [ ! -d .venv ]; then
  PYBIN="$(pick_python)" || {
    echo "ERROR: need CPython 3.10-3.13 on PATH (found none)."
    echo "       Your 'python3' is likely 3.14, which the pinned deps don't support yet."
    echo "       macOS:  brew install python@3.12   (then re-run)"
    echo "       or set PYTHON_BIN=/path/to/python3.12 and re-run."
    exit 1
  }
  echo "    creating .venv with $PYBIN ($("$PYBIN" --version 2>&1))"
  "$PYBIN" -m venv .venv
fi
. .venv/bin/activate
pip install -q --upgrade pip >/dev/null
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
