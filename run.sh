#!/bin/bash
# Run the Tax Client Intake Automation pipeline
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

if [ ! -f ".venv/bin/python" ]; then
    echo "Virtual environment not found. Creating .venv..."
    python3 -m venv .venv
    .venv/bin/pip install -r requirements.txt
    .venv/bin/playwright install chromium
fi

echo "Running Tax Client Intake Automation..."
exec .venv/bin/python main.py "$@"
