"""Command line entry point.

Examples::

    python -m family_router --config config.yaml           # run
    python -m family_router --config config.yaml --learn   # discover room_ids
    python -m family_router --config config.yaml --check   # test token + list doors
    python -m family_router --fingerprint 192.168.1.1      # print TLS pin
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
import sys
from typing import Sequence

from . import __version__
from .config import Config, ConfigError, load_config
from .tlsutil import fetch_fingerprint, format_fingerprint

_LOGGER = logging.getLogger("family_router")


def _setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
        force=True,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="family_router",
        description="Ring only the called family on a UniFi Access intercom.",
    )
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--config", help="YAML or JSON configuration file")
    source.add_argument("--options", help="Home Assistant add-on options.json")
    parser.add_argument("--learn", action="store_true", help="force learn mode")
    parser.add_argument(
        "--check", action="store_true", help="validate config, test the token, list doors"
    )
    parser.add_argument("--list-doors", action="store_true", help="list doors and their IDs")
    parser.add_argument(
        "--fingerprint", metavar="HOST[:PORT]", help="print the console TLS fingerprint"
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def _split_host_port(value: str, default_port: int = 12445) -> tuple[str, int]:
    if value.startswith("["):
        host, _, rest = value[1:].partition("]")
        return host, int(rest[1:]) if rest.startswith(":") else default_port
    if value.count(":") == 1:
        host, port = value.split(":")
        return host, int(port)
    return value, default_port


async def _list_doors(config: Config) -> int:
    from .access_api import AccessApiError, AccessClient

    client = AccessClient(config.access)
    try:
        doors = await client.list_doors()
    except AccessApiError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        if exc.status in (401, 403):
            print("Hint: the token needs the 'view:space' permission.", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"ERROR: cannot reach {config.access.host}:{config.access.port}: {exc}", file=sys.stderr)
        return 2
    if not doors:
        print("No doors found.")
        return 0
    print(f"{'DOOR ID':38}  {'NAME':30}  HUB")
    for door in doors:
        print(
            f"{str(door.get('id', '')):38}  {str(door.get('full_name') or door.get('name', '')):30}  "
            f"{'yes' if door.get('is_bind_hub') else 'no'}"
        )
    print()
    print("For 'unlock.doors' (rename freely, one line per door):")
    for door in doors:
        name = str(door.get("name") or door.get("full_name") or "Door").strip()
        print(f'  - "{name}={door.get("id", "")}"')
    return 0


async def _check(config: Config) -> int:
    """Validate the token against the WebSocket (view:device) and doors API."""

    from .access_api import AccessClient
    from .websocket import WebSocketError, WebSocketHandshakeError

    print(f"Configuration OK for family '{config.family.name}' ({config.family.id}).")
    client = AccessClient(config.access)
    try:
        ws = await client.connect_notifications()
    except WebSocketHandshakeError as exc:
        print(f"FAIL notifications (view:device): HTTP {exc.status}", file=sys.stderr)
        return 2
    except (OSError, WebSocketError, asyncio.TimeoutError) as exc:
        print(f"FAIL notifications: {exc or exc.__class__.__name__}", file=sys.stderr)
        return 2
    await ws.close(timeout=1.0)
    print("OK   notifications WebSocket (view:device)")
    result = await _list_doors(config)
    if result != 0 and not config.unlock.enabled:
        print("Note: doors are only needed when 'unlock.enabled' is true.")
        return 0
    if config.unlock.enabled and result == 0:
        for door in config.unlock.doors:
            print(f"Unlock is enabled for {door.name or 'the door'} (door_id {door.door_id}).")
    return result


async def _run(config: Config) -> int:
    from .service import Service

    service = Service(config)
    task = asyncio.create_task(service.run())
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, task.cancel)
        except (NotImplementedError, RuntimeError):  # pragma: no cover - Windows
            pass
    try:
        await task
    except asyncio.CancelledError:
        _LOGGER.info("Stopped")
        return 0
    return 1


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)

    if args.fingerprint:
        _setup_logging("info")
        host, port = _split_host_port(args.fingerprint)
        try:
            digest = fetch_fingerprint(host, port)
        except OSError as exc:
            print(f"ERROR: cannot read the certificate of {host}:{port}: {exc}", file=sys.stderr)
            return 2
        print(f"SHA-256 fingerprint of {host}:{port}:")
        print(format_fingerprint(digest))
        print("Put it in 'access.tls_fingerprint' after checking it on the console.")
        return 0

    path = args.config or args.options or os.environ.get("FAMILY_ROUTER_CONFIG", "")
    if not path:
        for candidate in ("/data/options.json", "/config/config.yaml", "config.yaml"):
            if os.path.isfile(candidate):
                path = candidate
                break
    if not path:
        print(
            "ERROR: no configuration file (use --config)\n"
            "ERRORE: nessun file di configurazione (usa --config)",
            file=sys.stderr,
        )
        return 2
    try:
        config = load_config(path, force_learn=args.learn)
    except ConfigError as exc:
        print(
            f"CONFIGURATION ERROR / ERRORE DI CONFIGURAZIONE: {exc}\n"
            "Fix the option above and restart. / Correggi l'opzione indicata e riavvia.",
            file=sys.stderr,
        )
        return 2

    _setup_logging(config.log_level)
    _LOGGER.info("unifi-access-family-router %s", __version__)

    if args.check:
        return asyncio.run(_check(config))
    if args.list_doors:
        return asyncio.run(_list_doors(config))

    return asyncio.run(_run(config))


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
