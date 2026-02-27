.PHONY: setup-backend migrate dev-up dev-api dev-web dev-db seed-demo grant-admin cleanup-diag-users test lint typecheck

setup-backend:
	python3 -m venv .venv
	. .venv/bin/activate && pip install --upgrade pip
	. .venv/bin/activate && pip install -r requirements.txt
	@if [ ! -f .env ]; then cp .env.example .env; fi

migrate:
	. .venv/bin/activate && alembic upgrade head

dev-api: setup-backend migrate
	. .venv/bin/activate && uvicorn server:app --reload --port 8080

dev-up:
	./scripts/dev_up.sh

dev-web:
	@if ! command -v pnpm >/dev/null 2>&1; then \
		echo "pnpm is not installed. Please install pnpm 9+ and retry."; \
		echo "Suggested: npm install -g pnpm"; \
		exit 1; \
	fi
	cd frontend && pnpm install --no-frozen-lockfile && pnpm -C apps/web dev

dev-db:
	docker compose -f docker-compose.yml -f docker-compose.postgres.yml up -d db

seed-demo:
	. .venv/bin/activate && PYTHONPATH=. python scripts/seed_demo.py

grant-admin:
	@if [ -z "$(EMAIL)" ]; then \
		echo "Usage: make grant-admin EMAIL=user@example.com"; \
		exit 1; \
	fi
	. .venv/bin/activate && PYTHONPATH=. python scripts/grant_admin.py --email "$(EMAIL)"

cleanup-diag-users:
	. .venv/bin/activate && PYTHONPATH=. python scripts/cleanup_diag_users.py $(if $(APPLY),--apply,)

test:
	. .venv/bin/activate && pytest -q

lint:
	. .venv/bin/activate && ruff check src tests alembic

typecheck:
	. .venv/bin/activate && mypy src
