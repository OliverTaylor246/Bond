# Changelog

All notable changes to Bond will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0] - 2025-10-21

### Added - Indie Quant + On-Chain gRPC MVP

#### On-Chain Data Support
- **On-chain gRPC connector** (`connectors/onchain_grpc.py`) for Solana Yellowstone Geyser
  - Mock mode by default for development/testing
  - Supports transaction, transfer, block, account, and metric events
  - Configurable via `GRPC_ENDPOINT` and `ONCHAIN_MODE` environment variables
- **OnchainEvent schema** with chain, kind, value, and metadata fields
- Integration with pipeline for aggregating on-chain event counts and values

#### API Enhancements
- **Stream limits** enforcement (5 streams max per project by default)
  - Configurable via `BOND_MAX_STREAMS` environment variable
  - Returns HTTP 429 when limit exceeded with clear error message
- **Metrics endpoint** (`GET /v1/streams/{sid}/metrics`)
  - Tracks messages in/out, p50/p95 latency, dropped events, uptime
  - Per-stream metrics with in-memory storage
- **Token refresh endpoint** (`POST /v1/streams/{sid}/token`)
  - Issues fresh tokens for existing streams
  - Configurable TTL via `BOND_TOKEN_TTL` (default 3600s)

#### Ring Buffer & Replay
- **Replay functionality** with `?from=` query parameter
  - Supports formats: `now-60s`, `now-5m`, Unix timestamp
  - Configurable window via `BOND_RING_SECONDS` (default 60s)
  - Seamless transition from replay to live streaming
- Redis Streams helper methods for time-range queries

#### Pipeline Improvements
- Added `onchain_count` and `onchain_value_sum` to aggregated events
- Window boundaries (`window_start`, `window_end`) in aggregated events
- Enhanced event buffering with proper timestamp tracking

#### Documentation & Compliance
- Apache 2.0 license (changed from MIT)
- Expanded environment variables documentation
- Compliance notes about data source Terms of Service
- "Not for redistribution" advisory in README

### Changed
- Updated `engine/schemas.py` with v0.2 schema version markers
- Enhanced `StreamSpec` to support on-chain source types
- Modified runtime to detect and launch on-chain connectors
- Broker WebSocket endpoint now supports replay query parameters

### Dependencies
- Added `grpcio==1.60.0` for gRPC support
- Added `grpcio-tools==1.60.0` for proto compilation
- Added `protobuf==4.25.1` for gRPC message serialization
- Added `orjson==3.9.12` for fast JSON serialization

### Notes
- On-chain gRPC connector currently in mock mode
  - Real Yellowstone Geyser integration requires proto files
  - Reference implementation available at specified path
  - Set `ONCHAIN_MODE=grpc` once proto stubs are generated
- Metrics tracking is in-memory (production should use Redis/TimescaleDB)
- Stream limits are global in MVP (per-user limits planned for v0.3)

## [0.1.0] - 2024-10-15

### Added - Initial MVP Release

#### Core Platform
- FastAPI-based REST API for stream management
- WebSocket broker for real-time event streaming
- Redis Streams as event buffer/message bus
- HMAC-based token authentication with expiration

#### Data Connectors
- CCXT polling connector for crypto exchange data (REST-based)
- Twitter/X connector with mock fallback
- Custom WebSocket connector with mapping hooks

#### Stream Processing
- Three-source pipeline merging crypto, social, and custom data
- 5-second aggregation windows (configurable)
- Real-time event normalization and transformation

#### Natural Language Compiler
- Heuristic parser for common stream patterns
- JSON spec validation with Pydantic
- Deterministic stream ID generation (Blake2b hashing)

#### Python SDK
- `EkkoClient` for stream creation and management
- `listen()` helper for simple consumption
- Async iterator pattern for event streaming

#### Infrastructure
- Docker Compose setup for local development
- Separate API (port 8000) and broker (port 8080) services
- Redis container with persistence

#### Developer Experience
- Makefile with common commands (install, up, down, test, lint)
- Environment variable configuration via .env
- Comprehensive README with quick start guide

### Known Limitations
- Uses CCXT REST polling instead of WebSocket (higher latency)
- Twitter connector requires API credentials (mock mode by default)
- Single-node Redis (not production-ready for scale)
- No stream persistence beyond Redis ring buffer
- No user authentication/authorization system
