#!/bin/bash
set -e

echo "Installing Playwright browsers at runtime..."
python -m playwright install --with-deps chromium

echo "Starting API server..."
exec uvicorn apps.api.main:app --host 0.0.0.0 --port 8000
