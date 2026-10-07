.PHONY: help setup up redis down data features train policy insights intel pipeline test lint fmt \
	migrate seed load api replay replay-live verify platform \
	review retrain shadow models promote console console-build smoke \
	demo demo-reset

API_PORT ?= 8010
API_URL ?= http://127.0.0.1:$(API_PORT)

help:
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | sed -E 's/:.*## /\t/'

demo: backend/.env ## Everything in Docker: build, populate, serve. Console on http://localhost:3100
	@echo "The first start builds the dataset and the models and replays the traffic: about ten minutes."
	@echo "Then sign in on http://localhost:3100 as analyst1, supervisor1 or admin"
	@echo "with the password in backend/.env (FRAUDLENS_SEED_PASSWORD)."
	docker compose --profile demo up --build

demo-reset: ## Stop the demo and delete its database, dataset and models
	docker compose --profile demo down --volumes

# A password for the demo accounts and a signing key, generated once and never committed.
backend/.env:
	@umask 077 && printf 'FRAUDLENS_SEED_PASSWORD=%s\nFRAUDLENS_JWT_SECRET=%s\n' \
		"$$(openssl rand -hex 12)" "$$(openssl rand -hex 32)" > $@
	@echo "wrote backend/.env with a generated demo password and signing key"

setup: ## Install backend and console dependencies
	cd backend && uv sync
	cd frontend && pnpm install --frozen-lockfile

up: ## Start Postgres and Redis
	docker compose up -d --wait

redis: ## Start Redis only, for a Postgres that runs on the host (set FRAUDLENS_DATABASE_URL)
	docker compose up -d --wait redis

down: ## Stop everything (the demo too) and keep the data
	docker compose --profile demo down

data: ## Generate the synthetic dataset
	cd backend && uv run python -m fraudlens.simulator.generate

features: ## Replay the dataset through the feature engine
	cd backend && uv run python -m fraudlens.features.build

train: ## Train and evaluate all models
	cd backend && uv run python -m fraudlens.models.train

policy: ## Evaluate the decision policy and build the similar-case index
	cd backend && uv run python -m fraudlens.decision.evaluate

insights: ## Threshold sweep, drift and fairness tables for the dashboards
	cd backend && uv run python -m fraudlens.decision.insights

intel: ## Train and evaluate the scam-message classifier (docs/FRAUD_TAXONOMY.md)
	cd backend && uv run python -m fraudlens.intel.train

pipeline: data features train policy insights intel ## Data, features, models and policy end to end

migrate: ## Bring the database schema up to date
	cd backend && uv run alembic upgrade head

seed: ## Create the demo accounts (password from FRAUDLENS_SEED_PASSWORD, or generated)
	cd backend && uv run python -m fraudlens.platform.seed

load: ## Load the historical period into the database (replaces what is there)
	cd backend && uv run python -m fraudlens.platform.load --reset

api: ## Run the API and its stream worker
	cd backend && uv run uvicorn fraudlens.api:create_app --factory --host 127.0.0.1 --port $(API_PORT)

worker: ## Run one more stream worker next to the API; start as many as needed (docs/SCALING.md)
	cd backend && uv run python -m fraudlens.platform.worker --port $${PORT:-8081}

loadtest: ## Throughput and latency for 1, 2 and 4 workers; RESETS the database (docs/SCALING.md)
	cd backend && uv run python -m fraudlens.platform.loadtest --workers 1 2 4

replay: ## Replay the test period, except its last day, through the event stream (needs `make api`)
	cd backend && uv run python -m fraudlens.platform.replay --via stream --to-day 118

replay-live: ## Send the last day one request at a time and measure latency (needs `make api`)
	cd backend && uv run python -m fraudlens.platform.replay --via http --from-day 119 --api $(API_URL)

verify: ## Check that what was served matches the offline evaluation
	cd backend && uv run python -m fraudlens.platform.verify

platform: migrate seed load ## Database ready for `make api`

review: ## Demo: close the older open cases with the simulation's ground truth (needs `make api`)
	cd backend && uv run python -m fraudlens.mlops.review --api $(API_URL)

retrain: ## Retrain with analyst verdicts as labels; registers a challenger, promotes nothing
	cd backend && uv run python -m fraudlens.mlops.retrain

shadow: ## Score past decisions with a challenger: make shadow VERSION=v3
	cd backend && uv run python -m fraudlens.mlops.shadow --version $(VERSION)

models: ## List the registered model versions
	cd backend && uv run python -m fraudlens.models.registry

promote: ## Serve a version after the next restart: make promote VERSION=v3
	cd backend && uv run python -m fraudlens.models.registry promote $(VERSION)

console: ## Run the analyst console on http://localhost:3100 (needs `make api`)
	cd frontend && pnpm dev --port 3100

console-build: ## Production build of the console
	cd frontend && pnpm build

smoke: ## Open every console page in a headless browser as each role (needs the API and the console)
	cd frontend && pnpm exec playwright install chromium-headless-shell && node scripts/smoke.cjs

test: ## Run backend tests (the platform tests need `make up`)
	cd backend && uv run pytest -q

lint: ## Lint and format check
	cd backend && uv run ruff check src tests && uv run ruff format --check src tests
	cd frontend && pnpm exec tsc --noEmit && pnpm exec eslint .

fmt: ## Auto-format
	cd backend && uv run ruff check --fix src tests && uv run ruff format src tests
