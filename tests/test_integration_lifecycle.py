import time

import pytest
from fakeredis.aioredis import FakeRedis

from apps.runtime import broker_events
from apps.runtime.redis_manager import LIFECYCLE_TTL, redis_manager
from apps.runtime.reaper import reap_once_for_testing, start_reaper, stop_reaper


@pytest.fixture(autouse=True)
async def fake_redis(monkeypatch):
  client = FakeRedis(decode_responses=True)
  monkeypatch.setattr(redis_manager, "_redis", client, raising=False)
  yield client
  await client.close()
  redis_manager._redis = None


@pytest.mark.asyncio
async def test_full_lifecycle_reaper(fake_redis):
  shutdowns: list[str] = []

  async def shutdown(stream_id: str):
    shutdowns.append(stream_id)

  await start_reaper(shutdown)

  stream_id = "integration-stream"
  for user in ("u1", "u2", "u3"):
    await broker_events.on_connect(None, stream_id, user)

  for user in ("u1", "u2", "u3"):
    await broker_events.on_disconnect(None, stream_id, user)

  ttl = await fake_redis.ttl(f"stream:{stream_id}:subscribers")
  assert 0 < ttl <= LIFECYCLE_TTL

  await fake_redis.set(f"stream:{stream_id}:heartbeat", str(time.time() - 400))
  await reap_once_for_testing()

  assert stream_id in shutdowns

  await stop_reaper()
