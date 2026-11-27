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
from .config import Config, DoorConfig
from .discovery import discovery_filter, discovery_messages
from .events import parse_message
from .mqtt import Message, MqttClient, Will
from .router import FamilyRouter, Publish

__all__ = ["Service"]

_LOGGER = logging.getLogger(__name__)

#: ring triggers older than this are not delivered after an MQTT outage
STALE_RING_SECONDS = 15.0
UNLOCK_PAYLOADS = frozenset({"PRESS", "UNLOCK"})
LOCK_PAYLOAD = "LOCK"
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
        self._doors: dict[str, DoorConfig] = (
            {self.topics.door_command(door): door for door in config.unlock.doors}
            if config.unlock.enabled
            else {}
        )
        self._last_unlock: dict[str, float] = {}
        self._relock_tasks: dict[str, "asyncio.Task[None]"] = {}
        self._discovery_filter = discovery_filter(config) if config.mqtt.discovery else ""
        self._discovery_topics: frozenset[str] = frozenset()
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
        if self._discovery_filter and self._is_discovery_topic(message.topic):
            await self._clean_discovery(client, message)
            return
        door = self._doors.get(message.topic)
        if door is None:
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
        label = door.name or "door"
        if payload == LOCK_PAYLOAD:
            # Access doors relock by themselves: just confirm the state.
            _LOGGER.info("Lock request for %s: the door relocks by itself", label)
            await client.publish(self.topics.door_state(door), "LOCKED", qos=1, retain=True)
            return
        if payload not in UNLOCK_PAYLOADS:
            _LOGGER.warning("Ignoring unlock command with payload %r", payload[:20])
            return
        now = self._clock()
        if now - self._last_unlock.get(door.key, float("-inf")) < self.config.unlock.cooldown_seconds:
            _LOGGER.info("Unlock of %s ignored: cooldown active", label)
            return
        self._last_unlock[door.key] = now
        result: dict[str, Any] = {
            "time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "door": door.name,
        }
        try:
            await self.access.unlock(
                door.door_id,
                actor_id=f"family-router-{self.config.family.id}",
                actor_name=self.config.unlock.actor_name,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - report every failure
            _LOGGER.error("Unlock of %s failed: %s", label, exc)
            result.update(ok=False, error=str(exc)[:200])
            state = "JAMMED"
        else:
            _LOGGER.info("%s unlocked for family '%s'", label.capitalize(), self.config.family.name)
            result.update(ok=True)
            state = "UNLOCKED"
        await client.publish(
            self.topics.door_result(door), json.dumps(result, sort_keys=True), qos=1
        )
        await client.publish(self.topics.door_state(door), state, qos=1, retain=True)
        previous = self._relock_tasks.pop(door.key, None)
        if previous is not None:
            previous.cancel()
        self._relock_tasks[door.key] = asyncio.create_task(
            self._relock(client, door), name=f"relock-{door.key or 'door'}"
        )

    async def _relock(self, client: MqttLike, door: DoorConfig) -> None:
        """Show the lock entity as locked again once the door has relocked."""

        await asyncio.sleep(self.config.unlock.relock_seconds)
        try:
            await client.publish(self.topics.door_state(door), "LOCKED", qos=1, retain=True)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - the next session republishes it
            _LOGGER.debug("Cannot publish the relocked state: %s", exc)

    def _is_discovery_topic(self, topic: str) -> bool:
        parts = topic.split("/")
        prefix = self.config.mqtt.discovery_prefix.split("/")
        return (
            len(parts) == len(prefix) + 4
            and parts[: len(prefix)] == prefix
            and parts[len(prefix) + 1] == f"access_family_router_{self.config.family.id}"
            and parts[-1] == "config"
        )

    async def _clean_discovery(self, client: MqttLike, message: Message) -> None:
        """Remove entities this family published earlier but no longer has.

        Renaming or removing a door would otherwise leave an "unavailable"
        entity behind in Home Assistant.
        """

        if not message.payload or message.topic in self._discovery_topics:
            return
        _LOGGER.info("Removing an entity that is no longer configured: %s", message.topic)
        await client.publish(message.topic, b"", qos=1, retain=True)

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
            name = door.get("full_name") or door.get("name") or "?"
            short = str(door.get("name") or name).strip()
            _LOGGER.warning(
                "LEARN: door '%s' -> door_id %s (add-on: unlock.doors \"%s=%s\")",
                name,
                door.get("id", "?"),
                short,
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
        discovery = discovery_messages(self.config, self.topics)
        self._discovery_topics = frozenset(m.topic for m in discovery if m.payload)
        for message in discovery:
            await client.publish(message.topic, message.payload, message.qos, message.retain)
        for message in self.router.reset_messages():
            await client.publish(message.topic, message.payload, message.qos, message.retain)
        for door in self._doors.values():
            await client.publish(self.topics.door_state(door), "LOCKED", qos=1, retain=True)
        for topic in self._doors:
            await client.subscribe(topic, qos=1)
        if self._discovery_filter:
            # retained discovery configs arrive right away: stale ones are removed
            await client.subscribe(self._discovery_filter, qos=1)
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
            relocks = list(self._relock_tasks.values())
            self._relock_tasks.clear()
            await _cancel_and_wait(tasks + relocks)

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
