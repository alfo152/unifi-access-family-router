"""Service orchestration: Access notifications -> router -> MQTT.

The Access WebSocket and the MQTT connection are independent: each one
reconnects on its own. Messages produced while MQTT is down are queued;
ring triggers that are too old by the time MQTT is back are dropped, so a
family is never rung minutes after the visitor left.
"""

from __future__ import annotations

import asyncio
import json
import logging
import ssl
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Protocol

from .aiotools import with_timeout
from .config import Config
from .discovery import discovery_messages
from .events import parse_message
from .mqtt import Message, MqttClient, Will
from .router import FamilyRouter, Publish

__all__ = ["Service"]

_LOGGER = logging.getLogger(__name__)

#: ring triggers older than this are not delivered after an MQTT outage
STALE_RING_SECONDS = 15.0
UNLOCK_PAYLOADS = frozenset({"PRESS", "UNLOCK"})
_QUEUE_LIMIT = 500


class AccessLike(Protocol):
    async def listen(
        self,
        on_message: Callable[[str | bytes], Awaitable[None]],
        on_state: Callable[[bool], None] | None = None,
    ) -> None: ...

    async def unlock(self, door_id: str, actor_id: str, actor_name: str) -> None: ...


class MqttLike(Protocol):
    async def connect(self) -> None: ...

    async def disconnect(self) -> None: ...

    async def publish(
        self, topic: str, payload: str | bytes, qos: int = 0, retain: bool = False
    ) -> None: ...

    async def subscribe(self, topic_filter: str, qos: int = 1) -> int: ...

    def messages(self) -> Any: ...

    async def wait_closed(self) -> None: ...


MqttFactory = Callable[[Will], MqttLike]


def _mqtt_ssl(config: Config) -> ssl.SSLContext | None:
    if not config.mqtt.tls:
        return None
    ctx = ssl.create_default_context()
    if config.mqtt.tls_insecure:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return ctx


async def _cancel_and_wait(tasks: list["asyncio.Task[Any]"], timeout: float = 5.0) -> None:
    """Cancel tasks and wait for them, without ever hanging forever."""

    for task in tasks:
        task.cancel()
    done, pending = await asyncio.wait(tasks, timeout=timeout)
    for task in pending:
        _LOGGER.error("Task %s did not stop within %.0fs", task.get_name(), timeout)
    for task in done:
        if not task.cancelled():
            task.exception()  # mark as retrieved


class Service:
    def __init__(
        self,
        config: Config,
        *,
        access: AccessLike | None = None,
        mqtt_factory: MqttFactory | None = None,
        clock: Callable[[], float] = time.monotonic,
        expiry_interval: float = 2.0,
        mqtt_initial_backoff: float = 1.0,
        mqtt_max_backoff: float = 30.0,
    ) -> None:
        self.config = config
        self.router = FamilyRouter(config, clock=clock)
        self.topics = self.router.topics
        self._clock = clock
        self._expiry_interval = expiry_interval
        self._mqtt_initial_backoff = mqtt_initial_backoff
        self._mqtt_max_backoff = mqtt_max_backoff
        if access is None:
            from .access_api import AccessClient

            access = AccessClient(config.access)
        self.access: AccessLike = access
        self._mqtt_factory = mqtt_factory or self._default_mqtt_factory
        self._queue: deque[tuple[float, Publish]] = deque()
        self._queue_event = asyncio.Event()
        self._last_unlock = float("-inf")
        self.access_connected = False
        self.mqtt_connected = asyncio.Event()

    # ------------------------------------------------------------ factory

    def _default_mqtt_factory(self, will: Will) -> MqttLike:
        q = self.config.mqtt
        return MqttClient(
            q.host,
            q.port,
            client_id=q.client_id,
            username=q.username,
            password=q.password,
            keepalive=q.keepalive,
            will=will,
            ssl_context=_mqtt_ssl(self.config),
        )

    # -------------------------------------------------------------- queue

    def _enqueue(self, messages: list[Publish]) -> None:
        now = self._clock()
        for message in messages:
            if len(self._queue) >= _QUEUE_LIMIT:
                dropped = self._queue.popleft()
                _LOGGER.warning("Outgoing queue full, dropping %s", dropped[1].topic)
            self._queue.append((now, message))
        if messages:
            self._queue_event.set()

    def _is_stale(self, queued_at: float, message: Publish) -> bool:
        ring_topics = {self.topics.ring, self.topics.homebridge_doorbell}
        return (
            message.topic in ring_topics
            and self._clock() - queued_at > STALE_RING_SECONDS
        )

    # ------------------------------------------------------------ handlers

    async def _on_access_message(self, raw: str | bytes) -> None:
        self._enqueue(self.router.handle(parse_message(raw)))

    def _on_access_state(self, connected: bool) -> None:
        self.access_connected = connected

    async def _handle_command(self, client: MqttLike, message: Message) -> None:
        if message.topic != self.topics.unlock_command:
            return
        payload = message.payload.decode("utf-8", "replace").strip().upper()
        if not payload:
            return  # empty payload = a retained command being cleared
        if message.retain:
            # A retained command would otherwise open the door at every
            # reconnect: ignore it and delete it from the broker.
            _LOGGER.warning("Ignoring and clearing retained unlock command on %s", message.topic)
            await client.publish(message.topic, b"", qos=1, retain=True)
            return
        if payload not in UNLOCK_PAYLOADS:
            _LOGGER.warning("Ignoring unlock command with payload %r", payload[:20])
            return
        now = self._clock()
        if now - self._last_unlock < self.config.unlock.cooldown_seconds:
            _LOGGER.info("Unlock ignored: cooldown active")
            return
        self._last_unlock = now
        result: dict[str, Any] = {"time": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        try:
            await self.access.unlock(
                self.config.unlock.door_id,
                actor_id=f"family-router-{self.config.family.id}",
                actor_name=self.config.unlock.actor_name,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - report every failure
            _LOGGER.error("Unlock failed: %s", exc)
            result.update(ok=False, error=str(exc)[:200])
        else:
            _LOGGER.info("Door unlocked for family '%s'", self.config.family.name)
            result.update(ok=True)
        await client.publish(
            self.topics.unlock_result, json.dumps(result, sort_keys=True), qos=1
        )

    async def _log_doors(self) -> None:
        """Learn mode helper: print the door IDs (needed for unlock.door_id)."""

        list_doors = getattr(self.access, "list_doors", None)
        if list_doors is None:
            return
        try:
            doors = await list_doors()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - informational only
            _LOGGER.warning(
                "LEARN: cannot list doors (%s). The token needs 'view:space' for unlock.", exc
            )
            return
        for door in doors:
            _LOGGER.warning(
                "LEARN: door '%s' -> door_id %s",
                door.get("full_name") or door.get("name") or "?",
                door.get("id", "?"),
            )

    # --------------------------------------------------------------- loops

    async def _expiry_loop(self) -> None:
        while True:
            await asyncio.sleep(self._expiry_interval)
            self._enqueue(self.router.expire())

    async def _publisher(self, client: MqttLike) -> None:
        while True:
            while self._queue:
                queued_at, message = self._queue[0]
                if self._is_stale(queued_at, message):
                    self._queue.popleft()
                    _LOGGER.warning("Dropping stale ring message for %s", message.topic)
                    continue
                await client.publish(message.topic, message.payload, message.qos, message.retain)
                self._queue.popleft()
            self._queue_event.clear()
            await self._queue_event.wait()

    async def _commands(self, client: MqttLike) -> None:
        async for message in client.messages():
            await self._handle_command(client, message)

    async def _mqtt_session(self, client: MqttLike) -> None:
        await client.publish(self.topics.status, "online", qos=1, retain=True)
        for message in discovery_messages(self.config, self.topics):
            await client.publish(message.topic, message.payload, message.qos, message.retain)
        for message in self.router.reset_messages():
            await client.publish(message.topic, message.payload, message.qos, message.retain)
        if self.config.unlock.enabled:
            await client.subscribe(self.topics.unlock_command, qos=1)
        self.mqtt_connected.set()
        _LOGGER.info(
            "MQTT ready on %s:%s (topics under %s)",
            self.config.mqtt.host,
            self.config.mqtt.port,
            self.topics.base,
        )
        tasks = [
            asyncio.create_task(self._publisher(client), name="mqtt-publisher"),
            asyncio.create_task(self._commands(client), name="mqtt-commands"),
            asyncio.create_task(client.wait_closed(), name="mqtt-wait-closed"),
        ]
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()  # re-raise the reason
            raise ConnectionError("MQTT session ended")
        finally:
            await _cancel_and_wait(tasks)

    async def _mqtt_loop(self) -> None:
        delay = self._mqtt_initial_backoff
        will = Will(self.topics.status, b"offline", qos=1, retain=True)
        while True:
            client = self._mqtt_factory(will)
            started = self._clock()
            try:
                await client.connect()
                try:
                    await self._mqtt_session(client)
                finally:
                    self.mqtt_connected.clear()
            except asyncio.CancelledError:
                try:
                    await with_timeout(
                        client.publish(self.topics.status, "offline", qos=1, retain=True), 2.0
                    )
                except BaseException:  # noqa: BLE001 - best effort on shutdown
                    pass
                try:
                    await with_timeout(client.disconnect(), 2.0)
                except BaseException:  # noqa: BLE001
                    pass
                raise
            except Exception as exc:  # noqa: BLE001 - reconnect on any failure
                _LOGGER.warning(
                    "MQTT connection to %s:%s failed: %s",
                    self.config.mqtt.host,
                    self.config.mqtt.port,
                    exc or exc.__class__.__name__,
                )
                try:
                    await client.disconnect()
                except Exception:  # noqa: BLE001
                    pass
            if self._clock() - started > 60:
                delay = self._mqtt_initial_backoff
            await asyncio.sleep(delay)
            delay = min(delay * 2, self._mqtt_max_backoff)

    # ---------------------------------------------------------------- run

    async def run(self) -> None:
        """Run until cancelled."""

        _LOGGER.info(
            "Starting for family '%s' (%s)%s",
            self.config.family.name,
            self.config.family.id,
            " in LEARN MODE" if self.config.learn_mode else "",
        )
        tasks = [
            asyncio.create_task(self._mqtt_loop(), name="mqtt-loop"),
            asyncio.create_task(
                self.access.listen(self._on_access_message, self._on_access_state),
                name="access-listen",
            ),
            asyncio.create_task(self._expiry_loop(), name="expiry"),
        ]
        helpers = []
        if self.config.learn_mode:
            helpers.append(asyncio.create_task(self._log_doors(), name="learn-doors"))
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
            raise RuntimeError("a service task stopped unexpectedly")
        finally:
            await _cancel_and_wait(tasks + helpers, timeout=10.0)
