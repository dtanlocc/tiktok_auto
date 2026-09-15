"""Generate the static Tauri v2 updater JSON after artifact signing."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
from pathlib import Path
from urllib.parse import urlsplit

SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-[0-9A-Za-z.-]+)?$")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--artifact-url", required=True)
    parser.add_argument("--signature-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--notes", default="Security and reliability update.")
    args = parser.parse_args()
    if not SEMVER.fullmatch(args.version):
        raise SystemExit("Version must be semantic versioning.")
    parsed = urlsplit(args.artifact_url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise SystemExit(
            "Updater artifact URL must be HTTPS without credentials or fragment."
        )
    signature_path = args.signature_file.expanduser().resolve()
    signature = signature_path.read_text(encoding="utf-8").strip()
    if not signature or len(signature) > 16_384 or "\n" in signature:
        raise SystemExit("Updater signature file is empty or malformed.")
    output = args.output.expanduser().resolve()
    if output.exists():
        raise SystemExit("Updater feed already exists; refusing to overwrite it.")
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": args.version,
        "notes": args.notes,
        "pub_date": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        "platforms": {
            "windows-x86_64": {
                "signature": signature,
                "url": args.artifact_url,
            }
        },
    }
    output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Tauri updater feed ready: {output}")


if __name__ == "__main__":
    main()
