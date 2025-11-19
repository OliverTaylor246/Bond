"""
Walkthrough: ensure the kk0 SDK can build a subscription payload and stream template events.
"""

from kk0 import Stream


def main() -> None:
    stream = Stream("wss://3kk0-broker-production.up.railway.app/stream")
    print("Stream ready with URL:", stream.url)
    print("You can use this stream to subscribe to trade/orderbook events.")


if __name__ == "__main__":
    main()
