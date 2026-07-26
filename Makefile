# Convenience targets for the Stage 1 POC. See README.md for the one-command path.
.PHONY: up db migrate seed backend frontend setup down

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
	cd backend && python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt
	cd frontend && npm install

migrate:
	cd backend && . .venv/bin/activate && alembic upgrade head

seed:
	cd backend && . .venv/bin/activate && python -m app.seed

backend:
	cd backend && . .venv/bin/activate && uvicorn app.main:app --reload --port 8000

frontend:
	cd frontend && npm run dev
