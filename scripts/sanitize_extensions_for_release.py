"""Chuẩn bị extension cho bản phát hành: không mang theo credential của ai.

⛔ ARCHIVE ĐÃ KÝ THÌ COPY NGUYÊN XI, KHÔNG SỬA MỘT BYTE. Bản đầu của script
này mở mọi XPI ra, xoá trường credential trong JSON, rồi ghi lại — và **bỏ hẳn
`META-INF/`**. Việc đó phá chữ ký Mozilla, nên runtime từ chối:

    RuntimeError: OmoCaptcha XPI signature was not preserved

Và Firefox cũng không kích hoạt add-on không chữ ký (`signedState > 0` là điều
kiện trong `playwright_adapter`). Nghĩa là **mọi bản biên dịch đều có OmoCaptcha
hỏng** — lỗi chỉ lộ ra khi ai đó mở trình duyệt bằng bản đã đóng gói, không bao
giờ thấy khi chạy dev vì dev dùng thẳng XPI gốc.

Cái giá của việc sửa XPI đó là gì? Nó chỉ thay `api_key` từ `"YOUR_CLIEN_KEY"`
— **chỗ trống của nhà cung cấp** — thành `""`. Tức phá chữ ký để đổi một
placeholder lấy một chuỗi rỗng. Không đáng, và khoá thật thì không nằm ở đó:
nó được tiêm vào `browser.storage.local` lúc chạy, từ Credential Manager của
từng máy.

⛔ VÀ CHÍNH SÁCH ĐƯỢC ĐỔI CHO ĐÚNG THỨ CẦN BẢO ĐẢM. Bản cũ bắt buộc "phải xoá
được ít nhất một trường, không thì dừng build" — một phép đo gián tiếp, và nó
buộc script phải sửa archive mới coi là đã làm việc. Giờ build dừng khi **còn
sót một credential trông như thật** trong bất kỳ archive nào sẽ đóng gói. Đó là
điều thật sự cần: không ship khoá của ai. Nó đúng kể cả khi không có gì phải
sửa.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import zipfile
from pathlib import Path

CREDENTIAL_NAME = re.compile(
    r"(?:api[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret|password|license[_-]?key)$",
    re.IGNORECASE,
)

#: Chỗ trống do nhà cung cấp để sẵn, không phải khoá của ai: chữ in hoa, số,
#: gạch dưới, gạch ngang. Ví dụ thật trong XPI OmoCaptcha: `YOUR_CLIEN_KEY`.
#:
#: ⛔ Phân biệt được hai thứ này là điều kiện để chính sách ở trên dùng được.
#: Coi placeholder là credential thì build không bao giờ qua; coi mọi thứ là
#: placeholder thì chính sách thành vô nghĩa.
PLACEHOLDER = re.compile(r"^[A-Z0-9_\- ]+$")


def looks_like_a_real_credential(value: object) -> bool:
    if not isinstance(value, str):
        return False
    text = value.strip()
    if not text:
        return False
    return not PLACEHOLDER.fullmatch(text)


def scrub(value: object) -> tuple[object, int]:
    if isinstance(value, dict):
        result: dict[str, object] = {}
        count = 0
        for key, item in value.items():
            if CREDENTIAL_NAME.search(str(key)) and looks_like_a_real_credential(item):
                result[str(key)] = ""
                count += 1
            else:
                result[str(key)], nested = scrub(item)
                count += nested
        return result, count
    if isinstance(value, list):
        cleaned_list: list[object] = []
        count = 0
        for item in value:
            cleaned, nested = scrub(item)
            cleaned_list.append(cleaned)
            count += nested
        return cleaned_list, count
    return value, 0


def is_signed(archive: Path) -> bool:
    with zipfile.ZipFile(archive) as incoming:
        return any(
            name.replace("\\", "/").lstrip("/").casefold().startswith("meta-inf/")
            for name in incoming.namelist()
        )


def sanitize_archive(source: Path, destination: Path) -> int:
    """Ghi lại archive KHÔNG ký, đã xoá credential. Trả về số trường đã xoá."""
    changed = 0
    with (
        zipfile.ZipFile(source, "r") as incoming,
        zipfile.ZipFile(
            destination, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
        ) as outgoing,
    ):
        for name in sorted(incoming.namelist()):
            normalized = name.replace("\\", "/").lstrip("/")
            if not normalized:
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


def leaked_credentials(archive: Path) -> list[str]:
    """Những trường credential trông như thật còn sót trong archive."""
    found: list[str] = []

    def walk(value: object, path: str) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                where = f"{path}.{key}" if path else str(key)
                if CREDENTIAL_NAME.search(str(key)) and looks_like_a_real_credential(item):
                    found.append(where)
                else:
                    walk(item, where)
        elif isinstance(value, list):
            for index, item in enumerate(value):
                walk(item, f"{path}[{index}]")

    with zipfile.ZipFile(archive) as incoming:
        for name in incoming.namelist():
            if not name.lower().endswith(".json"):
                continue
            try:
                parsed = json.loads(incoming.read(name).decode("utf-8-sig"))
            except (UnicodeDecodeError, json.JSONDecodeError, KeyError):
                continue
            walk(parsed, f"{name}")
    return found


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Chuan bi extension cho ban phat hanh, khong mang credential."
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

    cleared = 0
    kept_signed = 0
    for archive in archives:
        target = destination / archive.name
        if is_signed(archive):
            # ⛔ Nguyên xi. Sửa một byte là chữ ký hỏng, và Firefox sẽ không
            # kích hoạt add-on - lỗi chỉ lộ ra ở máy khách khi mở trình duyệt.
            shutil.copy2(archive, target)
            kept_signed += 1
        else:
            cleared += sanitize_archive(archive, target)

    leaked: dict[str, list[str]] = {}
    for archive in sorted(destination.iterdir()):
        if archive.suffix.lower() not in {".xpi", ".zip"}:
            continue
        where = leaked_credentials(archive)
        if where:
            leaked[archive.name] = where
    if leaked:
        lines = "\n".join(
            f"  {name}: {', '.join(fields)}" for name, fields in leaked.items()
        )
        # ⛔ MỌI THỨ IN RA ĐỀU KHÔNG DẤU, như các script build khác trong repo.
        # stdout của build đi qua PowerShell với codepage cp1252, và một chữ có
        # dấu ở đây làm `UnicodeEncodeError` giết cả build ở đúng bước cuối —
        # sau khi đã làm xong việc. Chú thích thì giữ dấu, vì chúng không in ra.
        raise SystemExit(
            "Credential con sot trong extension sap dong goi - dung build:\n"
            f"{lines}\n"
            "Archive da ky khong duoc sua, nen credential phai duoc go khoi "
            "nguon chu khong phai go luc build."
        )

    print(
        f"Extension: {kept_signed} archive da ky (giu nguyen chu ky), "
        f"{len(archives) - kept_signed} archive khong ky (xoa {cleared} truong); "
        "khong con credential nao trong nhu that."
    )


if __name__ == "__main__":
    main()
