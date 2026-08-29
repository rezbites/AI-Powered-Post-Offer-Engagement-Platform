# Shortcuts for the docker commands below - nothing more.
#
# `make seed` just runs:
#     docker compose run --rm api python -m app.db.seed
#
# If `make` is not installed (common on Windows), copy the command under any
# target and run it directly. Everything runs inside containers, so no local
# Python is needed - the host here has 3.9 and the code targets 3.12.
.PHONY: help up down build logs seed reset migrate revision test eval eval-live shell psql

help:
	@echo "up        Start the full stack (db + api + web)"
	@echo "down      Stop the stack"
	@echo "build     Rebuild images"
	@echo "logs      Tail API logs"
	@echo "migrate   Apply database migrations"
	@echo "revision  Autogenerate a migration:  make revision m='add x'"
	@echo "seed      Populate demo data (60 candidates)"
	@echo "reset     Drop volumes, rebuild, migrate and reseed from scratch"
	@echo "test      Run the backend test suite"
	@echo "eval      Score AI extraction against the golden set (mock, free)"
	@echo "eval-live Compare mock vs Gemini (uses real tokens)"
	@echo "shell     Shell into the API container"
	@echo "psql      Open a psql prompt against the dev database"

up:
	docker compose up --build

down:
	docker compose down

build:
	docker compose build

logs:
	docker compose logs -f api

migrate:
	docker compose run --rm api alembic upgrade head

revision:
	docker compose run --rm api alembic revision --autogenerate -m "$(m)"

seed:
	docker compose run --rm api python -m app.db.seed

# Destructive: wipes the database volume. Fastest way back to a clean demo.
reset:
	docker compose down -v
	docker compose up -d --build db
	docker compose run --rm api alembic upgrade head
	docker compose run --rm api python -m app.db.seed
	docker compose up -d

test:
	docker compose run --rm api pytest -v

# Deterministic and free - safe to run in CI.
eval:
	docker compose run --rm api python -m evals.run_eval --verbose

# Costs real tokens. Compares both providers on the same golden set.
eval-live:
	docker compose run --rm api python -m evals.run_eval --compare --verbose

shell:
	docker compose run --rm api sh

psql:
	docker compose exec db psql -U postgres -d engagement
