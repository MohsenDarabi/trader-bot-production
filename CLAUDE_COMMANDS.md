# Claude Commands Reference

This document provides a quick reference guide for common development, deployment, and maintenance tasks for the Trading Bot project. These commands are designed to help AI assistants (like Claude) efficiently work with the codebase by providing standardized workflows and best practices.

## What are Claude Commands?

Claude commands are standardized reference guides that help AI assistants quickly identify and execute common development tasks. They ensure consistency across development sessions and provide quick access to validated workflows that have been tested and proven to work.

## Development Commands

### Code Quality Checks

#### Essential Pre-Commit Checks
```bash
# Run all linting checks (syntax, imports, style)
make lint

# Quick essential checks only (faster)
make quick-check

# Full deployment safety check
make deploy-check
```

#### Specific Quality Checks
```bash
# Check Python syntax errors
make check-syntax

# Check for undefined names and missing imports
make check-imports

# Check code style with flake8
make check-style

# Check for dangerous code patterns (TODO, mock, etc.)
make check-dangerous

# Validate API type conversions (string to float safety)
make check-api-types

# Scan and report API field usage
make scan-api-usage
```

#### Code Formatting
```bash
# Auto-format imports
make format

# Clean temporary files
make clean
```

#### Pre-commit Hooks
```bash
# Install development environment
make dev-setup

# Run pre-commit hooks manually
make pre-commit

# Install just the hooks
make install-hooks
```

### Testing
```bash
# Run tests after linting
make test

# Comprehensive quality checks
make check-all
```

## Deployment Commands

### VM Deployment with deploy.sh

The deployment script supports any 3-8 character crypto symbol and automatically manages credentials.

#### Basic Usage Pattern
```bash
./deploy.sh [SYMBOL] [ACTION]
```

#### Supported Actions
- `stop` - Stop specified bot
- `update` - Update code and restart bot 
- `restart` - Restart bot without code update

#### Common Deployment Examples
```bash
# Update and restart ADA bot
./deploy.sh ada update

# Restart ADA bot without code update
./deploy.sh ada restart

# Stop ADA bot
./deploy.sh ada stop

# Deploy new AAVE bot (auto-creates .env.aave)
./deploy.sh aave update

# Deploy new VET bot
./deploy.sh vet update

# Deploy new DOGE bot
./deploy.sh doge update
```

#### Deployment Features
- **Automatic credential management:** Borrows unused credentials from non-running bots
- **Environment file creation:** Automatically creates .env files for new symbols
- **Market symbol conversion:** Automatically converts symbols to trading pairs (e.g., AAVE → AAVEUSDT)
- **Docker integration:** Ensures Docker is running before deployment
- **Cross-platform support:** Works on macOS and Linux

### Deployment Safety Workflow
```bash
# 1. Always run safety checks before deployment
make deploy-check

# 2. If checks pass, deploy to VM
./deploy.sh [symbol] update

# 3. Monitor logs on VM for successful startup
```

## Git Workflow with Safety Checks

### Pre-Commit Workflow
```bash
# 1. Run code quality checks
make lint

# 2. Stage files selectively
git add -p

# 3. Commit with meaningful message
git commit -m "Descriptive commit message"

# Note: Pre-commit hooks run automatically and will block invalid commits
```

### Automated Safety Features
- **Pre-commit hooks:** Automatically run on every commit
- **Syntax validation:** Prevents commits with Python syntax errors
- **Import checking:** Detects undefined names and missing imports
- **API type validation:** Ensures safe string-to-float conversions
- **Dangerous code detection:** Finds TODOs, mock functions, and debug code

### Branch Management
```bash
# Check current branch status
git status

# View recent commits
git log --oneline -20

# Safe branch switching (ensures clean state)
git stash
git checkout [branch-name]
git stash pop
```

## Common Development Patterns

### Before Making Changes
1. Run `make quick-check` for fast validation
2. Read relevant files to understand context
3. Use search tools to find similar patterns

### Before Committing
1. Run `make lint` to ensure code quality
2. Use `git add -p` for selective staging
3. Write descriptive commit messages
4. Let pre-commit hooks validate automatically

### Before Deployment
1. Run `make deploy-check` for full validation
2. Verify all tests pass
3. Check that dangerous patterns are resolved
4. Use deployment script for consistent VM updates

## Repeating Tasks That Could Be Automated

Based on recent development patterns, the following tasks are commonly repeated and could benefit from automation:

### Already Automated
- **Type conversion validation** - Pre-commit hooks catch dangerous float() usage
- **Code style checking** - Automated via make commands and pre-commit
- **Import validation** - Prevents commits with missing imports
- **Dangerous code detection** - Automatically scans for TODOs and mock functions

### Candidates for Future Automation
- **Price precision formatting** - Could create helper script for consistent decimal places
- **Environment file management** - Could create setup script for new bot instances
- **Log monitoring and analysis** - Could create monitoring script for key metrics
- **Performance metrics collection** - Could create reporting script for trading performance

## Emergency Commands

### Quick Problem Resolution
```bash
# If bot is not responding
./deploy.sh [symbol] restart

# If code has issues
make lint  # Identify problems
make clean # Clean temporary files

# If deployment fails
./deploy.sh [symbol] stop
# Fix issues, then:
./deploy.sh [symbol] update
```

### Debugging
```bash
# Check for common issues
make check-dangerous

# Validate API handling
make check-api-types

# Check recent git changes
git log --oneline -10
git diff HEAD~1
```

---

**Note:** This reference guide is designed to help AI assistants quickly identify the right commands for common tasks. Always verify that commands are appropriate for the current context before execution.