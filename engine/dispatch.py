"""
Redis Streams dispatch layer.
Publishes events to Redis for fan-out to WebSocket clients.

NOTE: Migrating to Kafka requires rewriting this module.
"""
import json
from typing import Any
import redis.asyncio as redis


class RedisDispatcher:
  """Handles publishing events to Redis Streams."""

  def __init__(self, redis_url: str = "redis://localhost:6379"):
    self.redis_url = redis_url
    self.client: redis.Redis | None = None

  async def connect(self) -> None:
    """Establish Redis connection."""
    self.client = await redis.from_url(self.redis_url, decode_responses=True)

  async def disconnect(self) -> None:
    """Close Redis connection."""
    if self.client:
      await self.client.close()

  async def publish(self, stream_id: str, event: dict[str, Any]) -> None:
    """
    Publish an event to a stream.

    Args:
      stream_id: Unique stream identifier (hash)
      event: Event dictionary to publish
    """
    if not self.client:
      raise RuntimeError("RedisDispatcher not connected")

    stream_key = f"stream:{stream_id}"

    # Serialize event as JSON
    payload = json.dumps(event, default=str)

    # Add to Redis Stream with XADD
    await self.client.xadd(
      stream_key,
      {"data": payload},
      maxlen=1000,  # Keep last 1000 events
    )

  async def read_stream(
    self,
    stream_id: str,
    last_id: str = "0",
    block: int = 1000
  ) -> list[tuple[str, dict]]:
    """
    Read events from a stream (for consumers).

    Args:
      stream_id: Stream identifier
      last_id: ID of last seen event ("0" for all, "$" for new only)
      block: Milliseconds to block waiting for new events

    Returns:
      List of (event_id, event_data) tuples
    """
    if not self.client:
      raise RuntimeError("RedisDispatcher not connected")

    stream_key = f"stream:{stream_id}"

    # XREAD with blocking
    result = await self.client.xread(
      {stream_key: last_id},
      count=100,
      block=block,
    )

    if not result:
      return []

    # result format: [(stream_key, [(id, {field: value}), ...])]
    events = []
    for _, entries in result:
      for event_id, fields in entries:
        data = json.loads(fields["data"])
        events.append((event_id, data))

    return events

  async def read_range(
    self,
    stream_id: str,
    start_id: str = "-",
    end_id: str = "+",
    count: int = 100
  ) -> list[tuple[str, dict]]:
    """
    Read a range of events from a stream (for replay).

    Args:
      stream_id: Stream identifier
      start_id: Start ID ("-" for earliest, timestamp format: "1234567890000-0")
      end_id: End ID ("+" for latest, timestamp format: "1234567890000-0")
      count: Maximum number of events to return

    Returns:
      List of (event_id, event_data) tuples
    """
    if not self.client:
      raise RuntimeError("RedisDispatcher not connected")

    stream_key = f"stream:{stream_id}"

    # XRANGE to read historical events
    result = await self.client.xrange(
      stream_key,
      min=start_id,
      max=end_id,
      count=count,
    )

    if not result:
      return []

    # result format: [(event_id, {field: value}), ...]
    events = []
    for event_id, fields in result:
      data = json.loads(fields["data"])
      events.append((event_id, data))

    return events

  def timestamp_to_stream_id(self, timestamp_sec: float) -> str:
    """
    Convert Unix timestamp to Redis Stream ID format.

    Args:
      timestamp_sec: Unix timestamp in seconds (can be float)

    Returns:
      Redis Stream ID string (e.g., "1698765432000-0")
    """
    timestamp_ms = int(timestamp_sec * 1000)
    return f"{timestamp_ms}-0"

  async def get_stream_info(self, stream_id: str) -> dict[str, Any]:
    """
    Get information about a stream.

    Args:
      stream_id: Stream identifier

    Returns:
      Dictionary with stream info (length, first/last ID, etc.)
    """
    if not self.client:
      raise RuntimeError("RedisDispatcher not connected")

    stream_key = f"stream:{stream_id}"

    try:
      info = await self.client.xinfo_stream(stream_key)
      return {
        "length": info.get("length", 0),
        "first_entry": info.get("first-entry"),
        "last_entry": info.get("last-entry"),
      }
    except Exception:
      return {"length": 0, "first_entry": None, "last_entry": None}

  async def delete_stream(self, stream_id: str) -> None:
    """
    Delete a stream and all its events.

    Args:
      stream_id: Stream identifier
    """
    if not self.client:
      raise RuntimeError("RedisDispatcher not connected")

    stream_key = f"stream:{stream_id}"
    await self.client.delete(stream_key)
