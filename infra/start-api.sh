#!/bin/bash
set -e

SERVICE=${1:-api}
PORT=${PORT:-8000}

if [ "$SERVICE" = "Broker" ]; then
  echo "Starting Bond Broker on PORT=${PORT}..."
  exec uvicorn apps.broker.broker:app --host 0.0.0.0 --port ${PORT}
else
  echo "Starting Bond API on PORT=${PORT}..."
  echo "Installing Playwright browsers at runtime..."
  python -m playwright install --with-deps chromium
  echo "Starting API server..."
  exec uvicorn apps.api.main:app --host 0.0.0.0 --port ${PORT}
fi
