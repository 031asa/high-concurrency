"""Offline signed licensing and activation state for the Linux client."""

from __future__ import annotations

import base64
import fcntl
import hashlib
import hmac
import json
import os
import platform
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    from ._public_key import PUBLIC_KEY_PEM
except ImportError:
    PUBLIC_KEY_PEM = b""


SCHEMA_VERSION = 1
PRODUCT_SALT = b"ydtrader-linux-license-v1"
LICENSE_AAD = b"ydtrader-license-envelope-v1"
STATE_KEY_LABEL = b"ydtrader-activation-state-v1"
ALLOWED_FEATURES = frozenset({"order", "monitor", "marketdata"})
ROLLBACK_TOLERANCE_SECONDS = 300

INSTALL_ROOT = Path("/opt/ydtrader")
STATE_ROOT = Path("/var/lib/ydtrader")
STATE_MARKER = STATE_ROOT / ".ydtrader-state-marker"
LICENSE_FILE = STATE_ROOT / "license.json"
STATE_FILE = STATE_ROOT / "activation.json"
MACHINE_ID_FILE = Path("/etc/machine-id")
BOOT_ID_FILE = Path("/proc/sys/kernel/random/boot_id")
UPTIME_FILE = Path("/proc/uptime")
DESTROY_HELPER = Path("/usr/local/libexec/ydtrader-destroy")
SYSTEMD_SERVICE = Path("/etc/systemd/system/ydtrader-expiry.service")
SYSTEMD_TIMER = Path("/etc/systemd/system/ydtrader-expiry.timer")


class LicenseError(RuntimeError):
    exit_code = 21


class LicenseMissingError(LicenseError):
    exit_code = 20


class LicenseInvalidError(LicenseError):
    exit_code = 21


class LicenseDeniedError(LicenseError):
    exit_code = 22


class LicenseExpiredError(LicenseError):
    exit_code = 23


@dataclass(frozen=True)
class LicenseGrant:
    license_id: str
    features: frozenset[str]
    activated_at: datetime
    expires_at: datetime


def canonical_json(value) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _b64decode(value, field):
    try:
        return base64.b64decode(value, validate=True)
    except Exception as exc:
        raise LicenseInvalidError(f"许可证字段不是有效Base64: {field}") from exc


def _utc_now():
    return datetime.now(timezone.utc)


def _parse_utc(value, field):
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise LicenseInvalidError(f"许可证时间字段无效: {field}") from exc
    if parsed.tzinfo is None:
        raise LicenseInvalidError(f"许可证时间字段缺少时区: {field}")
    return parsed.astimezone(timezone.utc)


def machine_code(machine_id_file=None):
    path = Path(machine_id_file or MACHINE_ID_FILE)
    try:
        machine_id = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise LicenseDeniedError(f"无法读取Linux机器标识: {path}") from exc
    architecture = platform.machine().lower()
    if architecture not in {"x86_64", "amd64"}:
        raise LicenseDeniedError(f"不支持的CPU架构: {architecture}")
    if not re.fullmatch(r"[0-9a-fA-F-]{16,128}", machine_id):
        raise LicenseDeniedError("Linux机器标识格式无效")
    digest = hashlib.sha256()
    digest.update(PRODUCT_SALT)
    digest.update(b"\0")
    digest.update(machine_id.encode("ascii"))
    digest.update(b"\0x86_64")
    return digest.hexdigest()


def derive_license_key(password, kdf):
    if not isinstance(password, str) or not password:
        raise LicenseInvalidError("运行密码不能为空")
    if kdf.get("algorithm") != "argon2id":
        raise LicenseInvalidError("许可证KDF算法不受支持")
    values = {
        "time_cost": int(kdf.get("time_cost", 0)),
        "memory_cost_kib": int(kdf.get("memory_cost_kib", 0)),
        "parallelism": int(kdf.get("parallelism", 0)),
        "length": int(kdf.get("length", 0)),
    }
    if not (1 <= values["time_cost"] <= 10):
        raise LicenseInvalidError("许可证KDF time_cost超出安全范围")
    if not (8192 <= values["memory_cost_kib"] <= 262144):
        raise LicenseInvalidError("许可证KDF memory_cost超出安全范围")
    if not (1 <= values["parallelism"] <= 8 and values["length"] == 32):
        raise LicenseInvalidError("许可证KDF参数无效")
    salt = _b64decode(kdf.get("salt", ""), "kdf.salt")
    if len(salt) != 16:
        raise LicenseInvalidError("许可证KDF盐长度无效")
    try:
        from argon2.low_level import Type, hash_secret_raw
    except ImportError as exc:
        raise LicenseInvalidError("缺少argon2运行依赖") from exc
    return hash_secret_raw(
        password.encode("utf-8"),
        salt,
        time_cost=values["time_cost"],
        memory_cost=values["memory_cost_kib"],
        parallelism=values["parallelism"],
        hash_len=values["length"],
        type=Type.ID,
    )


def _verify_signature(envelope, public_key_pem=None):
    public_key_pem = public_key_pem if public_key_pem is not None else PUBLIC_KEY_PEM
    if not public_key_pem:
        raise LicenseInvalidError("程序尚未嵌入生产许可证公钥")
    unsigned = dict(envelope)
    signature = _b64decode(unsigned.pop("signature", ""), "signature")
    try:
        from cryptography.hazmat.primitives.serialization import load_pem_public_key

        public_key = load_pem_public_key(public_key_pem)
        public_key.verify(signature, canonical_json(unsigned))
    except LicenseError:
        raise
    except Exception as exc:
        raise LicenseInvalidError("许可证签名无效") from exc
    return unsigned


def decrypt_license(envelope, password, public_key_pem=None):
    if not isinstance(envelope, dict) or envelope.get("schema") != SCHEMA_VERSION:
        raise LicenseInvalidError("许可证格式或版本无效")
    unsigned = _verify_signature(envelope, public_key_pem)
    key = derive_license_key(password, unsigned.get("kdf", {}))
    cipher = unsigned.get("cipher", {})
    if cipher.get("algorithm") != "AES-256-GCM":
        raise LicenseInvalidError("许可证加密算法不受支持")
    nonce = _b64decode(cipher.get("nonce", ""), "cipher.nonce")
    ciphertext = _b64decode(cipher.get("ciphertext", ""), "cipher.ciphertext")
    if len(nonce) != 12:
        raise LicenseInvalidError("许可证nonce长度无效")
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        plaintext = AESGCM(key).decrypt(nonce, ciphertext, LICENSE_AAD)
        payload = json.loads(plaintext.decode("utf-8"))
    except Exception as exc:
        raise LicenseInvalidError("运行密码错误或许可证密文损坏") from exc
    return _validate_payload(payload), key


def _validate_payload(payload):
    if not isinstance(payload, dict) or payload.get("schema") != SCHEMA_VERSION:
        raise LicenseInvalidError("许可证载荷版本无效")
    license_id = str(payload.get("license_id", ""))
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", license_id):
        raise LicenseInvalidError("许可证ID格式无效")
    machine = str(payload.get("machine_code", ""))
    if not re.fullmatch(r"[0-9a-f]{64}", machine):
        raise LicenseInvalidError("许可证机器码格式无效")
    features = payload.get("features")
    if not isinstance(features, list) or not features:
        raise LicenseInvalidError("许可证功能列表无效")
    feature_set = frozenset(map(str, features))
    if not feature_set.issubset(ALLOWED_FEATURES):
        raise LicenseInvalidError("许可证包含未知功能")
    ttl_seconds = int(payload.get("ttl_seconds", 0))
    if not (1 <= ttl_seconds <= 30 * 24 * 60 * 60):
        raise LicenseInvalidError("许可证有效期超出允许范围")
    _parse_utc(payload.get("issued_at", ""), "issued_at")
    normalized = dict(payload)
    normalized["license_id"] = license_id
    normalized["machine_code"] = machine
    normalized["features"] = sorted(feature_set)
    normalized["ttl_seconds"] = ttl_seconds
    return normalized


def _read_json(path, missing_message):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise LicenseMissingError(missing_message) from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise LicenseInvalidError(f"无法读取许可证状态: {path}") from exc


def _state_hmac_key(license_key):
    return hmac.digest(license_key, STATE_KEY_LABEL, "sha256")


def _sign_state(state, license_key):
    unsigned = dict(state)
    unsigned.pop("state_mac", None)
    result = dict(unsigned)
    result["state_mac"] = base64.b64encode(
        hmac.digest(_state_hmac_key(license_key), canonical_json(unsigned), "sha256")
    ).decode("ascii")
    return result


def _verify_state(state, license_key):
    if not isinstance(state, dict):
        raise LicenseInvalidError("激活状态格式无效")
    unsigned = dict(state)
    actual = _b64decode(unsigned.pop("state_mac", ""), "state_mac")
    expected = hmac.digest(_state_hmac_key(license_key), canonical_json(unsigned), "sha256")
    if not hmac.compare_digest(actual, expected):
        raise LicenseInvalidError("激活状态校验失败")
    return unsigned


def _write_json_atomic(path, data, mode=0o600, owner=None):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.chmod(temporary, mode)
    if owner is not None:
        os.chown(temporary, owner[0], owner[1])
    temporary.replace(path)


def _activated_owner():
    try:
        return int(os.environ["SUDO_UID"]), int(os.environ["SUDO_GID"])
    except (KeyError, ValueError) as exc:
        raise LicenseDeniedError("请由实际运行用户通过sudo执行激活") from exc


def _boot_snapshot():
    try:
        boot_id = BOOT_ID_FILE.read_text(encoding="ascii").strip()
        uptime_seconds = float(UPTIME_FILE.read_text(encoding="ascii").split()[0])
    except (OSError, ValueError, IndexError) as exc:
        raise LicenseDeniedError("无法读取Linux启动时钟") from exc
    if not re.fullmatch(r"[0-9a-fA-F-]{36}", boot_id) or uptime_seconds < 0:
        raise LicenseDeniedError("Linux启动时钟格式无效")
    return boot_id.lower(), uptime_seconds


def _assert_root_protected(path):
    try:
        status = Path(path).stat()
    except OSError as exc:
        raise LicenseMissingError(f"授权状态文件不存在: {path}") from exc
    if status.st_uid != 0 or status.st_mode & 0o022:
        raise LicenseInvalidError(f"授权状态文件所有权或权限不安全: {path}")


def install_expiry_timer(expires_at):
    if not DESTROY_HELPER.is_file():
        raise LicenseDeniedError(f"销毁程序不存在: {DESTROY_HELPER}")
    calendar = expires_at.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    service = """[Unit]\nDescription=Destroy expired YDTrader deployment\n\n[Service]\nType=oneshot\nExecStart=/usr/local/libexec/ydtrader-destroy\nStandardOutput=null\nStandardError=null\n"""
    timer = f"""[Unit]\nDescription=YDTrader absolute expiry timer\n\n[Timer]\nOnCalendar={calendar}\nPersistent=true\nAccuracySec=1s\nUnit=ydtrader-expiry.service\n\n[Install]\nWantedBy=timers.target\n"""
    SYSTEMD_SERVICE.write_text(service, encoding="utf-8")
    SYSTEMD_TIMER.write_text(timer, encoding="utf-8")
    os.chmod(SYSTEMD_SERVICE, 0o644)
    os.chmod(SYSTEMD_TIMER, 0o644)
    subprocess.run(["systemctl", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "enable", "--now", SYSTEMD_TIMER.name], check=True)


def activate(source_file, password, now=None, install_timer=True, public_key_pem=None):
    if os.geteuid() != 0:
        raise LicenseDeniedError("激活必须通过sudo以root权限执行")
    if STATE_FILE.exists() or LICENSE_FILE.exists():
        raise LicenseDeniedError("该部署已经激活，拒绝重新开始计时")
    activated_uid, activated_gid = _activated_owner()
    envelope = _read_json(source_file, f"许可证文件不存在: {source_file}")
    payload, key = decrypt_license(envelope, password, public_key_pem)
    current_machine = machine_code()
    if payload["machine_code"] != current_machine:
        raise LicenseDeniedError("许可证与当前Linux机器不匹配")
    activated_at = (now or _utc_now()).astimezone(timezone.utc)
    expires_at = activated_at + timedelta(seconds=payload["ttl_seconds"])
    boot_id, activated_uptime = _boot_snapshot()
    state = _sign_state(
        {
            "schema": SCHEMA_VERSION,
            "license_id": payload["license_id"],
            "machine_code": current_machine,
            "activated_at": activated_at.isoformat(),
            "expires_at": expires_at.isoformat(),
            "activated_boot_id": boot_id,
            "activated_uptime_seconds": activated_uptime,
            "activated_uid": activated_uid,
            "activated_gid": activated_gid,
        },
        key,
    )
    try:
        STATE_ROOT.mkdir(parents=True, exist_ok=True)
        os.chmod(STATE_ROOT, 0o755)
        STATE_MARKER.write_text("ydtrader-state-v1\n", encoding="ascii")
        os.chmod(STATE_MARKER, 0o600)
        _write_json_atomic(LICENSE_FILE, envelope, 0o644)
        _write_json_atomic(STATE_FILE, state, 0o644)
        if install_timer:
            install_expiry_timer(expires_at)
    except Exception:
        for path in (STATE_FILE, LICENSE_FILE, STATE_MARKER):
            try:
                path.unlink()
            except FileNotFoundError:
                pass
        raise
    return LicenseGrant(
        payload["license_id"],
        frozenset(payload["features"]),
        activated_at,
        expires_at,
    )


def verify_business_access(feature, password, now=None, public_key_pem=None):
    if feature not in ALLOWED_FEATURES:
        raise LicenseDeniedError(f"未知授权功能: {feature}")
    _assert_root_protected(LICENSE_FILE)
    _assert_root_protected(STATE_FILE)
    _assert_root_protected(STATE_MARKER)
    envelope = _read_json(LICENSE_FILE, "程序尚未激活")
    payload, key = decrypt_license(envelope, password, public_key_pem)
    current_machine = machine_code()
    if payload["machine_code"] != current_machine:
        raise LicenseDeniedError("许可证与当前Linux机器不匹配")
    try:
        with STATE_FILE.open("r", encoding="utf-8") as stream:
            fcntl.flock(stream.fileno(), fcntl.LOCK_SH)
            try:
                state = _verify_state(json.load(stream), key)
                if state.get("license_id") != payload["license_id"]:
                    raise LicenseDeniedError("激活状态与许可证ID不一致")
                if state.get("machine_code") != current_machine:
                    raise LicenseDeniedError("激活状态与当前机器不一致")
                activated_at = _parse_utc(state.get("activated_at", ""), "activated_at")
                expires_at = _parse_utc(state.get("expires_at", ""), "expires_at")
                expected_expiry = activated_at + timedelta(seconds=payload["ttl_seconds"])
                if abs((expires_at - expected_expiry).total_seconds()) > 1:
                    raise LicenseInvalidError("激活状态有效期校验失败")
                current = (now or _utc_now()).astimezone(timezone.utc)
                boot_id, uptime_seconds = _boot_snapshot()
                if boot_id == state.get("activated_boot_id"):
                    try:
                        elapsed = uptime_seconds - float(state["activated_uptime_seconds"])
                    except (KeyError, TypeError, ValueError) as exc:
                        raise LicenseInvalidError("激活状态启动时钟无效") from exc
                    expected_minimum = activated_at + timedelta(seconds=max(0, elapsed))
                    if current < expected_minimum - timedelta(seconds=ROLLBACK_TOLERANCE_SECONDS):
                        raise LicenseExpiredError("检测到系统时间回拨，拒绝运行")
                if current >= expires_at:
                    raise LicenseExpiredError("许可证已经到期")
                if feature not in payload["features"]:
                    raise LicenseDeniedError(f"许可证未授权功能: {feature}")
            finally:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
    except FileNotFoundError as exc:
        raise LicenseMissingError("程序尚未完成激活") from exc
    except PermissionError as exc:
        raise LicenseDeniedError("当前用户无权更新激活状态") from exc
    except json.JSONDecodeError as exc:
        raise LicenseInvalidError("激活状态JSON损坏") from exc
    return LicenseGrant(
        payload["license_id"],
        frozenset(payload["features"]),
        activated_at,
        expires_at,
    )
