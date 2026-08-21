#!/usr/bin/env python3
"""Issue a machine-bound encrypted and signed offline license."""

import argparse
import base64
import getpass
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.serialization import load_pem_private_key

from ydcore.licensing import (
    LICENSE_AAD,
    SCHEMA_VERSION,
    canonical_json,
    derive_license_key,
)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--private-key", required=True, type=Path)
    parser.add_argument("--machine-code", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--license-id", default=f"YD-{uuid.uuid4().hex[:16]}")
    parser.add_argument(
        "--features",
        nargs="+",
        choices=("order", "monitor", "marketdata"),
        default=("order", "monitor", "marketdata"),
    )
    parser.add_argument("--ttl-hours", type=float, default=24.0)
    args = parser.parse_args(argv)
    if args.output.exists():
        parser.error(f"refusing to overwrite existing license: {args.output}")
    ttl_seconds = round(args.ttl_hours * 3600)
    if not 1 <= ttl_seconds <= 30 * 24 * 60 * 60:
        parser.error("ttl must be between one second and 30 days")
    private_password = getpass.getpass("私钥密码（无密码直接回车）: ").encode("utf-8") or None
    private_key = load_pem_private_key(args.private_key.read_bytes(), private_password)
    run_password = getpass.getpass("设置运行密码: ")
    confirmation = getpass.getpass("再次输入运行密码: ")
    if not run_password or run_password != confirmation:
        parser.error("run passwords do not match")
    payload = {
        "schema": SCHEMA_VERSION,
        "license_id": args.license_id,
        "machine_code": args.machine_code.lower(),
        "features": sorted(set(args.features)),
        "ttl_seconds": ttl_seconds,
        "issued_at": datetime.now(timezone.utc).isoformat(),
    }
    kdf = {
        "algorithm": "argon2id",
        "salt": base64.b64encode(os.urandom(16)).decode("ascii"),
        "time_cost": 3,
        "memory_cost_kib": 65536,
        "parallelism": 2,
        "length": 32,
    }
    key = derive_license_key(run_password, kdf)
    nonce = os.urandom(12)
    cipher = {
        "algorithm": "AES-256-GCM",
        "nonce": base64.b64encode(nonce).decode("ascii"),
        "ciphertext": base64.b64encode(
            AESGCM(key).encrypt(nonce, canonical_json(payload), LICENSE_AAD)
        ).decode("ascii"),
    }
    envelope = {"schema": SCHEMA_VERSION, "kdf": kdf, "cipher": cipher}
    envelope["signature"] = base64.b64encode(
        private_key.sign(canonical_json(envelope))
    ).decode("ascii")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(envelope, indent=2), encoding="utf-8")
    os.chmod(args.output, 0o600)
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
