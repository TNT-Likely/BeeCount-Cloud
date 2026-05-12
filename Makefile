.PHONY: setup-backend migrate dev-up dev-api dev-web dev-db seed-demo grant-admin cleanup-diag-users test lint typecheck wipe-local

setup-backend:
	python3 -m venv .venv
	. .venv/bin/activate && pip install -r requirements.txt
	@if [ ! -f .env ]; then cp .env.example .env; fi

migrate:
	. .venv/bin/activate && alembic upgrade head

dev-api: setup-backend migrate
	# --host 0.0.0.0 必要：模拟器/手机经 WiFi 用 IP 访问时才能连进来。
	# uvicorn 默认绑 127.0.0.1，会导致 "Connection refused"。
	. .venv/bin/activate && uvicorn server:app --reload --host 0.0.0.0 --port 8080

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

# 一键清空本地开发数据：停服、删 sqlite dev DB、清 data/ 下运行时附件。
# 保留 data/ 目录结构和 .gitkeep；不动 Postgres。
#
# macOS BSD `find -delete` 不会自动 `-depth`,不加显式 -depth 时 find 会
# 按先序访问目录,试着删非空目录 → EPERM/ENOTEMPTY → `|| true` 把错误吞掉,
# 结果啥都没删。GNU find 默认会 -depth,macOS 必须显式加。
wipe-local:
	@pkill -f "python.*server\.py" 2>/dev/null || true
	@pkill -f "uvicorn server:app" 2>/dev/null || true
	@rm -f beecount.db
	# data/ 下 docs-index.*.sqlite 是构建产物(从 BeeCount-Website 拷贝过来,
	# 几 MB,不该被 wipe 当作业务脏数据清掉 — 删了会导致 AI 文档搜索报
	# AI_DOCS_INDEX_EMPTY,要重新拷贝)。其他业务文件全清。
	@find data -depth -mindepth 1 \
		-not -name '.gitkeep' \
		-not -name 'docs-index.*.sqlite' \
		-delete 2>/dev/null || true
	@echo "✓ wiped: beecount.db + data/* (docs-index.*.sqlite preserved)"
	@echo "next: make migrate && make seed-demo && make dev-api"
