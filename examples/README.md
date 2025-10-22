# Bond Examples

This directory contains example scripts and usage patterns for Bond.

## Quick Start

### 1. Platform Test Script

Test that Bond is running correctly:

```bash
./test_platform.sh
```

This script:
- Checks if API is running
- Creates a test stream
- Lists active streams
- Shows stream info

**Requirements**: `curl`, `jq`

### 2. Simple Python Client

Example of using the Bond Python SDK:

```bash
python simple_client.py
```

This demonstrates:
- Creating a stream with natural language
- Connecting to WebSocket
- Receiving real-time events

**Requirements**: Bond SDK installed (`pip install -e ../sdk/python`)

## Usage Patterns

### Pattern 1: One-liner Stream

```python
from bond import listen

async for event in listen("BTC price every 5s"):
  print(event["price_avg"])
```

### Pattern 2: Client Object

```python
from bond import BondClient

client = BondClient(api_url="http://localhost:8000")

# Create stream
result = await client.create_stream("BTC + ETH prices every 3s")
print(f"Stream ID: {result['stream_id']}")

# Listen
async for event in client.listen("BTC + ETH prices every 3s"):
  print(event)
```

### Pattern 3: With Callback

```python
from bond import listen

def handle_event(event):
  if event["price_avg"]:
    print(f"BTC: ${event['price_avg']:.2f}")

async for event in listen("BTC price every 5s", callback=handle_event):
  pass  # Callback already handles events
```

### Pattern 4: Multiple Streams

```python
import asyncio
from bond import listen

async def watch_btc():
  async for event in listen("BTC price every 5s"):
    print(f"BTC: {event['price_avg']}")

async def watch_eth():
  async for event in listen("ETH price every 5s"):
    print(f"ETH: {event['price_avg']}")

# Run both concurrently
await asyncio.gather(watch_btc(), watch_eth())
```

## Natural Language Examples

### Crypto Prices
```python
"BTC price every 5s"
"BTC/USDT market data every 3 seconds"
"ETH + SOL + AVAX prices every 10s"
```

### With Social Data
```python
"BTC price + tweets every 5s"
"ETH trades + twitter sentiment every 10 seconds"
```

### With Liquidations
```python
"BTC price + liquidations every 5s"
"SOL market data + liquidation alerts every 3s"
```

### Multiple Sources
```python
"BTC price + tweets + liquidations every 5s"
"ETH trades + volume + twitter feed every 10 seconds"
```

## Testing with wscat

Install wscat:
```bash
npm install -g wscat
```

Create a stream first:
```bash
curl -X POST http://localhost:8000/v1/streams \
  -H 'Content-Type: application/json' \
  -d '{"natural_language": "BTC price every 5s"}'
```

Connect to WebSocket:
```bash
wscat -c "ws://localhost:8080/ws/{stream_id}?token={token}"
```

## Troubleshooting

### Services not running
```bash
cd ../infra
docker compose up -d
docker compose logs -f
```

### Python SDK not found
```bash
cd ../sdk/python
pip install -e .
```

### WebSocket connection refused
- Check broker is running on port 8080
- Verify token is not expired (1 hour TTL)
- Check `WS_HOST` environment variable

### No events received
- Streams aggregate over intervals (default 5s)
- Check if stream is running: `curl http://localhost:8000/v1/streams`
- View broker logs: `docker compose logs broker`

## Advanced Examples

### Custom Aggregation Window
```python
spec = {
  "sources": [
    {"type": "ccxt", "symbols": ["BTC/USDT"]},
    {"type": "twitter", "symbols": ["BTC"]}
  ],
  "interval_sec": 1,  # 1 second aggregation
  "symbols": ["BTC/USDT"]
}

async for event in listen(spec):
  print(event)
```

### Multiple Symbols
```python
spec = {
  "sources": [{"type": "ccxt", "symbols": ["BTC/USDT", "ETH/USDT"]}],
  "interval_sec": 5,
  "symbols": ["BTC/USDT", "ETH/USDT"]
}
```

## More Examples

See the main README for additional usage patterns and API documentation.
