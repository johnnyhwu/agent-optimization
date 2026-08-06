#!/usr/bin/env bash
# One command to bring up the deployed form: Postgres + backend + nginx.
#
#   ./scripts/prod.sh           # build and start, detached; the stack keeps running
#
# Deliberately written to mirror scripts/dev.sh step for step, so that
#
#   diff scripts/dev.sh scripts/prod.sh
#
# reads as a list of exactly what deployment changes. Six things differ, each
# marked "DIFFERS FROM dev.sh" below.
#
# Requires: docker (with compose), and a repo-root .env — see the preflight step.
set -euo pipefail
cd "$(dirname "$0")/.."

# Not one of the six differences below — dev.sh sets this identically, so
# `diff scripts/dev.sh scripts/prod.sh` still reads as exactly the deployment
# changes.
#
# The frontend image sets a file mode with `COPY --chmod`, which needs BuildKit.
# Compose v2 normally selects it unaided, but a host missing the buildx plugin
# falls back to the legacy builder, where --chmod is a hard error. Asking for it
# explicitly turns that into a clear message about buildx instead.
export DOCKER_BUILDKIT=1

# DIFFERS FROM dev.sh (1/6): which compose files are used.
#
# This is the single most important line in the script. Naming the files
# explicitly is what suppresses the automatic docker-compose.override.yml, and
# with it every development-only setting: the published backend and Postgres
# ports, the source bind-mounts, uvicorn --reload and the Vite dev server.
#
# Compose *appends* list-valued keys like `ports` and `volumes` when layering
# files, so an overlay could not have removed those — leaving the override out
# is the mechanism.
COMPOSE=(docker compose -f docker-compose.yml -f docker-compose.prod.yml)

# DIFFERS FROM dev.sh (2/6): nothing is defaulted here.
#
# dev.sh exports development credentials so the stack runs with no setup at all.
# The deployed form must not do that, so the variables it needs are declared
# `${VAR:?}` in docker-compose.prod.yml and this step catches a missing one
# before anything is built.
#
# `config --quiet` performs the check by running compose's own interpolation —
# including its own precedence between the shell environment and a repo-root
# .env, which is fiddly enough that re-implementing it here would get it subtly
# wrong. It stops at the *first* missing variable, which is why the hint below
# lists the whole set: otherwise you rediscover them one failed run at a time.
echo "==> Checking required configuration"
if ! errors=$("${COMPOSE[@]}" config --quiet 2>&1); then
  echo
  echo "$errors"
  echo
  echo "Set these in a repo-root .env (see backend/.env.example):"
  echo
  echo "    POSTGRES_PASSWORD=…"
  echo "    DATABASE_URL=postgresql+asyncpg://agentopt:…@db:5432/agentopt"
  echo "    SYNC_DATABASE_URL=postgresql+psycopg://agentopt:…@db:5432/agentopt"
  echo "    KEYCLOAK_URL=https://<keycloak-host>/auth"
  echo
  echo "DATABASE_URL and SYNC_DATABASE_URL must carry the same password as"
  echo "POSTGRES_PASSWORD; percent-encode any URL-reserved characters."
  exit 1
fi
echo "    ok"

# DIFFERS FROM dev.sh (3/6): the frontend image is a different build target.
# `runner` compiles the bundle with `vite build` and serves it from nginx,
# instead of running the Vite dev server against a bind-mounted source tree.
echo "==> Building backend + frontend images (frontend: vite build -> nginx)"
"${COMPOSE[@]}" build

echo "==> Starting Postgres (docker compose)"
"${COMPOSE[@]}" up -d db

echo "==> Waiting for Postgres to be ready"
# The user and database name are deployment-configurable here, where dev.sh can
# hard-code them.
for i in $(seq 1 30); do
  if docker exec agentopt_db pg_isready \
      -U "${POSTGRES_USER:-agentopt}" -d "${POSTGRES_DB:-agentopt}" >/dev/null 2>&1; then
    echo "    ready"; break
  fi
  sleep 1
done

# DIFFERS FROM dev.sh (4/6): migrations are not run from here.
#
# The backend image's entrypoint runs `alembic upgrade head` when
# RUN_MIGRATIONS=1, which docker-compose.prod.yml sets. Doing it there rather
# than here means a container restarted by `restart: unless-stopped` — or by the
# host rebooting — also migrates, without anyone having to remember this script.
#
# There is no seed step either. Seeding creates eval sets owned by alice/bob/
# carol; under real sign-in nobody is those users, so the rows would exist and
# be invisible to everyone.
echo "==> Migrations run from the backend entrypoint (RUN_MIGRATIONS=1)"

echo "==> Starting backend + nginx (:${FRONTEND_PORT:-5173})"
"${COMPOSE[@]}" up -d backend frontend

# DIFFERS FROM dev.sh (5/6): wait for the backend to actually answer.
#
# dev.sh streams logs, so a backend that died on startup is obvious. Here the
# script exits, so a failure would otherwise be silent until someone opened the
# page. This also covers the entrypoint's migration step — the container is not
# healthy until uvicorn is serving, which is after Alembic has finished.
echo "==> Waiting for the backend to become healthy"
for i in $(seq 1 60); do
  status=$(docker inspect --format '{{.State.Health.Status}}' agentopt_backend 2>/dev/null || echo starting)
  if [ "$status" = "healthy" ]; then
    echo "    healthy"; break
  fi
  if [ "$i" = "60" ]; then
    echo "    still ${status} after 60s — check: ${COMPOSE[*]} logs backend"
    exit 1
  fi
  sleep 1
done

# DIFFERS FROM dev.sh (6/6): the script exits and the stack keeps running.
#
# dev.sh tails logs in the foreground so Ctrl-C stops the two app containers.
# Here every service is `restart: unless-stopped` — the point is that it survives
# this shell, a disconnect, and a host reboot — so trapping Ctrl-C to stop them
# would be exactly wrong.
echo
echo "App: http://localhost:${FRONTEND_PORT:-5173}"
echo
echo "  Only nginx is published. The backend and Postgres are reachable"
echo "  from inside the compose network only."
echo
echo "  logs:  ${COMPOSE[*]} logs -f --tail=100"
echo "  stop:  ${COMPOSE[*]} down"
echo
