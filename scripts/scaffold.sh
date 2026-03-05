#!/bin/bash
# mfl-ai-gm scaffold — run once from /root/mfl-ai-gm
# Usage: bash scripts/scaffold.sh

set -e

echo "=== mfl-ai-gm scaffold ==="
ROOT="/root/mfl-ai-gm"
cd "$ROOT"

# Verify we're in the right place
if [ ! -f "pyproject.toml" ]; then
    echo "ERROR: Run this from /root/mfl-ai-gm after placing pyproject.toml"
    exit 1
fi

# Source layer
mkdir -p src/mfl_ai_gm/domain
mkdir -p src/mfl_ai_gm/analysis
mkdir -p src/mfl_ai_gm/snapshot
mkdir -p src/mfl_ai_gm/use_cases
mkdir -p src/mfl_ai_gm/adapters

# Service layer
mkdir -p service/static

# Supporting dirs
mkdir -p scripts
mkdir -p tests
mkdir -p data
mkdir -p reports/daily
mkdir -p logs

# __init__.py files
touch src/mfl_ai_gm/__init__.py
touch src/mfl_ai_gm/domain/__init__.py
touch src/mfl_ai_gm/analysis/__init__.py
touch src/mfl_ai_gm/snapshot/__init__.py
touch src/mfl_ai_gm/use_cases/__init__.py
touch src/mfl_ai_gm/adapters/__init__.py
touch service/__init__.py
touch tests/__init__.py

# data gitkeep (data/*.json is gitignored, but keep the dir)
touch data/.gitkeep
touch reports/.gitkeep

echo ""
echo "=== Directory structure ==="
find . -not -path './.git/*' -not -path './.venv/*' -not -path './src/*.egg-info/*' \
    | sort | grep -v __pycache__

echo ""
echo "=== Next: create virtualenv ==="
echo "  python3 -m venv .venv"
echo "  source .venv/bin/activate"
echo "  pip install -e '.[dev]'"
echo "  which python  # should show /root/mfl-ai-gm/.venv/bin/python"
