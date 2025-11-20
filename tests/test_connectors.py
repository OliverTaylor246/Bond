from __future__ import annotations

from engine.connectors import (
    BinanceConnector,
    BybitConnector,
    ExtendedExchangeConnector,
    HyperliquidConnector,
    LighterConnector,
    OkxConnector,
)
from engine.schemas import BookUpdateType, Side


def test_binance_trade_normalization():
    connector = BinanceConnector()
    payload = {
        "p": "42000.5",
        "q": "0.010",
        "E": 1_630_000_000_000,
        "T": 1_630_000_000_000,
        "s": "BTCUSDT",
        "m": False,
        "t": 123456,
    }
    trade = connector._normalize_trade(payload, "btcusdt@trade")

    assert trade.exchange == "binance"
    assert trade.symbol == "BTC/USDT"
    assert trade.price == 42000.5
    assert trade.size == 0.01
    assert trade.side == Side.BUY
    assert trade.trade_id == "123456"


def test_binance_orderbook_snapshot_and_delta():
    connector = BinanceConnector()
    snapshot_payload = {
        "E": 1_630_000_000_000,
        "s": "BTCUSDT",
        "b": [["42000.0", "1"]],
        "a": [["42001.0", "2"]],
        "u": 100,
        "U": 99,
        "pu": 98,
        "firstUpdateId": 1,
    }
    snapshot = connector._normalize_orderbook(snapshot_payload, "btcusdt@depth20@100ms")

    assert snapshot.update_type == BookUpdateType.SNAPSHOT
    assert snapshot.sequence == 100
    assert snapshot.prev_sequence == 98
    assert snapshot.depth == connector._depth
    assert snapshot.bids[0] == (42000.0, 1.0)
    assert snapshot.asks[0] == (42001.0, 2.0)

    delta_payload = {
        "E": 1_630_000_000_500,
        "s": "BTCUSDT",
        "b": [["42005.0", "0.5"]],
        "a": [["42006.0", "0.4"]],
        "u": 105,
        "U": 101,
        "pu": 104,
    }
    delta = connector._normalize_orderbook(delta_payload, "btcusdt@depth20@100ms")

    assert delta.update_type == BookUpdateType.DELTA
    assert delta.sequence == 105
    assert delta.prev_sequence == 104
    assert delta.depth == connector._depth


def test_bybit_trade_and_orderbook_normalization():
    connector = BybitConnector()
    trade_payload = {
        "price": "18.28",
        "size": "1.5",
        "trade_time_ms": 1_630_000_000_000,
        "symbol": "BTCUSDT",
        "side": "Buy",
        "trade_id": 999,
    }
    trade_event = connector._normalize_trade(
        trade_payload,
        {"topic": "publicTrade.BTCUSDT", "ts": 1_630_000_000_000},
    )

    assert trade_event.exchange == "bybit"
    assert trade_event.symbol == "BTC/USDT"
    assert trade_event.price == 18.28
    assert trade_event.size == 1.5
    assert trade_event.side == Side.BUY
    assert trade_event.trade_id == "999"

    snapshot_payload = {
        "topic": "orderBookL2_25.BTCUSDT",
        "ts": 1_630_000_001_000,
        "type": "snapshot",
        "seqNum": 200,
        "prevSeqNum": 199,
        "data": [
            {"price": "41000.0", "size": "0.3", "side": "Buy"},
            {"price": "41010.0", "size": "0.1", "side": "Sell"},
        ],
    }
    snapshot = connector._normalize_orderbook(snapshot_payload)

    assert snapshot.update_type == BookUpdateType.SNAPSHOT
    assert snapshot.sequence == 200
    assert snapshot.prev_sequence == 199
    assert snapshot.depth == len(snapshot.bids)
    assert snapshot.bids[0][0] < snapshot.asks[0][0]

    delta_payload = {
        "topic": "orderBookL2_25.BTCUSDT",
        "ts": 1_630_000_001_500,
        "type": "delta",
        "seqNum": 201,
        "prevSeqNum": 200,
        "data": [
            {"price": "41005.0", "size": "0.05", "side": "Buy"},
            {"price": "41015.0", "size": "0.07", "side": "Sell"},
        ],
    }
    delta = connector._normalize_orderbook(delta_payload)

    assert delta.update_type == BookUpdateType.DELTA
    assert delta.sequence == 201
    assert delta.prev_sequence == 200


def test_hyperliquid_trade_and_orderbook_normalization():
    connector = HyperliquidConnector()
    trade_payload = {
        "coin": "BTC",
        "symbol": "BTC/USDT",
        "px": "19000",
        "sz": "0.5",
        "time": 1_630_000_000_000,
        "side": "B",
        "tid": "hl-1",
        "hash": "swap-1",
    }
    trade = connector._normalize_trade(trade_payload)

    assert trade.exchange == "hyperliquid"
    assert trade.symbol == "BTC/USDT"
    assert trade.price == 19000.0
    assert trade.size == 0.5
    assert trade.side == Side.BUY
    assert trade.trade_id == "hl-1"

    orderbook_payload = {
        "coin": "BTC",
        "symbol": "BTC/USDT",
        "time": 1_630_000_002_000,
        "levels": [
            [{"px": "19000", "sz": "1"}],
            [{"px": "19010", "sz": "0.6"}],
        ],
    }
    book = connector._normalize_orderbook(orderbook_payload)

    assert book.exchange == "hyperliquid"
    assert book.symbol == "BTC/USDT"
    assert book.update_type == BookUpdateType.SNAPSHOT
    assert book.depth is None
    assert book.bids == [(19000.0, 1.0)]
    assert book.asks == [(19010.0, 0.6)]


def test_extended_trade_and_orderbook_normalization():
    connector = ExtendedExchangeConnector()
    trade_payload = {
        "price": "1.23",
        "quantity": "0.8",
        "timestamp": 1_630_000_000_000,
        "symbol": "SOL/USDC",
        "side": "sell",
        "id": "ext-123",
    }
    trade = connector._normalize_trade(trade_payload, "trades.SOLUSDC")

    assert trade.exchange == "extended"
    assert trade.symbol == "SOL/USDC"
    assert trade.price == 1.23
    assert trade.size == 0.8
    assert trade.side == Side.SELL
    assert trade.trade_id == "ext-123"

    snapshot_payload = {
        "topic": "orderbook.SOLUSDC",
        "type": "snapshot",
        "timestamp": 1_630_000_003_000,
        "data": {
            "symbol": "SOL/USDC",
            "levels": [
                {"price": "30.0", "quantity": "2", "side": "bid"},
                {"price": "30.5", "quantity": "1", "side": "ask"},
            ],
            "sequence": 400,
            "prevSequence": 399,
        },
    }
    snapshot = connector._normalize_orderbook(
        snapshot_payload["data"],
        snapshot_payload["topic"],
        snapshot_payload,
    )

    assert snapshot.update_type == BookUpdateType.SNAPSHOT
    assert snapshot.sequence == 400
    assert snapshot.prev_sequence == 399
    assert snapshot.bids[0][0] < snapshot.asks[0][0]
    assert snapshot.depth == len(snapshot.bids)

    delta_payload = {
        "topic": "orderbook.SOLUSDC",
        "type": "update",
        "timestamp": 1_630_000_003_500,
        "data": {
            "symbol": "SOL/USDC",
            "levels": [
                {"price": "30.1", "quantity": "1", "side": "bid"},
                {"price": "30.6", "quantity": "0.5", "side": "ask"},
            ],
            "sequence": 401,
            "prevSequence": 400,
        },
    }
    delta = connector._normalize_orderbook(
        delta_payload["data"],
        delta_payload["topic"],
        delta_payload,
    )

    assert delta.update_type == BookUpdateType.DELTA
    assert delta.sequence == 401
    assert delta.prev_sequence == 400
    assert delta.depth == len(delta.bids)


def test_okx_trade_and_orderbook_normalization():
    connector = OkxConnector()
    trade_entry = {
        "instId": "BTC-USDT",
        "tradeId": "9001",
        "px": "42000.5",
        "sz": "0.05",
        "side": "buy",
        "ts": "1726330000000",
    }
    trade = connector._normalize_trade(trade_entry, {"channel": "trades", "instId": "BTC-USDT"})

    assert trade.exchange == "okx"
    assert trade.symbol == "BTC/USDT"
    assert trade.price == 42000.5
    assert trade.size == 0.05
    assert trade.side == Side.BUY
    assert trade.trade_id == "9001"

    orderbook_entry = {
        "asks": [["42001.0", "1"]],
        "bids": [["42000.0", "2"]],
        "ts": "1726330000500",
        "seqNum": 200,
        "preSeqNum": 199,
        "action": "snapshot",
    }
    snapshot = connector._normalize_orderbook(orderbook_entry, {"channel": "books-l2-tbt", "instId": "BTC-USDT"})

    assert snapshot.update_type == BookUpdateType.SNAPSHOT
    assert snapshot.sequence == 200
    assert snapshot.prev_sequence == 199
    assert snapshot.depth == connector._depth
    assert snapshot.bids[0] == (42000.0, 2.0)
    assert snapshot.asks[0] == (42001.0, 1.0)


def test_lighter_trade_and_orderbook_normalization():
    connector = LighterConnector()
    connector._market_map[13] = "TAO"

    trade_payload = {
        "trade_id": 14035051,
        "price": "3335.65",
        "size": "0.1187",
        "side": "sell",
        "timestamp": 1722339648,
    }
    trade = connector._normalize_trade(trade_payload, 13)

    assert trade.exchange == "lighter"
    assert trade.symbol == "TAO/USDC"
    assert trade.price == 3335.65
    assert trade.size == 0.1187
    assert trade.side == Side.SELL
    assert trade.trade_id == "14035051"

    orderbook_payload = {
        "code": 0,
        "asks": [{"price": "3327.46", "size": "29.0915"}],
        "bids": [{"price": "3338.80", "size": "10.2898"}],
        "offset": 41692864,
    }
    book = connector._normalize_orderbook(orderbook_payload, 13)

    assert book.exchange == "lighter"
    assert book.symbol == "TAO/USDC"
    assert book.update_type == BookUpdateType.SNAPSHOT
    assert book.depth == connector._depth
    assert book.bids[0] == (3338.8, 10.2898)
    assert book.asks[0] == (3327.46, 29.0915)
