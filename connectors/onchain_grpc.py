"""
On-chain gRPC connector for Solana Yellowstone Geyser.
Subscribes to on-chain events and emits normalized OnchainEvent objects.

NOTE: Currently uses mock mode by default. To enable real gRPC:
1. Copy proto files from reference implementation
2. Generate Python stubs with grpcio-tools
3. Set ONCHAIN_MODE=grpc in environment
"""
import asyncio
import os
import random
from datetime import datetime, timezone
from typing import AsyncIterator, Literal
from engine.schemas import OnchainEvent


MOCK_MODE = os.getenv("ONCHAIN_MODE", "mock") == "mock"
GRPC_ENDPOINT = os.getenv("GRPC_ENDPOINT", "chi1.cracknode.com:10000")


async def onchain_stream(
  chain: str = "sol",
  event_types: list[Literal["tx", "transfer", "metric", "block", "account"]] | None = None,
  interval: int = 5
) -> AsyncIterator[dict]:
  """
  Stream on-chain events from gRPC endpoint.

  Args:
    chain: Blockchain identifier (default: "sol" for Solana)
    event_types: Types of events to emit (default: ["tx", "transfer"])
    interval: Polling interval in seconds (for mock mode)

  Yields:
    OnchainEvent dictionaries
  """
  if event_types is None:
    event_types = ["tx", "transfer"]

  if MOCK_MODE:
    async for evt in _mock_onchain_stream(chain, event_types, interval):
      yield evt
  else:
    async for evt in _grpc_onchain_stream(chain, event_types):
      yield evt


async def _mock_onchain_stream(
  chain: str,
  event_types: list[str],
  interval: int
) -> AsyncIterator[dict]:
  """
  Mock on-chain event generator for testing and development.

  Generates realistic-looking events including:
  - Transaction counts
  - Transfer events with amounts
  - Block metrics
  - Account updates
  """
  counter = 0

  while True:
    await asyncio.sleep(interval)

    # Rotate through event types
    for event_type in event_types:
      counter += 1

      if event_type == "tx":
        # Mock transaction count
        evt = OnchainEvent(
          ts=datetime.now(tz=timezone.utc),
          source="onchain",
          chain=chain,
          kind="tx",
          value=float(random.randint(50, 200)),  # TPS
          meta={
            "block": 250_000_000 + counter,
            "program_id": "11111111111111111111111111111111",
            "mock": True,
          },
        )
        yield evt.model_dump()

      elif event_type == "transfer":
        # Mock token transfer
        evt = OnchainEvent(
          ts=datetime.now(tz=timezone.utc),
          source="onchain",
          chain=chain,
          kind="transfer",
          value=float(random.uniform(100, 10000)),  # Amount in USD equivalent
          meta={
            "from": "mock_sender_" + str(counter % 10),
            "to": "mock_receiver_" + str((counter + 1) % 10),
            "token": "USDC" if counter % 2 == 0 else "SOL",
            "mock": True,
          },
        )
        yield evt.model_dump()

      elif event_type == "metric":
        # Mock network metric
        evt = OnchainEvent(
          ts=datetime.now(tz=timezone.utc),
          source="onchain",
          chain=chain,
          kind="metric",
          value=float(random.uniform(0.5, 2.0)),  # Network congestion factor
          meta={
            "metric_name": "congestion",
            "slot": 250_000_000 + counter,
            "mock": True,
          },
        )
        yield evt.model_dump()

      elif event_type == "block":
        # Mock block production
        evt = OnchainEvent(
          ts=datetime.now(tz=timezone.utc),
          source="onchain",
          chain=chain,
          kind="block",
          value=float(counter),
          meta={
            "slot": 250_000_000 + counter,
            "leader": f"validator_{counter % 5}",
            "tx_count": random.randint(100, 500),
            "mock": True,
          },
        )
        yield evt.model_dump()

      elif event_type == "account":
        # Mock account update
        evt = OnchainEvent(
          ts=datetime.now(tz=timezone.utc),
          source="onchain",
          chain=chain,
          kind="account",
          value=float(random.uniform(1000, 100000)),  # Account balance
          meta={
            "account": f"mock_account_{counter % 20}",
            "program": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
            "mock": True,
          },
        )
        yield evt.model_dump()


async def _grpc_onchain_stream(
  chain: str,
  event_types: list[str]
) -> AsyncIterator[dict]:
  """
  Real gRPC implementation for Yellowstone Geyser.

  TODO: Implement when proto files are available:
  1. Import generated geyser_pb2 and geyser_pb2_grpc
  2. Create gRPC channel to GRPC_ENDPOINT
  3. Subscribe to relevant streams (transactions, accounts, blocks)
  4. Parse and normalize events into OnchainEvent objects
  
  Reference: /Users/olivertaylor/Quant/Skylar_Final_Manual/grpc/grpc_index/main.py
  """
  raise NotImplementedError(
    "Real gRPC mode not yet implemented. "
    "Set ONCHAIN_MODE=mock or implement gRPC client. "
    f"Target endpoint: {GRPC_ENDPOINT}"
  )


# Convenience functions for specific event types

async def onchain_tx_stream(chain: str = "sol", interval: int = 5) -> AsyncIterator[dict]:
  """Stream transaction events only."""
  async for evt in onchain_stream(chain, ["tx"], interval):
    yield evt


async def onchain_transfer_stream(chain: str = "sol", interval: int = 5) -> AsyncIterator[dict]:
  """Stream transfer events only."""
  async for evt in onchain_stream(chain, ["transfer"], interval):
    yield evt


async def onchain_block_stream(chain: str = "sol", interval: int = 5) -> AsyncIterator[dict]:
  """Stream block events only."""
  async for evt in onchain_stream(chain, ["block"], interval):
    yield evt
