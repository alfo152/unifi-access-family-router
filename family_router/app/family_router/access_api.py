"""UniFi Access API client (official developer API, port 12445).

Endpoints used (official API reference):

* ``GET  /api/v1/developer/devices/notifications``  WebSocket, scope ``view:device``
* ``GET  /api/v1/developer/doors``                  scope ``view:space``
* ``PUT  /api/v1/developer/doors/:id/unlock``       scope ``edit:space``
"""

from __future__ import annotations

import asyncio
import http.client
import json
import logging
import ssl
import time
from typing import Any, Awaitable, Callable
from urllib.parse import quote

from . import __version__
from .config import AccessConfig
from .tlsutil import check_fingerprint, make_ssl_context
from .websocket import (
    WebSocketClient,
    WebSocketClosed,
    WebSocketError,
    WebSocketHandshakeError,
)

__all__ = ["AccessApiError", "AccessClient", "NOTIFICATIONS_PATH"]

_LOGGER = logging.getLogger(__name__)

NOTIFICATIONS_PATH = "/api/v1/developer/devices/notifications"
DOORS_PATH = "/api/v1/developer/doors"
_USER_AGENT = f"unifi-access-family-router/{__version__}"
_MAX_RESPONSE = 4 * 1024 * 1024


class AccessApiError(Exception):
    """The Access API returned an error."""

    def __init__(self, message: str, status: int = 0, code: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.code = code


MessageHandler = Callable[[str | bytes], Awaitable[None]]
StateHandler = Callable[[bool], None]


class AccessClient:
    def __init__(
        self,
        config: AccessConfig,
        *,
        heartbeat_interval: float = 30.0,
        heartbeat_timeout: float = 10.0,
        max_backoff: float = 60.0,
        request_timeout: float = 10.0,
    ) -> None:
        self._config = config
        self._ssl = make_ssl_context(config)
        self._heartbeat_interval = heartbeat_interval
        self._heartbeat_timeout = heartbeat_timeout
        self._max_backoff = max_backoff
        self._request_timeout = request_timeout

    # --------------------------------------------------------------- REST

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._config.token}",
            "Accept": "application/json",
            "User-Agent": _USER_AGENT,
        }

    def _request_sync(self, method: str, path: str, body: dict[str, Any] | None) -> Any:
        conn = http.client.HTTPSConnection(
            self._config.host,
            self._config.port,
            context=self._ssl,
            timeout=self._request_timeout,
        )
        try:
            conn.connect()
            check_fingerprint(conn.sock, self._config.tls_fingerprint)
            headers = self._headers()
            data: bytes | None = None
            if body is not None:
                data = json.dumps(body, separators=(",", ":")).encode("utf-8")
                headers["Content-Type"] = "application/json"
            conn.request(method, path, body=data, headers=headers)
            response = conn.getresponse()
            raw = response.read(_MAX_RESPONSE + 1)
            status = response.status
        finally:
            conn.close()
        if len(raw) > _MAX_RESPONSE:
            raise AccessApiError("response too large", status)
        if status in (401, 403):
            raise AccessApiError(
                f"HTTP {status}: token rejected or missing permission for {method} {path}",
                status,
            )
        try:
            payload = json.loads(raw.decode("utf-8")) if raw else {}
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AccessApiError(f"HTTP {status}: invalid JSON response", status) from exc
        code = str(payload.get("code", "")) if isinstance(payload, dict) else ""
        if status >= 400 or (code and code != "SUCCESS"):
            message = payload.get("msg", "") if isinstance(payload, dict) else ""
            raise AccessApiError(
                f"HTTP {status} {code or ''} {message}".strip(), status, code
            )
        return payload.get("data") if isinstance(payload, dict) else payload

    async def request(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        return await asyncio.to_thread(self._request_sync, method, path, body)

    async def list_doors(self) -> list[dict[str, Any]]:
        data = await self.request("GET", DOORS_PATH)
        if not isinstance(data, list):
            raise AccessApiError("unexpected doors response")
        return [door for door in data if isinstance(door, dict)]

    async def unlock(self, door_id: str, actor_id: str, actor_name: str) -> None:
        if not door_id:
            raise AccessApiError("door_id is empty")
        path = f"{DOORS_PATH}/{quote(door_id, safe='')}/unlock"
        await self.request("PUT", path, {"actor_id": actor_id, "actor_name": actor_name})

    # ---------------------------------------------------------- WebSocket

    async def connect_notifications(self) -> WebSocketClient:
        return await WebSocketClient.connect(
            self._config.host,
            self._config.port,
            NOTIFICATIONS_PATH,
            headers={"Authorization": f"Bearer {self._config.token}", "User-Agent": _USER_AGENT},
            ssl_context=self._ssl,
            fingerprint=self._config.tls_fingerprint,
            timeout=self._request_timeout,
        )

    async def listen(
        self,
        on_message: MessageHandler,
        on_state: StateHandler | None = None,
    ) -> None:
        """Receive notifications forever, reconnecting with back-off.

        Cancel the task to stop. ``on_message`` exceptions are logged and do
        not drop the connection.
        """

        delay = 1.0
        while True:
            started = time.monotonic()
            try:
                ws = await self.connect_notifications()
            except WebSocketHandshakeError as exc:
                if exc.status in (401, 403):
                    _LOGGER.error(
                        "Access API refused the token (HTTP %s). Check 'access.token' and "
                        "that it has the 'view:device' permission.",
                        exc.status,
                    )
                    delay = max(delay, 30.0)
                else:
                    _LOGGER.warning("Access WebSocket upgrade failed: %s", exc)
            except ssl.SSLError as exc:
                _LOGGER.error("TLS error connecting to the console: %s", exc)
            except (OSError, asyncio.TimeoutError, WebSocketError, asyncio.IncompleteReadError) as exc:
                _LOGGER.warning(
                    "Cannot reach the Access API at %s:%s (%s)",
                    self._config.host,
                    self._config.port,
                    exc or exc.__class__.__name__,
                )
            else:
                _LOGGER.info("Connected to UniFi Access notifications")
                if on_state:
                    on_state(True)
                heartbeat = asyncio.create_task(
                    ws.heartbeat(self._heartbeat_interval, self._heartbeat_timeout)
                )
                try:
                    while True:
                        message = await ws.recv()
                        try:
                            await on_message(message)
                        except asyncio.CancelledError:
                            raise
                        except Exception:  # noqa: BLE001 - keep the connection alive
                            _LOGGER.exception("Error while handling an Access notification")
                except WebSocketClosed as exc:
                    _LOGGER.warning("Access WebSocket closed: %s", exc)
                finally:
                    heartbeat.cancel()
                    try:
                        await ws.close(timeout=1.0)
                    except Exception:  # noqa: BLE001
                        pass
                    if on_state:
                        on_state(False)
                if time.monotonic() - started > 60:
                    delay = 1.0
            _LOGGER.info("Reconnecting to the Access API in %.0fs", delay)
            await asyncio.sleep(delay)
            delay = min(delay * 2, self._max_backoff)
