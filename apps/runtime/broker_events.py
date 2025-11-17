"""Broker-side lifecycle hooks for subscriber tracking."""
from __future__ import annotations

import asyncio
import logging
import time

from fastapi import WebSocket

from apps.runtime.redis_manager import LIFECYCLE_TTL, redis_manager

LOGGER = logging.getLogger("3kk0.lifecycle.broker")


async def on_connect(
  websocket: WebSocket | None,
  stream_id: str,
  user_id: str | None = None,
) -> int:
  """Increment subscriber count when a WebSocket connects."""
  count = await redis_manager.incr_subscribers(stream_id)
  LOGGER.info(
    "Stream %s subscriber count is %s (ttl=%ss) user=%s",
    stream_id,
    count,
    LIFECYCLE_TTL,
    user_id or "anonymous",
  )
  return count


async def on_disconnect(
  websocket: WebSocket | None,
  stream_id: str,
  user_id: str | None = None,
) -> int:
  """Decrement subscribers and mark inactivity if needed."""
  count = await redis_manager.decr_subscribers(stream_id)
  if count <= 0:
    LOGGER.info(
      "Stream %s inactive at %s user=%s",
      stream_id,
      time.time(),
      user_id or "anonymous",
    )
  return count


async def forward_events(
  websocket: WebSocket,
  stream_id: str,
  receiver: asyncio.Queue,
) -> None:
  """Utility to forward messages from queue to websocket (optional helper)."""
  try:
    while True:
      event = await receiver.get()
      await websocket.send_json(event)
  except Exception:
    LOGGER.exception("Error forwarding events for %s", stream_id)
    raise


__all__ = ["on_connect", "on_disconnect", "forward_events"]
