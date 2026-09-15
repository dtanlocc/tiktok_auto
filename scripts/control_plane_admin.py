"""Minimal operator CLI for the vendor-only license control plane.

The administrator token is read from a file or environment variable and is
never accepted in a URL. Raw customer license keys are printed exactly once by
the create-license command; redirect that output into the customer vault.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx


def secret_value(inline: str, file_path: str) -> str:
    if inline and file_path:
        raise SystemExit("Set the admin token or token file, not both.")
    value = inline
    if file_path:
        value = Path(file_path).expanduser().resolve().read_text(encoding="utf-8")
    value = value.strip()
    if len(value) < 32:
        raise SystemExit("The administrator token is missing or too short.")
    return value


def validated_base_url(value: str, allow_http_localhost: bool) -> str:
    parsed = urlsplit(value.strip())
    local_http = (
        allow_http_localhost
        and parsed.scheme == "http"
        and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    )
    if (
        not parsed.hostname
        or (parsed.scheme != "https" and not local_http)
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise SystemExit("Control-plane URL must be a clean HTTPS URL.")
    return value.rstrip("/")


def request_json(
    client: httpx.Client,
    method: str,
    path: str,
    *,
    token: str | None = None,
    payload: dict[str, object] | None = None,
) -> dict[str, object]:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    response = client.request(method, path, headers=headers, json=payload)
    if not response.is_success:
        detail = response.text[:2_000]
        raise SystemExit(
            f"Control-plane request failed ({response.status_code}): {detail}"
        )
    try:
        value = response.json()
    except ValueError as exc:
        raise SystemExit("Control-plane returned invalid JSON.") from exc
    if not isinstance(value, dict):
        raise SystemExit("Control-plane returned an unexpected response.")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Operate TikTok Auto licenses and releases."
    )
    parser.add_argument(
        "--base-url",
        default=os.environ.get("TKAUTO_OPERATOR_CONTROL_PLANE_URL", ""),
    )
    parser.add_argument(
        "--admin-token",
        default=os.environ.get("TKAUTO_CONTROL_ADMIN_TOKEN", ""),
    )
    parser.add_argument(
        "--admin-token-file",
        default=os.environ.get("TKAUTO_CONTROL_ADMIN_TOKEN_FILE", ""),
    )
    parser.add_argument(
        "--allow-http-localhost",
        action="store_true",
        help="Only for a local development control-plane test.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("health")

    create = subparsers.add_parser("create-license")
    create.add_argument("--customer-reference", default="")
    create.add_argument("--plan", default="pro")
    create.add_argument(
        "--features",
        default="accounts.login,profiles.update,upload.video,interaction.run,analytics.sync",
    )
    create.add_argument("--max-accounts", type=int, default=100)
    create.add_argument("--max-tabs", type=int, default=4)
    create.add_argument("--max-devices", type=int, default=1)
    create.add_argument(
        "--channel", choices=("internal", "beta", "stable"), default="stable"
    )
    create.add_argument("--expires-days", type=int, required=True)

    list_licenses = subparsers.add_parser("list-licenses")
    list_licenses.add_argument("--limit", type=int, default=100)
    list_licenses.add_argument("--offset", type=int, default=0)

    update = subparsers.add_parser("update-license")
    update.add_argument("license_id")
    update.add_argument("--customer-reference")
    update.add_argument("--plan")
    update.add_argument("--features")
    update.add_argument("--max-accounts", type=int)
    update.add_argument("--max-tabs", type=int)
    update.add_argument("--max-devices", type=int)
    update.add_argument("--channel", choices=("internal", "beta", "stable"))
    update.add_argument("--expires-days", type=int)

    devices = subparsers.add_parser("list-devices")
    devices.add_argument("license_id")

    release = subparsers.add_parser("register-release")
    release.add_argument("--component", choices=("backend", "desktop"), required=True)
    release.add_argument("--version", required=True)
    release.add_argument(
        "--channel", choices=("internal", "beta", "stable"), default="stable"
    )
    release.add_argument("--artifact-filename", required=True)
    release.add_argument("--minimum-launcher-version", required=True)
    release.add_argument("--mandatory", action="store_true")

    revoke_license = subparsers.add_parser("revoke-license")
    revoke_license.add_argument("license_id")
    revoke_license.add_argument("--reason", required=True)

    revoke_device = subparsers.add_parser("revoke-device")
    revoke_device.add_argument("device_id")
    revoke_device.add_argument("--reason", required=True)

    deactivate_release = subparsers.add_parser("deactivate-release")
    deactivate_release.add_argument("artifact_id")
    deactivate_release.add_argument("--reason", required=True)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    base_url = validated_base_url(args.base_url, args.allow_http_localhost)
    token = ""
    if args.command != "health":
        token = secret_value(args.admin_token, args.admin_token_file)
    with httpx.Client(base_url=base_url, timeout=60, follow_redirects=False) as client:
        if args.command == "health":
            result = request_json(client, "GET", "/health")
        elif args.command == "create-license":
            if not 1 <= args.expires_days <= 3_650:
                raise SystemExit("expires-days must be between 1 and 3650.")
            features = [
                item.strip() for item in args.features.split(",") if item.strip()
            ]
            result = request_json(
                client,
                "POST",
                "/v1/admin/licenses",
                token=token,
                payload={
                    "plan": args.plan,
                    "customer_reference": args.customer_reference,
                    "features": features,
                    "max_accounts": args.max_accounts,
                    "max_tabs": args.max_tabs,
                    "max_devices": args.max_devices,
                    "channel": args.channel,
                    "expires_at": int(time.time()) + args.expires_days * 86_400,
                },
            )
        elif args.command == "list-licenses":
            result = request_json(
                client,
                "GET",
                f"/v1/admin/licenses?limit={args.limit}&offset={args.offset}",
                token=token,
            )
        elif args.command == "update-license":
            payload: dict[str, object] = {}
            for argument, field in (
                (args.customer_reference, "customer_reference"),
                (args.plan, "plan"),
                (args.max_accounts, "max_accounts"),
                (args.max_tabs, "max_tabs"),
                (args.max_devices, "max_devices"),
                (args.channel, "channel"),
            ):
                if argument is not None:
                    payload[field] = argument
            if args.features is not None:
                payload["features"] = [
                    item.strip() for item in args.features.split(",") if item.strip()
                ]
            if args.expires_days is not None:
                if not 1 <= args.expires_days <= 3_650:
                    raise SystemExit("expires-days must be between 1 and 3650.")
                payload["expires_at"] = int(time.time()) + args.expires_days * 86_400
            if not payload:
                raise SystemExit("update-license requires at least one changed field.")
            result = request_json(
                client,
                "PATCH",
                f"/v1/admin/licenses/{args.license_id}",
                token=token,
                payload=payload,
            )
        elif args.command == "list-devices":
            result = request_json(
                client,
                "GET",
                f"/v1/admin/licenses/{args.license_id}/devices",
                token=token,
            )
        elif args.command == "register-release":
            result = request_json(
                client,
                "POST",
                "/v1/admin/releases",
                token=token,
                payload={
                    "component": args.component,
                    "version": args.version,
                    "channel": args.channel,
                    "target": "windows-x86_64",
                    "artifact_filename": args.artifact_filename,
                    "minimum_launcher_version": args.minimum_launcher_version,
                    "mandatory": args.mandatory,
                },
            )
        elif args.command == "revoke-license":
            result = request_json(
                client,
                "POST",
                f"/v1/admin/licenses/{args.license_id}/revoke",
                token=token,
                payload={"reason": args.reason},
            )
        elif args.command == "revoke-device":
            result = request_json(
                client,
                "POST",
                f"/v1/admin/devices/{args.device_id}/revoke",
                token=token,
                payload={"reason": args.reason},
            )
        else:
            result = request_json(
                client,
                "POST",
                f"/v1/admin/releases/{args.artifact_id}/deactivate",
                token=token,
                payload={"reason": args.reason},
            )
    json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
