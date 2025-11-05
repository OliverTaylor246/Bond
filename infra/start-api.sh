#!/bin/bash
set -e

SERVICE="${1:-${BOND_SERVICE:-api}}"

if [[ "${SERVICE}" == "broker" ]]; then
  PORT_TO_USE="${PORT:-8080}"
  echo "Starting Bond Broker on PORT=${PORT_TO_USE}"
  exec uvicorn apps.broker.broker:app --host 0.0.0.0 --port "${PORT_TO_USE}"
else
  PORT_TO_USE="${PORT:-8000}"
  echo "Starting Bond API on PORT=${PORT_TO_USE}"
  echo "Installing Playwright browsers at runtime..."
  python -m playwright install --with-deps chromium
  echo "Starting API server..."
  exec uvicorn apps.api.main:app --host 0.0.0.0 --port "${PORT_TO_USE}"
fi
