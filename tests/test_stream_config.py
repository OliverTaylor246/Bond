from engine.schemas import StreamConfig
import pytest


def test_valid_binance_internal_ohlcv():
  cfg = StreamConfig(
    stream_id="test",
    exchange="binance",
    symbols=["BTC-USDT"],
    channels={
      "orderbook": {"enabled": True, "depth": 20, "l3": False},
      "trades": True,
      "ticker": True,
      "funding": False,
      "ohlcv": {"enabled": True, "interval": "1s", "mode": "internal"},
      "open_interest": False,
    },
    heartbeat_ms=1000,
    flush_interval_ms=50,
    ttl=30,
  )
  assert cfg.validate_against_capabilities() == cfg


def test_reject_l3_on_kucoin():
  cfg = StreamConfig(
    stream_id="bad",
    exchange="kucoin",
    symbols=["BTC-USDT"],
    channels={
      "orderbook": {"enabled": True, "depth": 50, "l3": True},
      "trades": True,
      "ticker": True,
      "funding": False,
      "ohlcv": {"enabled": False, "interval": "1s", "mode": "internal"},
      "open_interest": False,
    },
    heartbeat_ms=1000,
    flush_interval_ms=50,
    ttl=30,
  )
  with pytest.raises(ValueError):
    cfg.validate_against_capabilities()


def test_require_trades_for_internal_ohlcv():
  cfg = StreamConfig(
    stream_id="bad2",
    exchange="binance",
    symbols=["BTC-USDT"],
    channels={
      "orderbook": {"enabled": False, "depth": 20, "l3": False},
      "trades": False,
      "ticker": True,
      "funding": False,
      "ohlcv": {"enabled": True, "interval": "1s", "mode": "internal"},
      "open_interest": False,
    },
    heartbeat_ms=1000,
    flush_interval_ms=50,
    ttl=30,
  )
  with pytest.raises(ValueError):
    cfg.validate_against_capabilities()
