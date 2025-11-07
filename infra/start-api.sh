#!/bin/bash
echo "Starting Bond API on PORT=${PORT}"
set -e

echo "Installing Playwright browsers at runtime..."
python -m playwright install chromium

echo "Starting API server..."
exec uvicorn apps.api.main:app --host 0.0.0.0 --port ${PORT:-8000}
