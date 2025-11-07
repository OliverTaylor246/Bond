#!/bin/bash
# Start Xvfb in the background
Xvfb :99 -screen 0 1920x1080x24 &
export DISPLAY=:99

# Wait for Xvfb to be ready
sleep 2

# Start uvicorn
exec uvicorn apps.api.main:app --host 0.0.0.0 --port 8000
