#!/usr/bin/env python3
"""
Utility that exercises the `/raw` websocket and, optionally, the backing Redis stream.

Usage examples:
  python scripts/test_raw_endpoint.py \
    --raw-url ws://localhost:8000/raw \
    --mode direct \
    --redis-stream-id my_stream_id

The script connects to the configured websocket first (direct engine feed by default,
`?mode=unified` for the manager-backed raw stream). If a Redis stream ID is supplied it
also samples events from Redis so you can see where the data flowed post-engine.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import websockets

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engine.dispatch import RedisDispatcher


def _pretty(json_like: Any) -> str:
    try:
        return json.dumps(json_like, indent=2, sort_keys=True)
    except Exception:
        return str(json_like)


async def _probe_raw_websocket(url: str, mode: str, sample_size: int) -> None:
    query = f"?mode={mode}" if mode else ""
    target = f"{url}{query}"
    print(f"[raw] connecting to {target}", flush=True)
    try:
        async with websockets.connect(target) as websocket:
            print(f"[raw] connected, awaiting {sample_size} message(s)", flush=True)
            for idx in range(1, sample_size + 1):
                raw = await websocket.recv()
                try:
                    payload = json.loads(raw)
                except Exception:
                    payload = raw
                print(f"[raw #{idx}] {type(payload).__name__}:\n{_pretty(payload)}")
    except Exception as exc:
        print(f"[raw] failed: {exc}", file=sys.stderr)


async def _inspect_redis_stream(
    stream_id: str,
    redis_url: str,
    count: int,
) -> None:
    dispatcher = RedisDispatcher(redis_url)
    await dispatcher.connect()
    try:
        print(f"[redis] inspecting stream {stream_id}", flush=True)
        info = await dispatcher.get_stream_info(stream_id)
        print(f"[redis] stream info: {info}", flush=True)
        if info.get("length") == 0:
            print("[redis] stream is empty", flush=True)
            return
        start_id = info.get("first_entry", (None,))[0] or "-"
        events = await dispatcher.read_range(
            stream_id,
            start_id=start_id,
            end_id="+",
            count=count,
        )
        if not events:
            print("[redis] no events found in the inspected range", flush=True)
            return
        for idx, (event_id, payload) in enumerate(events, 1):
            print(f"[redis #{idx}] id={event_id}", flush=True)
            print(_pretty(payload))
    finally:
        await dispatcher.disconnect()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Smoke test the /raw websocket endpoint")
    parser.add_argument(
        "--raw-url",
        default="ws://localhost:8000/raw",
        help="WebSocket URL for the unified /raw endpoint",
    )
    parser.add_argument(
        "--mode",
        default="direct",
        choices=["direct", "unified", "broker", "manager", "aggregated"],
        help="Raw mode to exercise (empty=direct connector fanout)",
    )
    parser.add_argument(
        "--sample-size",
        type=int,
        default=1,
        help="Number of websocket messages to print",
    )
    parser.add_argument(
        "--redis-stream-id",
        help="Redis stream ID to inspect after websocket sampling (optional)",
    )
    parser.add_argument(
        "--redis-url",
        default="redis://localhost:6379",
        help="Redis connection URL for inspecting the broker stream",
    )
    parser.add_argument(
        "--redis-count",
        type=int,
        default=5,
        help="Maximum number of Redis events to dump for diagnostics",
    )
    return parser.parse_args()


async def main() -> None:
    args = _parse_args()
    await _probe_raw_websocket(args.raw_url, args.mode if args.mode != "direct" else "", args.sample_size)
    if args.redis_stream_id:
        await _inspect_redis_stream(
            args.redis_stream_id,
            args.redis_url,
            args.redis_count,
        )


if __name__ == "__main__":
    asyncio.run(main())
