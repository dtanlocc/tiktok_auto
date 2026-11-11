"""Nhan trinh duyet dang chay, va cho de sau nay cam them nhan khac.

⛔ MOT BAN BACKEND CHI CHUA NHUNG NHAN DA DUOC BIEN DICH VAO NO. Thu vien
trinh duyet di vao tep .exe luc build (Nuitka `--include-package`), nen
"doi nhan luc chay" chi co nghia TRONG SO nhung nhan co mat. Vi the
`set_browser_engine` tu choi mot nhan khong import duoc, thay vi ghi nhan do
xuong dia roi de trinh duyet chet o lan mo tiep theo - luc do khong con ai
noi duoc vi sao.

⛔ VA SO PHIEN BAN DOC TU `__version__` CUA MODULE, khong phai tu
`importlib.metadata`. Trong ban Nuitka khong co thu muc dist-info, nen
`metadata.version()` nem `PackageNotFoundError` - tuc man hinh "dang chay ban
nao" se trong rong o dung noi can no nhat: tren may khach.

Them mot nhan la them mot dong vao `ENGINES`.
"""
from __future__ import annotations

import importlib
from dataclasses import dataclass

from app.core.config import settings
from app.core.extension_settings import (
    _SETTINGS_LOCK,
    _read_document,
    _settings_path,
    _write_setting,
)

_BROWSER_ENGINE_KEY = "browser_engine"


@dataclass(frozen=True)
class BrowserEngine:
    """Mot nhan trinh duyet co the duoc bien dich vao ban backend."""

    key: str
    label: str
    #: Module cua lop dieu khien; thieu no la nhan khong co trong ban build.
    module: str
    #: Module giu seal engine (ban Firefox). None = nhan khong dung seal.
    seal_module: str | None = None


#: ⛔ THEM MOT NHAN LA THEM MOT DONG O DAY. Khong ham nao duoi day can sua.
ENGINES: dict[str, BrowserEngine] = {
    "invisible_playwright": BrowserEngine(
        key="invisible_playwright",
        label="invisible_playwright",
        module="invisible_playwright",
        seal_module="invisible_core",
    ),
}

DEFAULT_ENGINE = "invisible_playwright"


def _module_version(name: str) -> str:
    try:
        return str(getattr(importlib.import_module(name), "__version__", "")) or "?"
    except Exception:
        return "?"


def is_available(key: str) -> bool:
    """Nhan nay co THUC SU nam trong ban backend dang chay khong."""
    engine = ENGINES.get(key)
    if engine is None:
        return False
    try:
        importlib.import_module(engine.module)
    except Exception:
        return False
    return True


def describe(key: str) -> dict[str, object]:
    """Mo ta mot nhan: co mat khong, ban nao, engine Firefox doi nao."""
    engine = ENGINES.get(key)
    if engine is None:
        return {"key": key, "label": key, "available": False}
    info: dict[str, object] = {
        "key": engine.key,
        "label": engine.label,
        "available": is_available(key),
        "version": _module_version(engine.module),
    }
    if engine.seal_module:
        info["core_version"] = _module_version(engine.seal_module)
        try:
            seal = importlib.import_module(engine.seal_module).download.active_seal()
            info["engine_tag"] = seal.tag
            info["firefox"] = seal.upstream_version
        except Exception:
            # Seal doc duoc hay khong KHONG lam hong man hinh phien ban.
            info["engine_tag"] = "?"
            info["firefox"] = "?"
    return info


def load_browser_engine() -> str:
    """Nhan nguoi van hanh chon lan cuoi, song qua restart.

    Mot nhan da luu nhung khong con trong ban build (ha cap, hoac doi build)
    se bi bo qua va quay ve mac dinh - de app van chay duoc.
    """
    with _SETTINGS_LOCK:
        stored = _read_document(_settings_path()).get(_BROWSER_ENGINE_KEY)
    if isinstance(stored, str) and is_available(stored):
        return stored
    configured = str(getattr(settings, "BROWSER_ENGINE", "") or "")
    if is_available(configured):
        return configured
    return DEFAULT_ENGINE


def set_browser_engine(key: str) -> str:
    value = (key or "").strip()
    if value not in ENGINES:
        raise ValueError(
            f"Nhan trinh duyet khong co that: {key!r}. "
            f"Chon mot trong: {', '.join(sorted(ENGINES))}"
        )
    # ⛔ KHONG GHI MOT NHAN KHONG CO TRONG BAN BUILD. Ghi xuong thi trinh duyet
    # chet o lan mo tiep theo, cach xa cho da gay ra.
    if not is_available(value):
        raise ValueError(
            f"Nhan {value!r} khong co trong ban backend nay "
            f"({describe(value).get('version', '?')}). Can mot ban backend "
            "duoc build kem nhan do."
        )
    _write_setting(_BROWSER_ENGINE_KEY, value)
    settings.BROWSER_ENGINE = value
    return value


def runtime_versions() -> dict[str, object]:
    """Moi thu man hinh "dang chay ban nao" can, trong mot lan goi."""
    current = load_browser_engine()
    return {
        "app_version": str(getattr(settings, "APP_VERSION", "") or "?"),
        "browser_engine": current,
        "engine": describe(current),
        "engines": [describe(key) for key in sorted(ENGINES)],
    }
