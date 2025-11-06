"""
Test script for Bond SDK - Subscribe to an existing stream.

Usage:
    python test_sdk.py --stream-id <id> --token <token>
"""
import argparse
import asyncio
import os
import sys

# Add sdk to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "sdk/python"))

from bond.client import BondClient


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Subscribe to a Bond stream via the SDK.")
    parser.add_argument(
        "--stream-id",
        default=os.getenv("BOND_STREAM_ID", "e5df1750b8991581"),
        help="Stream identifier from the UI.",
    )
    parser.add_argument(
        "--token",
        default=os.getenv("BOND_ACCESS_TOKEN"),
        help="Access token for the stream. Prompts if omitted.",
    )
    parser.add_argument(
        "--api-url",
        default=os.getenv("BOND_API_URL", "http://localhost:8000"),
        help="HTTP API base URL.",
    )
    parser.add_argument(
        "--ws-url",
        default=os.getenv("BOND_WS_URL", "ws://localhost:8080"),
        help="WebSocket URL.",
    )
    parser.add_argument(
        "--max-events",
        type=int,
        default=int(os.getenv("BOND_MAX_EVENTS", "0")),
        help="Optional cap on number of events to receive (0 means unlimited).",
    )
    return parser.parse_args()


async def run_subscription(args: argparse.Namespace) -> None:
    access_token = args.token or input("Enter your access token from the UI: ").strip()

    client = BondClient(api_url=args.api_url, ws_url=args.ws_url)

    print(f"\nConnecting to stream {args.stream_id}...")
    print(f"API URL: {args.api_url}")
    print(f"WS URL: {args.ws_url}")
    print("Waiting for events (press Ctrl+C to stop)...\n")

    event_count = 0
    try:
        async for event in client.subscribe(args.stream_id, access_token):
            event_count += 1
            print(f"Event #{event_count}:")
            print(f"  Timestamp: {event.get('ts')}")
            print(f"  Price Avg: ${event.get('price_avg', 'N/A')}")
            print(f"  Volume Sum: {event.get('volume_sum', 'N/A')}")
            print(f"  Window: {event.get('window_start')} -> {event.get('window_end')}")
            print()

            if args.max_events and event_count >= args.max_events:
                print(f"Reached event limit of {args.max_events}. Stopping subscription.")
                break

    except KeyboardInterrupt:
        print(f"\nReceived {event_count} events. Disconnecting...")
    except Exception as exc:
        print(f"\nError while receiving events: {exc}")
    finally:
        print("Done.")


def main() -> None:
    args = parse_args()
    asyncio.run(run_subscription(args))


if __name__ == "__main__":
    main()
