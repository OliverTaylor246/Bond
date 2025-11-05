"""
WebSocket broker - fans out events from Redis Streams to WebSocket clients.
Supports ring buffer replay with ?from= query parameter.
"""
import asyncio
import json
import os
import re
import time
from contextlib import asynccontextmanager
from typing import Optional
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query
from engine.dispatch import RedisDispatcher
from apps.broker.auth import verify_token, get_secret


# Global dispatcher
dispatcher: RedisDispatcher | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
  """Lifespan context for broker startup/shutdown."""
  global dispatcher
  redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
  dispatcher = RedisDispatcher(redis_url)
  await dispatcher.connect()
  yield
  await dispatcher.disconnect()


app = FastAPI(
  title="Bond Broker",
  description="WebSocket event streaming broker",
  version="0.1.0",
  lifespan=lifespan,
)


@app.get("/")
async def root():
  """Health check."""
  return {"status": "ok", "service": "broker"}


@app.get("/health")
async def health():
  """Railway healthcheck endpoint."""
  return {"status": "ok", "service": "broker"}


def parse_from_param(from_param: Optional[str]) -> Optional[float]:
  """
  Parse ?from= query parameter to Unix timestamp.

  Supported formats:
    - "now-60s" -> 60 seconds ago
    - "now-5m" -> 5 minutes ago
    - "1698765432" -> Unix timestamp
    - None -> no replay

  Args:
    from_param: Query parameter value

  Returns:
    Unix timestamp in seconds, or None
  """
  if not from_param:
    return None

  # Match "now-Xs" or "now-Xm" pattern
  match = re.match(r"now-(\d+)([sm])", from_param)
  if match:
    value = int(match.group(1))
    unit = match.group(2)

    if unit == "s":
      return time.time() - value
    elif unit == "m":
      return time.time() - (value * 60)

  # Try parsing as Unix timestamp
  try:
    return float(from_param)
  except ValueError:
    return None


@app.websocket("/ws/{stream_id}")
async def websocket_endpoint(
  websocket: WebSocket,
  stream_id: str,
  token: str = Query(...),
  from_time: Optional[str] = Query(None, alias="from")
):
  """
  WebSocket endpoint for streaming events.

  Args:
    stream_id: Stream identifier
    token: Signed access token
    from_time: Optional replay start point (e.g., "now-60s", "now-5m")

  The client receives JSON events as they're published to Redis.
  If ?from= is specified, replays historical events first, then continues live.
  """
  # Verify token
  secret = get_secret()
  if not verify_token(stream_id, token, secret):
    await websocket.close(code=1008, reason="Invalid or expired token")
    return

  # Accept connection
  await websocket.accept()

  if not dispatcher:
    await websocket.close(code=1011, reason="Dispatcher not initialized")
    return

  # Determine replay start point
  replay_from_ts = parse_from_param(from_time)
  last_id = "$"  # Default: start with new events only

  try:
    # Phase 1: Replay historical events (if requested)
    if replay_from_ts is not None:
      ring_buffer_sec = int(os.getenv("BOND_RING_SECONDS", "60"))
      oldest_allowed = time.time() - ring_buffer_sec

      # Clamp to ring buffer window
      if replay_from_ts < oldest_allowed:
        replay_from_ts = oldest_allowed

      # Convert timestamp to Redis Stream ID
      start_id = dispatcher.timestamp_to_stream_id(replay_from_ts)

      # Read historical events
      replay_events = await dispatcher.read_range(
        stream_id,
        start_id=start_id,
        end_id="+",  # Up to latest
        count=1000
      )

      # Send replay events
      for event_id, event_data in replay_events:
        await websocket.send_json(event_data)
        last_id = event_id

      # If we got events, continue from last replayed ID
      # Otherwise, start with new events
      if not replay_events:
        last_id = "$"

    # Phase 2: Stream live events
    while True:
      # Read from Redis Stream
      events = await dispatcher.read_stream(stream_id, last_id, block=1000)

      # Send events to client
      for event_id, event_data in events:
        await websocket.send_json(event_data)
        last_id = event_id

      # Keep connection alive
      await asyncio.sleep(0.01)

  except WebSocketDisconnect:
    # Client disconnected normally
    pass
  except Exception as e:
    print(f"[broker] Error streaming to {stream_id}: {e}")
    await websocket.close(code=1011, reason="Internal error")
