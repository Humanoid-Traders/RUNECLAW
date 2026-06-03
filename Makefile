# =============================================================================
# RUNECLAW Makefile v2 -- Developer convenience targets
# =============================================================================

.PHONY: install lint format run-cli run-telegram run-scan run-api \
        test test-core test-security test-cov deploy health logs \
        logs-api logs-bot cert db-init db-users db-shell clean help

PYTHON   ?= python3
COMPOSE  := docker compose
API_URL  ?= http://localhost:8000

# -- Colours -----------------------------------------------------------------
CYAN  := \033[36m
RESET := \033[0m

help: ## Show all available targets
	@echo ""
	@echo "  RUNECLAW v2 -- make targets"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  $(CYAN)%-18s$(RESET) %s\n", $$1, $$2}'
	@echo ""

# -- Setup -------------------------------------------------------------------
install: ## Install all Python deps (bot + api_bridge)
	$(PYTHON) -m pip install --upgrade pip
	$(PYTHON) -m pip install -r bot/requirements.txt
	$(PYTHON) -m pip install "fastapi>=0.110" "uvicorn[standard]>=0.29"

# -- Code quality ------------------------------------------------------------
lint: ## ruff + mypy
	ruff check bot/ api_bridge.py
	ruff format bot/ api_bridge.py --check
	mypy bot/ --ignore-missing-imports

format: ## Auto-format with ruff
	ruff format bot/ api_bridge.py
	ruff check bot/ api_bridge.py --fix

# -- Local run ---------------------------------------------------------------
run-cli: ## Start interactive CLI
	$(PYTHON) -m bot.main --mode cli

run-telegram: ## Start Telegram bot
	$(PYTHON) -m bot.main --mode telegram

run-scan: ## One-shot market scan
	$(PYTHON) -m bot.main --mode scan

run-api: ## Start API bridge on :8000
	$(PYTHON) -m uvicorn api_bridge:app --host 0.0.0.0 --port 8000 --reload

# -- Tests -------------------------------------------------------------------
test: ## Run full test suite
	$(PYTHON) -m pytest tests/ -v --tb=short

test-core: ## Core tests only
	$(PYTHON) -m pytest tests/test_core.py -v --tb=short

test-security: ## Security tests only
	$(PYTHON) -m pytest tests/test_security.py -v --tb=short

test-cov: ## Tests with coverage report
	$(PYTHON) -m pytest tests/ -v --tb=short \
		--cov=bot --cov-report=html --cov-report=term-missing

# -- Docker / production -----------------------------------------------------
deploy: ## Build image + start all services (bot, api_bridge, redis, nginx)
	BUILD_SHA=$$(git rev-parse --short HEAD) \
		$(COMPOSE) up -d --build --wait

health: ## Check API bridge health endpoint
	@curl -sf \
		-H "Authorization: Bearer $$(grep DASHBOARD_TOKEN .env | cut -d= -f2)" \
		$(API_URL)/health \
		| python3 -m json.tool \
		|| echo "API not reachable at $(API_URL)"

logs: ## Follow all service logs
	$(COMPOSE) logs -f --tail=100

logs-api: ## Follow api_bridge logs only
	$(COMPOSE) logs -f --tail=100 api_bridge

logs-bot: ## Follow bot logs only
	$(COMPOSE) logs -f --tail=100 bot

# -- Database ----------------------------------------------------------------
db-init: ## Initialise SQLite user database
	$(PYTHON) -c "from bot.db.models import init_db; init_db(); print('DB ready')"

db-users: ## List all registered users
	$(PYTHON) -c "from bot.db.models import list_users, user_count; \
	  print(user_count()); \
	  [print(u['email'], '|', u['plan'], '|', 'linked' if u['chat_id'] else 'unlinked') \
	   for u in list_users()]"

db-shell: ## Open SQLite shell on data/runeclaw.db
	sqlite3 data/runeclaw.db

# -- Cert helper (run once on VPS) -------------------------------------------
cert: ## Get Let's Encrypt cert (set DOMAIN= e.g. make cert DOMAIN=runeclaw.example.com)
	certbot certonly --standalone -d $(DOMAIN)

# -- Cleanup -----------------------------------------------------------------
clean: ## Remove caches and build artefacts
	find . -type d -name __pycache__  -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .mypy_cache  -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .ruff_cache  -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
	rm -rf dist/ build/ htmlcov/
