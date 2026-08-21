import base64
import json
import os
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from ydcore import licensing


PASSWORD = "correct horse battery staple"
MACHINE_CODE = "a" * 64


def make_license(private_key, features=("order",), ttl_seconds=86400, machine=MACHINE_CODE):
    payload = {
        "schema": 1,
        "license_id": "TEST-001",
        "machine_code": machine,
        "features": list(features),
        "ttl_seconds": ttl_seconds,
        "issued_at": "2026-08-21T00:00:00+00:00",
    }
    kdf = {
        "algorithm": "argon2id",
        "salt": base64.b64encode(b"0123456789abcdef").decode("ascii"),
        "time_cost": 1,
        "memory_cost_kib": 8192,
        "parallelism": 1,
        "length": 32,
    }
    key = licensing.derive_license_key(PASSWORD, kdf)
    nonce = b"0123456789ab"
    cipher = {
        "algorithm": "AES-256-GCM",
        "nonce": base64.b64encode(nonce).decode("ascii"),
        "ciphertext": base64.b64encode(
            AESGCM(key).encrypt(nonce, licensing.canonical_json(payload), licensing.LICENSE_AAD)
        ).decode("ascii"),
    }
    envelope = {"schema": 1, "kdf": kdf, "cipher": cipher}
    envelope["signature"] = base64.b64encode(
        private_key.sign(licensing.canonical_json(envelope))
    ).decode("ascii")
    return envelope


@pytest.fixture
def license_env(tmp_path, monkeypatch):
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes(
        Encoding.PEM, PublicFormat.SubjectPublicKeyInfo
    )
    state_root = tmp_path / "state"
    monkeypatch.setattr(licensing, "STATE_ROOT", state_root)
    monkeypatch.setattr(licensing, "STATE_MARKER", state_root / ".ydtrader-state-marker")
    monkeypatch.setattr(licensing, "LICENSE_FILE", state_root / "license.json")
    monkeypatch.setattr(licensing, "STATE_FILE", state_root / "activation.json")
    monkeypatch.setattr(licensing, "machine_code", lambda: MACHINE_CODE)
    monkeypatch.setattr(licensing, "_boot_snapshot", lambda: ("1" * 36, 1000.0))
    monkeypatch.setattr(licensing, "_assert_root_protected", lambda _path: None)
    monkeypatch.setattr(licensing.os, "geteuid", lambda: 0)
    monkeypatch.setenv("SUDO_UID", str(os.getuid()))
    monkeypatch.setenv("SUDO_GID", str(os.getgid()))
    return private_key, public_key, tmp_path / "incoming.license.json"


def activate_test_license(license_env, features=("order",), ttl_seconds=86400):
    private_key, public_key, source = license_env
    source.write_text(
        json.dumps(make_license(private_key, features, ttl_seconds)), encoding="utf-8"
    )
    started = datetime(2026, 8, 21, tzinfo=timezone.utc)
    grant = licensing.activate(
        source, PASSWORD, now=started, install_timer=False, public_key_pem=public_key
    )
    return grant, public_key, started


def test_activate_and_authorize_only_selected_feature(license_env):
    grant, public_key, started = activate_test_license(license_env)
    assert grant.expires_at == started + timedelta(hours=24)
    assert licensing.STATE_MARKER.read_text(encoding="ascii") == "ydtrader-state-v1\n"
    assert licensing.verify_business_access(
        "order", PASSWORD, now=started + timedelta(minutes=1), public_key_pem=public_key
    ).license_id == "TEST-001"
    with pytest.raises(licensing.LicenseDeniedError) as exc:
        licensing.verify_business_access(
            "monitor", PASSWORD, now=started + timedelta(minutes=2), public_key_pem=public_key
        )
    assert exc.value.exit_code == 22


def test_wrong_password_and_tampered_signature_are_rejected(license_env):
    private_key, public_key, _ = license_env
    envelope = make_license(private_key)
    with pytest.raises(licensing.LicenseInvalidError):
        licensing.decrypt_license(envelope, "wrong", public_key)
    envelope["cipher"]["nonce"] = base64.b64encode(b"abcdefgh1234").decode("ascii")
    with pytest.raises(licensing.LicenseInvalidError):
        licensing.decrypt_license(envelope, PASSWORD, public_key)


def test_machine_mismatch_reactivation_expiry_and_rollback(license_env, monkeypatch):
    grant, public_key, started = activate_test_license(license_env)
    source = license_env[2]
    with pytest.raises(licensing.LicenseDeniedError):
        licensing.activate(source, PASSWORD, install_timer=False, public_key_pem=public_key)
    monkeypatch.setattr(licensing, "machine_code", lambda: "b" * 64)
    with pytest.raises(licensing.LicenseDeniedError):
        licensing.verify_business_access("order", PASSWORD, public_key_pem=public_key)
    monkeypatch.setattr(licensing, "machine_code", lambda: MACHINE_CODE)
    with pytest.raises(licensing.LicenseExpiredError) as exc:
        licensing.verify_business_access(
            "order", PASSWORD, now=grant.expires_at, public_key_pem=public_key
        )
    assert exc.value.exit_code == 23

    monkeypatch.setattr(licensing, "_boot_snapshot", lambda: ("1" * 36, 1600.0))
    with pytest.raises(licensing.LicenseExpiredError, match="回拨"):
        licensing.verify_business_access(
            "order", PASSWORD, now=started, public_key_pem=public_key
        )


def test_state_tamper_is_rejected(license_env):
    _, public_key, started = activate_test_license(license_env)
    state = json.loads(licensing.STATE_FILE.read_text(encoding="utf-8"))
    state["expires_at"] = (started + timedelta(days=3)).isoformat()
    licensing.STATE_FILE.write_text(json.dumps(state), encoding="utf-8")
    with pytest.raises(licensing.LicenseInvalidError):
        licensing.verify_business_access("order", PASSWORD, public_key_pem=public_key)


def test_expiry_timer_is_absolute_persistent_and_one_second(tmp_path, monkeypatch):
    helper = tmp_path / "ydtrader-destroy"
    helper.write_text("#!/bin/sh\n", encoding="ascii")
    service = tmp_path / "ydtrader-expiry.service"
    timer = tmp_path / "ydtrader-expiry.timer"
    calls = []
    monkeypatch.setattr(licensing, "DESTROY_HELPER", helper)
    monkeypatch.setattr(licensing, "SYSTEMD_SERVICE", service)
    monkeypatch.setattr(licensing, "SYSTEMD_TIMER", timer)
    monkeypatch.setattr(
        licensing.subprocess,
        "run",
        lambda command, check: calls.append((command, check)),
    )
    licensing.install_expiry_timer(datetime(2026, 8, 22, 1, 2, 3, tzinfo=timezone.utc))
    timer_text = timer.read_text(encoding="utf-8")
    assert "OnCalendar=2026-08-22 01:02:03 UTC" in timer_text
    assert "Persistent=true" in timer_text
    assert "AccuracySec=1s" in timer_text
    assert calls == [
        (["systemctl", "daemon-reload"], True),
        (["systemctl", "enable", "--now", "ydtrader-expiry.timer"], True),
    ]
