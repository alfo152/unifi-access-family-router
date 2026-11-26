"""REST client tests against an HTTPS double of the Access API."""

from __future__ import annotations

import unittest

from family_router.access_api import AccessApiError, AccessClient
from family_router.config import AccessConfig
from family_router.tlsutil import TlsFingerprintError

from .rest_fake import DOORS, RestFake
from .support import HAVE_OPENSSL, SelfSignedCert

FULL = "token-full"
WS_ONLY = "token-ws-only"


@unittest.skipUnless(HAVE_OPENSSL, "openssl not available")
class RestTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.cert = SelfSignedCert()
        cls.fake = RestFake(cls.cert, {FULL: {"view:device", "view:space", "edit:space"},
                                       WS_ONLY: {"view:device"}})
        cls.fake.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.fake.stop()
        cls.cert.cleanup()

    def setUp(self) -> None:
        self.fake.requests.clear()
        self.fake.fail_unlock_with = None

    def client(self, token: str = FULL, fingerprint: str | None = None) -> AccessClient:
        return AccessClient(
            AccessConfig(
                host="127.0.0.1",
                port=self.fake.port,
                token=token,
                tls_fingerprint=self.cert.fingerprint if fingerprint is None else fingerprint,
            )
        )

    async def test_list_doors(self) -> None:
        doors = await self.client().list_doors()
        self.assertEqual(doors, DOORS)
        request = self.fake.requests[-1]
        self.assertEqual((request["method"], request["path"], request["token"]),
                         ("GET", "/api/v1/developer/doors", FULL))

    async def test_unlock_sends_actor(self) -> None:
        await self.client().unlock(DOORS[0]["id"], "family-router-a", "Famiglia A")
        request = self.fake.unlocks()[-1]
        self.assertEqual(request["path"], f"/api/v1/developer/doors/{DOORS[0]['id']}/unlock")
        self.assertEqual(request["body"], {"actor_id": "family-router-a", "actor_name": "Famiglia A"})
        self.assertEqual(request["content_type"], "application/json")

    async def test_unlock_door_id_is_escaped(self) -> None:
        with self.assertRaises(AccessApiError):
            await self.client().unlock("../../doors", "a", "b")
        self.assertEqual(self.fake.unlocks()[-1]["path"], "/api/v1/developer/doors/..%2F..%2Fdoors/unlock")

    async def test_errors(self) -> None:
        with self.assertRaises(AccessApiError) as ctx:
            await self.client(token="nope").list_doors()
        self.assertEqual(ctx.exception.status, 401)

        with self.assertRaises(AccessApiError) as ctx:
            await self.client(token=WS_ONLY).unlock(DOORS[0]["id"], "a", "b")
        self.assertEqual(ctx.exception.status, 403)

        with self.assertRaises(AccessApiError) as ctx:
            await self.client().unlock("unknown-door", "a", "b")
        self.assertEqual(ctx.exception.code, "CODE_DEVICE_NOT_FOUND")

        self.fake.fail_unlock_with = (500, {"code": "CODE_SYSTEM_ERROR", "msg": "boom"})
        with self.assertRaises(AccessApiError) as ctx:
            await self.client().unlock(DOORS[0]["id"], "a", "b")
        self.assertEqual((ctx.exception.status, ctx.exception.code), (500, "CODE_SYSTEM_ERROR"))

        with self.assertRaises(AccessApiError):
            await self.client().unlock("", "a", "b")

    async def test_fingerprint_mismatch_sends_nothing(self) -> None:
        with self.assertRaises(TlsFingerprintError):
            await self.client(fingerprint="11" * 32).unlock(DOORS[0]["id"], "a", "b")
        self.assertEqual(self.fake.requests, [])

    async def test_unreachable(self) -> None:
        client = AccessClient(AccessConfig(host="127.0.0.1", port=1, token=FULL), request_timeout=2)
        with self.assertRaises(OSError):
            await client.list_doors()


if __name__ == "__main__":
    unittest.main()
