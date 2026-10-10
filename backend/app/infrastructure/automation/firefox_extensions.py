"""Install Firefox WebExtensions before the browser process starts.

Recent Firefox builds no longer discover an XPI merely because it was copied
to ``<profile>/extensions``.  Firefox still supports application-distributed
extensions from ``<firefox>/distribution/extensions``.  This module performs
that installation atomically so concurrent invisible_playwright sessions never
observe a partially-written archive.

The caller owns extension configuration.  In particular, this module never
logs or inspects resources such as API keys embedded in an XPI.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import threading
import zipfile
from pathlib import Path
from typing import Iterable


_INSTALL_LOCK = threading.Lock()
_ADDON_ID_RE = re.compile(r"^[A-Za-z0-9@._{}+-]+$")


class FirefoxExtensionInstallError(RuntimeError):
    """A requested Firefox extension could not be installed safely."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def firefox_extension_id(xpi_path: os.PathLike[str] | str) -> str:
    """Read and validate the Gecko ID declared by an XPI manifest."""

    source = Path(xpi_path).expanduser().resolve()
    if not source.is_file():
        raise FirefoxExtensionInstallError(f"Firefox extension does not exist: {source}")
    try:
        with zipfile.ZipFile(source) as archive:
            manifest = json.loads(archive.read("manifest.json").decode("utf-8-sig"))
    except (KeyError, UnicodeDecodeError, json.JSONDecodeError, zipfile.BadZipFile) as exc:
        raise FirefoxExtensionInstallError(
            f"Firefox extension has no valid root manifest.json: {source}"
        ) from exc

    addon_id = None
    for key in ("browser_specific_settings", "applications"):
        gecko = (manifest.get(key) or {}).get("gecko") if isinstance(manifest, dict) else None
        if isinstance(gecko, dict) and gecko.get("id"):
            addon_id = str(gecko["id"])
            break
    if not addon_id or not _ADDON_ID_RE.fullmatch(addon_id):
        raise FirefoxExtensionInstallError(
            f"Firefox extension has no valid Gecko ID: {source}"
        )
    return addon_id


def install_firefox_extensions(
    firefox_executable: os.PathLike[str] | str,
    xpi_paths: Iterable[os.PathLike[str] | str],
    remove_addon_ids: Iterable[str] = (),
) -> tuple[Path, ...]:
    """Atomically synchronize managed XPIs in the Firefox distribution.

    The destination is tied to the resolved engine binary, so a future
    invisible_playwright engine upgrade automatically receives the extensions
    again on its first launch. Existing identical files are left untouched.
    Explicit removals make a per-launch off switch reliable even when an XPI
    was installed into this shared engine by an earlier profile.
    """

    executable = Path(firefox_executable).expanduser().resolve()
    if not executable.is_file():
        raise FirefoxExtensionInstallError(f"Firefox executable does not exist: {executable}")
    sources = [Path(value).expanduser().resolve() for value in xpi_paths]
    removals = {str(value) for value in remove_addon_ids}
    invalid_removals = sorted(value for value in removals if not _ADDON_ID_RE.fullmatch(value))
    if invalid_removals:
        raise FirefoxExtensionInstallError(
            f"Invalid Firefox extension removal ID: {invalid_removals[0]!r}"
        )
    if not sources and not removals:
        return ()

    prepared = [(source, firefox_extension_id(source)) for source in sources]
    removals.difference_update(addon_id for _, addon_id in prepared)
    destination_dir = executable.parent / "distribution" / "extensions"
    installed: list[Path] = []

    with _INSTALL_LOCK:
        destination_dir.mkdir(parents=True, exist_ok=True)
        for addon_id in sorted(removals):
            destination = destination_dir / f"{addon_id}.xpi"
            try:
                destination.unlink(missing_ok=True)
            except OSError as exc:
                raise FirefoxExtensionInstallError(
                    f"Could not remove Firefox extension {addon_id} from {destination_dir}: {exc}"
                ) from exc
        for source, addon_id in prepared:
            destination = destination_dir / f"{addon_id}.xpi"
            if destination.is_file() and _sha256(destination) == _sha256(source):
                installed.append(destination)
                continue

            temporary: Path | None = None
            try:
                with tempfile.NamedTemporaryFile(
                    prefix=f".{addon_id}.",
                    suffix=".tmp",
                    dir=destination_dir,
                    delete=False,
                ) as target, source.open("rb") as origin:
                    shutil.copyfileobj(origin, target, length=1024 * 1024)
                    target.flush()
                    os.fsync(target.fileno())
                    temporary = Path(target.name)
                if _sha256(temporary) != _sha256(source):
                    raise FirefoxExtensionInstallError(
                        f"Firefox extension copy verification failed: {source}"
                    )
                os.replace(temporary, destination)
                temporary = None
            except OSError as exc:
                raise FirefoxExtensionInstallError(
                    f"Could not install Firefox extension {addon_id} into {destination_dir}: {exc}"
                ) from exc
            finally:
                if temporary is not None:
                    try:
                        temporary.unlink(missing_ok=True)
                    except OSError:
                        pass
            installed.append(destination)
    return tuple(installed)


def sync_engine_extensions(
    xpi_paths: Iterable[os.PathLike[str] | str],
    remove_addon_ids: Iterable[str] = (),
) -> tuple[Path, ...]:
    """Install these XPIs into the engine this session is about to launch.

    ⛔ THIS IS THE SEAM THAT USED TO BE A FORK. invisible_playwright had a
    patched `launcher.set_firefox_extensions()` that called the function above
    just before launching, and carrying that patch is what made this project
    vendor the whole library as a submodule - with a merge conflict on every
    upstream release, and 0.30.0 deleting files other patches lived in.

    It turns out the hook was a convenience, not a requirement: the installer
    only needs the engine's executable path, and upstream exposes
    `ensure_binary()` publicly ("Return a verified path to the sealed Firefox
    executable"). So the same work happens here, from our own code, against
    the official package.

    ⛔ THE IMPORT IS LAZY ON PURPOSE. Everything above this function takes a
    path and touches no browser library, which is why it could move out of the
    fork at all; importing invisible_playwright at module scope would hand that
    property back and make the installer untestable without the engine.

    ⛔ AND IT MUST RUN BEFORE THE BROWSER STARTS. Firefox reads
    `distribution/extensions` at startup, so installing after launch changes
    nothing until the next one.
    """
    from invisible_playwright import ensure_binary

    return install_firefox_extensions(
        ensure_binary(), xpi_paths, remove_addon_ids
    )


__all__ = [
    "sync_engine_extensions",
    "FirefoxExtensionInstallError",
    "firefox_extension_id",
    "install_firefox_extensions",
]
