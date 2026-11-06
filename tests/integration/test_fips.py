"""FIPS behaviour of the image these tests run in.

Only meaningful inside the driver image on a FIPS-mode node; skipped
elsewhere. An image with a non-FIPS OpenSSL fails them, which is the point.
"""

import hashlib
import ssl
from pathlib import Path

import pytest
from cryptography.exceptions import InternalError, UnsupportedAlgorithm
from cryptography.hazmat.primitives import hashes

FIPS_ENABLED = Path("/proc/sys/crypto/fips_enabled")


@pytest.fixture(autouse=True)
def fips_node():
    if not FIPS_ENABLED.exists() or FIPS_ENABLED.read_text().strip() != "1":
        pytest.skip("not on a FIPS-mode node")


def test_python_refuses_md5():
    with pytest.raises(ValueError):
        hashlib.md5()


def test_cryptography_refuses_md5():
    # cryptography's PyPI wheels carry their own OpenSSL, which would allow it.
    # When the FIPS provider refuses, cryptography can surface it as InternalError.
    with pytest.raises((UnsupportedAlgorithm, InternalError), match="(?i)unsupported"):
        hashes.Hash(hashes.MD5())


def test_tls_does_not_offer_chacha20():
    ciphers = [c["name"] for c in ssl.create_default_context().get_ciphers()]
    assert ciphers
    assert not [name for name in ciphers if "CHACHA" in name]


def test_flashblade_connections_use_fips_ciphers(fb, monkeypatch):
    handshakes = []
    do_handshake = ssl.SSLSocket.do_handshake

    def recording_handshake(self, *args, **kwargs):
        result = do_handshake(self, *args, **kwargs)
        handshakes.append((self.version(), self.cipher()[0]))
        return result

    monkeypatch.setattr(ssl.SSLSocket, "do_handshake", recording_handshake)

    fb._get_fresh_client()

    assert handshakes, "the client made no TLS connection through ssl.SSLSocket"
    for version, cipher in handshakes:
        assert version == "TLSv1.3"
        assert "AES" in cipher and "GCM" in cipher
