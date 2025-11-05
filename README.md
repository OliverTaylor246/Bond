# Bond Broker

Bond Broker is the lightweight WebSocket fan-out service used by Bond to deliver
real-time events to clients. It subscribes to Redis Streams and publishes every
message to connected WebSocket clients after validating their access tokens.

This repository contains only the broker runtime and the minimal tooling needed
to build and deploy it.

## Features
- FastAPI-based WebSocket endpoint with ring buffer replay support
- HMAC token verification compatible with the Bond API
- Async Redis Streams dispatcher for efficient event fan-out
- Docker image tailored for Railway or Docker Compose deployments

## Quick Start

Install dependencies and run the broker locally:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn apps.broker.broker:app --host 0.0.0.0 --port 8080
```

Set `REDIS_URL` if your Redis instance is not running on `localhost:6379`.
To use a custom token secret, export `BOND_SECRET`.

### Docker Compose

Start Redis and the broker together:

```bash
docker compose up -d
```

This exposes the broker on `localhost:8080` and Redis on `localhost:6379`.

## Environment Variables

| Variable       | Default                 | Description                              |
|----------------|-------------------------|------------------------------------------|
| `REDIS_URL`    | `redis://localhost:6379`| Redis connection string                  |
| `BOND_SECRET`  | `bond-dev-secret-change-in-production` | HMAC secret for token validation |
| `BOND_RING_SECONDS` | `60`               | Historical replay window in seconds      |

## Project Structure

```
.
├── apps/
│   └── broker/
│       ├── auth.py      # Token helpers
│       └── broker.py    # FastAPI WebSocket app
├── engine/
│   └── dispatch.py      # Redis Streams dispatcher
├── infra/
│   └── Dockerfile.broker
├── docker-compose.yml   # Local Redis + broker stack
├── Makefile             # Common dev commands
├── pyproject.toml
└── requirements.txt
```

## Deployment

Build the Docker image:

```bash
make build
```

Deploy the resulting image to Railway or your container platform of choice.
Ensure the container can reach your Redis instance and provide the `BOND_SECRET`
used when issuing client tokens.

## License

Licensed under the Apache 2.0 License. See [LICENSE](LICENSE) for details.
