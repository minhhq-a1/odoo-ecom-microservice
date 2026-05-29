.PHONY: install lint format type test test-unit test-int run worker beat migrate revision \
        docker-up docker-down docker-build security audit smoke help shopee-auth-url \
        shopee-exchange shopee-status

PYTHON := python3.12
PIP    := $(PYTHON) -m pip

help:
	@echo "make install         — Install runtime + dev deps"
	@echo "make lint            — ruff check"
	@echo "make format          — ruff format"
	@echo "make type            — mypy --strict"
	@echo "make test            — pytest all"
	@echo "make test-unit       — pytest unit only"
	@echo "make run             — uvicorn dev server"
	@echo "make worker          — celery worker normal queue"
	@echo "make beat            — celery beat"
	@echo "make migrate         — alembic upgrade head"
	@echo "make revision m='x'  — alembic autogenerate revision"
	@echo "make docker-up       — docker compose up -d"
	@echo "make security        — bandit + pip-audit"
	@echo "make shopee-auth-url REDIRECT=...   — Generate Shopee OAuth URL"
	@echo "make shopee-exchange CODE=... SHOP_ID=...  — Exchange code → tokens"
	@echo "make shopee-status   — Show current Shopee token state (needs API up)"

install:
	$(PIP) install -r requirements.txt -r requirements-dev.txt

lint:
	ruff check src/ tests/ migrations/

format:
	ruff format src/ tests/

type:
	mypy --strict src/

test:
	pytest

test-unit:
	pytest tests/unit -n auto --cov=src --cov-report=term-missing

test-int:
	pytest tests/integration

run:
	uvicorn src.api.main:app --reload --host 0.0.0.0 --port 8000

worker:
	celery -A src.workers.app worker --loglevel=info \
	  --queues=orders.created.normal,stock.sync,shipment.confirm,price.sync,reconciliation,default

beat:
	celery -A src.workers.app beat --loglevel=info

migrate:
	alembic upgrade head

revision:
	alembic revision --autogenerate -m "$(m)"

docker-up:
	docker compose up -d

docker-down:
	docker compose down

docker-build:
	docker compose build

security:
	bandit -r src/ -c pyproject.toml
	pip-audit -r requirements.txt

audit:
	detect-secrets scan --baseline .secrets.baseline

smoke:
	curl -fsS http://localhost:8000/health
	curl -fsS http://localhost:8000/ready | jq .

shopee-auth-url:
	@if [ -z "$(REDIRECT)" ]; then \
	  echo "Usage: make shopee-auth-url REDIRECT=https://yourdomain/admin/shopee/callback"; \
	  exit 1; \
	fi
	python3 scripts/shopee_gen_auth_url.py --redirect "$(REDIRECT)"

shopee-exchange:
	@if [ -z "$(CODE)" ] || [ -z "$(SHOP_ID)" ]; then \
	  echo "Usage: make shopee-exchange CODE=xxx SHOP_ID=yyy [DRY=1]"; \
	  exit 1; \
	fi
	python3 scripts/shopee_exchange_token.py --code "$(CODE)" --shop-id "$(SHOP_ID)" \
	  $(if $(DRY),--dry-run,)

shopee-status:
	@curl -fsS "http://localhost:8000/admin/shopee/auth-status?token=$$ADMIN_SECRET_TOKEN" | jq .
