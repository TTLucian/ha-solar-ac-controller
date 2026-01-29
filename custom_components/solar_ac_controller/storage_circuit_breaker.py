# custom_components/solar_ac_controller/storage_circuit_breaker.py
"""Circuit breaker pattern for storage operations."""

import asyncio
from typing import Optional

from homeassistant.util import dt as dt_util


class StorageCircuitBreaker:
    """Circuit breaker for storage operations to prevent cascading failures."""

    def __init__(self, max_failures: int = 3, reset_timeout: int = 300) -> None:
        """Initialize circuit breaker."""
        self.max_failures = max_failures
        self.reset_timeout = reset_timeout
        self.failure_count = 0
        self.last_failure_time: Optional[float] = None
        self._lock = asyncio.Lock()

    async def should_attempt_operation(self) -> bool:
        """Check if operation should be attempted."""
        async with self._lock:
            if self.failure_count < self.max_failures:
                return True

            if self.last_failure_time is None:
                return True

            # Check if reset timeout has passed
            if (
                dt_util.utcnow().timestamp() - self.last_failure_time
                > self.reset_timeout
            ):
                self.failure_count = 0
                self.last_failure_time = None
                return True

            return False

    async def record_success(self) -> None:
        """Record successful operation."""
        async with self._lock:
            self.failure_count = 0
            self.last_failure_time = None

    async def record_failure(self) -> None:
        """Record failed operation."""
        async with self._lock:
            self.failure_count += 1
            self.last_failure_time = dt_util.utcnow().timestamp()

    async def is_open(self) -> bool:
        """Check if circuit breaker is open."""
        return not await self.should_attempt_operation()
