from __future__ import annotations

import json
import os
import tempfile
import unittest

from family_router.config import (
    ConfigError,
    load_config,
    normalize_fingerprint,
    normalize_mac,
    parse_config,
)

from .support import MAC, ROOM_A, make_config, raw_config


class ConfigTests(unittest.TestCase):
    def test_minimal_valid(self) -> None:
        cfg = make_config()
        self.assertEqual(cfg.access.host, "192.168.50.1")
        self.assertEqual(cfg.access.port, 12445)
        self.assertEqual(cfg.family.id, "family_a")
        self.assertEqual(cfg.family.match.room_ids, (ROOM_A,))
        self.assertEqual(cfg.homebridge.camera_mac, MAC)
        self.assertEqual(cfg.mqtt.client_id, "access-family-router-family_a")
        self.assertEqual(cfg.mqtt.base_topic, "access_family_router")
        self.assertFalse(cfg.unlock.enabled)
        self.assertEqual(cfg.unlock.actor_name, "Family A (family router)")
        self.assertEqual(cfg.call_timeout_seconds, 60.0)

    def test_family_id_derived_from_name(self) -> None:
        cfg = make_config(family={"id": None, "name": "Famiglia Fingardo!"})
        self.assertEqual(cfg.family.id, "famiglia_fingardo")

    def test_invalid_family_id(self) -> None:
        with self.assertRaisesRegex(ConfigError, "family.id"):
            make_config(family={"id": "Family-A"})

    def test_missing_required(self) -> None:
        for sections, pattern in (
            ({"access": {"host": ""}}, "access.host"),
            ({"access": {"token": ""}}, "access.token"),
            ({"mqtt": {"host": ""}}, "mqtt.host"),
            ({"family": {"name": "", "id": ""}}, "family.name"),
        ):
            with self.subTest(sections=sections):
                with self.assertRaisesRegex(ConfigError, pattern):
                    make_config(**sections)

    def test_host_with_scheme_rejected(self) -> None:
        with self.assertRaisesRegex(ConfigError, "without 'https://'"):
            make_config(access={"host": "https://192.168.1.1"})

    def test_match_required_unless_learn(self) -> None:
        with self.assertRaisesRegex(ConfigError, "learn_mode"):
            make_config(family={"match": {}})
        cfg = parse_config({**raw_config(family={"match": {}}), "learn_mode": True}, env={})
        self.assertTrue(cfg.learn_mode)
        self.assertTrue(cfg.family.match.is_empty)

    def test_flat_match_keys(self) -> None:
        """Home Assistant add-on options put the rules directly under 'family'."""

        raw = raw_config(family={"match": None, "room_ids": [ROOM_A], "ring_all": False,
                                 "door_guard_ids": ["g1"], "device_ids": []})
        cfg = parse_config(raw, env={})
        self.assertEqual(cfg.family.match.room_ids, (ROOM_A,))
        self.assertEqual(cfg.family.match.door_guard_ids, ("g1",))
        # nested values win over flat ones
        raw = raw_config(family={"room_ids": ["flat"], "match": {"room_ids": ["nested"]}})
        self.assertEqual(parse_config(raw, env={}).family.match.room_ids, ("nested",))

    def test_ring_all(self) -> None:
        cfg = make_config(family={"match": {"ring_all": True}})
        self.assertTrue(cfg.family.match.ring_all)

    def test_lists_from_string_and_dedup(self) -> None:
        cfg = make_config(family={"match": {"room_ids": f"{ROOM_A}, {ROOM_A} WR-2"}})
        self.assertEqual(cfg.family.match.room_ids, (ROOM_A, "WR-2"))

    def test_bad_list_type(self) -> None:
        with self.assertRaisesRegex(ConfigError, "list of strings"):
            make_config(family={"match": {"room_ids": {"a": 1}}})

    def test_mac_normalisation(self) -> None:
        for value in ("f4:e2:c6:12:34:56", "F4-E2-C6-12-34-56", "f4e2c6123456", "F4E2.C612.3456"):
            self.assertEqual(normalize_mac(value), MAC)
        for bad in ("F4:E2:C6:12:34", "zz:e2:c6:12:34:56", ""):
            with self.assertRaises(ConfigError):
                normalize_mac(bad)

    def test_homebridge_requires_mac(self) -> None:
        with self.assertRaisesRegex(ConfigError, "camera_mac"):
            make_config(homebridge={"enabled": True, "camera_mac": ""})
        cfg = make_config(homebridge={"enabled": False, "camera_mac": ""})
        self.assertEqual(cfg.homebridge.camera_mac, "")

    def test_fingerprint_normalisation(self) -> None:
        digest = "ab" * 32
        self.assertEqual(normalize_fingerprint(digest), digest.upper())
        colon = ":".join(["AB"] * 32)
        self.assertEqual(normalize_fingerprint(colon), digest.upper())
        self.assertEqual(normalize_fingerprint("SHA256=" + colon), digest.upper())
        self.assertEqual(normalize_fingerprint(""), "")
        self.assertEqual(normalize_fingerprint("sha256 Fingerprint=" + colon), digest.upper())
        with self.assertRaises(ConfigError):
            normalize_fingerprint("ab" * 31)

    def test_fingerprint_from_openssl_output(self) -> None:
        import subprocess

        from .support import HAVE_OPENSSL, SelfSignedCert

        if not HAVE_OPENSSL:
            self.skipTest("openssl not available")
        cert = SelfSignedCert()
        try:
            out = subprocess.run(
                ["openssl", "x509", "-in", cert.cert, "-noout", "-fingerprint", "-sha256"],
                check=True, capture_output=True, text=True,
            ).stdout.strip()
            self.assertEqual(normalize_fingerprint(out), cert.fingerprint)
        finally:
            cert.cleanup()

    def test_unlock_requires_door(self) -> None:
        with self.assertRaisesRegex(ConfigError, "unlock.doors"):
            make_config(unlock={"enabled": True})
        cfg = make_config(unlock={"enabled": True, "door_id": "d1", "actor_name": "Casa A"})
        self.assertEqual(cfg.unlock.door_id, "d1")
        self.assertEqual(cfg.unlock.actor_name, "Casa A")

    def test_unlock_doors(self) -> None:
        cfg = make_config(unlock={"enabled": True, "doors": [
            "Portone=0ed545f8-2fcd-4839-9021-b39e707f6aa9",
            " Cancello carrabile = a8f2c6d1 ",
            {"name": "Garage", "door_id": "g-1"},
            {"name": "Ingresso più", "door_id": "i-1"},
            {"name": "🚪", "door_id": "e-1", "id": "emoji_door"},
            "",
        ]})
        doors = cfg.unlock.doors
        self.assertEqual([d.key for d in doors],
                         ["portone", "cancello_carrabile", "garage", "ingresso_piu", "emoji_door"])
        self.assertEqual(doors[1].name, "Cancello carrabile")
        self.assertEqual(doors[1].door_id, "a8f2c6d1")
        self.assertEqual(cfg.unlock.door_id, "0ed545f8-2fcd-4839-9021-b39e707f6aa9")
        self.assertEqual(cfg.unlock.relock_seconds, 5.0)

    def test_unlock_single_door_is_kept(self) -> None:
        cfg = make_config(unlock={"enabled": True, "door_id": "d1"})
        self.assertEqual(len(cfg.unlock.doors), 1)
        self.assertEqual((cfg.unlock.doors[0].key, cfg.unlock.doors[0].name), ("", ""))
        self.assertEqual(make_config(unlock={"enabled": False, "door_id": "d1"}).unlock.doors, ())

    def test_unlock_doors_errors(self) -> None:
        cases = [
            ({"doors": ["Portone"]}, "Name=door_id"),
            ({"doors": ["=abc"]}, "needs a name"),
            ({"doors": ["Portone="]}, "needs a door_id"),
            ({"doors": ["Portone=a b"]}, "invalid door_id"),
            ({"doors": ["Portone=a/b"]}, "invalid door_id"),
            ({"doors": ["🚪=x"]}, "letters or digits"),
            ({"doors": [{"name": "A", "door_id": "x", "id": "Bad Id"}]}, "letters or digits"),
            ({"doors": ["Portone=x", "portone=y"]}, "share the name"),
            ({"doors": ["Portone=x", "Cancello=x"]}, "listed twice"),
            ({"doors": [f"D{i}=id{i}" for i in range(11)]}, "at most 10"),
            ({"doors": "Portone=x"}, "must be a list"),
            ({"doors": [42]}, "Name=door_id"),
            ({"door_id": "x", "doors": ["Portone=y"]}, "not both"),
            ({"enabled": True}, "unlock.doors"),
            ({"relock_seconds": 0}, "relock_seconds"),
        ]
        for unlock, message in cases:
            with self.subTest(unlock=unlock):
                with self.assertRaisesRegex(ConfigError, message):
                    make_config(unlock={"enabled": True, **unlock})
        # while unlock is off, an unfinished door list does not block the router
        self.assertEqual(make_config(unlock={"enabled": False, "doors": ["Portone="]}).unlock.doors, ())

    def test_family_slug_folds_accents(self) -> None:
        cfg = make_config(family={"name": "Famiglia Città", "id": None})
        self.assertEqual(cfg.family.id, "famiglia_citta")

    def test_numbers_validated(self) -> None:
        with self.assertRaisesRegex(ConfigError, "access.port"):
            make_config(access={"port": 70000})
        with self.assertRaisesRegex(ConfigError, "whole number"):
            make_config(mqtt={"port": "abc"})
        with self.assertRaisesRegex(ConfigError, "call_timeout_seconds"):
            parse_config({**raw_config(), "call_timeout_seconds": 1}, env={})
        cfg = make_config(mqtt={"port": "8883"})
        self.assertEqual(cfg.mqtt.port, 8883)

    def test_bool_strings(self) -> None:
        cfg = make_config(mqtt={"discovery": "false", "tls": "yes"})
        self.assertFalse(cfg.mqtt.discovery)
        self.assertTrue(cfg.mqtt.tls)
        with self.assertRaisesRegex(ConfigError, "true or false"):
            make_config(mqtt={"tls": "maybe"})

    def test_topics_validated(self) -> None:
        with self.assertRaisesRegex(ConfigError, "base_topic"):
            make_config(mqtt={"base_topic": "a/+/b"})
        with self.assertRaisesRegex(ConfigError, "empty levels"):
            make_config(homebridge={"topic_prefix": "unifi//protect"})
        cfg = make_config(mqtt={"base_topic": "/home/intercom/"})
        self.assertEqual(cfg.mqtt.base_topic, "home/intercom")

    def test_password_without_user(self) -> None:
        with self.assertRaisesRegex(ConfigError, "mqtt.username"):
            make_config(mqtt={"password": "x"})

    def test_log_level(self) -> None:
        with self.assertRaisesRegex(ConfigError, "log_level"):
            parse_config({**raw_config(), "log_level": "verbose"}, env={})

    def test_env_overrides(self) -> None:
        env = {
            "MQTT_HOST": "core-mosquitto",
            "MQTT_PORT": "1884",
            "MQTT_USERNAME": "addons",
            "MQTT_PASSWORD": "secret",
            "FAMILY_ROUTER_ACCESS_TOKEN": "from-env",
        }
        cfg = parse_config(raw_config(mqtt={"host": ""}), env=env)
        self.assertEqual(
            (cfg.mqtt.host, cfg.mqtt.port, cfg.mqtt.username, cfg.mqtt.password),
            ("core-mosquitto", 1884, "addons", "secret"),
        )
        self.assertEqual(cfg.access.token, "from-env")
        # an explicit mqtt.host wins over the Supervisor values
        cfg = parse_config(raw_config(), env=env)
        self.assertEqual(cfg.mqtt.host, "127.0.0.1")

    def test_not_a_mapping(self) -> None:
        with self.assertRaises(ConfigError):
            parse_config([], env={})  # type: ignore[arg-type]
        with self.assertRaisesRegex(ConfigError, "'access' must be a mapping"):
            parse_config({**raw_config(), "access": "x"}, env={})

    def test_load_yaml_and_json(self) -> None:
        import yaml

        with tempfile.TemporaryDirectory() as tmp:
            yml = os.path.join(tmp, "config.yaml")
            with open(yml, "w", encoding="utf-8") as fh:
                yaml.safe_dump(raw_config(), fh)
            self.assertEqual(load_config(yml, env={}).family.id, "family_a")

            js = os.path.join(tmp, "options.json")
            with open(js, "w", encoding="utf-8") as fh:
                json.dump(raw_config(family={"match": {}}), fh)
            with self.assertRaises(ConfigError):
                load_config(js, env={})
            self.assertTrue(load_config(js, env={}, force_learn=True).learn_mode)

            empty = os.path.join(tmp, "empty.yaml")
            open(empty, "w").close()
            with self.assertRaisesRegex(ConfigError, "empty"):
                load_config(empty, env={})

            broken = os.path.join(tmp, "broken.json")
            with open(broken, "w") as fh:
                fh.write("{nope")
            with self.assertRaisesRegex(ConfigError, "invalid JSON"):
                load_config(broken, env={})

        with self.assertRaisesRegex(ConfigError, "not found"):
            load_config("/nonexistent/config.yaml", env={})

    def test_example_configs_are_valid(self) -> None:
        """The example files shipped in the repository must load."""

        import yaml

        from .support import ROOT

        example = ROOT / "config.example.yaml"
        raw = yaml.safe_load(example.read_text())
        cfg = parse_config(raw, env={})
        self.assertTrue(cfg.homebridge.enabled)

        stack = ROOT / "stack" / "router" / "config.yaml.example"
        cfg = parse_config(yaml.safe_load(stack.read_text()), env={})
        self.assertEqual(cfg.mqtt.host, "mosquitto")


if __name__ == "__main__":
    unittest.main()
