"""Verify the OmoCaptcha credential that used to be embedded is really gone.

⛔ IT READS THE SECRET OUT OF HISTORY, NOT OUT OF HEAD. The first version asked
`git show HEAD:backend/app/core/config.py` for the literal to scan for, which
can only work while HEAD still carries it - and the commit that introduced this
script (5fcb271, 2026-09-15) is the same one that emptied the field. So the gate
was unable to run from the moment it was written: every invocation since has
answered "Could not identify the prior embedded credential" and failed the
release. Measured 04/10/2026 packaging friends 0.1.10, which it stopped after
both executables had been built and the package audit had passed.

Walking back to the newest commit whose config.py still held a non-empty value
restores the check's actual purpose, and keeps working no matter how far the
removal recedes into the past.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

_PATTERN = re.compile(r'OMOCAPTCHA_KEY:\s*str\s*=\s*"([^"]*)"')
_CONFIG = "backend/app/core/config.py"


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="ignore",
        check=False,
    ).stdout


def previous_credential(root: Path) -> tuple[str, bytes] | None:
    """(commit, credential) from the newest commit that still embedded one."""
    history = _git(root, "log", "--format=%H", "--", _CONFIG).split()
    for commit in history:
        found = _PATTERN.search(_git(root, "show", f"{commit}:{_CONFIG}"))
        if found and found.group(1):
            return commit, found.group(1).encode()
    return None


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    carried = previous_credential(root)
    if carried is None:
        # Nothing was ever embedded, so there is nothing for this regression to
        # be about. Said out loud rather than passed silently: a gate that
        # scanned for nothing must not read like a gate that found nothing.
        print(
            "No embedded OmoCaptcha credential appears anywhere in this file's "
            "history; there is nothing for this scan to look for."
        )
        return
    commit, secret = carried
    hits: list[str] = []
    roots = (
        root / "backend",
        root / "frontend" / "src",
        root / "control_plane",
        root / "scripts",
        # ⛔ `release/friends` IS NOT SCANNED, and that is the original scope,
        # kept deliberately. The friends package embeds the signed OmoCaptcha
        # XPI with the packaging machine's own configuration inside it - its
        # README says so and tells the recipient to rotate the key. Adding that
        # directory here would turn a documented decision into a build failure.
        root / "release" / "backend",
    )
    for search_root in roots:
        if not search_root.exists():
            continue
        for path in search_root.rglob("*"):
            # Local ignored extensions may contain the currently configured
            # operator credential. Release builds consume only a scrubbed
            # staging copy, and the packaged XPI is deliberately the signed
            # original - audit_friends_package.py speaks for the package.
            if path.is_relative_to(root / "backend" / "extensions"):
                continue
            if not path.is_file() or any(
                part in {".venv", "node_modules", "target", "__pycache__"}
                for part in path.parts
            ):
                continue
            try:
                contains_secret = secret in path.read_bytes()
            except OSError:
                continue
            if contains_secret:
                hits.append(str(path.relative_to(root)))
    if hits:
        print("Removed credential is still present in: " + ", ".join(hits))
        raise SystemExit(1)
    print(
        f"Removed embedded credential (last carried in {commit[:9]}) is absent "
        "from protected source and release artifacts."
    )


if __name__ == "__main__":
    main()
