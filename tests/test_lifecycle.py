import asyncio
import time

import pytest
from fakeredis.aioredis import FakeRedis

from apps.runtime import broker_events
from apps.runtime.redis_manager import redis_manager
from apps.runtime.reaper import StreamReaper


@pytest.fixture(autouse=True)
async def fake_redis(monkeypatch):
  client = FakeRedis(decode_responses=True)
  monkeypatch.setattr(redis_manager, "_redis", client, raising=False)
  yield
  await client.close()
  redis_manager._redis = None


@pytest.mark.asyncio
async def test_broker_connect_disconnect_updates_subscribers():
  count = await broker_events.on_connect(None, "stream-1")
  assert count == 1
  redis = await redis_manager.connect()
  ttl = await redis.ttl("stream:stream-1:subscribers")
  assert ttl > 0

  count = await broker_events.on_disconnect(None, "stream-1")
  assert count == 0
  last_inactive = await redis.get("stream:stream-1:last_inactive")
  assert last_inactive is not None


@pytest.mark.asyncio
async def test_reaper_shutdown_when_idle(monkeypatch):
  shutdown_calls: list[str] = []

  async def shutdown(stream_id: str):
    shutdown_calls.append(stream_id)

  stream_id = "idle-1"
  await redis_manager.incr_subscribers(stream_id)
  await redis_manager.update_heartbeat(stream_id)
  await redis_manager.decr_subscribers(stream_id)

  redis = await redis_manager.connect()
  await redis.set(f"stream:{stream_id}:heartbeat", str(time.time() - 200))

  reaper = StreamReaper(shutdown_cb=shutdown, interval_sec=0, stale_after=60)
  await reaper._reap_once()

  assert stream_id in shutdown_calls
