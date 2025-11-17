import asyncio
import time
from typing import Any

import pytest
from anyio import ClosedResourceError
from fastapi import WebSocket
from fastapi.testclient import TestClient

from apps.broker.unified import app, manager
from engine.connectors.base import ExchangeConnector
from engine.schemas import BaseEvent, EventType, Side, Trade


class NoopConnector(ExchangeConnector):
    """Stub connector used to suppress real exchange traffic during the test."""

    exchange = "noop"
    supported_channels = {"trades", "orderbook"}

    def __init__(self) -> None:
        super().__init__()

    async def connect(self) -> None:
        self._running = True

    async def disconnect(self) -> None:
        self._running = False

    async def subscribe(self, **_: Any) -> None:
        return

    async def __aiter__(self):
        while self._running:
            await asyncio.sleep(0.1)
            if False:  # keep this an async generator without emitting values
                yield None

    def _normalize_trade(self, _: dict[str, Any]) -> Trade | None:
        return None

    def _normalize_orderbook(self, _: dict[str, Any]) -> None:
        return None


@pytest.fixture
def stream_with_trade(monkeypatch):
    original_connectors = manager._connectors
    manager._connectors = [NoopConnector()]
    trade_event = Trade(
        exchange="binance",
        symbol="BTC/USDT",
        price=42000.0,
        size=0.1,
        side=Side.BUY,
        ts_event=int(time.time() * 1000),
        ts_exchange=int(time.time() * 1000),
        trade_id="stream-test-trade",
    )
    original_register = manager.register
    original_safe_send = manager._safe_send
    sent: list[BaseEvent] = []

    async def capture_safe_send(websocket, event):
        sent.append(event)
        await original_safe_send(websocket, event)

    async def register_and_emit(websocket, payload):
        await original_register(websocket, payload)
        await manager._event_queue.put(trade_event)

    original_unregister = manager.unregister
    async def noop_unregister(websocket: WebSocket) -> None:  # type: ignore[name-defined]
        return

    monkeypatch.setattr(manager, "register", register_and_emit)
    monkeypatch.setattr(manager, "_safe_send", capture_safe_send)
    monkeypatch.setattr(manager, "unregister", noop_unregister)
    try:
        yield trade_event, sent
    finally:
        manager._connectors = original_connectors
        manager.register = original_register
        manager._safe_send = original_safe_send
        manager.unregister = original_unregister


def test_stream_pushes_trade(stream_with_trade):
    trade_event, sent_events = stream_with_trade
    payload = {
        "type": "subscribe",
        "symbols": ["BTC/USDT"],
        "exchanges": ["binance"],
        "channels": ["trades"],
        "depth": 20,
        "raw": False,
    }
    with TestClient(app) as client:
        with client.websocket_connect("/stream") as ws:
            ws.send_json(payload)
            ack = ws.receive_json()
            assert ack["type"] == "system"
            assert ack["status"] == "subscribed"
            start = time.time()
            deadline = start + 5
            while time.time() < deadline:
                try:
                    ws.receive_json(timeout=0.5)
                except ClosedResourceError:
                    break
                except Exception:
                    continue
            assert any(
                isinstance(event, BaseEvent) and event.type == EventType.TRADE
                and event.exchange == "binance"
                and event.symbol == "BTC/USDT"
                for event in sent_events
            )
