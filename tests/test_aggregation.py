import time

from engine.aggregation import AggregationConfig, AggregationEngine, subscription_filter
from engine.schemas import EventType, Side, Trade


def _make_trade(exchange: str, symbol: str, price: float, ts_event: int, seq: int) -> Trade:
    return Trade(
        exchange=exchange,
        symbol=symbol,
        price=price,
        size=0.01,
        side=Side.BUY,
        ts_event=ts_event,
        ts_exchange=ts_event,
        trade_id=str(seq),
        sequence=seq,
    )


def test_aggregation_dedupes_and_orders():
    config = AggregationConfig(max_lag_ms=0, dedupe_window=10)
    engine = AggregationEngine(config)
    now_ms = int(time.time() * 1000) - 1000

    t1 = _make_trade("binance", "BTC/USDT", 50000.0, now_ms, 1)
    t2 = _make_trade("binance", "BTC/USDT", 50000.0, now_ms, 1)
    t3 = _make_trade("binance", "BTC/USDT", 50001.0, now_ms + 1, 2)

    engine.ingest(t2)
    engine.ingest(t1)
    engine.ingest(t3)

    flushed = engine.flush_ready()
    assert len(flushed) == 2
    assert flushed[0].price == 50000.0
    assert flushed[1].price == 50001.0


def test_subscription_filter_wildcards():
    t = _make_trade("binance", "BTC/USDT", 100.0, int(time.time() * 1000), 10)
    # wildcard symbol
    assert subscription_filter(t, symbols={"*"}, exchanges={"binance"})
    # missing exchange should fail
    assert not subscription_filter(t, symbols={"BTC/USDT"}, exchanges={"okx"})


def test_trade_to_wire_converts_enums():
    t = _make_trade("binance", "BTC/USDT", 30000.0, int(time.time() * 1000), 5)
    wire = t.to_wire()
    assert wire["type"] == EventType.TRADE.value
    assert wire["side"] == Side.BUY.value
