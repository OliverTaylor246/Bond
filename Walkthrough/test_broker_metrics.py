"""
Walkthrough: peek at broker metrics / stream health.
"""

import argparse
import httpx


def fetch_metrics(url: str) -> dict:
    res = httpx.get(url, timeout=5.0)
    res.raise_for_status()
    return res.json()


def print_metrics(metrics: dict) -> None:
    print("Active subscriptions:", metrics.get("active_subscriptions"))
    print("Messages/s:", metrics.get("messages_per_second"))
    exchange_status = metrics.get("exchange_status") or {}
    for exchange, status in exchange_status.items():
        print(f"  {exchange}: {status}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Grab broker metrics")
    parser.add_argument(
        "--host", default="https://3kk0-broker-production.up.railway.app", help="Broker host"
    )
    args = parser.parse_args()

    metrics_url = f"{args.host.rstrip('/')}/metrics"
    metrics = fetch_metrics(metrics_url)
    print_metrics(metrics)


if __name__ == "__main__":
    main()
