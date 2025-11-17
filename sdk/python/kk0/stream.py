from __future__ import annotations

import asyncio
import json
import queue
import threading
import time
from typing import Any, AsyncIterator, Callable, Iterable, Iterator, Optional

import websockets

SubscribePayload = dict[str, Any]


class Stream:
    """
    Simple 3kk0 client.

    Supports both async iteration (`async for event in stream`) and
    synchronous iteration (`for event in stream`).
    """

    def __init__(
        self,
        *,
        symbols: Iterable[str] = ("BTC/USDT",),
        exchanges: Iterable[str] = ("binance",),
        channels: Iterable[str] = ("trades",),
        depth: int = 20,
        raw: bool = False,
        ws_url: str = "wss://api.3kk0.com/stream",
        on_error: Optional[Callable[[Exception], None]] = None,
        on_heartbeat: Optional[Callable[[dict[str, Any]], None]] = None,
    ) -> None:
        self.symbols = list(symbols)
        self.exchanges = list(exchanges)
        self.channels = list(channels)
        self.depth = depth
        self.raw = raw
        self.ws_url = ws_url
        self._on_error = on_error
        self._on_heartbeat = on_heartbeat
        self._stop = False
        self._queue: "queue.Queue[Optional[dict[str, Any]]]" = queue.Queue()
        self._thread: Optional[threading.Thread] = None
        self._loop = asyncio.new_event_loop()
        self._async_generator: Optional[AsyncIterator[dict[str, Any]]] = None

    def _build_payload(self) -> SubscribePayload:
        return {
            "type": "subscribe",
            "symbols": self.symbols,
            "exchanges": self.exchanges,
            "channels": self.channels,
            "depth": self.depth,
            "raw": self.raw,
        }

    def __aiter__(self) -> "Stream":
        return self

    async def __anext__(self) -> dict[str, Any]:
        if self._async_generator is None:
            self._async_generator = self._message_stream()
        assert self._async_generator is not None
        return await self._async_generator.__anext__()

    async def _message_stream(self) -> AsyncIterator[dict[str, Any]]:
        backoff = 1.0
        while not self._stop:
            try:
                async with websockets.connect(self.ws_url) as ws:
                    await ws.send(json.dumps(self._build_payload()))
                    while not self._stop:
                        raw = await ws.recv()
                        data = json.loads(raw)
                        self._handle_event(data)
                        yield data
            except Exception as exc:
                self._handle_error(exc)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 10.0)

    def __iter__(self) -> Iterator[dict[str, Any]]:
        if self._thread is None:
            self._thread = threading.Thread(
                target=self._run_sync_loop, daemon=True
            )
            self._thread.start()
        while True:
            item = self._queue.get()
            if item is None:
                break
            yield item

    def stop(self) -> None:
        self._stop = True

    def _run_sync_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._fill_sync_queue())
        finally:
            self._queue.put(None)

    async def _fill_sync_queue(self) -> None:
        async for event in self._message_stream():
            self._queue.put(event)

    def _handle_event(self, event: dict[str, Any]) -> None:
        if event.get("type") == "heartbeat" and self._on_heartbeat:
            self._on_heartbeat(event)

    def _handle_error(self, exc: Exception) -> None:
        if self._on_error:
            self._on_error(exc)
