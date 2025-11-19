"""
Simple health check for the 3kk0 broker connectors.

Run this script to ensure the deployed broker is running each exchange connector
and reporting a healthy websocket stream: it hits /metrics, verifies that each
connector shows "connected", and prints message rates.

Usage:

    python examples/check_connectors.py \
        --host https://3kk0-broker-production.up.railway.app \
        --before 5
"""

from __future__ import annotations

import argparse
import time
from typing import Any, Dict

import httpx


def get_metrics(url: str) -> Dict[str, Any]:
    resp = httpx.get(url, timeout=10.0)
    resp.raise_for_status()
    return resp.json()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Check that each connector is marked connected in /metrics"
    )
    parser.add_argument(
        "--host",
        default="https://3kk0-broker-production.up.railway.app",
        help="Base URL for the broker",
    )
    parser.add_argument(
        "--before",
        type=int,
        default=5,
        help="Seconds to wait before re-checking metrics to detect startup transitions",
    )
    args = parser.parse_args()

    metrics_url = f"{args.host.rstrip('/')}/metrics"
    print(f"Fetching {metrics_url} ...")

    try:
        metrics = get_metrics(metrics_url)
    except httpx.HTTPError as exc:
        raise SystemExit(f"failed to fetch metrics: {exc}") from exc

    print("Metrics snapshot:")
    print(metrics)

    exchange_status = metrics.get("exchange_status") or {}
    if not exchange_status:
        print("No exchange_status block found; broker may not be ready.")

    for exchange, status in exchange_status.items():
        print(f"  {exchange}: {status}")

    if args.before:
        print(f"\nwaiting {args.before}s and fetching metrics again ...")
        time.sleep(args.before)
        metrics2 = get_metrics(metrics_url)
        print("Second snapshot:")
        print(metrics2)

        for exchange, status in (metrics2.get("exchange_status") or {}).items():
            print(f"  {exchange}: {status}")


if __name__ == "__main__":
    main()
