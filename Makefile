# Convenience targets for the Stage 1 POC. See README.md for the one-command path.
# db, backend and frontend all run as containers, so the only host requirement
# is docker (with compose) — no host venv, no host node_modules.
#
# Everything above the "Deployment" section runs the development stack:
# docker-compose.yml plus the auto-loaded docker-compose.override.yml.
.PHONY: up up-seed db build setup migrate seed backend frontend down test test-sandbox preflight \
        prod-build prod-up prod-down prod-logs

# Naming the files explicitly is what suppresses docker-compose.override.yml,
# and with it the published ports, source bind-mounts and reload loops.
PROD := docker compose -f docker-compose.yml -f docker-compose.prod.yml

# One command: Postgres + backend + frontend (Ctrl-C stops backend+frontend).
up:
	./scripts/dev.sh

# Same, but (re)seed fake data after migrating.
up-seed:
	SEED=1 ./scripts/dev.sh

db:
	docker compose up -d db

down:
	docker compose down

# Build both app images: backend deps via uv, frontend deps via pnpm.
build:
	docker compose build

# Kept as an alias so the documented "install the deps" step still works.
setup: build

migrate:
	docker compose run --rm --no-deps backend alembic upgrade head

seed:
	docker compose run --rm --no-deps backend python -m app.seed

backend:
	docker compose up backend

frontend:
	docker compose up frontend

# Backend unit tests (no DB or external service needed). --no-deps keeps this
# working without the sandbox container: the suite runs the sandbox supervisor
# in-process (see tests/conftest.py), which is the real code over a socketpair.
test:
	docker compose run --rm --no-deps backend pytest -q

# The isolation tests, which need the sandbox container actually running: a
# different uid, an environment with no secrets in it, a /proc with nothing to
# find. `make test` cannot check any of that, so this is the target that proves
# uploaded scripts are still contained.
test-sandbox:
	docker compose run --rm backend pytest -q -m sandbox_container

# Ping whichever integrations are set to real; reports OK/FAIL per seam.
preflight:
	docker compose run --rm --no-deps backend python -m app.check_integrations

# --- Deployment -------------------------------------------------------------
# Built bundle behind nginx, no reload, only the nginx port published. Needs
# KEYCLOAK_URL, POSTGRES_PASSWORD, DATABASE_URL and SYNC_DATABASE_URL set (see
# backend/.env.example); compose refuses to start without them rather than
# falling back to development values.

prod-build:
	$(PROD) build

# Delegates to the script, the same way `up` delegates to dev.sh — the two
# scripts are written to mirror each other, so `diff scripts/dev.sh
# scripts/prod.sh` is a readable summary of what deployment changes.
prod-up:
	./scripts/prod.sh

prod-down:
	$(PROD) down

prod-logs:
	$(PROD) logs -f --tail=100
