# ============================================================================
# KnightMind – Local Development Commands
# ============================================================================
# Usage: make <target>
#   make dev          → Start both frontend + backend
#   make front        → Start frontend only
#   make back         → Start backend only
#   make test         → Run all tests
#   make lint         → Lint everything
#   make preflight    → Full pre-deploy checks
# ============================================================================

.PHONY: help dev front back test test-front test-back lint lint-front lint-back \
        build build-front preflight preflight-front preflight-back \
        migrate db-current kill \
        docker-up docker-down docker-build docker-logs docker-migrate docker-shell

# Colors
GREEN  := \033[0;32m
YELLOW := \033[0;33m
RED    := \033[0;31m
NC     := \033[0m  # No Color

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  $(GREEN)%-18s$(NC) %s\n", $$1, $$2}'

# ---------------------------------------------------------------------------
# Development servers
# ---------------------------------------------------------------------------

dev: ## Start frontend + backend (parallel)
	@echo "$(GREEN)Starting frontend + backend...$(NC)"
	@make -j2 front back

front: ## Start frontend dev server (port 5173)
	@echo "$(GREEN)Starting frontend...$(NC)"
	cd apps/web && npm run dev

back: ## Start backend API server (port 8000)
	@echo "$(GREEN)Starting backend...$(NC)"
	cd services/api && python -m uvicorn main:app --reload --port 8000

kill: ## Kill running backend processes
	@echo "$(YELLOW)Killing backend processes...$(NC)"
	python -m scripts.kill_backends || true

# ---------------------------------------------------------------------------
# Testing
# ---------------------------------------------------------------------------

test: test-back test-front ## Run all tests

test-front: ## Run frontend tests
	@echo "$(GREEN)Running frontend tests...$(NC)"
	cd apps/web && npm run test -- --run

test-back: ## Run backend tests
	@echo "$(GREEN)Running backend tests...$(NC)"
	python -m pytest

# ---------------------------------------------------------------------------
# Linting
# ---------------------------------------------------------------------------

lint: lint-back lint-front ## Lint everything

lint-front: ## Lint frontend
	@echo "$(GREEN)Linting frontend...$(NC)"
	cd apps/web && npm run lint

lint-back: ## Lint backend
	@echo "$(GREEN)Linting backend...$(NC)"
	python -m ruff check .
	python -m black --check .

# ---------------------------------------------------------------------------
# Building
# ---------------------------------------------------------------------------

build: build-front ## Build for production

build-front: ## Build frontend
	@echo "$(GREEN)Building frontend...$(NC)"
	cd apps/web && npm run build

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

migrate: ## Run Alembic migrations (upgrade head)
	@echo "$(GREEN)Running migrations...$(NC)"
	python -m alembic -c services/api/alembic.ini upgrade head

db-current: ## Show current migration revision
	python -m alembic -c services/api/alembic.ini current

# ---------------------------------------------------------------------------
# Preflight checks
# ---------------------------------------------------------------------------

preflight: preflight-back preflight-front ## Full pre-deploy preflight check
	@echo ""
	@echo "$(GREEN)========================================$(NC)"
	@echo "$(GREEN)  All preflight checks passed!$(NC)"
	@echo "$(GREEN)========================================$(NC)"

preflight-front: lint-front build-front ## Frontend preflight (lint + build)
	@echo "$(GREEN)Frontend preflight passed.$(NC)"

preflight-back: test-back lint-back db-current ## Backend preflight (tests + lint + migrations)
	@echo "$(GREEN)Backend preflight passed.$(NC)"

# ---------------------------------------------------------------------------
# Docker
# ---------------------------------------------------------------------------

docker-up: ## Start API + Postgres via Docker Compose
	@echo "$(GREEN)Starting Docker services...$(NC)"
	docker compose up -d
	@echo "$(GREEN)Services started. API at http://localhost:$${API_PORT:-8000}$(NC)"

docker-down: ## Stop Docker Compose services
	@echo "$(YELLOW)Stopping Docker services...$(NC)"
	docker compose down

docker-build: ## Rebuild the API Docker image
	@echo "$(GREEN)Building API image...$(NC)"
	docker compose build api

docker-logs: ## Tail Docker Compose logs
	docker compose logs -f

docker-migrate: ## Run Alembic migrations inside the API container
	@echo "$(GREEN)Running migrations in container...$(NC)"
	docker compose exec api alembic -c services/api/alembic.ini upgrade head

docker-shell: ## Open a shell in the running API container
	docker compose exec api bash
