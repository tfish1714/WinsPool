"""push_service must accept the VAPID private key in the formats operators
actually store it in (PKCS8 PEM, including one line with literal backslash-n
escapes, SEC1 PEM) as well as the raw / DER base64url forms py_vapid accepts.

Production bug: the deployed secret was a one-line PKCS8 PEM; py_vapid's
Vapid.from_string() only takes base64url (raw 32 bytes or DER), so every push
failed with "Could not deserialize key data". Only freshly generated keys are
used here, never a real one.
"""
import base64

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from py_vapid import Vapid

import services.push_service as ps

BS_N = chr(92) + "n"  # literal backslash + n (two characters)
BS_RN = chr(92) + "r" + chr(92) + "n"


def _b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


@pytest.fixture(scope="module")
def key():
    return ec.generate_private_key(ec.SECP256R1())


def _pkcs8_pem(key) -> str:
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()


def _sec1_pem(key) -> str:
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    ).decode()


def _der_b64u(key) -> str:
    return _b64u(key.private_bytes(
        serialization.Encoding.DER,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ))


def _raw_b64u(key) -> str:
    return _b64u(key.private_numbers().private_value.to_bytes(32, "big"))


def _public_b64u(vapid: Vapid) -> str:
    point = vapid.public_key.public_bytes(
        serialization.Encoding.X962,
        serialization.PublicFormat.UncompressedPoint,
    )
    return _b64u(point)


def _expected_public(key) -> str:
    point = key.public_key().public_bytes(
        serialization.Encoding.X962,
        serialization.PublicFormat.UncompressedPoint,
    )
    return _b64u(point)


def _variants(key) -> dict:
    pem = _pkcs8_pem(key)
    body = "".join(pem.strip().splitlines()[1:-1])
    header, footer = "-----BEGIN PRIVATE KEY-----", "-----END PRIVATE KEY-----"
    sec1 = _sec1_pem(key)
    return {
        "pkcs8_real_newlines": pem,
        "pkcs8_literal_backslash_n": pem.strip().replace("\n", BS_N),
        "pkcs8_literal_backslash_rn": pem.strip().replace("\n", BS_RN),
        "pkcs8_trailing_literal_backslash_n": pem.strip().replace("\n", BS_N) + BS_N,
        "pkcs8_collapsed_spaces": header + " " + body + " " + footer,
        "pkcs8_no_separators": header + body + footer,
        "pkcs8_double_quoted": '"' + pem.strip().replace("\n", BS_N) + '"',
        "pkcs8_single_quoted_padded": "  '" + pem.strip() + "'  \n",
        "sec1_real_newlines": sec1,
        "sec1_literal_backslash_n": sec1.strip().replace("\n", BS_N),
        "der_b64u": _der_b64u(key),
        "der_b64u_padded_whitespace": " " + _der_b64u(key) + "=\n",
        "raw_b64u": _raw_b64u(key),
        "raw_b64u_whitespace": "\n " + _raw_b64u(key) + " \t",
    }


def test_every_variant_parses_to_the_same_public_key(key):
    expected = _expected_public(key)
    for name, variant in _variants(key).items():
        normalized = ps._normalize_vapid_private_key(variant)
        vapid = Vapid.from_string(normalized)
        assert _public_b64u(vapid) == expected, name


def test_raw_and_der_base64url_are_returned_unchanged(key):
    raw, der = _raw_b64u(key), _der_b64u(key)
    assert len(raw) == 43
    assert ps._normalize_vapid_private_key(raw) == raw
    assert ps._normalize_vapid_private_key("  " + raw + "\n") == raw
    assert ps._normalize_vapid_private_key(der) == der


def test_pem_normalizes_to_unpadded_der_base64url(key):
    out = ps._normalize_vapid_private_key(_pkcs8_pem(key).strip().replace("\n", BS_N))
    assert out == _der_b64u(key)
    assert "=" not in out


def test_empty_returns_empty():
    assert ps._normalize_vapid_private_key("") == ""


@pytest.mark.parametrize("garbage", [
    "not a key at all",
    "-----BEGIN PRIVATE KEY-----\nAAAA\n-----END PRIVATE KEY-----",
    "-----BEGIN EC PRIVATE KEY-----\n!!!!\n-----END EC PRIVATE KEY-----",
    "-----BEGIN PRIVATE KEY-----",
    "ééé",
])
def test_garbage_is_returned_stripped_and_never_raises(garbage):
    assert ps._normalize_vapid_private_key("  " + garbage + "\n") == garbage.strip()


def test_none_like_input_does_not_raise():
    assert ps._normalize_vapid_private_key(None) == ""


def test_signing_works_end_to_end_with_pem(key):
    pem_one_line = _pkcs8_pem(key).strip().replace("\n", BS_N)
    vapid = Vapid.from_string(ps._normalize_vapid_private_key(pem_one_line))
    headers = vapid.sign({"sub": "mailto:a@b.co", "aud": "https://fcm.googleapis.com"})
    assert "Authorization" in headers
    assert "Crypto-Key" in headers or "Authorization" in headers


def test_deliver_hands_the_normalized_key_to_webpush(key, monkeypatch):
    import pywebpush

    seen = {}

    def recorder(**kwargs):
        seen.update(kwargs)

    monkeypatch.setattr(pywebpush, "webpush", recorder)
    pem_one_line = _pkcs8_pem(key).strip().replace("\n", BS_N)
    monkeypatch.setattr(ps, "_VAPID_PRIVATE", pem_one_line)

    result = ps._deliver(1, {"endpoint": "https://example.invalid/x", "keys": {}}, "t", "b")

    assert result == "sent"
    assert seen["vapid_private_key"] == _der_b64u(key)
    assert seen["vapid_private_key"] != pem_one_line
    assert _public_b64u(Vapid.from_string(seen["vapid_private_key"])) == _expected_public(key)


def test_deliver_normalizes_at_call_time_from_current_value(key, monkeypatch):
    import pywebpush

    seen = []
    monkeypatch.setattr(pywebpush, "webpush", lambda **kw: seen.append(kw["vapid_private_key"]))
    sub = {"endpoint": "https://example.invalid/x", "keys": {}}

    monkeypatch.setattr(ps, "_VAPID_PRIVATE", _raw_b64u(key))
    ps._deliver(1, sub, "t", "b")
    monkeypatch.setattr(ps, "_VAPID_PRIVATE", _pkcs8_pem(key))
    ps._deliver(1, sub, "t", "b")

    assert seen == [_raw_b64u(key), _der_b64u(key)]


def test_is_configured_semantics_unchanged(monkeypatch):
    monkeypatch.setattr(ps, "_VAPID_PUBLIC", "pub")
    monkeypatch.setattr(ps, "_VAPID_PRIVATE", "-----BEGIN PRIVATE KEY-----")
    assert ps.is_configured() is True
    monkeypatch.setattr(ps, "_VAPID_PRIVATE", "")
    assert ps.is_configured() is False
