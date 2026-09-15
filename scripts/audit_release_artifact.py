from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

FORBIDDEN_SUFFIXES = {
    ".py",
    ".pyc",
    ".pyo",
    ".map",
    ".env",
    ".db",
    ".sqlite",
    ".sqlite3",
    ".db-wal",
    ".db-shm",
}
FORBIDDEN_NAMES = {".git", ".env", "cookies.txt", "test_cookies.txt"}
SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-[0-9A-Za-z.-]+)?$")


def fail(message: str) -> None:
    print(f"RELEASE AUDIT FAILED: {message}", file=sys.stderr)
    raise SystemExit(1)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fail closed if a commercial release leaks source/runtime state."
    )
    parser.add_argument("artifact_dir", type=Path)
    parser.add_argument("--component", choices=("backend", "desktop"), required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument(
        "--secret-env",
        action="append",
        default=[],
        help="Environment variable whose value must not occur in the binary.",
    )
    args = parser.parse_args()

    root = args.artifact_dir.resolve()
    if not SEMVER.fullmatch(args.version):
        fail("version is not strict semantic versioning")
    if not root.is_dir():
        fail("artifact directory does not exist")

    files = [path for path in root.rglob("*") if path.is_file()]
    forbidden = []
    for path in files:
        relative = path.relative_to(root)
        lowered_parts = {part.lower() for part in relative.parts}
        lowered = path.name.lower()
        if lowered_parts & FORBIDDEN_NAMES or any(
            lowered.endswith(suffix) for suffix in FORBIDDEN_SUFFIXES
        ):
            forbidden.append(str(relative))
        if lowered.startswith("cookies") and lowered.endswith(".txt"):
            forbidden.append(str(relative))
    if forbidden:
        fail("forbidden files are present: " + ", ".join(sorted(set(forbidden))))

    executables = [path for path in files if path.suffix.lower() == ".exe"]
    if args.component == "backend" and len(files) != 1:
        fail("backend release must contain exactly one file")
    if len(executables) != 1:
        fail("release must contain exactly one Windows executable")
    executable = executables[0]
    with executable.open("rb") as stream:
        if stream.read(2) != b"MZ":
            fail("artifact does not have a Windows PE header")

    secret_values = []
    for name in args.secret_env:
        value = os.environ.get(name, "")
        if value and len(value.encode()) >= 8:
            secret_values.append(value.encode())
    if secret_values:
        binary = executable.read_bytes()
        if any(secret in binary for secret in secret_values):
            fail("an operator-supplied secret value is present in the executable")

    print(
        f"Release audit passed: {args.component} {args.version}, "
        f"{executable.name}, {executable.stat().st_size} bytes"
    )


if __name__ == "__main__":
    main()
