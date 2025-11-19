class KK0Error(Exception):
    """Base exception for the kk0 SDK."""


class SubscriptionError(KK0Error):
    """Raised when subscription payloads are invalid."""
