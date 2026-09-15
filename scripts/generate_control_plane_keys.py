from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

KEY_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,127}$")


def write_new(path: Path, data: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    except Exception:
        path.unlink(missing_ok=True)
        raise


def generate_pair(root: Path, purpose: str, key_id: str) -> str:
    key = Ed25519PrivateKey.generate()
    private_path = root / f"{purpose}-{key_id}.private.pem"
    public_path = root / f"{purpose}-{key_id}.public.pem"
    private_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    public_pem = key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    write_new(private_path, private_pem)
    write_new(public_path, public_pem)
    return public_pem.decode()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate separate Ed25519 lease/release keys in an operator-owned directory."
    )
    parser.add_argument("output_directory", type=Path)
    parser.add_argument("--lease-key-id", required=True)
    parser.add_argument("--release-key-id", required=True)
    args = parser.parse_args()
    if not KEY_ID.fullmatch(args.lease_key_id) or not KEY_ID.fullmatch(
        args.release_key_id
    ):
        raise SystemExit("Key IDs have an invalid format.")
    root = args.output_directory.expanduser().resolve()
    if root.exists():
        raise SystemExit("Output directory already exists; refusing to overwrite keys.")
    root.mkdir(parents=True, mode=0o700)
    lease_public = generate_pair(root, "lease", args.lease_key_id)
    release_public = generate_pair(root, "release", args.release_key_id)
    public_bundle = {
        "license_public_keys": {args.lease_key_id: lease_public},
        "release_public_keys": {args.release_key_id: release_public},
    }
    write_new(
        root / "desktop-public-keys.json",
        json.dumps(public_bundle, sort_keys=True, indent=2).encode(),
    )
    print(
        "Keys generated. Move private PEM files into KMS/HSM-backed secret storage; "
        "only desktop-public-keys.json may enter the client build environment."
    )


if __name__ == "__main__":
    main()
