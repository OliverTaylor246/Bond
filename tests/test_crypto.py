"""Tests for authentication and token generation."""
import time
import pytest
from apps.api.crypto import generate_token, verify_token


def test_generate_token():
  """Test token generation."""
  stream_id = "test_stream_123"
  token = generate_token(stream_id, secret="test-secret", ttl_sec=3600)

  assert isinstance(token, str)
  assert "." in token  # Format: expiry.signature


def test_verify_valid_token():
  """Test verification of valid token."""
  stream_id = "test_stream_123"
  secret = "test-secret"

  token = generate_token(stream_id, secret=secret, ttl_sec=3600)
  is_valid = verify_token(stream_id, token, secret=secret)

  assert is_valid is True


def test_verify_expired_token():
  """Test verification of expired token."""
  stream_id = "test_stream_123"
  secret = "test-secret"

  # Generate token that expires immediately
  token = generate_token(stream_id, secret=secret, ttl_sec=0)

  # Wait for expiration
  time.sleep(1)

  is_valid = verify_token(stream_id, token, secret=secret)

  assert is_valid is False


def test_verify_wrong_secret():
  """Test verification with wrong secret."""
  stream_id = "test_stream_123"

  token = generate_token(stream_id, secret="secret1", ttl_sec=3600)
  is_valid = verify_token(stream_id, token, secret="secret2")

  assert is_valid is False


def test_verify_wrong_stream_id():
  """Test verification with wrong stream ID."""
  secret = "test-secret"

  token = generate_token("stream1", secret=secret, ttl_sec=3600)
  is_valid = verify_token("stream2", token, secret=secret)

  assert is_valid is False


def test_verify_malformed_token():
  """Test verification of malformed token."""
  is_valid = verify_token("stream_id", "invalid_token", secret="secret")

  assert is_valid is False
