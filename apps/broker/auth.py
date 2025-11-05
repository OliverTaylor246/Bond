"""
Token utilities for the broker.

The broker validates client tokens before allowing websocket access.
These helpers mirror the API's HMAC token format so existing clients
can reuse their credentials.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import time

# Default secret - override in production with BOND_SECRET
DEFAULT_SECRET = "bond-dev-secret-change-in-production"


def generate_token(stream_id: str, secret: str = DEFAULT_SECRET, ttl_sec: int = 3600) -> str:
  """
  Generate a signed, time-limited access token for a stream.

  Args:
    stream_id: Stream identifier
    secret: HMAC secret key
    ttl_sec: Time-to-live in seconds

  Returns:
    Token string (format: timestamp.signature)
  """
  expiry = int(time.time()) + ttl_sec
  message = f"{stream_id}.{expiry}"

  signature = hmac.new(
    secret.encode(),
    message.encode(),
    hashlib.sha256,
  ).hexdigest()[:16]

  return f"{expiry}.{signature}"


def verify_token(stream_id: str, token: str, secret: str = DEFAULT_SECRET) -> bool:
  """Validate a stream access token."""
  try:
    expiry_str, signature = token.split(".", 1)
    expiry = int(expiry_str)

    if time.time() > expiry:
      return False

    expected_message = f"{stream_id}.{expiry}"
    expected_sig = hmac.new(
      secret.encode(),
      expected_message.encode(),
      hashlib.sha256,
    ).hexdigest()[:16]

    return hmac.compare_digest(signature, expected_sig)
  except (ValueError, AttributeError):
    return False


def get_secret() -> str:
  """Read BOND_SECRET from the environment or fall back to the dev default."""
  return os.getenv("BOND_SECRET", DEFAULT_SECRET)
