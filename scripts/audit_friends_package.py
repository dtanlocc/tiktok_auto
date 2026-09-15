"""Fail closed when a Friends package contains source, state, or extra payloads."""

from __future__ import annotations

import argparse
import hashlib
import re
from pathlib import Path

EXPECTED_FILES = {
    "TikTokAuto-Friends.exe",
    "TikTokAuto-Backend.exe",
    "README.txt",
    "SHA256SUMS.txt",
}
FORBIDDEN_SUFFIXES = {
    ".py",
    ".pyc",
    ".pyo",
    ".ts",
    ".tsx",
    ".map",
    ".env",
    ".db",
    ".sqlite",
    ".sqlite3",
    ".db-wal",
    ".db-shm",
}
HASH_LINE = re.compile(r"^[A-F0-9]{64}  (TikTokAuto-(?:Friends|Backend)\.exe)$")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package_dir", type=Path)
    args = parser.parse_args()
    root = args.package_dir.expanduser().resolve()
    if not root.is_dir():
        raise SystemExit("Friends package directory does not exist.")
    files = [path for path in root.rglob("*") if path.is_file()]
    relative = {path.relative_to(root).as_posix() for path in files}
    if relative != EXPECTED_FILES:
        raise SystemExit(
            "Friends package has unexpected or missing files: "
            + ", ".join(sorted(relative ^ EXPECTED_FILES))
        )
    if any(path.suffix.lower() in FORBIDDEN_SUFFIXES for path in files):
        raise SystemExit("Friends package contains a forbidden source/state file.")
    for name in ("TikTokAuto-Friends.exe", "TikTokAuto-Backend.exe"):
        path = root / name
        with path.open("rb") as stream:
            if stream.read(2) != b"MZ":
                raise SystemExit(f"{name} is not a Windows executable.")
    checksum_lines = (root / "SHA256SUMS.txt").read_text(encoding="ascii").splitlines()
    if len(checksum_lines) != 2 or any(
        HASH_LINE.fullmatch(line) is None for line in checksum_lines
    ):
        raise SystemExit("Friends checksum file is malformed.")
    expected_hashes = {
        line.split("  ", 1)[1]: line.split("  ", 1)[0] for line in checksum_lines
    }
    for name, expected in expected_hashes.items():
        actual = hashlib.sha256((root / name).read_bytes()).hexdigest().upper()
        if actual != expected:
            raise SystemExit(f"Checksum mismatch for {name}.")
    print("Friends package audit passed: two EXEs, README, checksums, no source/state.")


if __name__ == "__main__":
    main()
