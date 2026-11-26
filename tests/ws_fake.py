"""Python controller for tests/ws_server.js (Node 'ws' based Access double)."""

from __future__ import annotations

import asyncio
import itertools
import json
import os
from pathlib import Path
from typing import Any, Callable

from .support import NODE, SelfSignedCert

SCRIPT = Path(__file__).parent / "ws_server.js"


class NodeAccessServer:
    def __init__(
        self, cert: SelfSignedCert, tokens: list[str] | dict[str, list[str]], port: int = 0
    ) -> None:
        self.cert = cert
        self.tokens = tokens
        self.port = port
        self.events: list[dict[str, Any]] = []
        self._proc: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._changed = asyncio.Event()
        self._seq = itertools.count(1)

    async def start(self) -> None:
        assert NODE is not None
        self._proc = await asyncio.create_subprocess_exec(
            NODE,
            str(SCRIPT),
            self.cert.cert,
            self.cert.key,
            json.dumps(self.tokens),
            str(self.port),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            env={**os.environ},
        )
        self._reader_task = asyncio.create_task(self._read())
        ready = await self.wait_event(lambda e: e["event"] == "ready", timeout=10)
        self.port = ready["port"]

    async def _read(self) -> None:
        assert self._proc is not None and self._proc.stdout is not None
        while True:
            line = await self._proc.stdout.readline()
            if not line:
                break
            try:
                self.events.append(json.loads(line))
            except json.JSONDecodeError:
                self.events.append({"event": "raw", "line": line.decode(errors="replace")})
            self._changed.set()

    async def wait_event(
        self, predicate: Callable[[dict[str, Any]], bool], timeout: float = 5.0, start: int = 0
    ) -> dict[str, Any]:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        index = start
        while True:
            while index < len(self.events):
                event = self.events[index]
                index += 1
                if predicate(event):
                    return event
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise AssertionError(
                    f"timeout waiting for server event; last events: {self.events[-8:]}"
                )
            self._changed.clear()
            try:
                await asyncio.wait_for(self._changed.wait(), min(remaining, 0.1))
            except asyncio.TimeoutError:
                pass

    def count(self, predicate: Callable[[dict[str, Any]], bool]) -> int:
        return sum(1 for e in self.events if predicate(e))

    async def send(self, **cmd: Any) -> None:
        assert self._proc is not None and self._proc.stdin is not None
        seq = next(self._seq)
        cmd["seq"] = seq
        mark = len(self.events)
        self._proc.stdin.write((json.dumps(cmd) + "\n").encode())
        await self._proc.stdin.drain()
        await self.wait_event(lambda e: e.get("event") == "ack" and e.get("seq") == seq, start=mark)

    async def broadcast(self, data: str, token: str | None = None) -> None:
        await self.send(cmd="broadcast", data=data, token=token)

    async def stop(self) -> None:
        if self._proc is None:
            return
        if self._proc.returncode is None:
            try:
                assert self._proc.stdin is not None
                self._proc.stdin.write(b'{"cmd":"stop"}\n')
                await self._proc.stdin.drain()
            except (ConnectionError, RuntimeError):
                pass
            try:
                await asyncio.wait_for(self._proc.wait(), 5)
            except asyncio.TimeoutError:
                self._proc.kill()
                await self._proc.wait()
        if self._reader_task is not None:
            await asyncio.wait_for(self._reader_task, 5)
        self._proc = None
