.PHONY: dev test lint typecheck check-arch migrate seed run-api run-worker docker-up docker-down

dev:
	pip install -e ".[dev]"
	pre-commit install

test:
	pytest

lint:
	ruff check .
	ruff format --check .

typecheck:
	mypy .

check-arch:
	# Fails the build if core/ imports apps/, or if test_execution imports infra/llm —
	# see docs/PROJECT_STRUCTURE.md points 1 and 3, enforced via pyproject.toml [tool.importlinter]
	lint-imports

migrate:
	alembic upgrade head

seed:
	python scripts/seed_dev_db.py

run-api:
	uvicorn apps.api.main:app --reload

run-worker:
	# celery -A apps.worker.celery_app worker --loglevel=info
	# OR: arq apps.worker.arq_app.WorkerSettings
	# — fill in once the worker/queue library is chosen (docs/PROJECT_STRUCTURE.md)
	@echo "worker entrypoint not yet chosen — see docs/PROJECT_STRUCTURE.md"

docker-up:
	docker compose -f deploy/docker/docker-compose.yml up -d

docker-down:
	docker compose -f deploy/docker/docker-compose.yml down
