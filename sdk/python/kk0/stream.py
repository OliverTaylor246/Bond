import asyncio
import json
import logging
from typing import Iterable, Callable, Optional

import websockets
from websockets import WebSocketClientProtocol

logger = logging.getLogger(__name__)


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
        self.url = url
        self.reconnect_interval = reconnect_interval
        self._on_heartbeat = on_heartbeat
        self._ws: WebSocketClientProtocol | None = None
        self._stop = False

    async def __aenter__(self):
        self._ws = await self._connect()
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
    ):
        if not self._ws:
            raise RuntimeError("WebSocket isn't connected yet")
        payload = {
            "type": "subscribe",
            "channels": list(channels),
            "symbols": list(symbols),
            "exchanges": list(exchanges),
            "depth": depth,
        }
        await self._ws.send(json.dumps(payload))

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
