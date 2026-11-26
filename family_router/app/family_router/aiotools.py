"""Small asyncio helpers."""

from __future__ import annotations

import asyncio
from typing import Awaitable, TypeVar

__all__ = ["with_timeout"]

T = TypeVar("T")


async def with_timeout(awaitable: Awaitable[T], seconds: float) -> T:
    """Await with a timeout, raising :class:`TimeoutError`.

    ``asyncio.timeout()`` is used instead of ``asyncio.wait_for()``: on
    Python 3.11 ``wait_for`` can swallow a task cancellation when the awaited
    operation completes at the same moment (fixed upstream in 3.12), which
    would leave tasks running after shutdown.
    """

    async with asyncio.timeout(seconds):
        return await awaitable
