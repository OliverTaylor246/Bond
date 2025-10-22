"""
Metrics tracking for Bond streams.

Tracks per-stream metrics including:
- Message counts (in/out)
- Latency (p50, p95)
- Dropped events
- Uptime
"""
import time
from datetime import datetime, timezone
from typing import Optional
from collections import defaultdict


class StreamMetrics:
  """
  Tracks metrics for a single stream.
  
  MVP: In-memory metrics storage.
  Production: Should use Redis or time-series database.
  """
  
  def __init__(self, stream_id: str):
    self.stream_id = stream_id
    self.start_time = time.monotonic()
    self.created_at = datetime.now(tz=timezone.utc)
    
    # Counters
    self.msgs_in = 0
    self.msgs_out = 0
    self.dropped = 0
    
    # Latency tracking (simple list for percentiles)
    self.latencies: list[float] = []
    self.max_latency_samples = 1000  # Keep last 1000 for percentile calc
  
  def record_ingest(self, count: int = 1) -> None:
    """Record messages ingested from sources."""
    self.msgs_in += count
  
  def record_publish(self, count: int = 1) -> None:
    """Record messages published to Redis."""
    self.msgs_out += count
  
  def record_latency(self, latency_ms: float) -> None:
    """Record latency sample (in milliseconds)."""
    self.latencies.append(latency_ms)
    
    # Keep only recent samples
    if len(self.latencies) > self.max_latency_samples:
      self.latencies = self.latencies[-self.max_latency_samples:]
  
  def record_dropped(self, count: int = 1) -> None:
    """Record dropped events (backpressure)."""
    self.dropped += count
  
  def get_uptime_sec(self) -> float:
    """Get stream uptime in seconds."""
    return time.monotonic() - self.start_time
  
  def get_percentile(self, p: float) -> Optional[float]:
    """
    Calculate latency percentile.
    
    Args:
      p: Percentile (0-100)
    
    Returns:
      Latency in milliseconds, or None if no samples
    """
    if not self.latencies:
      return None
    
    sorted_latencies = sorted(self.latencies)
    index = int((p / 100.0) * len(sorted_latencies))
    index = min(index, len(sorted_latencies) - 1)
    return sorted_latencies[index]
  
  def to_dict(self) -> dict:
    """Export metrics as dictionary."""
    return {
      "stream_id": self.stream_id,
      "uptime_sec": round(self.get_uptime_sec(), 2),
      "created_at": self.created_at.isoformat(),
      "msgs_in": self.msgs_in,
      "msgs_out": self.msgs_out,
      "dropped": self.dropped,
      "latency_p50_ms": round(self.get_percentile(50) or 0.0, 2),
      "latency_p95_ms": round(self.get_percentile(95) or 0.0, 2),
      "latency_samples": len(self.latencies),
    }


class MetricsRegistry:
  """
  Global registry for all stream metrics.
  
  Provides centralized access to metrics for all active streams.
  """
  
  def __init__(self):
    self.metrics: dict[str, StreamMetrics] = {}
  
  def create(self, stream_id: str) -> StreamMetrics:
    """Create metrics tracker for a new stream."""
    if stream_id in self.metrics:
      return self.metrics[stream_id]
    
    m = StreamMetrics(stream_id)
    self.metrics[stream_id] = m
    return m
  
  def get(self, stream_id: str) -> Optional[StreamMetrics]:
    """Get metrics for a stream."""
    return self.metrics.get(stream_id)
  
  def delete(self, stream_id: str) -> None:
    """Remove metrics for a stopped stream."""
    if stream_id in self.metrics:
      del self.metrics[stream_id]
  
  def list_all(self) -> dict[str, dict]:
    """Get all metrics as dictionary."""
    return {
      sid: m.to_dict()
      for sid, m in self.metrics.items()
    }


# Global registry instance
metrics_registry = MetricsRegistry()
