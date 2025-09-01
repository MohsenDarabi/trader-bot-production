# 🔍 Automated Code Quality System

This project now has automated code quality checking to prevent bugs like missing imports, undefined variables, and syntax errors.

## 🚀 Quick Start

### 1. Setup (One-time)
```bash
# Install development dependencies and git hooks
make dev-setup
```

### 2. Daily Usage
```bash
# Quick essential checks (runs in ~5 seconds)
make quick-check

# Full lint checks (comprehensive)
make lint

# Check specific issues
make check-syntax      # Syntax errors only
make check-imports     # Missing imports & undefined names
make check-style       # Code style issues
```

## 🛡️ Automatic Protection

### Pre-commit Hooks
- **Automatically runs** before every `git commit`
- **Prevents committing** code with critical errors
- Checks for:
  - ✅ Undefined variables (like `OrderStatus` bug we found)
  - ✅ Missing imports
  - ✅ Syntax errors
  - ✅ Import sorting
  - ✅ Basic code style

### Manual Override (Emergency)
```bash
# Skip pre-commit hooks (use sparingly!)
git commit --no-verify -m "Emergency fix"
```

## 📊 What Gets Checked

### Critical Errors (Will Block Commits)
- ❌ **Undefined names**: `NameError` at runtime
- ❌ **Missing imports**: `ImportError` at runtime  
- ❌ **Syntax errors**: Won't even run
- ❌ **Merge conflicts**: Broken git state

### Code Quality Issues (Warnings)
- ⚠️  **Unused imports**: Clutter but not fatal
- ⚠️  **Unused variables**: Might indicate bugs
- ⚠️  **Style violations**: PEP 8 compliance
- ⚠️  **f-string placeholders**: Missing {}

## 🔧 Available Commands

| Command | Speed | Purpose | Use When |
|---------|-------|---------|----------|
| `make quick-check` | ⚡ Fast | Essential errors only | Before coding sessions |
| `make lint` | 🐌 Slower | All lint checks | Before commits |
| `make check-syntax` | ⚡ Fast | Python syntax only | After big changes |
| `make check-imports` | ⚡ Fast | Missing imports/undefined names | After refactoring |
| `make pre-commit` | 🐌 Slower | Run all pre-commit hooks | Testing hook setup |

## 🎯 Integration Points

### With Git
```bash
# Hooks run automatically on:
git commit    # Pre-commit hooks
git push      # Can add pre-push hooks later
```

### With Your Workflow
```bash
# Before starting work
make quick-check

# During development (optional)
make check-syntax

# Before committing
make lint
git add .
git commit -m "Your changes"  # Hooks run automatically
```

## 🐛 Found Issues

The system immediately caught these existing issues:
1. **FIXED**: `OrderStatus` undefined in `order_pairing_manager.py`
2. **TODO**: `pending_sell_orders` undefined in `trading_bot.py:1619,1626`
3. Various unused imports (non-critical)

## 🎛️ Configuration

### Modify Checks
Edit `.pre-commit-config.yaml` or `Makefile` to:
- Add/remove linters
- Change severity levels
- Exclude certain files
- Adjust rules

### Disable Specific Rules
```python
# pylint: disable=unused-variable
variable = "not used"

# flake8: noqa
import unused_module  # flake8: noqa
```

## 🚨 Emergency Procedures

### If Hooks Block Valid Code
```bash
# Temporary disable
git commit --no-verify -m "Fix critical bug"

# Then fix the quality issues
make lint
git add .
git commit -m "Clean up code quality"
```

### If System Gives False Positives
1. Edit `.pre-commit-config.yaml`
2. Add exceptions to specific rules
3. Re-run: `make pre-commit`

## 📈 Benefits You'll See

- ✅ **No more runtime NameErrors** from typos
- ✅ **No more ImportErrors** from missing imports  
- ✅ **Consistent code style** across the project
- ✅ **Early bug detection** before code reaches production
- ✅ **Cleaner git history** with fewer "fix typo" commits

## 💡 Pro Tips

1. **Run `make quick-check` frequently** - it's fast and catches critical issues
2. **Let pre-commit hooks do their job** - don't bypass unless emergency
3. **Fix warnings gradually** - unused imports won't break anything but clean them up over time
4. **Use `make help`** to see all available commands

---

*This system was implemented to automatically catch bugs like the `OrderStatus` import issue that was manually discovered.*