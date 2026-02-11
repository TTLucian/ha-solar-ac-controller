# custom_components/solar_ac_controller/storage_circuit_breaker.py
"""Circuit breaker pattern for storage operations."""

import asyncio
from typing import Any, Optional

from homeassistant.util import dt as dt_util


class StorageCircuitBreaker:
    """Circuit breaker for storage operations to prevent cascading failures."""

    def __init__(
        self, max_failures: int = 3, reset_timeout: int = 300, coordinator: Any = None
    ) -> None:
        """Initialize circuit breaker."""
        self.max_failures = max_failures
        self.reset_timeout = reset_timeout
        self.failure_count = 0
        self.last_failure_time: Optional[float] = None
        self._lock = asyncio.Lock()
        self.coordinator = coordinator
        self._last_state = "closed"  # Track state for logging changes

    async def should_attempt_operation(self) -> bool:
        """Check if operation should be attempted."""
        async with self._lock:
            current_state = self._get_current_state()

            # Log state changes
            if current_state != self._last_state:
                await self._log_state_change(self._last_state, current_state)
                self._last_state = current_state

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
            old_state = self._get_current_state()
            self.failure_count = 0
            self.last_failure_time = None
            new_state = self._get_current_state()

            if old_state != new_state:
                await self._log_state_change(old_state, new_state)
                self._last_state = new_state

    async def record_failure(self) -> None:
        """Record failed operation."""
        async with self._lock:
            old_state = self._get_current_state()
            self.failure_count += 1
            self.last_failure_time = dt_util.utcnow().timestamp()
            new_state = self._get_current_state()

            if old_state != new_state:
                await self._log_state_change(old_state, new_state)
                self._last_state = new_state

    def _get_current_state(self) -> str:
        """Get current circuit breaker state."""
        if self.failure_count >= self.max_failures:
            if self.last_failure_time and (
                dt_util.utcnow().timestamp() - self.last_failure_time
                > self.reset_timeout
            ):
                return "half-open"
            else:
                return "open"
        return "closed"

    async def _log_state_change(self, old_state: str, new_state: str) -> None:
        """Log circuit breaker state change."""
        if self.coordinator:
            if new_state == "open":
                message = f"Storage temporarily disabled due to {self.failure_count} consecutive save failures - will retry automatically"
            elif new_state == "half-open":
                message = f"Storage entering recovery mode after {self.failure_count} failures - testing if saves work again"
            else:  # closed
                message = f"Storage recovered and re-enabled after {self.failure_count} previous failures"

            await self.coordinator._log(
                message, "warning" if new_state == "open" else "info"
            )

    async def call_with_timeout(self, coro: Any, timeout: float = 10.0) -> Any:
        """
        Execute a coroutine with timeout protection when in half-open state.

        In half-open state, wraps the operation with asyncio.wait_for to prevent
        hanging operations from locking the circuit breaker indefinitely.
        """
        async with self._lock:
            state = self._get_current_state()
            is_half_open = state == "half-open"

        if is_half_open:
            # In half-open state, add timeout protection
            try:
                return await asyncio.wait_for(coro, timeout=timeout)
            except asyncio.TimeoutError:
                # Treat timeout as a failure for circuit breaker purposes
                await self.record_failure()
                raise
        else:
            # In closed or open state, execute normally
            return await coro

    async def is_open(self) -> bool:
        """Check if circuit breaker is open."""
        return not await self.should_attempt_operation()
