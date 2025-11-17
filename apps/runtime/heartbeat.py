"""Heartbeat utilities for active streams."""
from __future__ import annotations

import asyncio
import contextlib
import logging

from apps.runtime.redis_manager import HEARTBEAT_INTERVAL, redis_manager

LOGGER = logging.getLogger("3kk0.lifecycle.heartbeat")


class HeartbeatManager:
  """Schedules heartbeat loops per stream."""

  def __init__(self, interval: int = HEARTBEAT_INTERVAL):
    self.interval = interval
    self._tasks: dict[str, asyncio.Task] = {}

  async def start(self, stream_id: str) -> None:
    if stream_id in self._tasks:
      return
    task = asyncio.create_task(self._loop(stream_id), name=f"heartbeat:{stream_id}")
    self._tasks[stream_id] = task

  async def stop(self, stream_id: str) -> None:
    task = self._tasks.pop(stream_id, None)
    if not task:
      return
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
      await task

  async def stop_all(self) -> None:
    for stream_id in list(self._tasks.keys()):
      await self.stop(stream_id)

  async def _loop(self, stream_id: str) -> None:
    try:
      while True:
        await redis_manager.update_heartbeat(stream_id)
        await asyncio.sleep(self.interval)
    except asyncio.CancelledError:
      LOGGER.info("Heartbeat loop cancelled for %s", stream_id)
      raise
    except Exception:
      LOGGER.exception("Heartbeat loop error for %s", stream_id)


_MANAGER = HeartbeatManager()


async def start_heartbeat(stream_id: str) -> None:
  await _MANAGER.start(stream_id)


async def stop_heartbeat(stream_id: str) -> None:
  await _MANAGER.stop(stream_id)


async def stop_all_heartbeats() -> None:
  await _MANAGER.stop_all()


__all__ = [
  "HeartbeatManager",
  "start_heartbeat",
  "stop_heartbeat",
  "stop_all_heartbeats",
]
