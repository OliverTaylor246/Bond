"""
Tests for stream limit enforcement.
"""
import pytest
from apps.api.limits import StreamLimits


def test_check_limit_allows_under_max():
  """Test that streams under the limit are allowed."""
  limits = StreamLimits(max_streams=5)
  
  allowed, message = limits.check_limit(current_count=3)
  
  assert allowed is True
  assert "3/5" in message


def test_check_limit_rejects_at_max():
  """Test that streams at the limit are rejected."""
  limits = StreamLimits(max_streams=5)
  
  allowed, message = limits.check_limit(current_count=5)
  
  assert allowed is False
  assert "limit reached" in message.lower()
  assert "5" in message


def test_check_limit_rejects_over_max():
  """Test that streams over the limit are rejected."""
  limits = StreamLimits(max_streams=5)
  
  allowed, message = limits.check_limit(current_count=6)
  
  assert allowed is False
  assert "limit reached" in message.lower()


def test_get_quota_returns_info():
  """Test that quota information is returned."""
  limits = StreamLimits(max_streams=5)
  
  quota = limits.get_quota()
  
  assert quota["max_streams"] == 5
  assert quota["quota_type"] == "global"
  assert "free tier" in quota["note"].lower()


def test_custom_max_streams():
  """Test that custom max streams can be set."""
  limits = StreamLimits(max_streams=10)
  
  allowed, _ = limits.check_limit(current_count=7)
  assert allowed is True
  
  allowed, _ = limits.check_limit(current_count=10)
  assert allowed is False
