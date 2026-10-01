.PHONY: help setup up down data features train policy insights pipeline test lint fmt \
	migrate seed load api replay replay-live verify platform

API_PORT ?= 8010
API_URL ?= http://127.0.0.1:$(API_PORT)

help:
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | sed -E 's/:.*## /\t/'

setup: ## Install backend dependencies
	cd backend && uv sync

up: ## Start Postgres and Redis
	docker compose up -d --wait

down: ## Stop Postgres and Redis
	docker compose down

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

pipeline: data features train policy insights ## Data, features, models and policy end to end

migrate: ## Bring the database schema up to date
	cd backend && uv run alembic upgrade head

seed: ## Create the demo accounts (password from FRAUDLENS_SEED_PASSWORD, or generated)
	cd backend && uv run python -m fraudlens.platform.seed

load: ## Load the historical period into the database (replaces what is there)
	cd backend && uv run python -m fraudlens.platform.load --reset

api: ## Run the API and its stream worker
	cd backend && uv run uvicorn fraudlens.api:create_app --factory --host 127.0.0.1 --port $(API_PORT)

replay: ## Replay the test period, except its last day, through the event stream (needs `make api`)
	cd backend && uv run python -m fraudlens.platform.replay --via stream --to-day 118

replay-live: ## Send the last day one request at a time and measure latency (needs `make api`)
	cd backend && uv run python -m fraudlens.platform.replay --via http --from-day 119 --api $(API_URL)

verify: ## Check that what was served matches the offline evaluation
	cd backend && uv run python -m fraudlens.platform.verify

platform: migrate seed load ## Database ready for `make api`

test: ## Run backend tests (the platform tests need `make up`)
	cd backend && uv run pytest -q

lint: ## Lint and format check
	cd backend && uv run ruff check src tests && uv run ruff format --check src tests

fmt: ## Auto-format
	cd backend && uv run ruff check --fix src tests && uv run ruff format src tests
