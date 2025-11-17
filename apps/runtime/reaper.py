"""Reaper loop that shuts down idle streams based on Redis metadata."""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Awaitable, Callable

from apps.runtime.redis_manager import STALE_THRESHOLD, redis_manager

LOGGER = logging.getLogger("3kk0.lifecycle.reaper")


REAPER_INTERVAL = int(os.getenv("REAPER_INTERVAL", "60"))


class StreamReaper:
  """Periodically scans Redis heartbeat keys and reaps idle streams."""

  def __init__(
    self,
    shutdown_cb: Callable[[str], Awaitable[None]],
    interval_sec: int = REAPER_INTERVAL,
    stale_after: int = STALE_THRESHOLD,
  ):
    self.shutdown_cb = shutdown_cb
    self.interval = interval_sec
    self.stale_after = stale_after
    self._task: asyncio.Task | None = None

  async def start(self) -> None:
    if self._task:
      return
    self._task = asyncio.create_task(self._run(), name="stream-reaper")

  async def stop(self) -> None:
    if not self._task:
      return
    self._task.cancel()
    try:
      await self._task
    except asyncio.CancelledError:
      pass
    finally:
      self._task = None

  async def _run(self) -> None:
    while True:
      try:
        await self._reap_once()
      except asyncio.CancelledError:
        raise
      except Exception:
        LOGGER.exception("Reaper iteration failed")
      await asyncio.sleep(self.interval)

  async def _reap_once(self) -> None:
    now = time.time()
    async for key in redis_manager.scan_heartbeat_keys():
      stream_id = redis_manager.extract_stream_id(key)
      heartbeat = await redis_manager.get_heartbeat(stream_id)
      subs = await redis_manager.get_subscribers(stream_id)
      if subs > 0:
        continue
      if now - heartbeat <= self.stale_after:
        continue
      LOGGER.warning(
        "Reaping stream %s (subs=%s, heartbeat_age=%ss)",
        stream_id,
        subs,
        round(now - heartbeat, 2),
      )
      await self.shutdown_cb(stream_id)


async def shutdown_stream(stream_id: str) -> None:
  """Default shutdown stub that simply logs.

  Runtime should provide a real callback that stops the stream task.
  """
  LOGGER.info("Shutdown stub invoked for %s", stream_id)


_REAPER: StreamReaper | None = None


async def start_reaper(shutdown_cb: Callable[[str], Awaitable[None]] | None = None) -> None:
  """Start the global reaper loop if not already running."""
  global _REAPER
  if _REAPER:
    return
  _REAPER = StreamReaper(shutdown_cb or shutdown_stream)
  await _REAPER.start()


async def stop_reaper() -> None:
  global _REAPER
  if not _REAPER:
    return
  await _REAPER.stop()
  _REAPER = None


async def reap_once_for_testing() -> None:
  if _REAPER:
    await _REAPER._reap_once()  # type: ignore[attr-defined]


__all__ = [
  "StreamReaper",
  "shutdown_stream",
  "start_reaper",
  "stop_reaper",
  "reap_once_for_testing",
]
