# custom_components/solar_ac_controller/storage_circuit_breaker.py
"""Circuit breaker pattern for storage operations."""

import threading
import time
from typing import Optional


class StorageCircuitBreaker:
    """Circuit breaker for storage operations to prevent cascading failures."""

    def __init__(self, max_failures: int = 3, reset_timeout: int = 300) -> None:
        """Initialize circuit breaker."""
        self.max_failures = max_failures
        self.reset_timeout = reset_timeout
        self.failure_count = 0
        self.last_failure_time: Optional[float] = None
        self._lock = threading.Lock()

    def should_attempt_operation(self) -> bool:
        """Check if operation should be attempted."""
        with self._lock:
            if self.failure_count < self.max_failures:
                return True

            if self.last_failure_time is None:
                return True

            # Check if reset timeout has passed
            if time.time() - self.last_failure_time > self.reset_timeout:
                self.failure_count = 0
                self.last_failure_time = None
                return True

            return False

    def record_success(self) -> None:
        """Record successful operation."""
        with self._lock:
            self.failure_count = 0
            self.last_failure_time = None

    def record_failure(self) -> None:
        """Record failed operation."""
        with self._lock:
            self.failure_count += 1
            self.last_failure_time = time.time()

    @property
    def is_open(self) -> bool:
        """Check if circuit breaker is open."""
        return not self.should_attempt_operation()
