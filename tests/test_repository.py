"""Consistency checks of the files shipped in the repository."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import unicodedata
import unittest
from typing import Any

import yaml

from family_router import __version__
from family_router.config import parse_config
from family_router.discovery import discovery_messages
from family_router.router import Topics

from .support import ROOT

ADDON = ROOT / "family_router"
PACKAGE = ROOT / "examples" / "packages" / "intercom_family.yaml"
ENTITY_RE = re.compile(
    r"\b(?:event|button|lock|binary_sensor|sensor)\.intercom_famiglia_rossi_[a-z0-9_]+"
)


def ha_slug(text: str) -> str:
    """Home Assistant's slugify, enough for the names used in the examples."""

    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def expected_entity_ids(unlock: dict[str, Any]) -> set[str]:
    """Entity IDs Home Assistant derives from our discovery for Famiglia Rossi."""

    cfg = parse_config(
        {
            "access": {"host": "192.168.50.1", "token": "t"},
            "family": {"name": "Famiglia Rossi"},
            "mqtt": {"host": "127.0.0.1"},
            "unlock": {"enabled": True, **unlock},
            "learn_mode": True,
        },
        env={},
    )
    ids = set()
    for message in discovery_messages(cfg, Topics(cfg)):
        if not message.payload:
            continue
        body = json.loads(message.payload)
        component = message.topic.split("/")[1]
        ids.add(f"{component}.{ha_slug(body['device']['name'] + ' ' + body['name'])}")
    return ids


ALL_ENTITY_IDS = expected_entity_ids(
    {"doors": ["Portone=d-1", "Cancello=d-2"]}
) | expected_entity_ids({"door_id": "d-9"})


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

    def test_addon_doors_option(self) -> None:
        """Doors typed in the add-on UI ("Name=door_id" lines) parse as expected."""

        addon = yaml.safe_load((ADDON / "config.yaml").read_text())
        self.assertEqual(addon["schema"]["unlock"]["doors"], ["str"])
        options = json.loads(json.dumps(addon["options"]))
        options["access"].update(host="192.168.50.1", token="t")
        options["family"]["name"] = "Famiglia Rossi"
        options["unlock"].update(enabled=True, doors=["Portone=d-1", "Cancello carrabile=d-2"])
        cfg = parse_config(options, env={"MQTT_HOST": "core-mosquitto"})
        self.assertEqual([d.key for d in cfg.unlock.doors], ["portone", "cancello_carrabile"])
        self.assertEqual(cfg.unlock.relock_seconds, 5.0)
        # the old single-door field alone still works
        options["unlock"].update(doors=[], door_id="d-9")
        self.assertEqual(parse_config(options, env={"MQTT_HOST": "x"}).unlock.door_id, "d-9")

    def test_example_configs_parse(self) -> None:
        for name in ("config.example.yaml", "stack/router/config.yaml.example"):
            raw = yaml.safe_load((ROOT / name).read_text())
            raw["unlock"]["enabled"] = False
            raw["access"]["token"] = raw["access"].get("token") or "t"
            raw["access"]["host"] = raw["access"].get("host") or "192.168.50.1"
            raw["mqtt"]["host"] = raw["mqtt"].get("host") or "127.0.0.1"
            raw["learn_mode"] = True
            with self.subTest(name=name):
                try:
                    parse_config(raw, env={})
                except Exception as exc:  # pragma: no cover - reported below
                    self.fail(f"{name}: {exc}")

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
        paths = list((ROOT / "examples").rglob("*.yaml"))
        self.assertIn(PACKAGE, paths)
        for path in paths:
            self.assertIsNotNone(yaml.safe_load(path.read_text()), path.name)

    def test_entity_ids_in_docs_and_examples_exist(self) -> None:
        """Every intercom entity ID quoted in docs/examples is one we create."""

        self.assertIn("lock.intercom_famiglia_rossi_portone", ALL_ENTITY_IDS)
        self.assertIn("button.intercom_famiglia_rossi_unlock_cancello", ALL_ENTITY_IDS)
        self.assertIn("lock.intercom_famiglia_rossi_door", ALL_ENTITY_IDS)
        files = (
            list(ROOT.glob("*.md"))
            + list((ROOT / "docs").rglob("*.md"))
            + list((ROOT / "examples").rglob("*.yaml"))
            + [ADDON / "DOCS.md"]
        )
        found = 0
        for path in files:
            for entity_id in ENTITY_RE.findall(path.read_text()):
                found += 1
                self.assertIn(entity_id, ALL_ENTITY_IDS, f"{path.relative_to(ROOT)}: {entity_id}")
        self.assertGreater(found, 10)

    def test_docs_are_bilingual(self) -> None:
        """Every guide exists in English and Italian and links to the other."""

        docs = ROOT / "docs"
        english = {p.name for p in docs.glob("*.md")}
        italian = {p.name for p in (docs / "it").glob("*.md")}
        self.assertEqual(english, italian)
        for name in english:
            self.assertIn(f"(it/{name})", (docs / name).read_text(), name)
            self.assertIn(f"(../{name})", (docs / "it" / name).read_text(), f"it/{name}")
        self.assertIn("(LEGGIMI.md)", (ROOT / "README.md").read_text())
        self.assertIn("(README.md)", (ROOT / "LEGGIMI.md").read_text())
        self.assertIn("# 🇮🇹 Italiano", (ADDON / "DOCS.md").read_text())

    def test_homekit_package(self) -> None:
        """The HomeKit Bridge package is a valid Home Assistant 'homekit' config."""

        package = yaml.safe_load(PACKAGE.read_text())
        self.assertEqual(set(package), {"homekit"})
        instances = package["homekit"]
        self.assertIsInstance(instances, list)
        ports = [item["port"] for item in instances]
        names = [item["name"] for item in instances]
        self.assertEqual(len(ports), len(set(ports)), "each instance needs its own port")
        self.assertEqual(len(names), len(set(names)), "each instance needs its own name")
        self.assertNotIn(21063, ports, "21063 is Home Assistant's default instance")
        modes = {}
        for item in instances:
            self.assertLessEqual(set(item), {"name", "port", "mode", "filter", "entity_config"})
            self.assertIn(item["mode"], ("bridge", "accessory"))
            self.assertTrue(3 <= len(item["name"]) <= 25, "HomeKit names are 3-25 characters")
            self.assertTrue(1024 < item["port"] < 65536)
            included = item["filter"]["include_entities"]
            self.assertEqual(len(included), len(set(included)))
            if item["mode"] == "accessory":
                self.assertEqual(len(included), 1, "accessory mode exposes exactly one entity")
            for entity_id, options in (item.get("entity_config") or {}).items():
                self.assertIn(entity_id, included)
                self.assertLessEqual(
                    set(options), {"name", "linked_doorbell_sensor", "support_audio"}
                )
            modes[item["mode"]] = item
        camera = modes["accessory"]
        camera_id = camera["filter"]["include_entities"][0]
        self.assertTrue(camera_id.startswith("camera."))
        options = camera["entity_config"][camera_id]
        self.assertEqual(options["linked_doorbell_sensor"], "event.intercom_famiglia_rossi_doorbell")
        self.assertIs(options["support_audio"], True)
        locks = modes["bridge"]["filter"]["include_entities"]
        self.assertTrue(locks)
        for entity_id in locks:
            self.assertTrue(entity_id.startswith("lock."), entity_id)
            self.assertIn(entity_id, ALL_ENTITY_IDS)
        # the guides tell users to replace the lines marked with <<<
        text = PACKAGE.read_text()
        for entity_id in [camera_id, options["linked_doorbell_sensor"], *locks]:
            line = next(line for line in text.splitlines() if entity_id in line)
            self.assertTrue(
                "<<<" in line or "<<<" in text.splitlines()[text.splitlines().index(line) - 1],
                entity_id,
            )

    def test_markdown_links(self) -> None:
        for md in list(ROOT.glob("*.md")) + list((ROOT / "docs").rglob("*.md")) + [ADDON / "DOCS.md"]:
            for target in re.findall(r"\]\(([^)#\s]+)(?:#[^)]*)?\)", md.read_text()):
                if target.startswith(("http://", "https://", "mailto:")):
                    continue
                self.assertTrue((md.parent / target).exists(), f"{md.name}: broken link {target}")

    def test_issue_forms(self) -> None:
        """GitHub issue forms are valid and the README links point to them."""

        forms_dir = ROOT / ".github" / "ISSUE_TEMPLATE"
        forms = sorted(p for p in forms_dir.glob("*.yml") if p.name != "config.yml")
        self.assertTrue(forms)
        allowed = {"markdown", "input", "textarea", "dropdown", "checkboxes"}
        for form_path in forms:
            form = yaml.safe_load(form_path.read_text())
            for key in ("name", "description", "body"):
                self.assertTrue(form.get(key), f"{form_path.name}: missing {key}")
            ids = [item["id"] for item in form["body"] if "id" in item]
            self.assertEqual(len(ids), len(set(ids)), f"{form_path.name}: duplicate ids")
            for item in form["body"]:
                self.assertIn(item["type"], allowed, form_path.name)
                attrs = item["attributes"]
                if item["type"] == "markdown":
                    self.assertTrue(attrs.get("value"))
                    continue
                self.assertRegex(item["id"], r"^[A-Za-z0-9_-]+$")
                self.assertTrue(attrs.get("label"), f"{form_path.name}: {item['id']} label")
                if item["type"] == "dropdown":
                    self.assertTrue(attrs["options"])
                    self.assertTrue(all(isinstance(o, str) for o in attrs["options"]))
                if item["type"] == "checkboxes":
                    self.assertTrue(all(o.get("label") for o in attrs["options"]))
        config = yaml.safe_load((forms_dir / "config.yml").read_text())
        self.assertIsInstance(config["blank_issues_enabled"], bool)
        for md in ("README.md", "LEGGIMI.md"):
            for name in re.findall(r"issues/new\?template=([\w.-]+)", (ROOT / md).read_text()):
                self.assertTrue((forms_dir / name).exists(), f"{md}: missing form {name}")

    def test_no_secrets_tracked(self) -> None:
        if not shutil.which("git"):
            self.skipTest("git not available")
        ignore = (ROOT / ".gitignore").read_text()
        for pattern in ("stack/router/config.yaml", "stack/mosquitto/passwd"):
            self.assertIn(pattern, ignore)


if __name__ == "__main__":
    unittest.main()
