import sys
from pathlib import Path


SYNC_PATH = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SYNC_PATH))

"""
Walkthrough: exercise the aggregation + dedupe logic.
"""

from datetime import datetime, timedelta

from engine.aggregation import AggregationEngine
from engine.schemas import Trade, Side


def make_trade(seq: int) -> Trade:
    ts = int((datetime.utcnow() - timedelta(seconds=seq)).timestamp() * 1000)
    return Trade(
        exchange="binance",
        symbol="BTC/USDT",
        price=50000 + seq,
        size=0.1,
        side=Side.BUY,
        ts_event=ts,
        ts_exchange=ts,
        trade_id=str(seq),
    )


def main() -> None:
    engine = AggregationEngine()
    trades = [make_trade(i) for i in range(4)]
    for trade in trades:
        engine.ingest(trade)
    flushed = engine.flush_ready()
    print("Flushed trades:", len(flushed))
    for event in flushed:
        print(event.price, event.trade_id)


if __name__ == "__main__":
    main()
