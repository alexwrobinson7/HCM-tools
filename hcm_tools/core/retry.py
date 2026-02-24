"""Exponential backoff retry helper for async callables."""

import asyncio
import logging
import random
from typing import Awaitable, Callable, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


async def with_retry(
    fn: Callable[[], Awaitable[T]],
    *,
    max_attempts: int = 3,
    base_delay: float = 2.0,
    max_delay: float = 60.0,
    jitter: bool = True,
    label: str = "operation",
) -> T:
    """
    Call async *fn* with exponential back-off on failure.

    Delay schedule: ``base_delay * 2^(attempt-1)``, capped at *max_delay*.
    With *jitter=True* each delay is scaled by a uniform factor in [0.5, 1.5]
    to spread load when many workers retry simultaneously.

    Raises the last exception if all attempts are exhausted.
    """
    last_exc: Exception = RuntimeError("No attempts made")

    for attempt in range(1, max_attempts + 1):
        try:
            return await fn()
        except Exception as exc:
            last_exc = exc
            if attempt == max_attempts:
                logger.error(
                    f"[retry] {label}: all {max_attempts} attempt(s) failed — {exc}"
                )
                break

            delay = min(base_delay * (2 ** (attempt - 1)), max_delay)
            if jitter:
                delay *= 0.5 + random.random()  # 50 %–150 % of computed delay

            logger.warning(
                f"[retry] {label}: attempt {attempt}/{max_attempts} failed "
                f"({type(exc).__name__}: {exc}), retrying in {delay:.1f}s"
            )
            await asyncio.sleep(delay)

    raise last_exc
