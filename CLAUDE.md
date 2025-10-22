# CLAUDE.md

## Project Overview
Project Name: **Bond** (v0.2 - Indie Quant + On-Chain gRPC)
Description: A hosted platform + SDK targeting **indie quants and ML researchers** to create **custom real-time market data streams** on demand. Users describe ("BTC price + on-chain transfers + tweets every 5s") and the system produces a live WebSocket feed with a unique hash ID.

**Audience**: Indie quants, ML researchers, algorithmic traders
**Primary Sources**:
- Crypto market data (CCXT REST polling, ~5s latency)
- On-chain data via gRPC (Solana Yellowstone/Geyser from chi1.cracknode.com:10000)
- Twitter/X filtered stream (real API or mock)
- Custom WebSocket feeds (configurable)

**Limits (Free Tier MVP)**:
- Max 5 concurrent streams per user/project
- No paid plans yet
- Ring buffer replay: 60 seconds (configurable)

## Architecture & Tech Stack
- **Control plane**: FastAPI (Python) for REST endpoints with structured routes
- **Data plane**: Redis Streams as event buffer & fan-out with replay capability
- **Connectors**: CCXT polling, on-chain gRPC (Solana), X API, custom WS
- **Pipeline engine**: Async Python (asyncio), merges sources, applies transforms v1 (resample, count, project)
- **Transforms v1**: resample (time windows + aggregations), count (event counting), project (field selection)
- **Metrics**: Per-stream performance tracking (msgs in/out, p50/p95 latency, dropped, uptime)
- **Replay**: Ring buffer with ?from=now-60s support via Redis Streams
- **SDK**: Python BondClient with listen() helpers
- **Auth**: HMAC-based tokens (sid.exp.signature format), 1h TTL default
- **Infra**: Docker Compose locally (redis + api + broker), Kubernetes/Helm later for scale
- **License**: Apache-2.0

## Directory Structure
```
bond/
├── apps/
│   ├── api/
│   │   ├── main.py
│   │   ├── routes/
│   │   │   ├── streams.py      # Stream CRUD endpoints
│   │   │   └── metrics.py      # Metrics endpoints
│   │   ├── schemas.py          # API request/response models
│   │   ├── crypto.py           # HMAC tokens (SECURITY-CRITICAL)
│   │   └── limits.py           # 5-stream limit enforcement
│   ├── broker/
│   │   └── broker.py           # WebSocket /ws/{sid}?token=...
│   ├── compiler/
│   │   ├── dsl.py              # Pydantic StreamSpec models
│   │   ├── schema.json         # JSON Schema for StreamSpec v1
│   │   └── compiler.py         # NL → StreamSpec; canonicalize+hash
│   └── runtime.py              # Pipeline orchestration, replay, limits
│
├── connectors/
│   ├── ccxt_polling.py         # CCXT REST ticker/trades (5s default)
│   ├── onchain_grpc.py         # Solana Yellowstone/Geyser connector
│   ├── x_stream.py             # Twitter/X (real or mock)
│   └── custom_ws.py            # Configurable WS + mapping
│
├── engine/
│   ├── schemas.py              # Event models (DO NOT MODIFY - see below)
│   ├── pipeline_three.py       # 3-source merge + windowing + transforms
│   └── dispatch.py             # Redis Streams publisher + replay helpers
│
├── sdk/python/bond/
│   ├── __init__.py
│   └── client.py               # BondClient, listen(), listen_sid()
│
├── examples/
│   ├── quickstart_nl.py        # Natural language quickstart
│   ├── spec_btc_onchain_tweets.json  # JSON StreamSpec example
│   ├── langchain_agent.py      # LangChain agent with alerts
│   └── notebooks/
│       └── vwap_zscore.ipynb   # VWAP + z-score example
│
├── infra/
│   ├── docker-compose.yml
│   ├── Dockerfile.api
│   └── Dockerfile.broker
│
├── tests/
│   ├── test_spec_validation.py
│   ├── test_create_and_listen.py
│   ├── test_replay_and_metrics.py
│   └── test_limits.py
│
├── LICENSE                     # Apache-2.0
├── README.md                   # User-facing docs (v2 indie quant positioning)
├── CHANGELOG.md                # Version history
├── requirements.txt
└── Makefile
```

## Coding & Style Conventions
- Use **2 spaces** for indentation in Python (not 4, not tabs)
- All modules named in `snake_case`
- Classes in `PascalCase`; functions in `snake_case`
- **Type hints mandatory** for all public functions
- Use **async/await** for all I/O operations
- Avoid modifying `engine/schemas.py` without strong justification (it defines the standard event model)
- Performance: latency must stay under **500ms** for test loads in `engine/pipeline_three.py`

## Environment Variables
```
REDIS_URL                 # default: redis://redis:6379
BOND_SECRET               # HMAC secret (MUST change in prod)
BOND_RING_SECONDS         # default: 60
BOND_TOKEN_TTL            # default: 3600 (1 hour)
BOND_MAX_STREAMS          # default: 5
GRPC_ENDPOINT             # default: chi1.cracknode.com:10000
X_BEARER                  # optional; enables real Twitter API
CUSTOM_WS_URL             # optional; for custom connector demo
PUBLIC_WS_BASE            # default: ws://localhost:8080/ws
```

## StreamSpec v1 (JSON DSL)

**Allowed source.type**:
- `ccxt.ticker` | `ccxt.trades` (REST approximation; note in README)
- `onchain.grpc` (Solana Yellowstone/Geyser)
- `x.tweets` (Twitter/X)
- `custom.ws` (generic WebSocket)

**Allowed transforms v1**:
- `resample` — time-based resampling with aggregations (mean/last/sum)
- `count` — count events over a window
- `project` — select specific fields

**Schema Notes**:
- Canonicalization: sort keys, remove whitespace; used for stable sid hashing
- JSON Schema mirrors Pydantic models with strict enums
- Breaking changes require backward compatibility plan

## API Endpoints

### Stream Management
- `POST /v1/streams` — Create stream from NL or JSON spec
- `GET /v1/streams/{sid}` — Get stream status
- `POST /v1/streams/{sid}/token` — Issue fresh token
- `DELETE /v1/streams/{sid}` — Stop and delete stream

### Metrics
- `GET /v1/streams/{sid}/metrics` — Get performance metrics
  ```json
  {
    "uptime_sec": 120,
    "msgs_in": 450,
    "msgs_out": 90,
    "p50_ms": 12.5,
    "p95_ms": 45.2,
    "dropped": 0
  }
  ```

### WebSocket
- `WS /ws/{sid}?token={token}` — Stream events
- `WS /ws/{sid}?token={token}&from=now-60s` — Replay + live

## Token Auth (HMAC)
Format: `sid.exp.signature`
- `signature = HMAC_SHA256(SECRET, f"{sid}.{exp}")` then base64url
- TTL default: 1h (configurable via BOND_TOKEN_TTL)
- Validation: check expiry and HMAC signature match

## Workflow & Commands
- `docker compose up -d` → launch local dev stack
- `make test` → run unit + integration tests
- `make lint` → run flake8 + mypy
- `make deploy` → build docker image and push for staging environment

## Agent Prompts & Usage Guidelines
When interacting with Claude Code:
- **Always** ask for clarification if prompt is ambiguous
- Code changes must include tests or update existing tests
- When modifying stream logic (in `engine/pipeline_three.py`), ensure latency stays under 500ms
- Do **not** refactor connector modules unless you specify "connector interface redesign" explicitly
- For any new streaming source, update `README.md` and document in examples

## Known Limitations & Do-Not-Touch

**SECURITY-CRITICAL** (do not alter without review):
- `apps/api/crypto.py` — Token generation/verification logic

**IMMUTABLE** (strong justification required):
- `engine/schemas.py` — Standard event model; breaking changes need backward compatibility plan
- StreamSpec JSON DSL — Must maintain backward compatibility

**DEV-ONLY** (not production):
- `infra/docker-compose.yml` — Simplified for local dev only
- Redis Streams — Single-node; migrate to Kafka/Redpanda for scale

**KNOWN ISSUES**:
- CCXT uses REST polling (~5s latency) instead of WebSocket
- Tests assume Redis Streams; migrating to Kafka requires rewriting `engine/dispatch.py`

## Compliance & Redistribution
- Users must follow source ToS (CCXT, Twitter, gRPC provider)
- Redistribution of data is **discouraged** (document in README, no blocking in code)
- Bond is provided "as-is" under Apache-2.0 license
- No warranty or liability for data quality/compliance

## Purpose of This File
This `CLAUDE.md` serves as the "brain" of the AI assistant (Claude Code) for this repository. Each session prepends the content so the model understands context, architecture, conventions, and workflows. Keep it up to date as the project evolves.

## Repository Scope & Public Release

**This Repository (bond-sdk)**:
- Contains SDK, examples, local development tools, mock connectors
- Public under Apache-2.0 license
- For indie quants to experiment locally
- Docker Compose for development only

**Not Included (Bond Cloud - Private)**:
- Production connectors with real-time WebSocket feeds (ccxt.pro)
- Hosted runtime with enterprise SLAs
- Advanced monitoring, metrics dashboards
- Multi-region deployment infrastructure
- Billing/quotas system
- Custom connector development services

**Positioning**:
- Public enough to attract users (great SDK, examples, docs)
- Private enough to protect hard bits (production connectors, scaling know-how)
- Hosted service is the "real thing" with reliability, latency, data quality, and support

**Version**: v0.2 (Indie Quant + On-Chain gRPC MVP)
**Last Updated**: 2025-10-21
