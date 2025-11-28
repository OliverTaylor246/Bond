"""
MEXC connector skeleton implementing the unified stream_exchange API.
"""
from __future__ import annotations

from typing import AsyncIterator

from engine.schemas import StreamConfig


async def stream_exchange(config: StreamConfig) -> AsyncIterator[dict]:
  raise NotImplementedError("MEXC connector not yet implemented")

