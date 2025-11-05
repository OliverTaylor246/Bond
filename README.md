# Bond

[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![Python](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](CONTRIBUTING.md)
[![Code style: black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)

**Real-time market data streaming platform for indie quants and AI agents**

Bond lets you spin up custom real-time market data streams on demand. Describe what you want in natural language (e.g., "BTC price + on-chain activity + tweets every 5s"), and get back a live WebSocket feed with aggregated, time-aligned data.

> **Note**: This repository contains the Ekko SDK, examples, and local development tools. Production-grade connectors, hosted runtime with SLAs, and enterprise features are available through [Echo Cloud](#echo-cloud-hosted-service) (contact for access).

## Features

- **Natural language interface** - Describe streams in plain English
- **Multiple data sources** - CCXT (crypto exchanges), Twitter/X, custom WebSockets
- **Real-time aggregation** - Merge and window multiple feeds
- **Signed WebSocket URLs** - Secure, time-limited access tokens
- **Simple Python SDK** - One-line stream creation and consumption
- **Redis Streams backend** - Fast, reliable event buffering
- **Docker-based deployment** - Easy local development

## Quick Start

### 1. Start the platform

```bash
cd infra
docker compose up -d
```

This starts:
- Redis (port 6379)
- API server (port 8000)
- WebSocket broker (port 8080)

### 2. Create a stream

```bash
curl -X POST http://localhost:8000/v1/streams \
  -H 'Content-Type: application/json' \
  -d '{"natural_language": "BTC price + tweets every 5s"}'
```

Response:
```json
{
  "stream_id": "8f2a1c9d4b3e7a10",
  "ws_url": "ws://localhost:8080/ws/8f2a1c9d4b3e7a10?token=...",
  "spec": {...}
}
```

### 3. Connect and listen

Using `wscat`:
```bash
wscat -c "ws://localhost:8080/ws/8f2a1c9d4b3e7a10?token=..."
```

Or using the Ekko Python SDK:
```python
from ekko import listen

async for event in listen("BTC price + tweets every 5s"):
  print(f"Price: {event['price_avg']}, Tweets: {event['tweets']}")
```

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                      Control Plane                      │
│  FastAPI API (port 8000)                                │
│  - Create/list/delete streams                           │
│  - Compile specs, generate stream IDs                   │
│  - Issue signed tokens                                  │
└────────────────────────┬────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────┐
│                      Data Plane                         │
│                                                          │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  │
│  │ CCXT Polling │  │ Twitter/X    │  │ Custom WS    │  │
│  │ Connector    │  │ Connector    │  │ Connector    │  │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘  │
│         │                  │                  │          │
│         └──────────────────┼──────────────────┘          │
│                            ▼                             │
│                  ┌──────────────────┐                    │
│                  │  Pipeline Engine │                    │
│                  │  (aggregate)     │                    │
│                  └────────┬─────────┘                    │
│                           │                              │
│                           ▼                              │
│                  ┌──────────────────┐                    │
│                  │  Redis Streams   │                    │
│                  │  (event buffer)  │                    │
│                  └────────┬─────────┘                    │
└───────────────────────────┼──────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────┐
│                  WebSocket Broker                       │
│  (port 8080)                                            │
│  - Validate tokens                                      │
│  - Fan out events to connected clients                  │
└─────────────────────────────────────────────────────────┘
```

## Project Structure

```
bond/
├── apps/
│   ├── api/              # REST API (create/manage streams)
│   │   ├── main.py       # FastAPI app
│   │   └── crypto.py     # HMAC token auth (security-critical)
│   ├── broker/           # WebSocket fan-out
│   │   └── broker.py     # WS endpoint
│   ├── compiler/         # NL → StreamSpec parser
│   │   └── compiler.py   # Spec compilation and hashing
│   └── runtime.py        # Pipeline orchestration
│
├── connectors/
│   ├── ccxt_polling.py   # CCXT REST polling connector
│   ├── x_stream.py       # Twitter/X connector (mock + real)
│   └── custom_ws.py      # Generic WebSocket connector
│
├── engine/
│   ├── schemas.py        # Event models (DO NOT MODIFY)
│   ├── pipeline_three.py # 3-source merge + aggregate
│   └── dispatch.py       # Redis Streams publisher/consumer
│
├── sdk/
│   └── python/ekko/
│       └── client.py     # Python SDK
│
├── infra/
│   ├── docker-compose.yml
│   ├── Dockerfile.api
│   └── Dockerfile.broker
│
├── requirements.txt
├── Makefile
└── README.md
```

## Python SDK

### Installation

```bash
pip install -e sdk/python
```

### Usage

```python
from ekko import EkkoClient, listen

# Create client
client = EkkoClient(api_url="http://localhost:8000")

# Create and listen to a stream
async for event in client.listen("BTC price + liquidations every 3s"):
  print(event)

# Or use the convenience function
async for event in listen("ETH trades + tweets every 10s"):
  if event["price_avg"]:
    print(f"ETH: ${event['price_avg']:.2f}")
```

## Data Sources

### Crypto Exchanges (CCXT)
- **Type**: `ccxt` or `ccxt.ticker`
- **Exchanges**: Binance, OKX, Bybit
- **Polling**: Every 5 seconds (default, configurable)
- **Data**: Price, volume, trades
- **Latency**: 1-5 seconds

### On-Chain Data (v0.2+)
- **Type**: `onchain` or `onchain.grpc`
- **Network**: Solana (via Yellowstone Geyser)
- **Mode**: Mock mode by default (set `ONCHAIN_MODE=grpc` for real data)
- **Event Types**: `tx`, `transfer`, `block`, `account`, `metric`
- **Endpoint**: Configurable via `GRPC_ENDPOINT` (default: chi1.cracknode.com:10000)

Example on-chain source:
```json
{
  "type": "onchain",
  "chain": "sol",
  "event_types": ["tx", "transfer"]
}
```

### Twitter/X
- **Type**: `twitter` or `x.tweets`
- **Mode**: Mock mode by default (requires `X_BEARER` token for real data)
- **Filtering**: By symbol mentions
- **Rate limits**: Subject to Twitter API limits

### Custom WebSocket
- **Type**: `custom` or `custom.ws`
- **Usage**: Connect to any WebSocket endpoint
- **Mapping**: Custom event mapping function

## API Reference

### Create Stream

**POST** `/v1/streams`

```json
{
  "natural_language": "BTC price + tweets every 5s"
}
```

Or with structured spec:

```json
{
  "spec": {
    "sources": [
      {"type": "ccxt", "symbols": ["BTC/USDT"]},
      {"type": "twitter", "symbols": ["BTC"]}
    ],
    "interval_sec": 5,
    "symbols": ["BTC/USDT"]
  }
}
```

Response:
```json
{
  "stream_id": "8f2a1c9d4b3e7a10",
  "ws_url": "ws://localhost:8080/ws/8f2a1c9d4b3e7a10?token=...",
  "spec": {...}
}
```

### List Streams

**GET** `/v1/streams`

### Delete Stream

**DELETE** `/v1/streams/{stream_id}`

### Refresh Token

**POST** `/v1/streams/{stream_id}/token`

Returns a fresh access token for an existing stream:

```json
{
  "token": "8f2a1c9d4b3e7a10.1698765432.abc123...",
  "ws_url": "ws://localhost:8080/ws/8f2a1c9d4b3e7a10?token=...",
  "expires_in_sec": 3600
}
```

### Get Metrics

**GET** `/v1/streams/{stream_id}/metrics`

Returns performance metrics for a stream:

```json
{
  "stream_id": "8f2a1c9d4b3e7a10",
  "uptime_sec": 125.4,
  "created_at": "2025-10-21T13:30:00Z",
  "msgs_in": 245,
  "msgs_out": 48,
  "dropped": 0,
  "latency_p50_ms": 12.3,
  "latency_p95_ms": 45.7,
  "latency_samples": 245
}
```

## Stream Limits

- **Free tier**: Maximum 5 concurrent streams per project/user
- **Configurable**: Set `BOND_MAX_STREAMS` environment variable
- **Error**: HTTP 429 when limit reached with clear message
- **No paid plans**: Currently no payment system (roadmap item)

## Ring Buffer & Replay

Bond maintains a ring buffer of recent events (default: 60 seconds).

### Replay Historical Events

Connect with `?from=` query parameter:

```bash
# Replay last 60 seconds, then continue live
wscat -c "ws://localhost:8080/ws/{stream_id}?token=...&from=now-60s"

# Replay last 5 minutes
wscat -c "ws://localhost:8080/ws/{stream_id}?token=...&from=now-5m"
```

**Configuration**:
- `BOND_RING_SECONDS` - Ring buffer window (default: 60)
- Older events are automatically trimmed
- Redis Streams MAXLEN keeps last 1000 events

**Use cases**:
- Backfill data after disconnect
- Test strategies on recent data
- Warm up ML models with context

## Natural Language Examples

```
"BTC price + tweets every 5s"
"ETH trades + liquidations every 10 seconds"
"BTC/USDT market data with twitter feed"
"SOL price every 3s"
```

The compiler extracts:
- **Symbols**: BTC, ETH, SOL, etc.
- **Interval**: "every 5s", "every 10 seconds"
- **Sources**: price → CCXT, tweets → Twitter, liquidations → custom

## Event Schema

Aggregated events have this structure:

```json
{
  "ts": "2025-10-21T13:30:05Z",
  "price_avg": 68123.5,
  "volume_sum": 134.2,
  "tweets": 8,
  "custom_count": 2,
  "raw_data": {
    "trades": 15,
    "sources": ["binance", "okx"]
  }
}
```

## Development

### Install dependencies

```bash
make install
```

### Run tests

```bash
make test
```

### Lint code

```bash
make lint
```

### Start services

```bash
make up
```

### View logs

```bash
make logs
```

### Stop services

```bash
make down
```

## Configuration

### Environment Variables

- `REDIS_URL` - Redis connection string (default: `redis://localhost:6379`)
- `WS_HOST` - WebSocket host for client URLs (default: `localhost:8080`)
- `BOND_SECRET` - HMAC secret for token signing (**must change in production**)

### Coding Conventions

- **2 spaces** for Python indentation
- Type hints mandatory for all public functions
- Use `async/await` for all I/O operations
- Classes in `PascalCase`, functions in `snake_case`

## Known Limitations

- This MVP uses CCXT polling (REST) instead of `ccxt.pro` (WebSocket)
  - Slightly higher latency (~1-5s) but simpler implementation
- Twitter connector is mock by default (requires API credentials for real data)
- Docker Compose setup is for **dev only** (not production-ready)
- Redis Streams is single-node (migrate to Kafka/Redpanda for scale)

## Security Notes

- **Token authentication**: All WebSocket connections require signed tokens
- **Tokens expire** after 1 hour by default (configurable)
- **HMAC verification** prevents token forgery
- **⚠️  Change `BOND_SECRET`** in production (use strong random value)
- Do not modify `apps/api/crypto.py` without security review

## Roadmap

- [ ] Add `ccxt.pro` WebSocket support for lower latency
- [ ] Real Twitter API v2 integration
- [ ] Additional connectors (news, on-chain data, options flow)
- [ ] JavaScript/TypeScript SDK
- [ ] Stream transforms (filters, projections, custom functions)
- [ ] Walk-forward optimization for AI agents
- [ ] Kubernetes deployment with Helm charts

## Contributing

See [CLAUDE.md](CLAUDE.md) for architecture details and AI agent guidelines.

## Echo Cloud (Hosted Service)

Echo Cloud provides production-grade infrastructure for running Echo streams at scale:

- **Production connectors**: Real-time WebSocket feeds (ccxt.pro), authenticated Twitter streams, verified on-chain gRPC
- **Enterprise SLAs**: 99.9% uptime, guaranteed latency targets, 24/7 monitoring
- **Higher limits**: 100+ concurrent streams, custom rate limits, dedicated resources
- **Advanced features**: Multi-region deployment, custom transforms, historical replay (24h+), ML model serving
- **Support**: Priority support, custom connector development, integration assistance

**Interested in Echo Cloud?** Contact us for pilot access or enterprise pricing.

## Compliance & Usage Notes

**Important:** Users of Echo must comply with the Terms of Service of all data sources they connect to:

- **CCXT exchanges**: Respect rate limits and usage policies of Binance, OKX, Bybit, etc.
- **Twitter/X API**: Requires valid API credentials and adherence to Twitter's Terms of Service
- **On-chain data**: Yellowstone Geyser usage subject to provider's terms

**Redistribution Advisory:** Echo is designed for personal/internal use by indie quants and researchers. Redistribution of raw market data streams may violate data provider agreements. Users are responsible for ensuring their usage complies with all applicable terms and regulations.

## License

Apache License 2.0 - See [LICENSE](LICENSE) file for details.

Copyright 2024-2025 Echo Contributors
