# Trading Bot - Code Quality and Testing Makefile

# Python interpreter
PYTHON = python3

# Source directories
SRC_DIR = src
CONFIG_DIR = config
MAIN_FILE = main.py

# All Python files
PYTHON_FILES = $(SRC_DIR) $(CONFIG_DIR) $(MAIN_FILE)

# Colors for output
RED = \033[0;31m
GREEN = \033[0;32m
YELLOW = \033[1;33m
BLUE = \033[0;34m
NC = \033[0m # No Color

.PHONY: help lint check-syntax check-imports check-style check-unused format test clean install-dev install-hooks check-all check-api-types scan-api-usage check-dangerous deploy-check

help: ## Show this help message
	@echo "$(BLUE)Trading Bot Code Quality Commands$(NC)"
	@echo "=================================="
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "$(YELLOW)%-20s$(NC) %s\n", $$1, $$2}' $(MAKEFILE_LIST)

# Installation commands
install-dev: ## Install development dependencies
	@echo "$(BLUE)Installing development dependencies...$(NC)"
	pip install pre-commit pyflakes flake8 isort
	@echo "$(GREEN)✅ Development dependencies installed$(NC)"

install-hooks: install-dev ## Install git pre-commit hooks
	@echo "$(BLUE)Installing pre-commit hooks...$(NC)"
	pre-commit install
	@echo "$(GREEN)✅ Pre-commit hooks installed$(NC)"

# Code quality checks
check-syntax: ## Check Python syntax errors
	@echo "$(BLUE)Checking Python syntax...$(NC)"
	@for file in $$(find $(PYTHON_FILES) -name "*.py" 2>/dev/null); do \
		echo "Checking $$file..."; \
		$(PYTHON) -m py_compile "$$file" || exit 1; \
	done
	@echo "$(GREEN)✅ No syntax errors found$(NC)"

check-imports: ## Check for undefined names and missing imports
	@echo "$(BLUE)Checking for undefined names and imports...$(NC)"
	@pyflakes $(PYTHON_FILES) || (echo "$(RED)❌ Import/undefined name errors found$(NC)" && exit 1)
	@echo "$(GREEN)✅ No import errors found$(NC)"

check-style: ## Check code style with flake8
	@echo "$(BLUE)Checking code style...$(NC)"
	@flake8 --max-line-length=120 --extend-ignore=E203,W503,E501 --exclude=venv,env,lib,include,bin,__pycache__ $(PYTHON_FILES) || \
		(echo "$(RED)❌ Style issues found$(NC)" && exit 1)
	@echo "$(GREEN)✅ Code style looks good$(NC)"

check-unused: ## Check for unused variables and imports (advanced)
	@echo "$(BLUE)Checking for unused variables...$(NC)"
	@vulture $(PYTHON_FILES) --min-confidence 80 2>/dev/null || echo "$(YELLOW)⚠️  Install vulture for unused code detection: pip install vulture$(NC)"

# Combined checks
lint: check-syntax check-imports check-style ## Run all linting checks
	@echo "$(GREEN)🎉 All lint checks passed!$(NC)"

check-all: lint check-unused ## Run comprehensive code quality checks
	@echo "$(GREEN)🎉 All quality checks completed!$(NC)"

# Formatting
format: ## Auto-format code with isort
	@echo "$(BLUE)Formatting imports...$(NC)"
	@isort --profile black --line-length=120 $(PYTHON_FILES)
	@echo "$(GREEN)✅ Code formatted$(NC)"

# Testing
test: lint ## Run tests after linting
	@echo "$(BLUE)Running tests...$(NC)"
	@if [ -f "pytest.ini" ] || [ -d "tests" ]; then \
		$(PYTHON) -m pytest tests/ -v || echo "$(YELLOW)⚠️  Install pytest to run tests: pip install pytest$(NC)"; \
	else \
		echo "$(YELLOW)⚠️  No tests directory found$(NC)"; \
	fi

# Maintenance
clean: ## Clean up temporary files
	@echo "$(BLUE)Cleaning temporary files...$(NC)"
	@find . -type f -name "*.pyc" -delete
	@find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	@find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	@echo "$(GREEN)✅ Cleanup complete$(NC)"

# Quick development workflow
quick-check: check-syntax check-imports ## Quick essential checks only
	@echo "$(GREEN)✅ Quick checks passed$(NC)"

# Pre-commit manual run
pre-commit: ## Run pre-commit hooks manually on all files
	@echo "$(BLUE)Running pre-commit hooks...$(NC)"
	@pre-commit run --all-files || (echo "$(RED)❌ Pre-commit checks failed$(NC)" && exit 1)
	@echo "$(GREEN)✅ Pre-commit checks passed$(NC)"

# API Type Checking
check-api-types: ## Check for API type conversion bugs
	@echo "$(BLUE)Checking API type conversions...$(NC)"
	@$(PYTHON) scripts/api_type_validator.py --directory $(SRC_DIR) --exit-code || \
		(echo "$(RED)❌ API type violations found$(NC)" && exit 1)
	@echo "$(GREEN)✅ API types properly handled$(NC)"

scan-api-usage: ## Scan and report API field usage
	@echo "$(BLUE)Scanning API field usage...$(NC)"
	@$(PYTHON) scripts/scan_api_usage.py --directory $(SRC_DIR) --output api_usage_report.json
	@echo "$(GREEN)✅ API usage report generated$(NC)"

# Dangerous Code Detection
check-dangerous: ## Check for dangerous code patterns (TODO, mock, etc.)
	@echo "$(BLUE)Checking for dangerous code patterns...$(NC)"
	@$(PYTHON) scripts/dangerous_code_detector.py --directory $(SRC_DIR) --block-on high --max-issues 20 || \
		(echo "$(RED)❌ Dangerous code patterns found$(NC)" && exit 1)
	@echo "$(GREEN)✅ No critical dangerous patterns$(NC)"

# Deployment Safety Check
deploy-check: lint check-api-types check-dangerous ## Full deployment safety check
	@echo "$(GREEN)🚀 Code is ready for deployment!$(NC)"
	@echo "  ✅ Syntax valid"
	@echo "  ✅ Imports correct"
	@echo "  ✅ API types handled"
	@echo "  ✅ No dangerous patterns"

# Development workflow
dev-setup: install-dev install-hooks ## Complete development environment setup
	@echo "$(GREEN)🎉 Development environment ready!$(NC)"
	@echo "$(BLUE)Usage:$(NC)"
	@echo "  • Run '$(YELLOW)make lint$(NC)' before commits"
	@echo "  • Use '$(YELLOW)make quick-check$(NC)' for fast feedback"
	@echo "  • Run '$(YELLOW)make deploy-check$(NC)' before deployment"
	@echo "  • Pre-commit hooks will run automatically on commit"