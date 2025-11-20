"""
Quick debug script to compare normalized vs raw broker streams.

Usage (defaults assume local broker on port 8000):
  python examples/debug_streams.py

Env toggles:
  KK0_STREAM_URL   - websocket URL for normalized endpoint (default ws://localhost:8000/stream)
  KK0_RAW_URL      - websocket URL for raw endpoint (default derived by swapping to /raw)
  KK0_CHANNELS     - comma-separated channels (trades,orderbook)
  KK0_SYMBOLS      - comma-separated symbols (e.g. BTC/USDT,ETH/USDT)
  KK0_EXCHANGES    - comma-separated exchanges (* for all)
  KK0_DEPTH        - depth integer (default 20)
"""

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Iterable, List
from urllib.parse import quote_plus

# Add kk0 SDK to path
root = Path(__file__).resolve().parents
sdk_path: Path | None = None
for parent in root:
    candidate = parent / "sdk" / "python"
    if candidate.exists():
        sdk_path = candidate
        break

if not sdk_path:
    raise SystemExit("kk0 SDK directory not found in repo tree")

sys.path.insert(0, str(sdk_path))

from kk0 import Stream  # noqa: E402


def _parse_list(env: str, default: Iterable[str]) -> List[str]:
    value = os.getenv(env)
    if not value:
        return list(default)
    return [part.strip() for part in value.split(",") if part.strip()]


CHANNELS = _parse_list("KK0_CHANNELS", ["trades"])
SYMBOLS = _parse_list("KK0_SYMBOLS", ["BTC/USDT"])
EXCHANGES = _parse_list("KK0_EXCHANGES", ["*"])
DEPTH = int(os.getenv("KK0_DEPTH") or 20)

STREAM_URL = os.getenv("KK0_STREAM_URL", "ws://localhost:8000/stream")
RAW_URL_BASE = os.getenv("KK0_RAW_URL") or STREAM_URL.rsplit("/", 1)[0] + "/raw"


def _build_raw_url() -> str:
    # If caller supplied full URL with params, keep it
    if "?" in RAW_URL_BASE:
        return RAW_URL_BASE
    params = {
        "channels": ",".join(CHANNELS),
        "symbols": ",".join(SYMBOLS),
        "exchange": ",".join(EXCHANGES),
        "depth": str(DEPTH),
        "raw": "true",
    }
    query = "&".join(
        f"{k}={quote_plus(v)}" for k, v in params.items() if v and v != ","
    )
    return f"{RAW_URL_BASE}?{query}"


async def _consume(label: str, stream: Stream, expect_json: bool, max_events: int = 5):
    """
    Consume a handful of events and print them.
    Notes blockers if we time out waiting for data.
    """
    print(f"\n[{label}] listening for up to {max_events} events...")
    received = 0
    while received < max_events:
        try:
            event = await asyncio.wait_for(stream.__anext__(), timeout=10)
        except asyncio.TimeoutError:
            print(
                f"[{label}] timeout waiting for activity."
                " Possible blockers: no connector producing data for"
                f" {SYMBOLS}, filters too narrow (channels={CHANNELS}, exchanges={EXCHANGES}),"
                " broker not pushing normalized events, or hitting /raw without params."
            )
            return
        except Exception as exc:
            print(f"[{label}] error receiving event: {exc}")
            return

        received += 1
        if expect_json and isinstance(event, str):
            try:
                event = json.loads(event)
            except Exception:
                pass
        print(f"[{label}] event #{received}: {event}")
    print(f"[{label}] done after {received} events")


async def run_normalized():
    async with Stream(STREAM_URL) as stream:
        await stream.subscribe(
            channels=CHANNELS, symbols=SYMBOLS, exchanges=EXCHANGES, depth=DEPTH
        )
        await _consume("normalized", stream, expect_json=True)


async def run_raw():
    raw_url = _build_raw_url()
    async with Stream(raw_url) as stream:
        # /raw endpoint ignores subscribe messages; filters must be in URL
        await _consume("raw", stream, expect_json=True)


async def main():
    print(
        "Starting stream debug with\n"
        f"  normalized: {STREAM_URL}\n"
        f"  raw:        {_build_raw_url()}\n"
        f"  channels:   {CHANNELS}\n"
        f"  symbols:    {SYMBOLS}\n"
        f"  exchanges:  {EXCHANGES}\n"
        f"  depth:      {DEPTH}"
    )
    await asyncio.gather(run_normalized(), run_raw())


if __name__ == "__main__":
    asyncio.run(main())
