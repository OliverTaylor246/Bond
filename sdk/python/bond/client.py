"""
Bond Python SDK - simple client for creating and consuming streams.
"""
import asyncio
import json
from typing import Any, AsyncIterator, Callable
import httpx
import websockets


class BondClient:
  """Client for interacting with bond API and streams."""

  def __init__(
    self,
    api_url: str = "http://localhost:8000",
    ws_url: str = "ws://localhost:8080"
  ):
    self.api_url = api_url.rstrip("/")
    self.ws_url = ws_url.rstrip("/")

  async def create_stream(
    self,
    spec: str | dict[str, Any]
  ) -> dict[str, Any]:
    """
    Create a new stream.

    Args:
      spec: Natural language string or dict specification

    Returns:
      Dict with stream_id, ws_url, and spec

    Example:
      >>> client = BondClient()
      >>> result = await client.create_stream("BTC price + tweets every 5s")
      >>> print(result["stream_id"])
    """
    async with httpx.AsyncClient() as http:
      if isinstance(spec, str):
        payload = {"natural_language": spec}
      else:
        payload = {"spec": spec}

      response = await http.post(
        f"{self.api_url}/v1/streams",
        json=payload,
      )
      response.raise_for_status()
      return response.json()

  async def list_streams(self) -> list[dict[str, Any]]:
    """
    List all active streams.

    Returns:
      List of stream info dicts
    """
    async with httpx.AsyncClient() as http:
      response = await http.get(f"{self.api_url}/v1/streams")
      response.raise_for_status()
      return response.json()

  async def delete_stream(self, stream_id: str) -> dict[str, Any]:
    """
    Delete a stream.

    Args:
      stream_id: Stream identifier

    Returns:
      Deletion confirmation dict
    """
    async with httpx.AsyncClient() as http:
      response = await http.delete(f"{self.api_url}/v1/streams/{stream_id}")
      response.raise_for_status()
      return response.json()

  async def listen(
    self,
    spec: str | dict[str, Any],
    callback: Callable[[dict], None] | None = None
  ) -> AsyncIterator[dict[str, Any]]:
    """
    Create a stream and listen for events.

    Args:
      spec: Natural language or dict specification
      callback: Optional callback function for each event

    Yields:
      Event dictionaries

    Example:
      >>> async for event in client.listen("BTC price every 5s"):
      ...   print(event["price_avg"])
    """
    # Create stream
    result = await self.create_stream(spec)
    ws_url = result["ws_url"]

    # Connect to WebSocket
    async with websockets.connect(ws_url) as ws:
      async for message in ws:
        event = json.loads(message)

        if callback:
          callback(event)

        yield event


async def listen(
  spec: str | dict[str, Any],
  api_url: str = "http://localhost:8000",
  callback: Callable[[dict], None] | None = None
) -> AsyncIterator[dict[str, Any]]:
  """
  Convenience function to create and listen to a stream.

  Args:
    spec: Natural language or dict specification
    api_url: API endpoint URL
    callback: Optional callback for each event

  Yields:
    Event dictionaries

  Example:
    >>> from bond import listen
    >>> async for event in listen("BTC + ETH prices every 3s"):
    ...   print(event)
  """
  client = BondClient(api_url)
  async for event in client.listen(spec, callback):
    yield event
