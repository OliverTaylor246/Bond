#!/bin/bash
# Quick platform test script

set -e

echo "Bond Platform Test"
echo "=================="
echo ""

# Check if services are running
echo "1. Checking if API is running..."
if curl -s http://localhost:8000/ | grep -q "ok"; then
  echo "   ✓ API is running"
else
  echo "   ✗ API is not running. Start with: cd infra && docker compose up -d"
  exit 1
fi

echo ""
echo "2. Creating a test stream..."
RESPONSE=$(curl -s -X POST http://localhost:8000/v1/streams \
  -H 'Content-Type: application/json' \
  -d '{"natural_language": "BTC price + tweets every 5s"}')

STREAM_ID=$(echo $RESPONSE | jq -r '.stream_id')
WS_URL=$(echo $RESPONSE | jq -r '.ws_url')

echo "   Stream ID: $STREAM_ID"
echo "   WebSocket URL: $WS_URL"

echo ""
echo "3. Listing active streams..."
curl -s http://localhost:8000/v1/streams | jq '.'

echo ""
echo "4. Stream info:"
curl -s http://localhost:8000/v1/streams/$STREAM_ID | jq '.'

echo ""
echo "✓ Platform is working!"
echo ""
echo "To connect to the stream:"
echo "  wscat -c \"$WS_URL\""
echo ""
echo "Or use the Python SDK:"
echo "  python examples/simple_client.py"
