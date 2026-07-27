# Convenience targets for the Stage 1 POC. See README.md for the one-command path.
# db, backend and frontend all run as containers, so the only host requirement
# is docker (with compose) — no host venv, no host node_modules.
.PHONY: up up-seed db build setup migrate seed backend frontend down

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
