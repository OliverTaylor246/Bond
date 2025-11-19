"""
Walkthrough: tail Redis Streams created by the broker so you can print normalized events.

Usage:
  REDIS_URL=redis://localhost:6379 python Walkthrough/read_redis_stream.py --stream mystream
  python Walkthrough/read_redis_stream.py --stream stream:default
"""

from __future__ import annotations

import argparse
import os
import time
from typing import Any

import redis


def fetch_stream(redis_url: str, stream_key: str, last_id: str = "$") -> list[tuple[str, dict[str, Any]]]:
    client = redis.from_url(redis_url, decode_responses=True)
    try:
        entries = client.xrange(stream_key, min="-", max="+", count=5)
        return entries
    finally:
        client.close()


def tail_stream(redis_url: str, stream_key: str) -> None:
    client = redis.from_url(redis_url, decode_responses=True)
    last_id = "0"
    try:
        while True:
            events = client.xread({stream_key: last_id}, block=5000, count=10)
            if not events:
                continue
            for _, entries in events:
                for event_id, fields in entries:
                    print(f"{stream_key} {event_id} -> {fields.get('data')}")
                    last_id = event_id
    finally:
        client.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Peek into the Redis Streams used by 3kk0.")
    parser.add_argument("--redis-url", default=os.environ.get("REDIS_URL", "redis://localhost:6379"))
    parser.add_argument("--stream", default="stream:default")
    parser.add_argument("--tail", action="store_true", help="Tail new events instead of printing history.")
    args = parser.parse_args()

    if args.tail:
        print(f"Tailing {args.stream} (ctrl-c to stop)")
        tail_stream(args.redis_url, args.stream)
    else:
        print(f"Fetching latest events from {args.stream}")
        events = fetch_stream(args.redis_url, args.stream)
        for event_id, fields in events:
            print(f"{event_id}: {fields.get('data')}")


if __name__ == "__main__":
    main()
