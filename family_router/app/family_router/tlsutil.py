"""TLS helpers.

UniFi consoles use a self-signed certificate on the Access API port
(12445). The recommended setting is certificate *pinning*: the SHA-256
fingerprint of the console certificate is stored in the configuration
(``access.tls_fingerprint``) and every connection is rejected if the
certificate presented does not match.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import socket
import ssl

from .config import AccessConfig

__all__ = [
    "TlsFingerprintError",
    "check_fingerprint",
    "fetch_fingerprint",
    "fingerprint_of",
    "format_fingerprint",
    "make_ssl_context",
]

_LOGGER = logging.getLogger(__name__)
_warned_insecure = False


class TlsFingerprintError(ssl.SSLError):
    """The server certificate does not match the pinned fingerprint."""


def fingerprint_of(der: bytes) -> str:
    """SHA-256 of a DER certificate as 64 upper-case hex characters."""

    return hashlib.sha256(der).hexdigest().upper()


def format_fingerprint(hex_digest: str) -> str:
    """``AABB...`` -> ``AA:BB:...``"""

    return ":".join(hex_digest[i : i + 2] for i in range(0, len(hex_digest), 2))


def make_ssl_context(access: AccessConfig) -> ssl.SSLContext:
    """Build the client SSL context for the Access API."""

    global _warned_insecure
    if access.tls_fingerprint:
        ctx = ssl.create_default_context()
        # Identity is verified by the pinned fingerprint instead of a CA.
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    elif access.verify_ssl:
        ctx = ssl.create_default_context(cafile=access.ca_file or None)
    else:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        if not _warned_insecure:
            _warned_insecure = True
            _LOGGER.warning(
                "TLS certificate of the console is NOT verified. Set "
                "'access.tls_fingerprint' (see 'python -m family_router "
                "--fingerprint') to pin it."
            )
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    return ctx


def check_fingerprint(ssl_object: ssl.SSLObject | ssl.SSLSocket | None, expected: str) -> None:
    """Raise :class:`TlsFingerprintError` unless the peer matches ``expected``."""

    if not expected:
        return
    if ssl_object is None:
        raise TlsFingerprintError("no TLS session to verify")
    der = ssl_object.getpeercert(binary_form=True)
    if not der:
        raise TlsFingerprintError("server did not present a certificate")
    actual = fingerprint_of(der)
    if not hmac.compare_digest(actual, expected.upper()):
        raise TlsFingerprintError(
            "certificate fingerprint mismatch: expected "
            f"{format_fingerprint(expected.upper())}, got {format_fingerprint(actual)}"
        )


def fetch_fingerprint(host: str, port: int = 12445, timeout: float = 10.0) -> str:
    """Connect once and return the server certificate SHA-256 fingerprint."""

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    with socket.create_connection((host, port), timeout=timeout) as raw:
        with ctx.wrap_socket(raw, server_hostname=host) as tls:
            der = tls.getpeercert(binary_form=True)
    if not der:
        raise TlsFingerprintError("server did not present a certificate")
    return fingerprint_of(der)
