# custom_components/solar_ac_controller/metrics.py
"""Metrics collection for Solar AC Controller."""

import time
from typing import Any, Dict


class MetricsCollector:
    """Collects performance and diagnostic metrics."""

    def __init__(self) -> None:
        """Initialize metrics collector."""
        self.cycle_count = 0
        self.error_count = 0
        self.last_cycle_duration = 0.0
        self.total_cycle_duration = 0.0
        self.start_time = time.time()
        self.last_sensor_values: Dict[str, Any] = {}

    def record_cycle_start(self) -> float:
        """Record start of a cycle."""
        return time.time()

    def record_cycle_end(self, start_time: float, success: bool = True) -> None:
        """Record end of a cycle."""
        duration = time.time() - start_time
        self.cycle_count += 1
        self.last_cycle_duration = duration
        self.total_cycle_duration += duration

        if not success:
            self.error_count += 1

    def record_sensor_values(self, grid: float, solar: float, ac_power: float) -> None:
        """Record sensor values for diagnostics."""
        self.last_sensor_values = {
            "grid": grid,
            "solar": solar,
            "ac_power": ac_power,
            "timestamp": time.time(),
        }

    def get_summary(self) -> Dict[str, Any]:
        """Get metrics summary."""
        uptime = time.time() - self.start_time
        return {
            "cycle_count": self.cycle_count,
            "error_count": self.error_count,
            "error_rate": self.error_count / max(self.cycle_count, 1),
            "avg_cycle_duration": self.total_cycle_duration / max(self.cycle_count, 1),
            "last_cycle_duration": self.last_cycle_duration,
            "uptime_seconds": uptime,
            "cycles_per_second": self.cycle_count / max(uptime, 1),
            "last_sensor_values": self.last_sensor_values,
        }
