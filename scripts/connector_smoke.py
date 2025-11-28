#!/usr/bin/env python3
"""
Comprehensive smoke-test for exchange connectors.

Tests all feed types:
- Trades
- L2 Orderbook
- Tickers (mark/index/last price)
- Funding Rates
- Open Interest
- OHLCV
"""
from __future__ import annotations

import asyncio
from typing import Sequence, Dict, Any
from pathlib import Path
import sys

# Ensure project root on sys.path
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from apps.runtime.exchange_registry import get_exchange_connector
from engine.schemas import StreamConfig


def make_config(exchange: str, symbols: Sequence[str], feed_type: str) -> StreamConfig:
    """Create config for specific feed type"""
    configs = {
        "trades": {
            "orderbook": {"enabled": False},
            "trades": True,
            "ticker": False,
            "funding": False,
            "ohlcv": {"enabled": False},
            "open_interest": False,
        },
        "lob": {
            "orderbook": {"enabled": True, "depth": 20, "l3": False},
            "trades": False,
            "ticker": False,
            "funding": False,
            "ohlcv": {"enabled": False},
            "open_interest": False,
        },
        "ticker": {
            "orderbook": {"enabled": False},
            "trades": False,
            "ticker": True,
            "funding": False,
            "ohlcv": {"enabled": False},
            "open_interest": False,
        },
        "funding": {
            "orderbook": {"enabled": False},
            "trades": False,
            "ticker": False,
            "funding": True,
            "ohlcv": {"enabled": False},
            "open_interest": False,
        },
        "oi": {
            "orderbook": {"enabled": False},
            "trades": False,
            "ticker": False,
            "funding": False,
            "ohlcv": {"enabled": False},
            "open_interest": True,
        },
        "ohlcv": {
            "orderbook": {"enabled": False},
            "trades": False,
            "ticker": False,
            "funding": False,
            "ohlcv": {"enabled": True, "interval": "1s", "mode": "internal"},
            "open_interest": False,
        },
    }
    
    return StreamConfig(
        stream_id=f"smoke-{exchange}-{feed_type}",
        exchange=exchange,
        symbols=list(symbols),
        channels=configs.get(feed_type, configs["trades"]),
        heartbeat_ms=2000,
        flush_interval_ms=100,
        ttl=30,
    ).validate_against_capabilities()


async def run_once(
    exchange: str, 
    symbols: Sequence[str], 
    feed_type: str,
    max_events: int = 3, 
    timeout: float = 8.0
) -> Dict[str, Any]:
    """Test a specific feed type for an exchange"""
    result = {
        "exchange": exchange,
        "feed_type": feed_type,
        "status": "unknown",
        "events": [],
        "error": None
    }
    
    try:
        connector = get_exchange_connector(exchange)
    except ValueError as err:
        result["status"] = "not_registered"
        result["error"] = str(err)
        return result

    try:
        cfg = make_config(exchange, symbols, feed_type)
    except ValueError as err:
        # Exchange doesn't support this feed type
        result["status"] = "not_supported"
        result["error"] = str(err)
        return result
    
    events = connector.stream_exchange(cfg)

    received = 0
    last_error = None
    try:
        while received < max_events:
            try:
                evt = await asyncio.wait_for(events.__anext__(), timeout=timeout)
                result["events"].append(evt)
                received += 1
            except asyncio.TimeoutError:
                if received == 0:
                    result["status"] = "timeout"
                    result["error"] = f"No events received within {timeout}s timeout"
                else:
                    result["status"] = "partial"
                    result["error"] = f"Only {received} events received before timeout"
                break
            except StopAsyncIteration:
                result["status"] = "stream_ended"
                result["error"] = "Stream ended unexpectedly"
                break
            except Exception as err:
                last_error = err
                result["status"] = "error"
                result["error"] = f"{type(err).__name__}: {str(err)}"
                break
        
        if received >= max_events:
            result["status"] = "success"
            
    except NotImplementedError as err:
        result["status"] = "not_implemented"
        result["error"] = str(err)
    except Exception as err:
        result["status"] = "error"
        result["error"] = f"{type(err).__name__}: {str(err)}"
    
    return result


def print_result(result: Dict[str, Any]) -> None:
    """Pretty print test result"""
    exchange = result["exchange"]
    feed = result["feed_type"]
    status = result["status"]
    events = result["events"]
    
    status_emoji = {
        "success": "✅",
        "partial": "⚠️",
        "timeout": "❌",
        "error": "❌",
        "not_implemented": "🚫",
        "not_registered": "🚫",
        "not_supported": "⊘"
    }
    
    emoji = status_emoji.get(status, "❓")
    print(f"\n[{exchange}] {feed.upper()} {emoji} {status}")
    
    if events:
        print(f"  Received {len(events)} events:")
        for i, evt in enumerate(events[:2], 1):  # Show first 2
            # Show key fields only
            evt_type = evt.get("type", "unknown")
            symbol = evt.get("symbol", "?")
            key_fields = []
            
            if evt_type == "trade":
                key_fields = [f"px={evt.get('px')}", f"size={evt.get('size')}", f"side={evt.get('side')}"]
            elif evt_type == "lob.update":
                key_fields = [f"bids={len(evt.get('bids', []))}", f"asks={len(evt.get('asks', []))}"]
            elif evt_type == "ticker":
                key_fields = [f"mark={evt.get('mark')}", f"index={evt.get('index')}", f"last={evt.get('last')}"]
            elif evt_type == "funding.update":
                key_fields = [f"rate={evt.get('rate')}", f"next={evt.get('next_funding')}"]
            elif evt_type == "oi.update":
                key_fields = [f"oi={evt.get('oi')}"]
            elif evt_type == "ohlcv":
                key_fields = [f"o={evt.get('open')}", f"h={evt.get('high')}", f"l={evt.get('low')}", f"c={evt.get('close')}"]
            
            print(f"    #{i} {symbol}: {evt_type} {' '.join(key_fields)}")
    
    if result["error"]:
        print(f"  Error: {result['error']}")


async def main() -> None:
    """Run comprehensive smoke tests"""
    print("=" * 70)
    print("EXCHANGE CONNECTOR COMPREHENSIVE SMOKE TEST")
    print("=" * 70)
    
    # Test configuration: exchange -> symbols
    # Note: Using spot symbols by default; connectors should handle gracefully
    test_configs = {
        "binance": ["BTCUSDT"],  # Spot - funding/OI won't work
        "hyperliquid": ["BTC"],  # Perp-only exchange
        "bybit": ["BTCUSDT"],    # Spot - funding/OI won't work  
        "lighter": ["BTC"],      # Perp-only exchange
    }
    
    # Feed types to test
    feed_types = ["trades", "lob", "ticker", "funding", "oi"]
    
    # Run all tests
    all_results = []
    for exchange, symbols in test_configs.items():
        print(f"\n{'=' * 70}")
        print(f"Testing: {exchange.upper()}")
        print(f"{'=' * 70}")
        
        for feed_type in feed_types:
            result = await run_once(exchange, symbols, feed_type, max_events=3, timeout=8.0)
            all_results.append(result)
            print_result(result)
    
    # Summary
    print(f"\n{'=' * 70}")
    print("SUMMARY")
    print(f"{'=' * 70}")
    
    by_exchange = {}
    for result in all_results:
        ex = result["exchange"]
        if ex not in by_exchange:
            by_exchange[ex] = {"success": 0, "total": 0}
        by_exchange[ex]["total"] += 1
        if result["status"] == "success":
            by_exchange[ex]["success"] += 1
    
    for exchange, stats in by_exchange.items():
        success_rate = (stats["success"] / stats["total"]) * 100 if stats["total"] > 0 else 0
        print(f"{exchange:12s}: {stats['success']}/{stats['total']} feeds working ({success_rate:.0f}%)")
    
    print(f"\n{'=' * 70}")


if __name__ == "__main__":
    asyncio.run(main())
