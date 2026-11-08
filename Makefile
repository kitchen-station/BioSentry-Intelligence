.PHONY: web-install web-dev web-build web-typecheck web-lint web-test check \
        api-install api-dev api-test db-reset db-purge-eicu contract

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

# --- Backend: Python 3.11 through uv, SQLite at backend/data/biosentry.db (BIOSENTRY_DB overrides) ---

api-install:
	cd $(BACKEND) && uv sync --locked

# Loopback only: identity is a stub. The sim posts to /sim/* with this key.
BIOSENTRY_INGEST_KEY ?= dev-local
api-dev:
	cd $(BACKEND) && BIOSENTRY_INGEST_KEY=$(BIOSENTRY_INGEST_KEY) uv run uvicorn biosentry.api.main:app --host 127.0.0.1 --port 8000

api-test:
	cd $(BACKEND) && uv run pytest

# Drop the database and seed it again (schema, clinicians, the fixture ward).
db-reset:
	cd $(BACKEND) && db="$${BIOSENTRY_DB:-data/biosentry.db}" && rm -f "$$db" "$$db-wal" "$$db-shm" && uv run python -m biosentry.db.seed

# Run this before anything holding replay data is shared. The audit table has no foreign key to run, so the trail stays.
db-purge-eicu:
	cd $(BACKEND) && uv run python -c "from biosentry.core.config import Settings; from biosentry.repositories.sqlite import connect; print(connect(Settings.from_env().db).execute(\"DELETE FROM run WHERE source='eicu_demo'\").rowcount, 'eicu_demo run(s) removed')"

# Pydantic schemas -> frontend/contracts/openapi.json -> frontend/src/types/generated.ts
contract:
	cd $(BACKEND) && uv run python -m biosentry.schemas.openapi --out ../$(FRONTEND)/contracts/openapi.json
	cd $(FRONTEND)/contracts && npm ci && npm run generate

check: web-typecheck web-lint web-test api-test
