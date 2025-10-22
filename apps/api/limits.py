"""
Stream limits enforcement for Bond platform.

MVP: In-memory limit tracking (5 streams max per project/user).
Future: Database-backed limits with per-user quotas, API keys, etc.
"""
import os
from typing import Optional


# Global limit (MVP uses single-tenant model)
MAX_STREAMS = int(os.getenv("BOND_MAX_STREAMS", "5"))


class StreamLimits:
  """
  Enforces stream creation limits.
  
  MVP implementation: Simple in-memory counter for global limit.
  Production: Should use Redis or database for distributed tracking.
  """
  
  def __init__(self, max_streams: int = MAX_STREAMS):
    self.max_streams = max_streams
  
  def check_limit(self, current_count: int, user_id: Optional[str] = None) -> tuple[bool, str]:
    """
    Check if a new stream can be created.
    
    Args:
      current_count: Number of currently active streams
      user_id: Optional user identifier (for future multi-tenant support)
    
    Returns:
      Tuple of (allowed: bool, message: str)
    """
    if current_count >= self.max_streams:
      return False, (
        f"Stream limit reached. Maximum {self.max_streams} concurrent streams allowed. "
        f"Please delete an existing stream before creating a new one."
      )
    
    return True, f"OK: {current_count}/{self.max_streams} streams active"
  
  def get_quota(self, user_id: Optional[str] = None) -> dict:
    """
    Get quota information for a user/project.
    
    Args:
      user_id: Optional user identifier
    
    Returns:
      Dictionary with quota details
    """
    return {
      "max_streams": self.max_streams,
      "quota_type": "global",  # MVP: single global limit
      "note": "Free tier - no payment plans available yet",
    }


# Global instance
limits = StreamLimits()
