"""Unified entrypoint for API and runtime services."""
from __future__ import annotations

import asyncio
import os
import signal

import uvicorn

from apps.runtime import StreamRuntime


async def _run_runtime_service() -> None:
  runtime = StreamRuntime(os.getenv("REDIS_URL", "redis://localhost:6379"))
  await runtime.start()
  stop_event = asyncio.Event()

  def _handle_stop(*_: object) -> None:
    if not stop_event.is_set():
      stop_event.set()

  loop = asyncio.get_running_loop()
  for sig in (signal.SIGINT, signal.SIGTERM):
    try:
      loop.add_signal_handler(sig, _handle_stop)
    except NotImplementedError:
      signal.signal(sig, lambda *_: stop_event.set())

  await stop_event.wait()
  await runtime.stop()


def main() -> None:
  target = os.getenv("SERVICE_TARGET", "api").lower()
  port = int(os.getenv("PORT", "8000"))
  if target == "runtime":
    asyncio.run(_run_runtime_service())
  elif target == "api":
    uvicorn.run(
      "apps.api.main:app",
      host="0.0.0.0",
      port=port,
      reload=os.getenv("UVICORN_RELOAD", "0") == "1",
    )
  else:
    raise ValueError(f"Unknown SERVICE_TARGET '{target}'")


if __name__ == "__main__":
  main()
