#!/bin/bash
echo "Starting Bond API on PORT=${PORT}"
set -e

echo "Installing Playwright browsers at runtime..."
python -m playwright install --with-deps chromium

echo "Starting API server..."
exec uvicorn apps.broker.broker:app --host 0.0.0.0 --port ${PORT:-8000}
