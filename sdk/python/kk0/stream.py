import asyncio
import json
import logging
from typing import Iterable, Callable, Optional, Sequence, Set

import websockets
from websockets import WebSocketClientProtocol

logger = logging.getLogger(__name__)


def _is_raw_url(url: str) -> bool:
    """Detect if the client is connecting to the /raw endpoint.

    /raw DOES NOT ACCEPT subscribe messages.
    /stream EXPECTS a subscribe message.
    """
    try:
        # Normalize URL
        path = url.split("://", 1)[-1]
        # Extract the path portion
        path = path.split("/", 1)[-1]
        return path.startswith("raw")
    except Exception:
        return False


class Stream:
    """
    Resilient kk0 client that keeps the websocket alive and exposes an iterator.
    """

    def __init__(
        self,
        url: str,
        *,
        reconnect_interval: float = 0.5,
        on_heartbeat: Optional[Callable[[dict], None]] = None,
    ):
        self._is_raw = False
        self._subscribed = False
        self.url = url
        self.reconnect_interval = reconnect_interval
        self._on_heartbeat = on_heartbeat
        self._ws: WebSocketClientProtocol | None = None
        self._stop = False


    async def __aenter__(self):
        self._ws = await self._connect()

        # NEW — Detect raw endpoint
        self._is_raw = _is_raw_url(self.url)

        # NOTE: /raw does not accept subscribe messages.
        return self


    async def __aexit__(self, exc_type, exc, tb):
        await self.close()

    async def _connect(self) -> WebSocketClientProtocol:
        while not self._stop:
            try:
                ws = await websockets.connect(self.url, ping_interval=20, close_timeout=5)
                return ws
            except Exception as exc:
                logger.debug("kk0 stream reconnecting after connect error: %s", exc)
                await asyncio.sleep(self.reconnect_interval)
        raise RuntimeError("Stream stopped before connection could be established")

    async def subscribe(
        self,
        *,
        channels: Iterable[str],
        symbols: Iterable[str],
        exchanges: Iterable[str],
        depth: int = 20,
        speed_ms: Optional[int] = None,
        raw: bool = False,
    ):
        if not self._ws:
            raise RuntimeError("WebSocket isn't connected yet")
        channel_list = self._normalize_channels(channels)
        payload = {
            "type": "subscribe",
            "channels": channel_list,
            "symbols": list(symbols),
            "exchanges": list(exchanges),
            "depth": depth,
            "raw": raw,
        }
        if speed_ms is not None:
            payload["speed_ms"] = int(speed_ms)
        payload_json = json.dumps(payload)

        # /raw endpoints do NOT accept subscribe messages
        if self._is_raw:
            # Save subscription info locally so __anext__ still works
            self._subscribed = True
            return

        await self._ws.send(payload_json)

    async def close(self) -> None:
        self._stop = True
        if self._ws:
            await self._ws.close()
            self._ws = None

    def __aiter__(self):
        return self

    async def __anext__(self):
        while True:
            if not self._ws:
                self._ws = await self._connect()
            assert self._ws
            try:
                msg = await self._ws.recv()
                # NEW: raw mode → return event as-is
                if self._is_raw:
                    try:
                        return json.loads(msg)
                    except Exception:
                        return msg
                event = json.loads(msg)
                if event.get("type") == "heartbeat" and self._on_heartbeat:
                    self._on_heartbeat(event)
                return event
            except websockets.ConnectionClosedOK:
                logger.debug("kk0 stream closed cleanly, reconnecting")
                self._ws = None
                await asyncio.sleep(self.reconnect_interval)
                continue
            except websockets.ConnectionClosedError as exc:
                logger.warning("kk0 stream closed unexpectedly: %s", exc)
                self._ws = None
                await asyncio.sleep(self.reconnect_interval)
                continue

    def _normalize_channels(self, channels: Iterable[str]) -> list[str]:
        normalized: Set[str] = set()
        for ch in channels or []:
            lowered = str(ch).strip().lower()
            if not lowered:
                continue
            if lowered in {"price", "prices"}:
                lowered = "ticker"
            normalized.add(lowered)
        if not normalized:
            normalized = {"trades", "orderbook"}
        return list(normalized)
