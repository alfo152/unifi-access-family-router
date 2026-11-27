"""Tests for tools/watch_calls.py, the standalone read-only room_id check."""

from __future__ import annotations

import ast
import asyncio
import importlib.util
import io
import json
import os
import re
import signal
import sys
import unittest
from pathlib import Path

from .support import HAVE_NODE_WS, HAVE_OPENSSL, SelfSignedCert, end_message, ring_message

SCRIPT = Path(__file__).resolve().parent.parent / "tools" / "watch_calls.py"
_spec = importlib.util.spec_from_file_location("watch_calls", SCRIPT)
assert _spec is not None and _spec.loader is not None
watch_calls = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(watch_calls)

TOKEN = "token-view-device"
ROOM_A = "WR-e4388386be1d-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
ROOM_B = "WR-e4388386be1d-BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"


class PureFunctionTests(unittest.TestCase):
    def test_python_38_syntax(self) -> None:
        ast.parse(SCRIPT.read_text(), feature_version=(3, 8))

    def test_parse_target(self) -> None:
        cases = {
            "192.168.1.1": ("192.168.1.1", 12445),
            "192.168.1.1:443": ("192.168.1.1", 443),
            "console.local": ("console.local", 12445),
            "[fd00::1]:12445": ("fd00::1", 12445),
            "[fd00::1]": ("fd00::1", 12445),
            "fd00::1": ("fd00::1", 12445),
        }
        for text, expected in cases.items():
            self.assertEqual(watch_calls.parse_target(text), expected, text)
        for bad in ("", ":12445", "host:0", "host:70000", "[fd00::1", "[fd00::1]x"):
            with self.assertRaises(ValueError, msg=bad):
                watch_calls.parse_target(bad)

    def test_fingerprint(self) -> None:
        value = "ab" * 32
        for text in (value, value.upper(), watch_calls.format_fingerprint(value),
                     "SHA256=" + value, "sha256 Fingerprint=" + watch_calls.format_fingerprint(value)):
            self.assertEqual(watch_calls.normalize_fingerprint(text), value)
        with self.assertRaises(ValueError):
            watch_calls.normalize_fingerprint("abc")

    def test_accept_key_rfc_example(self) -> None:
        self.assertEqual(watch_calls.accept_key("dGhlIHNhbXBsZSBub25jZQ=="),
                         "s3pPLMBiTxaQ9kYGzzhZRbK+xOo=")

    def test_redact(self) -> None:
        message = json.loads(ring_message(ROOM_A, "r1"))
        redacted = watch_calls.redact(message)
        self.assertEqual(redacted["data"]["token"], "<redacted>")
        self.assertEqual(redacted["data"]["channel"], "<redacted>")
        self.assertEqual(redacted["data"]["room_id"], ROOM_A)

    def recorder(self) -> "watch_calls.Recorder":
        return watch_calls.Recorder(out=io.StringIO())

    def test_verdict_different_rooms(self) -> None:
        rec = self.recorder()
        rec.handle(ring_message(ROOM_A, "r1"))
        rec.handle(end_message("r1", 105))
        rec.handle(ring_message(ROOM_B, "r2"))
        summary = rec.summary()
        self.assertIn("Calls seen: 2, different room_id: 2", summary)
        self.assertIn("Result: OK", summary)
        self.assertIn(f"END   room_id={ROOM_A}  reason=timed out (105)", rec.out.getvalue())

    def test_verdict_same_room(self) -> None:
        rec = self.recorder()
        rec.handle(ring_message(ROOM_A, "r1"))
        rec.handle(ring_message(ROOM_A, "r2"))
        self.assertIn("does NOT work", rec.summary())

    def test_verdict_one_and_none(self) -> None:
        rec = self.recorder()
        self.assertIn("No calls seen", rec.summary())
        rec.handle(ring_message(ROOM_A, "r1"))
        self.assertIn("Only one call seen", rec.summary())

    def test_italian_summary(self) -> None:
        rec = watch_calls.Recorder(out=io.StringIO(), lang="it")
        self.assertIn("Nessuna chiamata vista", rec.summary())
        rec.handle(ring_message(ROOM_A, "r1"))
        self.assertIn("Vista una sola chiamata", rec.summary())
        rec.handle(ring_message(ROOM_B, "r2"))
        summary = rec.summary()
        self.assertIn("=== Riepilogo ===", summary)
        self.assertIn("Chiamate viste: 2, room_id diversi: 2", summary)
        self.assertIn("Risultato: OK", summary)
        self.assertIn("non contiene token", summary)
        # the CALL lines are data: the same in every language
        self.assertIn(f"CALL  room_id={ROOM_A}", rec.out.getvalue())
        rec = watch_calls.Recorder(out=io.StringIO(), lang="it")
        rec.handle(ring_message(ROOM_A, "r1"))
        rec.handle(ring_message(ROOM_A, "r2"))
        self.assertIn("NON funziona", rec.summary())

    def test_messages_cover_both_languages(self) -> None:
        en, it = watch_calls.MESSAGES["en"], watch_calls.MESSAGES["it"]
        self.assertEqual(set(en), set(it))
        for key in en:
            self.assertEqual(
                sorted(re.findall(r"{(\w+)", en[key])), sorted(re.findall(r"{(\w+)", it[key])), key
            )

    def test_detect_language(self) -> None:
        detect = watch_calls.detect_language
        self.assertEqual(detect({"LANG": "it_IT.UTF-8"}), "it")
        self.assertEqual(detect({"LANG": "en_US.UTF-8"}), "en")
        self.assertEqual(detect({"LC_ALL": "C", "LANG": "it_IT.UTF-8"}), "en")
        self.assertEqual(detect({"LC_ALL": "it_IT.UTF-8", "LANG": "en_US"}), "it")
        self.assertEqual(detect({}), "en")

    def test_ignores_noise(self) -> None:
        rec = self.recorder()
        for text in ("Hello", "[]", "null", '{"event": "access.device.update", "data": {}}',
                     '{"event": "access.remote_view", "data": "oops"}'):
            rec.handle(text)
        self.assertEqual(len(rec.calls), 1)  # the malformed call is still counted
        self.assertEqual(rec.calls[0]["room_id"], "(missing)")

    def test_raw_mode_hides_tokens(self) -> None:
        rec = watch_calls.Recorder(raw=True, out=io.StringIO())
        rec.handle(ring_message(ROOM_A, "r1"))
        output = rec.out.getvalue()
        self.assertIn("<redacted>", output)
        self.assertNotIn("6dff120f-2688-497d-856f-0ca08b383d1d", output)


@unittest.skipUnless(HAVE_NODE_WS and HAVE_OPENSSL, "node 'ws' or openssl not available")
class LiveTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        from .ws_fake import NodeAccessServer

        self.cert = SelfSignedCert()
        self.server = NodeAccessServer(self.cert, {TOKEN: ["view:device"]})
        await self.server.start()

    async def asyncTearDown(self) -> None:
        await self.server.stop()
        self.cert.cleanup()

    async def spawn(self, *args: str, token: str = TOKEN) -> asyncio.subprocess.Process:
        env = {**os.environ, "UNIFI_ACCESS_TOKEN": token, "LC_ALL": "C.UTF-8"}
        return await asyncio.create_subprocess_exec(
            sys.executable, "-I", str(SCRIPT), f"127.0.0.1:{self.server.port}", *args,
            stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, env=env,
        )

    async def finish(self, proc: asyncio.subprocess.Process) -> tuple[int, str, str]:
        out, err = await asyncio.wait_for(proc.communicate(), 20)
        assert proc.returncode is not None
        return proc.returncode, out.decode(), err.decode()

    async def test_full_session(self) -> None:
        proc = await self.spawn("--duration", "4")
        connected = await self.server.wait_event(lambda e: e["event"] == "connected", timeout=10)
        self.assertEqual(connected["token"], TOKEN)
        self.assertTrue(connected["ua"].startswith("watch-calls/"))
        await self.server.broadcast(ring_message(ROOM_A, "r1"))
        await self.server.broadcast(end_message("r1", 106))
        # A notification split into WebSocket fragments, and a server ping.
        text = ring_message(ROOM_B, "r2")
        await self.server.send(cmd="fragmented", parts=[text[:50], text[50:120], text[120:]])
        await self.server.send(cmd="ping", data="keepalive")
        await self.server.wait_event(lambda e: e["event"] == "pong" and e["data"] == "keepalive")
        code, out, err = await self.finish(proc)
        self.assertEqual(code, 0, err)
        self.assertIn("Certificate SHA-256: ", out)
        self.assertIn(f"CALL  room_id={ROOM_A}", out)
        self.assertIn(f"END   room_id={ROOM_A}  reason=rejected (106)", out)
        self.assertIn(f"CALL  room_id={ROOM_B}", out)
        self.assertIn("Calls seen: 2, different room_id: 2", out)
        self.assertNotIn(TOKEN, out + err)
        closed = await self.server.wait_event(lambda e: e["event"] == "closed")
        self.assertEqual(closed["code"], 1000)

    async def test_ctrl_c_prints_summary(self) -> None:
        proc = await self.spawn()
        await self.server.wait_event(lambda e: e["event"] == "connected", timeout=10)
        await self.server.broadcast(ring_message(ROOM_A, "r1"))
        await asyncio.sleep(0.5)
        proc.send_signal(signal.SIGINT)
        code, out, err = await self.finish(proc)
        self.assertEqual(code, 0, err)
        self.assertIn("Only one call seen", out)

    async def test_italian_session(self) -> None:
        proc = await self.spawn("--duration", "3", "--lang", "it")
        await self.server.wait_event(lambda e: e["event"] == "connected", timeout=10)
        await self.server.broadcast(ring_message(ROOM_A, "r1"))
        await self.server.broadcast(ring_message(ROOM_B, "r2"))
        code, out, err = await self.finish(proc)
        self.assertEqual(code, 0, err)
        self.assertIn("Certificato SHA-256: ", out)
        self.assertIn("Collegato.", out)
        self.assertIn("Si ferma da solo tra 3 s.", out)
        self.assertIn("Chiamate viste: 2, room_id diversi: 2", out)
        # an Italian computer gets Italian without --lang
        env = {**os.environ, "UNIFI_ACCESS_TOKEN": "wrong", "LC_ALL": "it_IT.UTF-8"}
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-I", str(SCRIPT), f"127.0.0.1:{self.server.port}",
            "--duration", "1", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        code, _, err = await self.finish(proc)
        self.assertEqual(code, 2)
        self.assertIn("il token è sbagliato", err)

    async def test_wrong_token(self) -> None:
        proc = await self.spawn("--duration", "2", token="wrong")
        code, out, err = await self.finish(proc)
        self.assertEqual(code, 2)
        self.assertIn("view:device", err)
        self.assertNotIn("wrong", out)

    async def test_fingerprint_enforced(self) -> None:
        proc = await self.spawn("--duration", "2", "--fingerprint", "00" * 32)
        code, _, err = await self.finish(proc)
        self.assertEqual(code, 1)
        self.assertIn("does not match", err)
        proc = await self.spawn("--duration", "1", "--fingerprint", self.cert.fingerprint)
        code, out, err = await self.finish(proc)
        self.assertEqual(code, 0, err)
        self.assertIn("No calls seen", out)

    async def test_server_closes(self) -> None:
        proc = await self.spawn()
        await self.server.wait_event(lambda e: e["event"] == "connected", timeout=10)
        await self.server.send(cmd="close", code=1001, reason="going away")
        code, out, err = await self.finish(proc)
        self.assertEqual(code, 1)
        self.assertIn("1001", err)
        self.assertIn("=== Summary ===", out)

    async def test_unreachable(self) -> None:
        port = self.server.port
        await self.server.stop()
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-I", str(SCRIPT), f"127.0.0.1:{port}", "--duration", "1",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            env={**os.environ, "UNIFI_ACCESS_TOKEN": TOKEN, "LC_ALL": "C.UTF-8"},
        )
        code, _, err = await self.finish(proc)
        self.assertEqual(code, 1)
        self.assertIn("Cannot connect", err)


if __name__ == "__main__":
    unittest.main()
