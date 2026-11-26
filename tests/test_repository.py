"""Consistency checks of the files shipped in the repository."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import unittest
from typing import Any

import yaml

from family_router import __version__
from family_router.config import parse_config

from .support import ROOT

ADDON = ROOT / "family_router"


def schema_keys(node: Any, prefix: str = "") -> set[str]:
    keys: set[str] = set()
    if isinstance(node, dict):
        for key, value in node.items():
            path = f"{prefix}.{key}" if prefix else key
            keys.add(path)
            keys |= schema_keys(value, path)
    return keys


class RepositoryTests(unittest.TestCase):
    def test_versions_match(self) -> None:
        addon = yaml.safe_load((ADDON / "config.yaml").read_text())
        self.assertEqual(addon["version"], __version__)
        pyproject = (ROOT / "pyproject.toml").read_text()
        self.assertIn(f'version = "{__version__}"', pyproject)
        self.assertIn(f"## {__version__}", (ROOT / "CHANGELOG.md").read_text())

    def test_addon_options_match_schema(self) -> None:
        addon = yaml.safe_load((ADDON / "config.yaml").read_text())
        self.assertEqual(schema_keys(addon["options"]), schema_keys(addon["schema"]))
        self.assertRegex(addon["slug"], r"^[a-z0-9_]+$")
        build = yaml.safe_load((ADDON / "build.yaml").read_text())
        self.assertEqual(sorted(build["build_from"]), sorted(addon["arch"]))
        self.assertIn("mqtt:want", addon["services"])
        self.assertFalse(addon["init"])

    def test_addon_defaults_are_valid_once_filled(self) -> None:
        """The add-on defaults + the 3 fields a user must fill = valid config."""

        addon = yaml.safe_load((ADDON / "config.yaml").read_text())
        options = json.loads(json.dumps(addon["options"]))
        options["access"].update(host="192.168.50.1", token="t")
        options["family"]["name"] = "Famiglia Rossi"
        env = {"MQTT_HOST": "core-mosquitto", "MQTT_PORT": "1883",
               "MQTT_USERNAME": "addons", "MQTT_PASSWORD": "pw"}
        cfg = parse_config(options, env=env)
        self.assertTrue(cfg.learn_mode)
        self.assertEqual(cfg.family.id, "famiglia_rossi")
        self.assertEqual(cfg.mqtt.host, "core-mosquitto")
        # after learn mode
        options["learn_mode"] = False
        options["family"]["room_ids"] = ["WR-x"]
        options["homebridge"].update(enabled=True, camera_mac="aa:bb:cc:dd:ee:ff")
        options["unlock"].update(enabled=True, door_id="d")
        cfg = parse_config(options, env=env)
        self.assertEqual(cfg.family.match.room_ids, ("WR-x",))
        self.assertEqual(cfg.homebridge.camera_mac, "AABBCCDDEEFF")

    def test_translations_cover_top_level_options(self) -> None:
        addon = yaml.safe_load((ADDON / "config.yaml").read_text())
        for lang in ("en", "it"):
            data = yaml.safe_load((ADDON / "translations" / f"{lang}.yaml").read_text())
            self.assertEqual(set(data["configuration"]), set(addon["options"]), lang)

    def test_run_script(self) -> None:
        run = ADDON / "run.sh"
        self.assertTrue(os.access(run, os.X_OK), "run.sh must be executable")
        subprocess.run(["bash", "-n", str(run)], check=True)
        self.assertIn("--options", run.read_text())
        if shutil.which("shellcheck"):
            subprocess.run(["shellcheck", str(run)], check=True)

    def test_dockerfiles_copy_existing_paths(self) -> None:
        for dockerfile, context in ((ROOT / "Dockerfile", ROOT), (ADDON / "Dockerfile", ADDON)):
            for match in re.finditer(r"^COPY\s+(\S+)\s+", dockerfile.read_text(), re.M):
                self.assertTrue((context / match.group(1)).exists(), f"{dockerfile}: {match.group(1)}")

    def test_stack_files(self) -> None:
        compose = yaml.safe_load((ROOT / "stack" / "docker-compose.yml").read_text())
        self.assertEqual(set(compose["services"]), {"mosquitto", "router", "homebridge"})
        self.assertEqual(compose["services"]["homebridge"]["network_mode"], "host")
        conf = (ROOT / "stack" / "mosquitto" / "mosquitto.conf").read_text()
        self.assertIn("allow_anonymous false", conf)
        platform = json.loads(
            (ROOT / "stack" / "homebridge" / "unifi-protect-platform.example.json").read_text()
        )
        self.assertEqual(platform["platform"], "UniFi Protect")
        self.assertEqual(platform["controllers"][0]["mqttTopic"], "unifi/protect")

    def test_examples_parse(self) -> None:
        for path in (ROOT / "examples").glob("*.yaml"):
            self.assertIsNotNone(yaml.safe_load(path.read_text()), path.name)

    def test_markdown_links(self) -> None:
        for md in list(ROOT.glob("*.md")) + list((ROOT / "docs").glob("*.md")) + [ADDON / "DOCS.md"]:
            for target in re.findall(r"\]\(([^)#\s]+)(?:#[^)]*)?\)", md.read_text()):
                if target.startswith(("http://", "https://", "mailto:")):
                    continue
                self.assertTrue((md.parent / target).exists(), f"{md.name}: broken link {target}")

    def test_no_secrets_tracked(self) -> None:
        if not shutil.which("git"):
            self.skipTest("git not available")
        ignore = (ROOT / ".gitignore").read_text()
        for pattern in ("stack/router/config.yaml", "stack/mosquitto/passwd"):
            self.assertIn(pattern, ignore)


if __name__ == "__main__":
    unittest.main()
