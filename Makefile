# =============================================================================
# RUNECLAW Makefile -- Developer convenience targets
# =============================================================================

.PHONY: install lint format run-cli run-telegram run-scan test clean help

PYTHON ?= python3

help: ## Show this help message
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

install: ## Install Python dependencies
	$(PYTHON) -m pip install --upgrade pip
	$(PYTHON) -m pip install -r bot/requirements.txt

lint: ## Run ruff linter and mypy type checks
	ruff check bot/
	ruff format bot/ --check
	mypy bot/ --ignore-missing-imports

format: ## Auto-format code with ruff
	ruff format bot/
	ruff check bot/ --fix

run-cli: ## Start the interactive CLI
	$(PYTHON) -m bot.main --mode cli

run-telegram: ## Start the Telegram bot
	$(PYTHON) -m bot.main --mode telegram

run-scan: ## Run a one-shot market scan
	$(PYTHON) -m bot.main --mode scan

test: ## Run the test suite
	$(PYTHON) -m pytest tests/ -v --tb=short

clean: ## Remove caches and build artifacts
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .mypy_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .ruff_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
	rm -rf dist/ build/
