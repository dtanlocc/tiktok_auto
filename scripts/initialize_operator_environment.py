"""Create an operator-owned control-plane secret/key directory once."""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import secrets
from pathlib import Path
from urllib.parse import quote

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

KEY_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,127}$")
DB_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")


def write_new(path: Path, data: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def random_secret(size: int = 48) -> str:
    return base64.urlsafe_b64encode(secrets.token_bytes(size)).rstrip(b"=").decode()


def key_pair(keys_dir: Path, purpose: str) -> str:
    key = Ed25519PrivateKey.generate()
    write_new(
        keys_dir / f"{purpose}-private.pem",
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ),
    )
    public = key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    write_new(keys_dir / f"{purpose}-public.pem", public)
    return public.decode("ascii")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output_directory", type=Path)
    parser.add_argument("--database-host", required=True)
    parser.add_argument("--database-user", required=True)
    parser.add_argument("--database-name", required=True)
    parser.add_argument("--lease-key-id", required=True)
    parser.add_argument("--release-key-id", required=True)
    args = parser.parse_args()
    for key_id in (args.lease_key_id, args.release_key_id):
        if not KEY_ID.fullmatch(key_id):
            raise SystemExit("Signing key ID is invalid.")
    if not DB_NAME.fullmatch(args.database_user) or not DB_NAME.fullmatch(
        args.database_name
    ):
        raise SystemExit("Database user or database name is invalid.")
    if not args.database_host or any(
        character.isspace() for character in args.database_host
    ):
        raise SystemExit("Database host is invalid.")

    root = args.output_directory.expanduser().resolve()
    if root.exists():
        raise SystemExit(
            "Output directory already exists; refusing to overwrite secrets."
        )
    keys_dir = root / "keys"
    secrets_dir = root / "secrets"
    artifacts_dir = root / "artifacts"
    keys_dir.mkdir(parents=True, mode=0o700)
    secrets_dir.mkdir(mode=0o700)
    artifacts_dir.mkdir(mode=0o700)

    lease_public = key_pair(keys_dir, "lease")
    release_public = key_pair(keys_dir, "release")
    postgres_password = random_secret(36)
    database_url = (
        "postgresql+psycopg://"
        f"{quote(args.database_user, safe='')}:{quote(postgres_password, safe='')}"
        f"@{args.database_host}:5432/{quote(args.database_name, safe='')}"
    )
    values = {
        "postgres-password": postgres_password,
        "database-url": database_url,
        "admin-token": random_secret(),
        "license-key-pepper": random_secret(),
        "download-grant-secret": random_secret(),
    }
    for name, value in values.items():
        write_new(secrets_dir / name, (value + "\n").encode("utf-8"))
    write_new(
        root / "desktop-public-keys.json",
        json.dumps(
            {
                "license_public_keys": {args.lease_key_id: lease_public},
                "release_public_keys": {args.release_key_id: release_public},
            },
            indent=2,
            sort_keys=True,
        ).encode("utf-8"),
    )
    print(f"Operator environment created at {root}")
    print("Back it up encrypted before deploying. Never copy it to a customer machine.")


if __name__ == "__main__":
    main()
