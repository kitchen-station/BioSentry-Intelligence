.PHONY: web-install web-dev web-build web-typecheck web-lint web-test check \
        api-install api-dev api-test db-up db-down db-migrate db-reset db-passphrase contract

FRONTEND := frontend
BACKEND := backend

web-install:
	cd $(FRONTEND) && npm ci

web-dev:
	cd $(FRONTEND) && npm run dev

web-build:
	cd $(FRONTEND) && npm run build

web-typecheck:
	cd $(FRONTEND) && npx tsc -b --noEmit

web-lint:
	cd $(FRONTEND) && npm run lint

web-test:
	cd $(FRONTEND) && npm test

# --- Backend: Python 3.11 through uv; PostgreSQL from compose.yaml (BIOSENTRY_DATABASE_URL overrides) ---

api-install:
	cd $(BACKEND) && uv sync --locked

# Loopback only. Local sign-in: every clinician has the dev passphrase (README). The sim posts to /sim/* with this key.
BIOSENTRY_INGEST_KEY ?= dev-local
api-dev:
	cd $(BACKEND) && BIOSENTRY_INGEST_KEY=$(BIOSENTRY_INGEST_KEY) uv run uvicorn biosentry.api.main:app --host 127.0.0.1 --port 8000

# The integration tests need the database: `make db-up` first. Each test gets its own copy of a
# seeded template database, dropped afterwards, so the dev database is not touched.
api-test:
	@cd $(BACKEND) && uv run python -c "import psycopg; from biosentry.core.config import Settings; psycopg.connect(Settings.from_env().database_url, connect_timeout=3).close()" 2>/dev/null \
		|| { echo "api-test needs PostgreSQL and it is not reachable: run 'make db-up' first" >&2; exit 1; }
	cd $(BACKEND) && uv run coverage run -m pytest && uv run coverage report  # fails below 100% of lines

# Start the local PostgreSQL (compose.yaml, 127.0.0.1:5432) and wait until it is healthy. Needs Docker.
db-up:
	docker compose up -d --wait

# Stop it. The data stays in the named volume; `docker compose down -v` also deletes that.
db-down:
	docker compose down

# Apply pending migrations (backend/src/biosentry/db/migrations/) without starting the API.
db-migrate:
	cd $(BACKEND) && uv run python -m biosentry.db.migrate

# Drop every table and seed again (schema, clinicians, the fixture ward). The audit trail goes too.
db-reset:
	cd $(BACKEND) && uv run python -m biosentry.db.seed --reset

# Set a clinician's passphrase (prompts twice, or pipe it in): make db-passphrase id=rn-manalo
# It writes to the database BIOSENTRY_DATABASE_URL names (the compose one when unset).
db-passphrase:
	@test -n "$(id)" || { echo "usage: make db-passphrase id=<clinician id>" >&2; exit 1; }
	cd $(BACKEND) && uv run python -m biosentry.db.passphrase "$(id)"

# Pydantic schemas -> frontend/contracts/openapi.json -> frontend/src/types/generated.ts
contract:
	cd $(BACKEND) && uv run python -m biosentry.schemas.openapi --out ../$(FRONTEND)/contracts/openapi.json
	cd $(FRONTEND)/contracts && npm ci && npm run generate

check: web-typecheck web-lint web-test api-test
