"""MQTT encoding checked against the OASIS MQTT 3.1.1 specification."""

from __future__ import annotations

import unittest

from family_router.mqtt import (
    MqttError,
    Will,
    decode_remaining_length,
    encode_connect,
    encode_publish,
    encode_remaining_length,
    encode_subscribe,
)


class RemainingLengthTests(unittest.TestCase):
    # Table 2.4 "Size of Remaining Length field"
    VECTORS = {
        0: bytes([0x00]),
        127: bytes([0x7F]),
        128: bytes([0x80, 0x01]),
        16383: bytes([0xFF, 0x7F]),
        16384: bytes([0x80, 0x80, 0x01]),
        2097151: bytes([0xFF, 0xFF, 0x7F]),
        2097152: bytes([0x80, 0x80, 0x80, 0x01]),
        268435455: bytes([0xFF, 0xFF, 0xFF, 0x7F]),
    }

    def test_vectors(self) -> None:
        for value, encoded in self.VECTORS.items():
            with self.subTest(value=value):
                self.assertEqual(encode_remaining_length(value), encoded)
                self.assertEqual(decode_remaining_length(encoded + b"\x99"), (value, len(encoded)))

    def test_limits(self) -> None:
        with self.assertRaises(MqttError):
            encode_remaining_length(268435456)
        with self.assertRaises(MqttError):
            encode_remaining_length(-1)
        with self.assertRaises(MqttError):
            decode_remaining_length(bytes([0xFF, 0xFF, 0xFF, 0xFF]))


class PacketTests(unittest.TestCase):
    def test_connect_minimal(self) -> None:
        expected = bytes(
            [0x10, 14, 0x00, 0x04]) + b"MQTT" + bytes([0x04, 0x02, 0x00, 0x1E, 0x00, 0x02]) + b"c1"
        self.assertEqual(encode_connect("c1", 30), expected)

    def test_connect_full(self) -> None:
        packet = encode_connect("c1", 30, "u", "p", Will("s", b"offline", qos=1, retain=True))
        body = (
            b"\x00\x04MQTT\x04" + bytes([0xEE]) + b"\x00\x1e"
            + b"\x00\x02c1" + b"\x00\x01s" + b"\x00\x07offline" + b"\x00\x01u" + b"\x00\x01p"
        )
        self.assertEqual(packet, bytes([0x10, len(body)]) + body)

    def test_connect_user_without_password(self) -> None:
        packet = encode_connect("c", 10, "user")
        self.assertEqual(packet[9], 0x82)  # clean session + user name flag only

    def test_connect_password_requires_user(self) -> None:
        with self.assertRaises(MqttError):
            encode_connect("c", 10, "", "secret")

    def test_publish(self) -> None:
        self.assertEqual(
            encode_publish("a/b", b"hi", 0, True),
            bytes([0x31, 0x07, 0x00, 0x03]) + b"a/b" + b"hi",
        )
        self.assertEqual(
            encode_publish("a/b", b"hi", 1, False, 10),
            bytes([0x32, 0x09, 0x00, 0x03]) + b"a/b" + b"\x00\x0a" + b"hi",
        )
        big = encode_publish("t", b"x" * 200, 0)
        self.assertEqual(big[:3], bytes([0x30, 0xCB, 0x01]))  # 203 = 0xCB 0x01

    def test_publish_rejects_wildcards_and_qos2(self) -> None:
        for topic in ("a/+", "a/#", "", "a\x00"):
            with self.assertRaises(MqttError):
                encode_publish(topic, b"", 0)
        with self.assertRaises(MqttError):
            encode_publish("a", b"", 2)

    def test_subscribe(self) -> None:
        self.assertEqual(
            encode_subscribe(1, "x/#", 1),
            bytes([0x82, 0x08, 0x00, 0x01, 0x00, 0x03]) + b"x/#" + b"\x01",
        )

    def test_utf8_topic(self) -> None:
        packet = encode_publish("città", b"", 0)
        self.assertEqual(packet[2:4], b"\x00\x06")  # 'à' is two bytes


if __name__ == "__main__":
    unittest.main()
