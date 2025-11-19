"""
3kk0 Unified WebSocket broker.

Single subscription endpoint that fans normalized trades/orderbooks to clients.
"""
import asyncio
import contextlib
import json
import logging
import time
from typing import Any, Dict, List, Optional, Set

from fastapi import FastAPI, Query, WebSocket, WebSocketDisconnect

from engine.aggregation import AggregationConfig, AggregationEngine, subscription_filter
from engine.connectors import (
    BinanceConnector,
    BybitConnector,
    ExchangeConnector,
    ExtendedExchangeConnector,
    HyperliquidConnector,
)
from engine.schemas import BaseEvent, EventType, Heartbeat, Millis, RawMessage, normalize_symbol

logger = logging.getLogger(__name__)

DEFAULT_SYMBOLS = ("BTC/USDT", "SOL/USDT")
DEFAULT_CHANNELS = ("trades", "orderbook")
DEFAULT_DEPTH = 20
MAX_LAG_MS = 100


RAW_CONNECTOR_CLASSES = (
    BinanceConnector,
    BybitConnector,
    HyperliquidConnector,
)

UNIFIED_RAW_MODES = {"unified", "broker", "manager", "aggregated"}


HEARTBEAT_INTERVAL_SEC = 30


class UnifiedBrokerManager:
    def __init__(self) -> None:
        self._event_queue: asyncio.Queue[BaseEvent] = asyncio.Queue()
        self._agg = AggregationEngine(
            AggregationConfig(max_lag_ms=MAX_LAG_MS, dedupe_window=5000)
        )
        self._flush_interval = max(self._agg.config.max_lag_ms / 1000.0, 0.05)
        self._clients: Dict[WebSocket, Dict[str, Any]] = {}
        self._clients_lock = asyncio.Lock()
        self._raw_clients: Set[WebSocket] = set()
        self._client_depths: Dict[WebSocket, int] = {}
        self._raw_subscribers = 0
        self._raw_mode_enabled = False
        self._depth = DEFAULT_DEPTH
        self._connector_config_lock = asyncio.Lock()
        self._connectors: List[ExchangeConnector] = [
            BinanceConnector(),
            BybitConnector(),
            HyperliquidConnector(),
            # ExtendedExchangeConnector(),  # Disabled temporarily; re-enable later.
        ]
        self._connector_subscription_specs: Dict[str, Dict[str, List[str]]] = {}
        self._connector_tasks: List[asyncio.Task[None]] = []
        self._broadcast_task: Optional[asyncio.Task[None]] = None
        self._flush_task: Optional[asyncio.Task[None]] = None
        self._heartbeat_tasks: Dict[WebSocket, asyncio.Task[None]] = {}
        self._running = False
        self._metrics = {
            "messages_sent": 0,
            "start_time": time.time(),
            "reconnects": 0,
        }

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._metrics["start_time"] = time.time()
        self._metrics["messages_sent"] = 0
        self._metrics["reconnects"] = 0
        self._connector_tasks = []
        self._connector_subscription_specs.clear()
        self._connector_tasks = []
        self._client_depths.clear()
        self._raw_subscribers = 0
        self._raw_mode_enabled = False
        self._depth = DEFAULT_DEPTH

        for connector in self._connectors:
            await connector.connect()
            spec = self._default_subscription_spec(connector)
            self._connector_subscription_specs[connector.exchange] = spec
            await connector.subscribe(
                symbols=spec["symbols"],
                channels=spec["channels"],
                depth=self._depth,
                raw=False,
            )
            task = asyncio.create_task(self._run_connector(connector))
            self._connector_tasks.append(task)

        self._broadcast_task = asyncio.create_task(self._broadcast_loop())
        self._flush_task = asyncio.create_task(self._flush_loop())

    async def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        for task in self._connector_tasks:
            task.cancel()
        for task in self._connector_tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task

        if self._broadcast_task:
            self._broadcast_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._broadcast_task
            self._broadcast_task = None

        if self._flush_task:
            self._flush_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._flush_task
            self._flush_task = None

        for connector in self._connectors:
            await connector.shutdown()

        for task in self._heartbeat_tasks.values():
            task.cancel()

        async with self._clients_lock:
            self._clients.clear()
            self._client_depths.clear()
        self._heartbeat_tasks.clear()
        self._raw_subscribers = 0
        self._raw_mode_enabled = False
        self._depth = DEFAULT_DEPTH
        self._connector_subscription_specs.clear()

    async def _run_connector(self, connector: ExchangeConnector) -> None:
        try:
            async for event in connector:
                await self._event_queue.put(event)
                if event.raw and self._raw_subscribers > 0:
                    raw_event = self._build_raw_event(event)
                    await self._event_queue.put(raw_event)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._metrics["reconnects"] += 1
            logger.exception("Connector %s error", connector.exchange, exc_info=exc)

    async def _broadcast_loop(self) -> None:
        try:
            while self._running:
                event = await self._event_queue.get()
                self._agg.ingest(event)
                ready = self._agg.flush_ready()
                for ready_event in ready:
                    await self._dispatch(ready_event)
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("Broadcast loop error", exc_info=True)

    async def _flush_loop(self) -> None:
        try:
            while self._running:
                await asyncio.sleep(self._flush_interval)
                ready = self._agg.flush_ready()
                for ready_event in ready:
                    await self._dispatch(ready_event)
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("Flush loop error", exc_info=True)

    async def _dispatch(self, event: BaseEvent) -> None:
        async with self._clients_lock:
            subscribers: List[WebSocket] = []
            for ws, spec in self._clients.items():
                if not subscription_filter(
                    event,
                    symbols=spec.get("symbols"),
                    exchanges=spec.get("exchanges"),
                ):
                    continue
                channel: Optional[str] = None
                if event.type == EventType.TRADE:
                    channel = "trades"
                elif event.type == EventType.ORDERBOOK:
                    channel = "orderbook"
                elif event.type == EventType.RAW:
                    if not spec.get("raw"):
                        continue
                    channel = "raw"
                channels = spec.get("channels") or set()
                if channel and "*" not in channels and channel not in channels:
                    continue
                subscribers.append(ws)
        for ws in subscribers:
            asyncio.create_task(self._safe_send(ws, event))
        await self._broadcast_raw(event)

    async def _broadcast_raw(self, event: BaseEvent) -> None:
        async with self._clients_lock:
            raw_clients = list(self._raw_clients)
        for ws in raw_clients:
            asyncio.create_task(self._safe_send(ws, event))

    async def _safe_send(self, websocket: WebSocket, event: BaseEvent) -> None:
        try:
            await websocket.send_json(event.to_wire())
            self._metrics["messages_sent"] += 1
        except Exception:
            await self.unregister(websocket)

    async def register(self, websocket: WebSocket, payload: Dict[str, Any]) -> None:
        channels = self._normalize_channels(payload.get("channels"))
        raw_flag = self._to_bool(payload.get("raw"))
        if raw_flag:
            channels.add("raw")
        symbols = self._normalize_symbols(payload.get("symbols")) or {"*"}
        exchanges = self._normalize_exchanges(payload.get("exchanges")) or {"*"}
        depth_value = self._normalize_depth(payload.get("depth")) or DEFAULT_DEPTH

        async with self._clients_lock:
            self._clients[websocket] = {
                "symbols": symbols,
                "exchanges": exchanges,
                "channels": channels,
                "raw": raw_flag,
                "depth": depth_value,
            }
            self._client_depths[websocket] = depth_value
            if raw_flag:
                self._raw_subscribers += 1
            depth_needed = self._calculate_target_depth()
            raw_needed = self._raw_subscribers > 0

        await self._maybe_update_connector_configs(depth_needed, raw_needed)
        heartbeat_task = asyncio.create_task(self._heartbeat_loop(websocket))
        self._heartbeat_tasks[websocket] = heartbeat_task

    async def unregister(self, websocket: WebSocket) -> None:
        async with self._clients_lock:
            spec = self._clients.pop(websocket, None)
            if spec and spec.get("raw"):
                self._raw_subscribers = max(self._raw_subscribers - 1, 0)
            self._client_depths.pop(websocket, None)
            depth_needed = self._calculate_target_depth()
            raw_needed = self._raw_subscribers > 0

        await self._maybe_update_connector_configs(depth_needed, raw_needed)
        task = self._heartbeat_tasks.pop(websocket, None)
        if task:
            task.cancel()

    async def register_raw(self, websocket: WebSocket) -> None:
        async with self._clients_lock:
            self._raw_clients.add(websocket)

    async def unregister_raw(self, websocket: WebSocket) -> None:
        async with self._clients_lock:
            self._raw_clients.discard(websocket)

    async def _heartbeat_loop(self, websocket: WebSocket) -> None:
        try:
            while True:
                heartbeat = Heartbeat(
                    exchange="system",
                    symbol="",
                    ts_event=self._now_ms(),
                    ts_exchange=self._now_ms(),
                    raw={"status": "ok"},
                )
                await websocket.send_json(heartbeat.to_wire())
                await asyncio.sleep(HEARTBEAT_INTERVAL_SEC)
        except asyncio.CancelledError:
            pass
        except Exception:
            await self.unregister(websocket)

    def get_metrics(self) -> Dict[str, Any]:
        uptime = time.time() - self._metrics["start_time"]
        active = len(self._clients)
        messages_per_second = self._metrics["messages_sent"] / max(uptime, 1.0)
        status = {
            connector.exchange: "connected" if connector._running else "disconnected"
            for connector in self._connectors
        }
        return {
            "active_subscriptions": active,
            "messages_per_second": round(messages_per_second, 2),
            "uptime": int(uptime),
            "reconnects": self._metrics["reconnects"],
            "exchange_status": status,
        }

    async def _maybe_update_connector_configs(
        self, depth_needed: int, raw_needed: bool
    ) -> None:
        depth_changed = depth_needed != self._depth
        raw_changed = raw_needed != self._raw_mode_enabled
        self._depth = depth_needed
        self._raw_mode_enabled = raw_needed
        if not self._running or (not depth_changed and not raw_changed):
            return
        await self._apply_connector_subscriptions()

    async def _apply_connector_subscriptions(self) -> None:
        if not self._running:
            return
        async with self._connector_config_lock:
            depth = self._depth
            raw = self._raw_mode_enabled
            for connector in self._connectors:
                spec = self._connector_subscription_specs.get(connector.exchange)
                if not spec:
                    continue
                try:
                    await connector.subscribe(
                        symbols=list(spec["symbols"]),
                        channels=list(spec["channels"]),
                        depth=depth,
                        raw=raw,
                    )
                except Exception:
                    logger.exception(
                        "Failed to refresh %s subscriptions", connector.exchange
                    )

    def _default_subscription_spec(
        self, connector: ExchangeConnector
    ) -> Dict[str, List[str]]:
        return {
            "symbols": self._default_symbols_for(connector),
            "channels": self._default_channels(),
        }

    def _default_symbols_for(self, connector: ExchangeConnector) -> List[str]:
        if connector.exchange == "extended":
            return ["BTC/USDC"]
        return list(DEFAULT_SYMBOLS)

    def _default_channels(self) -> List[str]:
        return list(DEFAULT_CHANNELS)

    def _build_raw_event(self, event: BaseEvent) -> RawMessage:
        payload = {"type": event.type.value, "data": event.raw}
        return RawMessage(
            exchange=event.exchange,
            symbol=event.symbol,
            ts_event=event.ts_event,
            ts_exchange=event.ts_exchange,
            payload=payload,
        )

    def _calculate_target_depth(self) -> int:
        if not self._client_depths:
            return DEFAULT_DEPTH
        return max(self._client_depths.values())

    def _normalize_symbols(self, raw_symbols: Any) -> Set[str]:
        result: Set[str] = set()
        for item in self._ensure_sequence(raw_symbols):
            if item is None:
                continue
            candidate = str(item).strip()
            if not candidate:
                continue
            if candidate == "*":
                result.add(candidate)
            else:
                result.add(normalize_symbol("", candidate))
        return result

    def _normalize_exchanges(self, raw_exchanges: Any) -> Set[str]:
        result: Set[str] = set()
        for item in self._ensure_sequence(raw_exchanges):
            if item is None:
                continue
            candidate = str(item).strip().lower()
            if candidate:
                result.add(candidate)
        return result

    def _normalize_channels(self, raw_channels: Any) -> Set[str]:
        result: Set[str] = set()
        for item in self._ensure_sequence(raw_channels):
            if item is None:
                continue
            candidate = str(item).strip().lower()
            if candidate:
                result.add(candidate)
        if not result:
            result = set(DEFAULT_CHANNELS)
        return result

    @staticmethod
    def _normalize_depth(value: Any) -> Optional[int]:
        if value is None:
            return None
        try:
            depth = int(value)
            if depth <= 0:
                return None
            return depth
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _to_bool(value: Any) -> bool:
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)

    @staticmethod
    def _ensure_sequence(value: Any) -> List[Any]:
        if value is None:
            return []
        if isinstance(value, str):
            return [value]
        if isinstance(value, (list, tuple, set)):
            return list(value)
        return [value]

    @staticmethod
    def _now_ms() -> Millis:
        return int(time.time() * 1000)


manager = UnifiedBrokerManager()

app = FastAPI(
    title="3kk0 Unified Broker",
    description="Single WS endpoint streaming normalized trades/orderbooks",
)


async def _websocket_raw_unified(websocket: WebSocket) -> None:
    await manager.register_raw(websocket)
    try:
        while True:
            await websocket.receive()
    except WebSocketDisconnect:
        pass
    finally:
        await manager.unregister_raw(websocket)


async def _pump_connector_events(websocket: WebSocket, connector: ExchangeConnector) -> None:
    try:
        async for event in connector:
            try:
                await websocket.send_json(event.to_wire())
            except RuntimeError:
                break
    except asyncio.CancelledError:
        pass
    except Exception:
        logger.exception(
            "Raw direct connector failed: %s", connector.exchange, exc_info=True
        )
        with contextlib.suppress(Exception):
            await websocket.close(code=1011, reason="Raw connector failure")


async def _websocket_raw_direct(
    websocket: WebSocket,
    symbols: list[str] | None,
    channels: list[str] | None,
    depth: int | None,
    raw: bool | None,
) -> None:
    normalized_symbols = manager._normalize_symbols(symbols) or set(DEFAULT_SYMBOLS)
    normalized_symbols = [sym for sym in normalized_symbols if sym != "*"]
    if not normalized_symbols:
        normalized_symbols = list(DEFAULT_SYMBOLS)
    channel_set = manager._normalize_channels(channels)
    channel_list = sorted(channel_set)
    depth_value = manager._normalize_depth(depth) or DEFAULT_DEPTH
    raw_mode = manager._to_bool(raw)

    connectors: list[ExchangeConnector] = []
    tasks: list[asyncio.Task[None]] = []
    try:
        for connector_cls in RAW_CONNECTOR_CLASSES:
            connector = connector_cls()
            connectors.append(connector)
            await connector.connect()
            await connector.subscribe(
                symbols=normalized_symbols,
                channels=channel_list,
                depth=depth_value,
                raw=raw_mode,
            )
            tasks.append(asyncio.create_task(_pump_connector_events(websocket, connector)))
        await websocket.send_json(
            {
                "type": "system",
                "status": "connected",
                "mode": "raw-direct",
                "symbols": normalized_symbols,
                "channels": channel_list,
                "depth": depth_value,
                "raw": raw_mode,
            }
        )
        try:
            while True:
                await websocket.receive()
        except WebSocketDisconnect:
            pass
    except Exception:
        logger.exception("Failed to start raw direct stream")
        with contextlib.suppress(Exception):
            await websocket.close(code=1011, reason="Raw connector setup failed")
    finally:
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        for connector in connectors:
            await connector.shutdown()


@app.on_event("startup")
async def _startup() -> None:
    await manager.start()


@app.on_event("shutdown")
async def _shutdown() -> None:
    await manager.stop()


@app.get("/metrics")
async def metrics() -> Dict[str, Any]:
    return manager.get_metrics()


@app.get("/health")
async def health() -> Dict[str, str]:
    return {"status": "ok", "service": "3kk0 Unified Broker"}


@app.websocket("/stream")
async def websocket_stream(websocket: WebSocket) -> None:
    await websocket.accept()
    payload = await websocket.receive_json()
    if payload.get("type") != "subscribe":
        await websocket.close(code=1003)
        return
    await manager.register(websocket, payload)
    await websocket.send_json(
        {"type": "system", "status": "subscribed", "received": payload}
    )
    try:
        await websocket.wait_closed()
    except (WebSocketDisconnect, AttributeError):
        pass
    finally:
        await manager.unregister(websocket)


@app.websocket("/raw")
async def websocket_raw(websocket: WebSocket):
    params = websocket.query_params

    mode_value = (params.get("mode") or "").strip().lower()

    symbol = params.get("symbol")
    symbols = params.getlist("symbols")
    channels = params.getlist("channels")
    depth = params.get("depth")
    raw = params.get("raw")

    await websocket.accept()

    # unified mode
    if mode_value in UNIFIED_RAW_MODES:
        await _websocket_raw_unified(websocket)
        return

    # combine symbol lists
    if symbol and symbols:
        requested_symbols = [*symbols, symbol]
    elif symbol:
        requested_symbols = [symbol]
    else:
        requested_symbols = symbols

    await _websocket_raw_direct(
        websocket,
        symbols=requested_symbols,
        channels=channels,
        depth=depth,
        raw=raw,
    )
