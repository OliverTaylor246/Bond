"""
Simple LOB consumer that prints orderbook updates from the normalized `/stream`
endpoint. Useful for sanity-checking that orderbook pushes are arriving.

Usage:
  python examples/debug_lob_consumer.py

Env options:
  KK0_STREAM_URL  - websocket URL (default ws://localhost:8000/stream)
  KK0_EXCHANGE    - exchange name (default hyperliquid)
  KK0_SYMBOL      - symbol (default BTC/USDT)
  KK0_DEPTH       - depth to request (default 20)
"""

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Iterable, List

# SDK import
root = Path(__file__).resolve().parents
sdk_path: Path | None = None
repo_root: Path | None = None
for parent in root:
    candidate = parent / "sdk" / "python"
    if candidate.exists():
        sdk_path = candidate
        repo_root = parent
        break

if not sdk_path:
    raise SystemExit("kk0 SDK directory not found in repo tree")

sys.path.insert(0, str(sdk_path))
if repo_root:
    sys.path.insert(0, str(repo_root))

from kk0 import Stream  # noqa: E402

STREAM_URL = os.getenv("KK0_STREAM_URL", "ws://localhost:8000/stream")
EXCHANGE = os.getenv("KK0_EXCHANGE", "hyperliquid")
SYMBOL = os.getenv("KK0_SYMBOL", "BTC/USDT")
DEPTH = int(os.getenv("KK0_DEPTH") or 20)


def _as_list(value: Iterable[str]) -> List[str]:
    return [str(v) for v in value]


async def main():
    async with Stream(STREAM_URL) as stream:
        await stream.subscribe(
            channels=["orderbook"],
            symbols=[SYMBOL],
            exchanges=[EXCHANGE],
            depth=DEPTH,
        )
        print(
            f"Subscribed to orderbook {SYMBOL} on {EXCHANGE} (depth={DEPTH}) @ {STREAM_URL}"
        )
        async for event in stream:
            try:
                if event.get("type") != "orderbook":
                    continue
            except AttributeError:
                # raw string
                try:
                    event = json.loads(event)
                except Exception:
                    continue
                if event.get("type") != "orderbook":
                    continue

            bids = event.get("bids") or []
            asks = event.get("asks") or []
            update_type = event.get("update_type")

            ts_internal = event.get("ts_internal")
            seq_internal = event.get("seq_internal")
            print(
                f"[seq={seq_internal} ts_internal={ts_internal}] "
                f"best_bid={bids[0] if bids else None} "
                f"best_ask={asks[0] if asks else None} "
                f"levels: bids={len(bids)} asks={len(asks)} "
                f"update_type={update_type}"
            )


if __name__ == "__main__":
    asyncio.run(main())
