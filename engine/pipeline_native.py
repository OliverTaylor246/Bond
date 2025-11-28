"""
Native pipeline: batching + optional LOB reconstruction + Redis fan-out.
"""
from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass, field
from typing import AsyncIterator, Optional

from engine.dispatch import RedisDispatcher


@dataclass
class PipelineOptions:
  flush_interval_ms: int = 100
  max_batch: int = 200
  # Future hook: enable microdelta generation if connectors emit raw book deltas
  enable_microdeltas: bool = True
  # Future hook: clamp timestamps if exchange ts skews too far
  clamp_ts_skew_ms: Optional[int] = None


async def run_native_pipeline(
  stream_id: str,
  events: AsyncIterator[dict],
  dispatcher: RedisDispatcher,
  flush_interval_ms: int,
  options: Optional[PipelineOptions] = None,
) -> None:
  """
  Consume normalized events and push them to Redis Streams with simple batching.
  """
  opts = options or PipelineOptions(flush_interval_ms=flush_interval_ms)
  buffer: list[dict] = []
  flush_interval = max(flush_interval_ms, 10) / 1000.0
  stop_event = asyncio.Event()

  async def flush_loop() -> None:
    try:
      while not stop_event.is_set():
        await asyncio.sleep(flush_interval)
        if buffer:
          batch = buffer[:]
          buffer.clear()
          for event in batch:
            await dispatcher.publish(stream_id, event)
    except asyncio.CancelledError:
      pass

  flusher = asyncio.create_task(flush_loop())

  try:
    async for event in events:
      buffer.append(event)
      # Opportunistic flush to avoid unbounded growth
      if len(buffer) >= opts.max_batch:
        batch = buffer[:]
        buffer.clear()
        for evt in batch:
          await dispatcher.publish(stream_id, evt)
  finally:
    stop_event.set()
    flusher.cancel()
    with suppress(asyncio.CancelledError):
      await flusher
    # Final flush
    for event in buffer:
      await dispatcher.publish(stream_id, event)
