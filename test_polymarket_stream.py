import asyncio

import httpx
import pytest

from connectors.polymarket_stream import polymarket_events_stream


@pytest.mark.asyncio
async def test_polymarket_stream_normalizes_and_filters():
  sample_events = [
    {
      "id": "evt-1",
      "slug": "test-event",
      "title": "Test Event",
      "category": "Sports",
      "active": True,
      "closed": False,
      "openInterest": "1200.50",
      "liquidity": "340.1",
      "volume": "999.9",
      "volume24hr": "123.45",
      "updatedAt": "2024-01-01T00:00:00Z",
      "markets": [
        {
          "id": "mkt-1",
          "question": "Will it rain?",
          "slug": "rain",
          "category": "Weather",
          "liquidity": "50.0",
          "volume": "75.5",
          "bestBid": "0.45",
          "bestAsk": "0.55",
          "lastTradePrice": "0.5",
          "outcomes": "[\"Yes\",\"No\"]",
          "outcomePrices": "[\"0.45\",\"0.55\"]",
          "endDate": "2024-01-02T00:00:00Z",
          "startDate": "2024-01-01T00:00:00Z",
        }
      ],
    },
    {
      "id": "evt-2",
      "slug": "skip-me",
      "title": "Skip Me",
      "category": "Politics",
      "active": True,
      "closed": False,
      "openInterest": "10",
      "liquidity": "5",
      "volume": "15",
      "volume24hr": "0",
      "updatedAt": "2024-01-01T00:00:00Z",
    },
  ]

  async def handler(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json=sample_events)

  transport = httpx.MockTransport(handler)

  async with httpx.AsyncClient(transport=transport) as client:
    stream = polymarket_events_stream(
      event_ids=["evt-1"],
      categories=["sports"],
      include_closed=False,
      interval=15,
      http_client=client,
    )

    event = await asyncio.wait_for(stream.__anext__(), timeout=1)
    await stream.aclose()

  assert event["source"] == "polymarket"
  assert event["event_id"] == "evt-1"
  assert event["category"] == "Sports"
  assert event["open_interest"] == pytest.approx(1200.5)
  assert event["liquidity"] == pytest.approx(340.1)
  assert event["volume_total"] == pytest.approx(999.9)
  assert event["volume_24h"] == pytest.approx(123.45)
  assert event["market_count"] == 1
  market = event["markets"][0]
  assert market["question"] == "Will it rain?"
  assert market["outcomes"] == ["Yes", "No"]
  assert market["outcome_prices"] == [pytest.approx(0.45), pytest.approx(0.55)]
