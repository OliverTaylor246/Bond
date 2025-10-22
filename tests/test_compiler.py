"""Tests for stream compiler."""
import pytest
from apps.compiler import compile_spec, generate_stream_id
from engine.schemas import StreamSpec


def test_compile_natural_language_btc():
  """Test NL parsing for BTC price."""
  spec = compile_spec("BTC price + tweets every 5s")

  assert isinstance(spec, StreamSpec)
  assert spec.interval_sec == 5
  assert "BTC/USDT" in spec.symbols or "BTC" in spec.symbols
  assert len(spec.sources) >= 2


def test_compile_natural_language_interval():
  """Test interval extraction."""
  spec = compile_spec("ETH trades every 10 seconds")

  assert spec.interval_sec == 10


def test_compile_structured_spec():
  """Test structured spec compilation."""
  input_spec = {
    "sources": [{"type": "ccxt", "symbols": ["BTC/USDT"]}],
    "interval_sec": 3,
    "symbols": ["BTC/USDT"],
  }

  spec = compile_spec(input_spec)

  assert spec.interval_sec == 3
  assert spec.symbols == ["BTC/USDT"]


def test_generate_stream_id_deterministic():
  """Test that stream ID is deterministic."""
  spec1 = compile_spec("BTC price every 5s")
  spec2 = compile_spec("BTC price every 5s")

  id1 = generate_stream_id(spec1)
  id2 = generate_stream_id(spec2)

  assert id1 == id2
  assert len(id1) == 16  # 8 bytes = 16 hex chars


def test_generate_stream_id_unique():
  """Test that different specs produce different IDs."""
  spec1 = compile_spec("BTC price every 5s")
  spec2 = compile_spec("ETH price every 10s")

  id1 = generate_stream_id(spec1)
  id2 = generate_stream_id(spec2)

  assert id1 != id2
