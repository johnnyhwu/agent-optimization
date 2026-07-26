# Convenience targets for the Stage 1 POC. See README.md for the one-command path.
.PHONY: up db migrate seed backend frontend setup down

# A CPython the pinned deps have wheels for (3.10-3.13). Prefer a versioned
# interpreter so a system default of 3.14 (no wheels yet) isn't picked.
# Override with: make setup PYTHON_BIN=/path/to/python3.12
PYTHON_BIN ?= $(shell command -v python3.12 || command -v python3.11 || command -v python3.13 || command -v python3.10 || command -v python3)

# One command: Postgres + backend + frontend (Ctrl-C stops backend+frontend).
up:
	./scripts/dev.sh

# Same, but (re)seed fake data after migrating.
up-seed:
	SEED=1 ./scripts/dev.sh

db:
	docker compose up -d

down:
	docker compose down

setup:
	cd backend && $(PYTHON_BIN) -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt
	cd frontend && npm install

migrate:
	cd backend && . .venv/bin/activate && alembic upgrade head

seed:
	cd backend && . .venv/bin/activate && python -m app.seed

backend:
	cd backend && . .venv/bin/activate && uvicorn app.main:app --reload --port 8000

frontend:
	cd frontend && npm run dev
