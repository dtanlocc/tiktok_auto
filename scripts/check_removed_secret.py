from __future__ import annotations

import re
import subprocess
from pathlib import Path


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    previous = subprocess.check_output(
        ["git", "show", "HEAD:backend/app/core/config.py"],
        cwd=root,
        text=True,
        encoding="utf-8",
        errors="ignore",
    )
    match = re.search(r'OMOCAPTCHA_KEY:\s*str\s*=\s*"([^"]+)"', previous)
    if match is None or not match.group(1):
        raise SystemExit(
            "Could not identify the prior embedded credential for regression scanning."
        )
    secret = match.group(1).encode()
    hits: list[str] = []
    roots = (
        root / "backend",
        root / "frontend" / "src",
        root / "control_plane",
        root / "scripts",
        root / "release" / "backend",
    )
    for search_root in roots:
        if not search_root.exists():
            continue
        for path in search_root.rglob("*"):
            # Local ignored extensions may contain the currently configured
            # operator credential. Release builds consume only a scrubbed
            # staging copy and are scanned separately below.
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
        "Removed embedded credential is absent from protected source and production artifacts."
    )


if __name__ == "__main__":
    main()
