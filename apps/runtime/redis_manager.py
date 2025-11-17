"""Shared Redis manager for stream lifecycle metadata.

Provides a singleton helper that wraps aioredis connection pooling,
common key helpers, and configurable TTLs used across broker/runtime/API.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import AsyncIterator

from redis import asyncio as aioredis

LOGGER = logging.getLogger("3kk0.lifecycle.redis")

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
LIFECYCLE_TTL = int(os.getenv("LIFECYCLE_TTL", "300"))
HEARTBEAT_INTERVAL = int(os.getenv("HEARTBEAT_INTERVAL_SEC", "15"))
STALE_THRESHOLD = int(os.getenv("STREAM_STALE_THRESHOLD_SEC", "120"))


class RedisManager:
  """Thin wrapper around aioredis with lifecycle-aware helpers."""

  def __init__(self, redis_url: str = REDIS_URL):
    self.redis_url = redis_url
    self._redis: aioredis.Redis | None = None
    self._lock = asyncio.Lock()

  async def connect(self) -> aioredis.Redis:
    if self._redis:
      return self._redis
    async with self._lock:
      if self._redis:
        return self._redis
      self._redis = aioredis.from_url(
        self.redis_url,
        decode_responses=True,
      )
      LOGGER.info("RedisManager connected to %s", self.redis_url)
      return self._redis

  async def close(self) -> None:
    if not self._redis:
      return
    await self._redis.close()
    self._redis = None

  async def incr_subscribers(self, stream_id: str) -> int:
    redis = await self.connect()
    key = self._subscribers_key(stream_id)
    new_value = await redis.incr(key)
    await redis.expire(key, LIFECYCLE_TTL)
    LOGGER.info("Subscriber increment %s -> %s", stream_id, new_value)
    return new_value

  async def decr_subscribers(self, stream_id: str) -> int:
    redis = await self.connect()
    key = self._subscribers_key(stream_id)
    new_value = await redis.decr(key)
    await redis.expire(key, LIFECYCLE_TTL)
    if new_value <= 0:
      await redis.set(self._inactive_key(stream_id), str(time.time()), ex=LIFECYCLE_TTL)
      LOGGER.info("Subscriber count dropped to %s for %s", new_value, stream_id)
    return new_value

  async def update_heartbeat(self, stream_id: str) -> None:
    redis = await self.connect()
    key = self._heartbeat_key(stream_id)
    now = str(time.time())
    await redis.set(key, now, ex=LIFECYCLE_TTL)

  async def get_subscribers(self, stream_id: str) -> int:
    redis = await self.connect()
    key = self._subscribers_key(stream_id)
    raw = await redis.get(key)
    return int(raw or 0)

  async def get_heartbeat(self, stream_id: str) -> float:
    redis = await self.connect()
    raw = await redis.get(self._heartbeat_key(stream_id))
    return float(raw or 0.0)

  async def scan_heartbeat_keys(self) -> AsyncIterator[str]:
    redis = await self.connect()
    cursor = 0
    pattern = "stream:*:heartbeat"
    while True:
      cursor, keys = await redis.scan(cursor=cursor, match=pattern, count=200)
      for key in keys:
        yield key
      if cursor == 0:
        break

  async def dbsize(self) -> int:
    redis = await self.connect()
    return await redis.dbsize()

  @staticmethod
  def extract_stream_id(key: str) -> str:
    """Convert a Redis heartbeat key into stream_id."""
    return key.split(":")[1] if ":" in key else key

  @staticmethod
  def _heartbeat_key(stream_id: str) -> str:
    return f"stream:{stream_id}:heartbeat"

  @staticmethod
  def _subscribers_key(stream_id: str) -> str:
    return f"stream:{stream_id}:subscribers"

  @staticmethod
  def _inactive_key(stream_id: str) -> str:
    return f"stream:{stream_id}:last_inactive"


redis_manager = RedisManager()

__all__ = [
  "RedisManager",
  "redis_manager",
  "LIFECYCLE_TTL",
  "HEARTBEAT_INTERVAL",
  "STALE_THRESHOLD",
]
