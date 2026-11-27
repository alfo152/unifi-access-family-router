"""Command line tests (real subprocesses)."""

from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
import tempfile
import unittest

from .broker import TestBroker
from .support import HAVE_NODE_WS, HAVE_OPENSSL, MAC, ROOM_A, ROOT, SelfSignedCert, ring_message

APP = str(ROOT / "family_router" / "app")


async def run_cli(*args: str, timeout: float = 30) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "family_router", *args,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        env={**os.environ, "PYTHONPATH": APP},
    )
    out, err = await asyncio.wait_for(proc.communicate(), timeout)
    assert proc.returncode is not None
    return proc.returncode, out.decode(), err.decode()


class CliBasicsTests(unittest.IsolatedAsyncioTestCase):
    async def test_version(self) -> None:
        code, out, _ = await run_cli("--version")
        self.assertEqual(code, 0)
        self.assertIn("0.2.0", out)

    async def test_missing_and_invalid_config(self) -> None:
        code, _, err = await run_cli("--config", "/nonexistent.yaml")
        self.assertEqual(code, 2)
        self.assertIn("not found", err)
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            json.dump({"access": {"host": "1.2.3.4"}}, fh)
        try:
            code, _, err = await run_cli("--config", fh.name)
            self.assertEqual(code, 2)
            self.assertIn("CONFIGURATION ERROR", err)
            self.assertIn("ERRORE DI CONFIGURAZIONE", err)
            self.assertIn("access.token", err)
        finally:
            os.unlink(fh.name)

    async def test_fingerprint_unreachable(self) -> None:
        code, _, err = await run_cli("--fingerprint", "127.0.0.1:1")
        self.assertEqual(code, 2)
        self.assertIn("cannot read the certificate", err)


@unittest.skipUnless(HAVE_NODE_WS and HAVE_OPENSSL, "node 'ws' module or openssl not available")
class CliLiveTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        from .ws_fake import NodeAccessServer

        self.cert = SelfSignedCert()
        self.console = NodeAccessServer(
            self.cert, {"full": ["view:device", "view:space", "edit:space"], "ws": ["view:device"]}
        )
        await self.console.start()
        self.tmp = tempfile.mkdtemp()

    async def asyncTearDown(self) -> None:
        await self.console.stop()
        self.cert.cleanup()

    def write_config(self, token: str, mqtt_port: int = 1883, **extra: object) -> str:
        raw: dict[str, object] = {
            "access": {"host": "127.0.0.1", "port": self.console.port, "token": token,
                       "tls_fingerprint": self.cert.fingerprint},
            "family": {"name": "Famiglia A", "id": "a", "match": {"room_ids": [ROOM_A]}},
            "mqtt": {"host": "127.0.0.1", "port": mqtt_port},
            "homebridge": {"enabled": True, "camera_mac": MAC},
            **extra,
        }
        path = os.path.join(self.tmp, f"{token}-{mqtt_port}.json")
        with open(path, "w") as fh:
            json.dump(raw, fh)
        return path

    async def test_fingerprint_command(self) -> None:
        code, out, _ = await run_cli("--fingerprint", f"127.0.0.1:{self.console.port}")
        self.assertEqual(code, 0)
        expected = ":".join(self.cert.fingerprint[i : i + 2] for i in range(0, 64, 2))
        self.assertIn(expected, out)

    async def test_check_and_list_doors(self) -> None:
        code, out, err = await run_cli("--config", self.write_config("full"), "--check")
        self.assertEqual(code, 0, err)
        self.assertIn("OK   notifications WebSocket", out)
        self.assertIn("0ed545f8-2fcd-4839-9021-b39e707f6aa9", out)

        # notifications-only token: check passes, doors are optional
        code, out, _ = await run_cli("--config", self.write_config("ws"), "--check")
        self.assertEqual(code, 0)
        self.assertIn("only needed when 'unlock.enabled'", out)

        # ...but not when unlock is enabled
        cfg = self.write_config("ws", unlock={"enabled": True, "door_id": "x"})
        code, _, err = await run_cli("--config", cfg, "--check")
        self.assertEqual(code, 2)
        self.assertIn("view:space", err)

        code, _, err = await run_cli("--config", self.write_config("bad"), "--check")
        self.assertEqual(code, 2)
        self.assertIn("HTTP 401", err)

        code, out, _ = await run_cli("--config", self.write_config("full"), "--list-doors")
        self.assertEqual(code, 0)
        self.assertIn("UCG-Max - GF - Portone", out)
        self.assertIn('- "Portone=0ed545f8-2fcd-4839-9021-b39e707f6aa9"', out)
        self.assertIn('- "Cancello=a8f2c6d1-3b4e-4f5a-9c7d-1e2f3a4b5c6d"', out)

        # --check names every configured door
        cfg = self.write_config("full", unlock={"enabled": True, "doors": [
            "Portone=0ed545f8-2fcd-4839-9021-b39e707f6aa9", "Cancello=a8f2c6d1-3b4e-4f5a-9c7d-1e2f3a4b5c6d"]})
        code, out, err = await run_cli("--config", cfg, "--check")
        self.assertEqual(code, 0, err)
        self.assertIn("Unlock is enabled for Portone", out)
        self.assertIn("Unlock is enabled for Cancello", out)

    async def test_run_learn_and_sigterm(self) -> None:
        broker = TestBroker()
        await broker.start()
        cfg = self.write_config("full", broker.port)
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "family_router", "--config", cfg, "--learn",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            env={**os.environ, "PYTHONPATH": APP},
        )
        try:
            await broker.wait_for(lambda: bool(broker.messages("access_family_router/a/status")), 10)
            await self.console.wait_event(lambda e: e["event"] == "connected", timeout=10)
            await self.console.broadcast(ring_message(ROOM_A, "cli-1"))
            await broker.wait_for(lambda: bool(broker.messages("access_family_router/a/learn")), 5)
            learn = json.loads(broker.messages("access_family_router/a/learn")[-1].payload)
            self.assertEqual(learn["room_id"], ROOM_A)
            self.assertTrue(learn["matched"])
            await broker.wait_for(lambda: bool(broker.messages(f"unifi/protect/{MAC}/doorbell/set")), 5)
            # learn mode also lists the doors (REST call): let it finish
            await self.console.wait_event(
                lambda e: e["event"] == "http" and e["path"] == "/api/v1/developer/doors", timeout=10
            )
            await asyncio.sleep(1.0)
        finally:
            proc.send_signal(signal.SIGTERM)
            output = (await asyncio.wait_for(proc.communicate(), 15))[0].decode()
        self.assertEqual(proc.returncode, 0, output)
        self.assertIn("LEARN: ring", output)
        self.assertIn("LEARN: door 'UCG-Max - GF - Portone' -> door_id 0ed545f8-2fcd-4839-9021-b39e707f6aa9", output)
        self.assertIn("Stopped", output)
        self.assertEqual(broker.messages("access_family_router/a/status")[-1].payload, b"offline")
        await broker.stop()


if __name__ == "__main__":
    unittest.main()
