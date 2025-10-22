"""
Authentication and token generation for stream access.

SECURITY-CRITICAL: Do not alter without review.
Uses HMAC-based tokens with expiration for WebSocket authentication.
"""
import hashlib
import hmac
import time
from typing import Optional


# Default secret - MUST be overridden in production via environment variable
DEFAULT_SECRET = "bond-dev-secret-change-in-production"


def generate_token(
  stream_id: str,
  secret: str = DEFAULT_SECRET,
  ttl_sec: int = 3600
) -> str:
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
    hashlib.sha256
  ).hexdigest()[:16]  # Truncate to 16 chars

  return f"{expiry}.{signature}"


def verify_token(
  stream_id: str,
  token: str,
  secret: str = DEFAULT_SECRET
) -> bool:
  """
  Verify a stream access token.

  Args:
    stream_id: Stream identifier
    token: Token to verify (format: timestamp.signature)
    secret: HMAC secret key

  Returns:
    True if token is valid and not expired
  """
  try:
    expiry_str, signature = token.split(".", 1)
    expiry = int(expiry_str)

    # Check expiration
    if time.time() > expiry:
      return False

    # Verify signature
    expected_message = f"{stream_id}.{expiry}"
    expected_sig = hmac.new(
      secret.encode(),
      expected_message.encode(),
      hashlib.sha256
    ).hexdigest()[:16]

    return hmac.compare_digest(signature, expected_sig)

  except (ValueError, AttributeError):
    return False


def get_secret() -> str:
  """
  Get the HMAC secret from environment or use default.

  Returns:
    Secret key string
  """
  import os
  return os.getenv("BOND_SECRET", DEFAULT_SECRET)
