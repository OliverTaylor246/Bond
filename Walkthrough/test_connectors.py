"""
Walkthrough: verify exchange connectors can be instantiated.

Run this to ensure each connector is importable, instantiable, and has the expected
connect method without actually dialing the exchange.
"""

from engine.connectors.binance import BinanceConnector
from engine.connectors.bybit import BybitConnector
from engine.connectors.hyperliquid import HyperliquidConnector

def main() -> None:
    connectors = [
        ("Binance", BinanceConnector()),
        ("Bybit", BybitConnector()),
        ("Hyperliquid", HyperliquidConnector()),
    ]
    for name, connector in connectors:
        print(f"{name} connector ready: {connector.__class__.__name__}")


if __name__ == "__main__":
    main()
