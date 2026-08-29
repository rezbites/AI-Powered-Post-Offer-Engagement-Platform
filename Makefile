# Convenience wrappers. Everything runs inside containers, so no local Python
# installation is required (the host here has 3.9; the code targets 3.12).
.PHONY: help up down build logs seed reset migrate revision test lint shell psql

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

shell:
	docker compose run --rm api sh

psql:
	docker compose exec db psql -U postgres -d engagement
