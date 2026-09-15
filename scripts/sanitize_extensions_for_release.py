from __future__ import annotations

import argparse
import json
import re
import zipfile
from pathlib import Path

CREDENTIAL_NAME = re.compile(
    r"(?:api[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret|password|license[_-]?key)$",
    re.IGNORECASE,
)


def scrub(value: object) -> tuple[object, int]:
    if isinstance(value, dict):
        result: dict[str, object] = {}
        count = 0
        for key, item in value.items():
            if CREDENTIAL_NAME.search(str(key)) and isinstance(item, str) and item:
                result[str(key)] = ""
                count += 1
            else:
                result[str(key)], nested = scrub(item)
                count += nested
        return result, count
    if isinstance(value, list):
        result = []
        count = 0
        for item in value:
            cleaned, nested = scrub(item)
            result.append(cleaned)
            count += nested
        return result, count
    return value, 0


def sanitize_archive(source: Path, destination: Path) -> int:
    changed = 0
    with (
        zipfile.ZipFile(source, "r") as incoming,
        zipfile.ZipFile(
            destination, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
        ) as outgoing,
    ):
        for name in sorted(incoming.namelist()):
            normalized = name.replace("\\", "/").lstrip("/")
            if not normalized or normalized.casefold().startswith("meta-inf/"):
                continue
            raw = incoming.read(name)
            if normalized.lower().endswith(".json"):
                try:
                    parsed = json.loads(raw.decode("utf-8-sig"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    parsed = None
                if parsed is not None:
                    parsed, item_count = scrub(parsed)
                    if item_count:
                        raw = json.dumps(
                            parsed, ensure_ascii=False, separators=(",", ":")
                        ).encode("utf-8")
                        changed += item_count
            info = zipfile.ZipInfo(normalized, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            outgoing.writestr(info, raw)
    return changed


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create credential-free unsigned extension archives for a customer binary."
    )
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    source = args.source.resolve()
    destination = args.destination.resolve()
    if not source.is_dir():
        raise SystemExit("Extension source directory does not exist.")
    destination.mkdir(parents=True, exist_ok=False)
    archives = sorted(
        path
        for path in source.iterdir()
        if path.is_file() and path.suffix.lower() in {".xpi", ".zip"}
    )
    if not archives:
        raise SystemExit("No top-level extension archives were found.")
    total = 0
    for archive in archives:
        total += sanitize_archive(archive, destination / archive.name)
    if total == 0:
        raise SystemExit(
            "No credential fields were found; release policy requires an explicit sanitization result."
        )
    print(
        f"Sanitized {len(archives)} extension archive(s); cleared {total} credential field(s)."
    )


if __name__ == "__main__":
    main()
