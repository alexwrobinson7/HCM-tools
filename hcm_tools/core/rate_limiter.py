"""Async sliding-window rate limiter."""

import asyncio
import logging
import time
from collections import deque

logger = logging.getLogger(__name__)


class RateLimiter:
    """
    Allows at most *max_calls* downloads within any rolling *window* seconds.

    All concurrent workers share a single instance.  Each worker calls
    ``await limiter.acquire()`` before starting a download; the call blocks
    until a slot is available.
    """

    def __init__(self, max_calls: int, window: float = 60.0):
        if max_calls < 1:
            raise ValueError("max_calls must be >= 1")
        self.max_calls = max_calls
        self.window = window
        self._slots: deque[float] = deque()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        """Block until a rate-limit slot is available, then claim it."""
        async with self._lock:
            now = time.monotonic()

            # Drop timestamps that have aged out of the rolling window
            while self._slots and self._slots[0] <= now - self.window:
                self._slots.popleft()

            if len(self._slots) >= self.max_calls:
                # Oldest slot must expire before we can proceed
                wait = (self._slots[0] + self.window) - now
                if wait > 0:
                    logger.debug(
                        f"Rate limit ({self.max_calls}/{self.window:.0f}s): "
                        f"sleeping {wait:.1f}s"
                    )
                    await asyncio.sleep(wait)
                self._slots.popleft()

            self._slots.append(time.monotonic())

    @property
    def current_rate(self) -> int:
        """Number of calls recorded in the current window (approximate)."""
        now = time.monotonic()
        return sum(1 for t in self._slots if t > now - self.window)
