"""
Tests for StreamSpec validation and compilation.
"""
import pytest
from engine.schemas import StreamSpec
from apps.compiler import compile_spec, generate_stream_id


def test_valid_spec_ccxt_only():
  """Test that a valid CCXT-only spec is accepted."""
  spec_dict = {
    "sources": [{"type": "ccxt", "symbols": ["BTC/USDT"]}],
    "interval_sec": 5,
    "symbols": ["BTC/USDT"]
  }
  
  spec = StreamSpec(**spec_dict)
  
  assert spec.version == "v1"
  assert len(spec.sources) == 1
  assert spec.interval_sec == 5


def test_valid_spec_with_onchain():
  """Test that a spec with on-chain source is accepted."""
  spec_dict = {
    "sources": [
      {"type": "ccxt", "symbols": ["BTC/USDT"]},
      {"type": "onchain", "chain": "sol", "event_types": ["tx", "transfer"]}
    ],
    "interval_sec": 5,
    "symbols": ["BTC/USDT"]
  }
  
  spec = StreamSpec(**spec_dict)
  
  assert len(spec.sources) == 2
  assert spec.sources[1]["type"] == "onchain"


def test_compile_natural_language():
  """Test natural language compilation."""
  spec = compile_spec("BTC price + tweets every 5s")
  
  assert isinstance(spec, StreamSpec)
  assert spec.interval_sec == 5
  assert "BTC/USDT" in spec.symbols or "BTC" in str(spec.symbols)


def test_compile_dict_spec():
  """Test dictionary spec compilation."""
  spec_dict = {
    "sources": [{"type": "ccxt"}],
    "interval_sec": 10
  }
  
  spec = compile_spec(spec_dict)
  
  assert isinstance(spec, StreamSpec)
  assert spec.interval_sec == 10


def test_generate_stream_id_deterministic():
  """Test that stream ID generation is deterministic."""
  spec = compile_spec("BTC price every 5s")
  
  id1 = generate_stream_id(spec)
  id2 = generate_stream_id(spec)
  
  assert id1 == id2
  assert len(id1) == 16  # Blake2b with digest_size=8 produces 16 hex chars


def test_generate_stream_id_different_specs():
  """Test that different specs produce different IDs."""
  spec1 = compile_spec("BTC price every 5s")
  spec2 = compile_spec("ETH price every 10s")
  
  id1 = generate_stream_id(spec1)
  id2 = generate_stream_id(spec2)
  
  assert id1 != id2


def test_spec_with_transforms():
  """Test that specs with transforms are accepted."""
  spec_dict = {
    "sources": [{"type": "ccxt"}],
    "interval_sec": 5,
    "transforms": [
      {"op": "resample", "window": "1m", "agg": ["mean"]},
      {"op": "project", "fields": ["price", "volume"]}
    ]
  }
  
  spec = StreamSpec(**spec_dict)
  
  assert spec.transforms is not None
  assert len(spec.transforms) == 2
