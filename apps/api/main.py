"""
FastAPI control plane for bond.
REST endpoints to create and manage streams.
"""
import os
from contextlib import asynccontextmanager
from typing import Any
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from apps.compiler import compile_spec, generate_stream_id
from apps.runtime import StreamRuntime
from apps.api.crypto import generate_token, get_secret
from apps.api.limits import limits
from apps.api.metrics import metrics_registry


# Global runtime instance
runtime: StreamRuntime | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
  """Lifespan context manager for startup/shutdown."""
  global runtime
  redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
  runtime = StreamRuntime(redis_url)
  await runtime.start()
  yield
  await runtime.stop()


app = FastAPI(
  title="Bond API",
  description="Real-time market data stream platform",
  version="0.1.0",
  lifespan=lifespan,
)


class CreateStreamRequest(BaseModel):
  """Request to create a new stream."""
  natural_language: str | None = Field(None, description="Natural language spec")
  spec: dict[str, Any] | None = Field(None, description="Structured spec")


class CreateStreamResponse(BaseModel):
  """Response with stream details."""
  stream_id: str
  ws_url: str
  spec: dict[str, Any]


class StreamInfo(BaseModel):
  """Information about an active stream."""
  stream_id: str
  running: bool


@app.get("/")
async def root():
  """Health check endpoint."""
  return {"status": "ok", "service": "bond"}


@app.post("/v1/streams", response_model=CreateStreamResponse)
async def create_stream(req: CreateStreamRequest):
  """
  Create a new data stream.

  Accepts either natural language or structured spec.
  Returns stream ID and signed WebSocket URL.
  """
  if not runtime:
    raise HTTPException(500, "Runtime not initialized")

  # Check limits before creating
  current_count = len(runtime.list_streams())
  allowed, message = limits.check_limit(current_count)
  if not allowed:
    raise HTTPException(429, message)

  # Compile spec
  if req.natural_language:
    spec = compile_spec(req.natural_language)
  elif req.spec:
    spec = compile_spec(req.spec)
  else:
    raise HTTPException(400, "Must provide natural_language or spec")

  # Generate stream ID
  stream_id = generate_stream_id(spec)

  # Launch stream if not already running
  if not runtime.is_running(stream_id):
    await runtime.launch_stream(stream_id, spec)
    # Create metrics tracker
    metrics_registry.create(stream_id)

  # Generate access token
  secret = get_secret()
  token = generate_token(stream_id, secret, ttl_sec=3600)

  # Build WebSocket URL
  ws_host = os.getenv("WS_HOST", "localhost:8080")
  ws_url = f"ws://{ws_host}/ws/{stream_id}?token={token}"

  return CreateStreamResponse(
    stream_id=stream_id,
    ws_url=ws_url,
    spec=spec.model_dump(),
  )


@app.get("/v1/streams", response_model=list[StreamInfo])
async def list_streams():
  """List all active streams."""
  if not runtime:
    raise HTTPException(500, "Runtime not initialized")

  stream_ids = runtime.list_streams()

  return [
    StreamInfo(stream_id=sid, running=True)
    for sid in stream_ids
  ]


@app.delete("/v1/streams/{stream_id}")
async def delete_stream(stream_id: str):
  """Stop and delete a stream."""
  if not runtime:
    raise HTTPException(500, "Runtime not initialized")

  if not runtime.is_running(stream_id):
    raise HTTPException(404, f"Stream {stream_id} not found")

  await runtime.stop_stream(stream_id)
  
  # Clean up metrics
  metrics_registry.delete(stream_id)

  return {"status": "deleted", "stream_id": stream_id}


@app.get("/v1/streams/{stream_id}", response_model=StreamInfo)
async def get_stream(stream_id: str):
  """Get stream information."""
  if not runtime:
    raise HTTPException(500, "Runtime not initialized")

  running = runtime.is_running(stream_id)

  if not running:
    raise HTTPException(404, f"Stream {stream_id} not found")

  return StreamInfo(stream_id=stream_id, running=running)


class TokenResponse(BaseModel):
  """Token response with WebSocket URL."""
  token: str
  ws_url: str
  expires_in_sec: int


@app.post("/v1/streams/{stream_id}/token", response_model=TokenResponse)
async def refresh_token(stream_id: str):
  """
  Generate a new access token for an existing stream.
  
  Useful when the previous token has expired or is about to expire.
  """
  if not runtime:
    raise HTTPException(500, "Runtime not initialized")

  if not runtime.is_running(stream_id):
    raise HTTPException(404, f"Stream {stream_id} not found")

  # Generate fresh token
  secret = get_secret()
  ttl_sec = int(os.getenv("BOND_TOKEN_TTL", "3600"))
  token = generate_token(stream_id, secret, ttl_sec=ttl_sec)

  # Build WebSocket URL
  ws_host = os.getenv("WS_HOST", "localhost:8080")
  ws_url = f"ws://{ws_host}/ws/{stream_id}?token={token}"

  return TokenResponse(
    token=token,
    ws_url=ws_url,
    expires_in_sec=ttl_sec,
  )


@app.get("/v1/streams/{stream_id}/metrics")
async def get_stream_metrics(stream_id: str):
  """
  Get metrics for a stream.
  
  Returns message counts, latency percentiles, dropped events, and uptime.
  """
  if not runtime:
    raise HTTPException(500, "Runtime not initialized")

  if not runtime.is_running(stream_id):
    raise HTTPException(404, f"Stream {stream_id} not found")

  metrics = metrics_registry.get(stream_id)
  if not metrics:
    # Stream exists but no metrics yet (just started)
    return {
      "stream_id": stream_id,
      "uptime_sec": 0.0,
      "msgs_in": 0,
      "msgs_out": 0,
      "dropped": 0,
      "latency_p50_ms": 0.0,
      "latency_p95_ms": 0.0,
      "note": "Metrics collection starting",
    }

  return metrics.to_dict()
