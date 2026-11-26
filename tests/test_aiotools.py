from __future__ import annotations

import asyncio
import unittest

from family_router.aiotools import with_timeout


class WithTimeoutTests(unittest.IsolatedAsyncioTestCase):
    async def test_result_and_timeout(self) -> None:
        self.assertEqual(await with_timeout(asyncio.sleep(0, result=7), 1), 7)
        with self.assertRaises(TimeoutError):
            await with_timeout(asyncio.sleep(5), 0.05)
        with self.assertRaises(asyncio.TimeoutError):  # same class since 3.11
            await with_timeout(asyncio.sleep(5), 0.05)

    async def test_cancel_is_not_swallowed_when_result_arrives_together(self) -> None:
        """Regression for the Python 3.11 wait_for race (gh-86296)."""

        for _ in range(50):
            loop = asyncio.get_running_loop()
            future: asyncio.Future[int] = loop.create_future()
            reached_after = False

            async def waiter() -> None:
                nonlocal reached_after
                await with_timeout(future, 5)
                reached_after = True
                await asyncio.sleep(10)

            task = asyncio.create_task(waiter())
            await asyncio.sleep(0)
            future.set_result(1)
            task.cancel()
            done, _ = await asyncio.wait([task], timeout=2)
            self.assertIn(task, done, "task ignored the cancellation")
            self.assertTrue(task.cancelled())
            self.assertFalse(reached_after)


if __name__ == "__main__":
    unittest.main()
