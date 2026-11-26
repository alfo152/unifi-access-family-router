"""Configuration loading and validation.

The same nested structure is used for the standalone YAML file and for the
Home Assistant add-on options (``/data/options.json``).
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

__all__ = [
    "AccessConfig",
    "Config",
    "ConfigError",
    "FamilyConfig",
    "HomebridgeConfig",
    "MatchConfig",
    "MqttConfig",
    "UnlockConfig",
    "load_config",
    "parse_config",
]

_SLUG_RE = re.compile(r"^[a-z0-9_]{1,40}$")
_HEX_RE = re.compile(r"^[0-9A-F]+$")
_TOPIC_FORBIDDEN = ("+", "#", "\x00")
_LOG_LEVELS = ("debug", "info", "warning", "error")


class ConfigError(ValueError):
    """Raised when the configuration is missing or invalid."""


@dataclass(frozen=True)
class AccessConfig:
    """Connection to the UniFi Access API on the console (port 12445)."""

    host: str
    token: str
    port: int = 12445
    tls_fingerprint: str = ""
    verify_ssl: bool = False
    ca_file: str = ""


@dataclass(frozen=True)
class MatchConfig:
    """How a ring event is recognised as being for this family."""

    room_ids: tuple[str, ...] = ()
    door_guard_ids: tuple[str, ...] = ()
    device_ids: tuple[str, ...] = ()
    ring_all: bool = False

    @property
    def is_empty(self) -> bool:
        return not (self.ring_all or self.room_ids or self.door_guard_ids)


@dataclass(frozen=True)
class FamilyConfig:
    id: str
    name: str
    match: MatchConfig = field(default_factory=MatchConfig)


@dataclass(frozen=True)
class MqttConfig:
    host: str
    port: int = 1883
    username: str = ""
    password: str = ""
    tls: bool = False
    tls_insecure: bool = False
    base_topic: str = "access_family_router"
    discovery: bool = True
    discovery_prefix: str = "homeassistant"
    client_id: str = ""
    keepalive: int = 30


@dataclass(frozen=True)
class HomebridgeConfig:
    enabled: bool = False
    topic_prefix: str = "unifi/protect"
    camera_mac: str = ""


@dataclass(frozen=True)
class UnlockConfig:
    enabled: bool = False
    door_id: str = ""
    actor_name: str = ""
    cooldown_seconds: float = 3.0


@dataclass(frozen=True)
class Config:
    access: AccessConfig
    family: FamilyConfig
    mqtt: MqttConfig
    homebridge: HomebridgeConfig = field(default_factory=HomebridgeConfig)
    unlock: UnlockConfig = field(default_factory=UnlockConfig)
    learn_mode: bool = False
    call_timeout_seconds: float = 60.0
    log_level: str = "info"


# --------------------------------------------------------------------------
# helpers


def _section(raw: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    value = raw.get(name)
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ConfigError(f"'{name}' must be a mapping")
    return value


def _str(section: Mapping[str, Any], key: str, where: str, default: str = "") -> str:
    value = section.get(key, default)
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise ConfigError(f"'{where}.{key}' must be a string")
    return str(value).strip()


def _bool(section: Mapping[str, Any], key: str, where: str, default: bool) -> bool:
    value = section.get(key, default)
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in ("true", "yes", "on", "1"):
        return True
    if isinstance(value, str) and value.strip().lower() in ("false", "no", "off", "0", ""):
        return False
    raise ConfigError(f"'{where}.{key}' must be true or false")


def _int(
    section: Mapping[str, Any], key: str, where: str, default: int, lo: int, hi: int
) -> int:
    value = section.get(key, default)
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        raise ConfigError(f"'{where}.{key}' must be a whole number")
    try:
        number = int(str(value).strip())
    except ValueError as exc:
        raise ConfigError(f"'{where}.{key}' must be a whole number") from exc
    if not lo <= number <= hi:
        raise ConfigError(f"'{where}.{key}' must be between {lo} and {hi}")
    return number


def _float(
    section: Mapping[str, Any],
    key: str,
    where: str,
    default: float,
    lo: float,
    hi: float,
) -> float:
    value = section.get(key, default)
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        raise ConfigError(f"'{where}.{key}' must be a number")
    try:
        number = float(str(value).strip())
    except ValueError as exc:
        raise ConfigError(f"'{where}.{key}' must be a number") from exc
    if not lo <= number <= hi:
        raise ConfigError(f"'{where}.{key}' must be between {lo:g} and {hi:g}")
    return number


def _str_list(section: Mapping[str, Any], key: str, where: str) -> tuple[str, ...]:
    value = section.get(key)
    if value is None or value == "":
        return ()
    if isinstance(value, str):
        items = [part for part in re.split(r"[,\s]+", value) if part]
    elif isinstance(value, (list, tuple)):
        items = []
        for item in value:
            if isinstance(item, bool) or not isinstance(item, (str, int)):
                raise ConfigError(f"'{where}.{key}' must be a list of strings")
            text = str(item).strip()
            if text:
                items.append(text)
    else:
        raise ConfigError(f"'{where}.{key}' must be a list of strings")
    unique: list[str] = []
    for item in items:
        if item not in unique:
            unique.append(item)
    return tuple(unique)


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    return slug[:40]


def _check_topic(value: str, where: str) -> str:
    value = value.strip().strip("/")
    if not value:
        raise ConfigError(f"'{where}' must not be empty")
    if any(ch in value for ch in _TOPIC_FORBIDDEN):
        raise ConfigError(f"'{where}' must not contain '+', '#' or NUL")
    if "//" in value:
        raise ConfigError(f"'{where}' must not contain empty levels ('//')")
    return value


def normalize_mac(value: str) -> str:
    """Return a MAC address as 12 upper-case hex characters (HBUP format)."""

    cleaned = re.sub(r"[\s:\-.]", "", value).upper()
    if len(cleaned) != 12 or not _HEX_RE.match(cleaned):
        raise ConfigError(
            "'homebridge.camera_mac' must be a MAC address such as "
            "AA:BB:CC:DD:EE:FF (12 hex characters)"
        )
    return cleaned


def normalize_fingerprint(value: str) -> str:
    """Return a SHA-256 certificate fingerprint as 64 upper-case hex chars."""

    if not value:
        return ""
    # accepts "AB:CD:..", "abcd..", "SHA256=AB:.." and the OpenSSL output
    # "sha256 Fingerprint=AB:CD:.."
    if "=" in value:
        value = value.rsplit("=", 1)[1]
    cleaned = re.sub(r"[\s:]", "", value).upper()
    if len(cleaned) != 64 or not _HEX_RE.match(cleaned):
        raise ConfigError(
            "'access.tls_fingerprint' must be a SHA-256 fingerprint "
            "(64 hex characters, colons allowed)"
        )
    return cleaned


# --------------------------------------------------------------------------
# parsing


def parse_config(
    raw: Mapping[str, Any], env: Mapping[str, str] | None = None
) -> Config:
    """Validate a raw mapping and build a :class:`Config`.

    ``env`` provides optional overrides (``FAMILY_ROUTER_ACCESS_TOKEN`` and,
    when ``mqtt.host`` is empty, ``MQTT_HOST``/``MQTT_PORT``/``MQTT_USERNAME``/
    ``MQTT_PASSWORD`` as provided by the Home Assistant Supervisor).
    """

    if not isinstance(raw, Mapping):
        raise ConfigError("the configuration must be a mapping")
    env = env if env is not None else {}

    learn_mode = _bool(raw, "learn_mode", "root", False)
    log_level = _str(raw, "log_level", "root", "info").lower()
    if log_level not in _LOG_LEVELS:
        raise ConfigError(f"'log_level' must be one of: {', '.join(_LOG_LEVELS)}")
    call_timeout = _float(raw, "call_timeout_seconds", "root", 60.0, 5.0, 600.0)

    # access ---------------------------------------------------------------
    a = _section(raw, "access")
    host = _str(a, "host", "access")
    if not host:
        raise ConfigError("'access.host' is required (IP or name of the UniFi console)")
    if "/" in host or " " in host:
        raise ConfigError("'access.host' must be a host name or IP, without 'https://'")
    token = env.get("FAMILY_ROUTER_ACCESS_TOKEN", "").strip() or _str(a, "token", "access")
    if not token:
        raise ConfigError("'access.token' is required (UniFi Access API token)")
    access = AccessConfig(
        host=host,
        token=token,
        port=_int(a, "port", "access", 12445, 1, 65535),
        tls_fingerprint=normalize_fingerprint(_str(a, "tls_fingerprint", "access")),
        verify_ssl=_bool(a, "verify_ssl", "access", False),
        ca_file=_str(a, "ca_file", "access"),
    )
    if access.ca_file and not Path(access.ca_file).is_file():
        raise ConfigError(f"'access.ca_file' not found: {access.ca_file}")

    # family ---------------------------------------------------------------
    f = _section(raw, "family")
    name = _str(f, "name", "family")
    family_id = _str(f, "id", "family") or _slugify(name)
    if not name and family_id:
        name = family_id
    if not family_id:
        raise ConfigError("'family.name' (or 'family.id') is required")
    if not _SLUG_RE.match(family_id):
        raise ConfigError(
            "'family.id' may only contain lower-case letters, digits and '_' "
            "(max 40 characters)"
        )
    # Match rules may be nested under "family.match" (YAML) or placed directly
    # under "family" (Home Assistant add-on options, one nesting level only).
    flat = {k: f[k] for k in ("room_ids", "door_guard_ids", "device_ids", "ring_all") if k in f}
    m = {**flat, **_section(f, "match")}
    match = MatchConfig(
        room_ids=_str_list(m, "room_ids", "family.match"),
        door_guard_ids=_str_list(m, "door_guard_ids", "family.match"),
        device_ids=_str_list(m, "device_ids", "family.match"),
        ring_all=_bool(m, "ring_all", "family.match", False),
    )
    if match.is_empty and not learn_mode:
        raise ConfigError(
            "no match rule configured for this family: set 'family.match.room_ids' "
            "(run once with 'learn_mode: true' to discover them) or set "
            "'family.match.ring_all: true' for a single-family building"
        )
    family = FamilyConfig(id=family_id, name=name, match=match)

    # mqtt -----------------------------------------------------------------
    q = _section(raw, "mqtt")
    mqtt_host = _str(q, "host", "mqtt")
    mqtt_port = _int(q, "port", "mqtt", 1883, 1, 65535)
    mqtt_user = _str(q, "username", "mqtt")
    mqtt_pass = _str(q, "password", "mqtt")
    if not mqtt_host and env.get("MQTT_HOST"):
        mqtt_host = env["MQTT_HOST"].strip()
        mqtt_port = _int(env, "MQTT_PORT", "env", 1883, 1, 65535)
        mqtt_user = env.get("MQTT_USERNAME", "").strip()
        mqtt_pass = env.get("MQTT_PASSWORD", "").strip()
    if not mqtt_host:
        raise ConfigError("'mqtt.host' is required (the family's own MQTT broker)")
    mqtt = MqttConfig(
        host=mqtt_host,
        port=mqtt_port,
        username=mqtt_user,
        password=mqtt_pass,
        tls=_bool(q, "tls", "mqtt", False),
        tls_insecure=_bool(q, "tls_insecure", "mqtt", False),
        base_topic=_check_topic(
            _str(q, "base_topic", "mqtt", "access_family_router"), "mqtt.base_topic"
        ),
        discovery=_bool(q, "discovery", "mqtt", True),
        discovery_prefix=_check_topic(
            _str(q, "discovery_prefix", "mqtt", "homeassistant"), "mqtt.discovery_prefix"
        ),
        client_id=_str(q, "client_id", "mqtt") or f"access-family-router-{family_id}",
        keepalive=_int(q, "keepalive", "mqtt", 30, 5, 600),
    )
    if mqtt.password and not mqtt.username:
        raise ConfigError("'mqtt.password' is set but 'mqtt.username' is empty")
    if len(mqtt.client_id.encode("utf-8")) > 65535:
        raise ConfigError("'mqtt.client_id' is too long")

    # homebridge -----------------------------------------------------------
    h = _section(raw, "homebridge")
    hb_enabled = _bool(h, "enabled", "homebridge", False)
    hb_mac = _str(h, "camera_mac", "homebridge")
    homebridge = HomebridgeConfig(
        enabled=hb_enabled,
        topic_prefix=_check_topic(
            _str(h, "topic_prefix", "homebridge", "unifi/protect"), "homebridge.topic_prefix"
        ),
        camera_mac=normalize_mac(hb_mac) if (hb_enabled or hb_mac) else "",
    )

    # unlock ---------------------------------------------------------------
    u = _section(raw, "unlock")
    unlock = UnlockConfig(
        enabled=_bool(u, "enabled", "unlock", False),
        door_id=_str(u, "door_id", "unlock"),
        actor_name=_str(u, "actor_name", "unlock") or f"{name} (family router)",
        cooldown_seconds=_float(u, "cooldown_seconds", "unlock", 3.0, 0.0, 60.0),
    )
    if unlock.enabled and not unlock.door_id:
        raise ConfigError(
            "'unlock.door_id' is required when unlock is enabled "
            "(run 'python -m family_router --list-doors' to find it)"
        )

    return Config(
        access=access,
        family=family,
        mqtt=mqtt,
        homebridge=homebridge,
        unlock=unlock,
        learn_mode=learn_mode,
        call_timeout_seconds=call_timeout,
        log_level=log_level,
    )


def load_config(
    path: str | os.PathLike[str],
    env: Mapping[str, str] | None = None,
    *,
    force_learn: bool = False,
) -> Config:
    """Load a YAML (``.yaml``/``.yml``) or JSON configuration file.

    ``force_learn`` turns learn mode on regardless of the file content.
    """

    file = Path(path)
    if not file.is_file():
        raise ConfigError(f"configuration file not found: {file}")
    text = file.read_text(encoding="utf-8")
    if file.suffix.lower() == ".json":
        try:
            raw = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ConfigError(f"invalid JSON in {file}: {exc}") from exc
    else:
        try:
            import yaml
        except ImportError as exc:  # pragma: no cover - depends on image
            raise ConfigError(
                "PyYAML is required for YAML files; install it or use JSON"
            ) from exc
        try:
            raw = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            raise ConfigError(f"invalid YAML in {file}: {exc}") from exc
    if raw is None:
        raise ConfigError(f"configuration file is empty: {file}")
    if not isinstance(raw, Mapping):
        raise ConfigError(f"the configuration in {file} must be a mapping")
    if force_learn:
        raw = {**raw, "learn_mode": True}
    return parse_config(raw, env if env is not None else os.environ)
