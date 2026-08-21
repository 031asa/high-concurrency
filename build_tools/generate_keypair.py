#!/usr/bin/env python3
"""Generate an encrypted Ed25519 issuer key and its public key."""

import argparse
import getpass
import os
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    BestAvailableEncryption,
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--private-key", required=True, type=Path)
    parser.add_argument("--public-key", required=True, type=Path)
    parser.add_argument(
        "--unencrypted-private-key",
        action="store_true",
        help="not recommended; intended only for isolated CI secret storage",
    )
    args = parser.parse_args(argv)
    for path in (args.private_key, args.public_key):
        if path.exists():
            parser.error(f"refusing to overwrite existing key: {path}")
        path.parent.mkdir(parents=True, exist_ok=True)
    if args.unencrypted_private_key:
        encryption = NoEncryption()
    else:
        password = getpass.getpass("新私钥密码: ")
        confirmation = getpass.getpass("再次输入私钥密码: ")
        if not password or password != confirmation:
            parser.error("private-key passwords do not match")
        encryption = BestAvailableEncryption(password.encode("utf-8"))
    private_key = Ed25519PrivateKey.generate()
    args.private_key.write_bytes(
        private_key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, encryption)
    )
    os.chmod(args.private_key, 0o600)
    args.public_key.write_bytes(
        private_key.public_key().public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo)
    )
    os.chmod(args.public_key, 0o644)
    print(args.public_key)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
