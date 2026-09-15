from __future__ import annotations

import argparse
import hashlib
import http.server
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from pathlib import Path

import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.app.security.license import LeaseClaims, LeaseSigner
from backend.app.security.local_auth import LocalRequestSigner, generate_session_secret


_NATIVE_UPLOAD_PAGE = b"""<!doctype html><html><body>
<input id="media" type="file" accept="video/mp4" hidden>
<button id="choose" type="button" onclick="document.getElementById('media').click()">
Choose video
</button>
<output id="result"></output>
<script>
document.getElementById('media').addEventListener('change', event => {
  document.getElementById('result').textContent = event.target.files[0]?.name || '';
});
</script></body></html>"""


class _NativeUploadHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(_NATIVE_UPLOAD_PAGE)))
        self.end_headers()
        self.wfile.write(_NATIVE_UPLOAD_PAGE)

    def log_message(self, *_args: object) -> None:
        pass


def free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def assert_packaged_extension_is_sanitized(started_at: float) -> None:
    temp_root = Path(tempfile.gettempdir())
    candidates = sorted(
        (
            path
            for path in temp_root.glob("onefile_*")
            if path.is_dir() and path.stat().st_mtime >= started_at - 5
        ),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for directory in candidates:
        for archive in directory.rglob("*.xpi"):
            try:
                with zipfile.ZipFile(archive) as package:
                    names = package.namelist()
                    config_name = next(
                        name for name in names if name.lower().endswith("configs.json")
                    )
                    config = json.loads(package.read(config_name))
                    if str(config.get("api_key", "")).strip():
                        raise RuntimeError(
                            "Packaged extension still contains an API credential."
                        )
                    if any(name.casefold().startswith("meta-inf/") for name in names):
                        raise RuntimeError(
                            "Sanitized extension retained an invalid stale signature."
                        )
                    return
            except (zipfile.BadZipFile, KeyError, StopIteration, json.JSONDecodeError):
                continue
    raise RuntimeError(
        "Could not find the sanitized packaged extension in onefile payload."
    )


def assert_packaged_extension_is_preserved(
    started_at: float, expected_extension: Path
) -> None:
    """Require the onefile payload to contain the exact signed source XPI."""

    expected_extension = expected_extension.resolve()
    expected_hash = hashlib.sha256(expected_extension.read_bytes()).digest()
    temp_root = Path(tempfile.gettempdir())
    candidates = sorted(
        (
            path
            for path in temp_root.glob("onefile_*")
            if path.is_dir() and path.stat().st_mtime >= started_at - 5
        ),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for directory in candidates:
        for archive_path in directory.rglob(expected_extension.name):
            try:
                archive_bytes = archive_path.read_bytes()
                if hashlib.sha256(archive_bytes).digest() != expected_hash:
                    continue
                with zipfile.ZipFile(archive_path) as package:
                    names = package.namelist()
                    json.loads(package.read("configs.json").decode("utf-8-sig"))
                    if not any(
                        name.casefold().startswith("meta-inf/") for name in names
                    ):
                        raise RuntimeError(
                            "Preserved OmoCaptcha extension has no Mozilla signature."
                        )
                return
            except (zipfile.BadZipFile, KeyError, json.JSONDecodeError) as exc:
                raise RuntimeError(
                    "Preserved OmoCaptcha extension is not a valid signed XPI."
                ) from exc
    raise RuntimeError(
        "Packaged OmoCaptcha XPI is missing or differs from the signed source file."
    )


def assert_packaged_tk_runtime(started_at: float) -> None:
    """Require all native/data components used by the Windows file picker."""

    required = {"_tkinter.pyd", "tcl86t.dll", "tk86t.dll", "init.tcl"}
    temp_root = Path(tempfile.gettempdir())
    candidates = sorted(
        (
            path
            for path in temp_root.glob("onefile_*")
            if path.is_dir() and path.stat().st_mtime >= started_at - 5
        ),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for directory in candidates:
        packaged_names = {
            path.name.casefold() for path in directory.rglob("*") if path.is_file()
        }
        missing = required - packaged_names
        if not missing:
            return
    raise RuntimeError(
        "Packaged backend is missing the Tcl/Tk file-picker runtime: "
        + ", ".join(sorted(required))
    )


def assert_extension_browser_starts(
    *, signer: LocalRequestSigner, port: int
) -> None:
    """Open an offline blank Firefox long enough to validate extension startup."""

    start_path = "/api/v1/tasks/debug-blank"
    stop_path = "/api/v1/tasks/debug-blank/stop"
    active_path = "/api/v1/tasks/debug-blank/active"
    body = json.dumps(
        {"url": "about:blank", "proxy_id": None}, separators=(",", ":")
    ).encode()
    response = httpx.post(
        f"http://127.0.0.1:{port}{start_path}",
        content=body,
        headers={
            **signer.headers(method="POST", path=start_path, body=body),
            "Content-Type": "application/json",
        },
        timeout=10,
    )
    if response.status_code != 200:
        raise RuntimeError(
            f"Blank extension browser did not start: HTTP {response.status_code}."
        )
    stable_since: float | None = None
    deadline = time.monotonic() + 45
    try:
        while time.monotonic() < deadline:
            active_response = httpx.get(
                f"http://127.0.0.1:{port}{active_path}",
                headers=signer.headers(method="GET", path=active_path),
                timeout=5,
            )
            active = (
                active_response.status_code == 200
                and active_response.json().get("active") is True
            )
            if active:
                stable_since = stable_since or time.monotonic()
                if time.monotonic() - stable_since >= 15:
                    return
            else:
                stable_since = None
            time.sleep(0.5)
        raise RuntimeError(
            "Blank extension browser did not remain active after extension validation."
        )
    finally:
        try:
            httpx.post(
                f"http://127.0.0.1:{port}{stop_path}",
                headers=signer.headers(method="POST", path=stop_path),
                timeout=15,
            )
        except httpx.HTTPError:
            pass


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Smoke-test a compiled production or Friends backend binary."
    )
    parser.add_argument("executable", type=Path)
    parser.add_argument("--version", required=True)
    parser.add_argument("--friends", action="store_true")
    parser.add_argument("--expected-extension", type=Path)
    parser.add_argument("--require-tk-runtime", action="store_true")
    parser.add_argument("--test-extension-browser", action="store_true")
    parser.add_argument("--test-native-upload", action="store_true")
    parser.add_argument(
        "--python-source",
        action="store_true",
        help="Run the entry point with the current Python for source/binary A-B diagnostics.",
    )
    args = parser.parse_args()
    executable = args.executable.resolve()
    if not executable.is_file():
        raise SystemExit("Backend executable/source is missing.")
    if not args.python_source and executable.read_bytes()[:2] != b"MZ":
        raise SystemExit("Backend executable is not a Windows PE file.")

    secret = generate_session_secret()
    signer = LocalRequestSigner(secret)
    port = free_port()

    with tempfile.TemporaryDirectory(
        prefix="tiktok-auto-binary-smoke-",
        dir=REPO_ROOT / "release",
        ignore_cleanup_errors=True,
    ) as directory:
        root = Path(directory)
        native_server = None
        native_thread = None
        native_result = root / "native-upload-smoke.json"
        process_env = None
        if args.test_native_upload:
            native_media = root / "native-upload-smoke.mp4"
            native_media.write_bytes(b"packaged-native-upload-smoke")
            native_server = http.server.ThreadingHTTPServer(
                ("127.0.0.1", 0), _NativeUploadHandler
            )
            native_thread = threading.Thread(
                target=native_server.serve_forever,
                name="native-upload-smoke-page",
                daemon=True,
            )
            native_thread.start()
            process_env = dict(os.environ)
            process_env.update(
                {
                    "TKAUTO_NATIVE_UPLOAD_SMOKE_URL": (
                        f"http://127.0.0.1:{native_server.server_address[1]}"
                    ),
                    "TKAUTO_NATIVE_UPLOAD_SMOKE_FILE": str(native_media),
                    "TKAUTO_NATIVE_UPLOAD_SMOKE_RESULT": str(native_result),
                }
            )
        bootstrap = {
            "local_session_secret": secret,
            "app_version": args.version,
            "database_url": f"sqlite:///{(root / 'database.db').as_posix()}",
            "omocaptcha_key": "customer-owned-smoke-test-key",
            "port": port,
        }
        if not args.friends:
            signing_key = Ed25519PrivateKey.generate()
            key_id = "smoke-lease-key"
            device_id = "device_smoke_test"
            now = int(time.time())
            claims = LeaseClaims(
                license_id="license_smoke_test",
                device_id=device_id,
                plan="smoke",
                features=("app.start", "upload.video"),
                max_accounts=2,
                max_tabs=1,
                channel="internal",
                minimum_version=args.version,
                minimum_backend_version=args.version,
                issued_at=now,
                not_before=now - 5,
                expires_at=now + 600,
                jti="lease_smoke_test",
            )
            public_pem = (
                signing_key.public_key()
                .public_bytes(
                    serialization.Encoding.PEM,
                    serialization.PublicFormat.SubjectPublicKeyInfo,
                )
                .decode()
            )
            lease_path = root / "current.lease"
            lease_path.write_text(
                LeaseSigner(key_id, signing_key).sign(claims), encoding="utf-8"
            )
            bootstrap.update(
                {
                    "device_id": device_id,
                    "lease_path": str(lease_path),
                    "license_public_keys_json": json.dumps({key_id: public_pem}),
                }
            )
        creation_flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        log_path = root / "backend-smoke.log"
        log_stream = log_path.open("wb")
        started_at = time.time()
        command = (
            [sys.executable, str(executable)]
            if args.python_source
            else [str(executable)]
        )
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=log_stream,
            stderr=subprocess.STDOUT,
            creationflags=creation_flags,
            env=process_env,
        )
        log_stream.close()
        try:
            assert process.stdin is not None
            process.stdin.write((json.dumps(bootstrap) + "\n").encode())
            process.stdin.close()
            deadline = time.monotonic() + (150 if args.test_native_upload else 90)
            last_error: Exception | None = None
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    log_tail = log_path.read_text(encoding="utf-8", errors="replace")[
                        -6000:
                    ]
                    native_detail = ""
                    if native_result.is_file():
                        native_detail = "\n" + native_result.read_text(
                            encoding="utf-8", errors="replace"
                        )
                    raise RuntimeError(
                        f"Backend exited early with code {process.returncode}."
                        f"\n{log_tail}{native_detail}"
                    )
                try:
                    response = httpx.get(
                        f"http://127.0.0.1:{port}/",
                        headers=signer.headers(method="GET", path="/"),
                        timeout=2,
                    )
                    if (
                        response.status_code == 200
                        and response.json().get("status") == "ONLINE"
                    ):
                        if args.test_native_upload:
                            if not native_result.is_file():
                                raise RuntimeError(
                                    "Packaged native upload smoke produced no result."
                                )
                            native_status = json.loads(
                                native_result.read_text(encoding="utf-8")
                            )
                            if native_status.get("ok") is not True:
                                raise RuntimeError(
                                    f"Packaged native upload failed: {native_status}"
                                )
                        if not args.python_source:
                            if args.expected_extension:
                                assert_packaged_extension_is_preserved(
                                    started_at, args.expected_extension
                                )
                            else:
                                assert_packaged_extension_is_sanitized(started_at)
                            if args.require_tk_runtime:
                                assert_packaged_tk_runtime(started_at)
                            if args.test_extension_browser:
                                assert_extension_browser_starts(
                                    signer=signer, port=port
                                )
                        flavor = "Friends" if args.friends else "licensed"
                        print(f"Compiled {flavor} backend smoke test passed.")
                        return
                except Exception as exc:  # process startup is intentionally polled
                    last_error = exc
                time.sleep(0.5)
            raise RuntimeError(f"Backend did not become ready: {last_error}")
        finally:
            if sys.platform == "win32" and process.poll() is None:
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )
            elif process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
            # Antivirus/onefile extraction can retain the redirected log handle
            # briefly after the process tree has exited.
            time.sleep(1)
            if native_server is not None:
                native_server.shutdown()
                native_server.server_close()
            if native_thread is not None:
                native_thread.join(timeout=2)


if __name__ == "__main__":
    main()
