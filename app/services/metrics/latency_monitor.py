"""
Latency Monitoring and Measurement.

Tracks end-to-end latency metrics for WebRTC streaming and HID input,
providing percentile statistics and real-time monitoring.
"""

import time
import logging
from collections import deque
from typing import List, Dict, Optional
from dataclasses import dataclass, asdict
from datetime import datetime
import statistics

logger = logging.getLogger(__name__)


@dataclass
class LatencyMeasurement:
    """Single latency measurement."""
    timestamp: float
    latency_ms: float
    measurement_type: str  # 'video', 'input', 'round_trip'
    metadata: Optional[dict] = None


class LatencyMonitor:
    """
    Monitor and track latency metrics.
    
    Provides rolling window statistics with percentile calculations
    for video frame latency and input round-trip time.
    """
    
    def __init__(self, window_size: int = 1000, history_seconds: int = 300):
        """
        Initialize latency monitor.
        
        Args:
            window_size: Number of measurements to keep for statistics
            history_seconds: How long to keep measurement history
        """
        self.window_size = window_size
        self.history_seconds = history_seconds
        
        # Measurement queues (ring buffers)
        self.video_latencies = deque(maxlen=window_size)
        self.input_latencies = deque(maxlen=window_size)
        self.round_trip_latencies = deque(maxlen=window_size)
        
        # Full history (for graphing)
        self.history = deque()
        
        # Current statistics
        self.video_stats = {}
        self.input_stats = {}
        self.round_trip_stats = {}
        
        # Measurement counts
        self.total_measurements = 0
        self.video_measurements = 0
        self.input_measurements = 0
        
        # Start time
        self.start_time = time.time()
    
    def record_video_latency(
        self,
        capture_timestamp: float,
        send_timestamp: Optional[float] = None,
        metadata: Optional[dict] = None
    ):
        """
        Record video frame latency.
        
        Args:
            capture_timestamp: When frame was captured (monotonic time)
            send_timestamp: When frame was sent (monotonic time, defaults to now)
            metadata: Optional metadata (frame number, resolution, etc.)
        """
        if send_timestamp is None:
            send_timestamp = time.monotonic()
        
        latency_ms = (send_timestamp - capture_timestamp) * 1000
        
        measurement = LatencyMeasurement(
            timestamp=time.time(),
            latency_ms=latency_ms,
            measurement_type='video',
            metadata=metadata
        )
        
        self.video_latencies.append(latency_ms)
        self.history.append(measurement)
        self.video_measurements += 1
        self.total_measurements += 1
        
        self._update_video_stats()
        self._cleanup_history()
    
    def record_input_latency(
        self,
        client_timestamp: float,
        server_timestamp: Optional[float] = None,
        metadata: Optional[dict] = None
    ):
        """
        Record input event latency.
        
        Args:
            client_timestamp: When input was sent from client (epoch time)
            server_timestamp: When input was processed (epoch time, defaults to now)
            metadata: Optional metadata (event type, etc.)
        """
        if server_timestamp is None:
            server_timestamp = time.time()
        
        latency_ms = (server_timestamp - client_timestamp) * 1000
        
        # Sanity check (reject obviously wrong timestamps)
        if latency_ms < 0 or latency_ms > 10000:
            logger.warning(f"Rejecting invalid input latency: {latency_ms}ms")
            return
        
        measurement = LatencyMeasurement(
            timestamp=time.time(),
            latency_ms=latency_ms,
            measurement_type='input',
            metadata=metadata
        )
        
        self.input_latencies.append(latency_ms)
        self.history.append(measurement)
        self.input_measurements += 1
        self.total_measurements += 1
        
        self._update_input_stats()
        self._cleanup_history()
    
    def record_round_trip_latency(
        self,
        latency_ms: float,
        metadata: Optional[dict] = None
    ):
        """
        Record round-trip latency (client request → server → client response).
        
        Args:
            latency_ms: Round-trip time in milliseconds
            metadata: Optional metadata
        """
        measurement = LatencyMeasurement(
            timestamp=time.time(),
            latency_ms=latency_ms,
            measurement_type='round_trip',
            metadata=metadata
        )
        
        self.round_trip_latencies.append(latency_ms)
        self.history.append(measurement)
        self.total_measurements += 1
        
        self._update_round_trip_stats()
        self._cleanup_history()
    
    def _update_video_stats(self):
        """Update video latency statistics."""
        if not self.video_latencies:
            return
        
        latencies = list(self.video_latencies)
        self.video_stats = self._calculate_stats(latencies)
    
    def _update_input_stats(self):
        """Update input latency statistics."""
        if not self.input_latencies:
            return
        
        latencies = list(self.input_latencies)
        self.input_stats = self._calculate_stats(latencies)
    
    def _update_round_trip_stats(self):
        """Update round-trip latency statistics."""
        if not self.round_trip_latencies:
            return
        
        latencies = list(self.round_trip_latencies)
        self.round_trip_stats = self._calculate_stats(latencies)
    
    def _calculate_stats(self, latencies: List[float]) -> dict:
        """
        Calculate statistics for a list of latencies.
        
        Args:
            latencies: List of latency values in milliseconds
        
        Returns:
            Statistics dictionary
        """
        if not latencies:
            return {}
        
        sorted_latencies = sorted(latencies)
        
        return {
            'count': len(latencies),
            'min': round(min(latencies), 2),
            'max': round(max(latencies), 2),
            'mean': round(statistics.mean(latencies), 2),
            'median': round(statistics.median(latencies), 2),
            'p50': round(statistics.median(latencies), 2),
            'p90': round(self._percentile(sorted_latencies, 90), 2),
            'p95': round(self._percentile(sorted_latencies, 95), 2),
            'p99': round(self._percentile(sorted_latencies, 99), 2),
            'stdev': round(statistics.stdev(latencies), 2) if len(latencies) > 1 else 0,
        }
    
    def _percentile(self, sorted_data: List[float], percentile: float) -> float:
        """
        Calculate percentile from sorted data.
        
        Args:
            sorted_data: Sorted list of values
            percentile: Percentile to calculate (0-100)
        
        Returns:
            Percentile value
        """
        if not sorted_data:
            return 0.0
        
        k = (len(sorted_data) - 1) * percentile / 100
        f = int(k)
        c = f + 1
        
        if c >= len(sorted_data):
            return sorted_data[-1]
        
        d0 = sorted_data[f] * (c - k)
        d1 = sorted_data[c] * (k - f)
        
        return d0 + d1
    
    def _cleanup_history(self):
        """Remove old measurements from history."""
        cutoff_time = time.time() - self.history_seconds
        
        while self.history and self.history[0].timestamp < cutoff_time:
            self.history.popleft()
    
    def get_stats(self) -> dict:
        """
        Get all current statistics.
        
        Returns:
            Dictionary with video, input, and round-trip stats
        """
        uptime_seconds = time.time() - self.start_time
        
        return {
            'uptime_seconds': round(uptime_seconds, 1),
            'total_measurements': self.total_measurements,
            'video': {
                'measurements': self.video_measurements,
                'stats': self.video_stats
            },
            'input': {
                'measurements': self.input_measurements,
                'stats': self.input_stats
            },
            'round_trip': {
                'stats': self.round_trip_stats
            },
            'window_size': self.window_size,
            'history_seconds': self.history_seconds,
        }
    
    def get_history(
        self,
        measurement_type: Optional[str] = None,
        last_n_seconds: Optional[int] = None
    ) -> List[dict]:
        """
        Get measurement history.
        
        Args:
            measurement_type: Filter by type ('video', 'input', 'round_trip')
            last_n_seconds: Only return measurements from last N seconds
        
        Returns:
            List of measurement dictionaries
        """
        measurements = list(self.history)
        
        # Filter by type
        if measurement_type:
            measurements = [m for m in measurements if m.measurement_type == measurement_type]
        
        # Filter by time
        if last_n_seconds:
            cutoff_time = time.time() - last_n_seconds
            measurements = [m for m in measurements if m.timestamp >= cutoff_time]
        
        # Convert to dictionaries
        result = []
        for m in measurements:
            d = asdict(m)
            d['timestamp'] = m.timestamp  # Keep as float for client-side processing
            result.append(d)
        
        return result
    
    def get_summary(self) -> str:
        """
        Get human-readable summary of current latencies.
        
        Returns:
            Summary string
        """
        lines = ["Latency Statistics:"]
        
        if self.video_stats:
            lines.append(
                f"  Video: {self.video_stats.get('p50', 0)}ms (p50), "
                f"{self.video_stats.get('p95', 0)}ms (p95), "
                f"{self.video_stats.get('p99', 0)}ms (p99)"
            )
        
        if self.input_stats:
            lines.append(
                f"  Input: {self.input_stats.get('p50', 0)}ms (p50), "
                f"{self.input_stats.get('p95', 0)}ms (p95), "
                f"{self.input_stats.get('p99', 0)}ms (p99)"
            )
        
        if self.round_trip_stats:
            lines.append(
                f"  Round-trip: {self.round_trip_stats.get('p50', 0)}ms (p50), "
                f"{self.round_trip_stats.get('p95', 0)}ms (p95), "
                f"{self.round_trip_stats.get('p99', 0)}ms (p99)"
            )
        
        return "\n".join(lines)
    
    def reset(self):
        """Reset all measurements and statistics."""
        self.video_latencies.clear()
        self.input_latencies.clear()
        self.round_trip_latencies.clear()
        self.history.clear()
        
        self.video_stats = {}
        self.input_stats = {}
        self.round_trip_stats = {}
        
        self.total_measurements = 0
        self.video_measurements = 0
        self.input_measurements = 0
        
        self.start_time = time.time()
        
        logger.info("Latency monitor reset")
