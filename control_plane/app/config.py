from pathlib import Path
from urllib.parse import urlsplit

from pydantic_settings import BaseSettings, SettingsConfigDict

from backend.app.security.license import _parse_semver


class ControlPlaneSettings(BaseSettings):
    environment: str = "development"
    database_url: str = "sqlite:///./control_plane.db"
    database_url_file: str = ""
    admin_token: str = ""
    admin_token_file: str = ""
    license_key_pepper: str = ""
    license_key_pepper_file: str = ""
    lease_signing_key_id: str = ""
    lease_signing_private_key_path: str = ""
    release_signing_key_id: str = ""
    release_signing_private_key_path: str = ""
    artifact_storage_root: str = ""
    public_base_url: str = ""
    download_grant_secret: str = ""
    download_grant_secret_file: str = ""
    download_grant_ttl_seconds: int = 300
    lease_ttl_seconds: int = 3_600
    max_clock_skew_seconds: int = 300
    minimum_client_version: str = "0.1.0"
    minimum_backend_version: str = "0.1.0"
    trusted_hosts: str = ""
    activation_rate_limit_per_minute: int = 10
    device_rate_limit_per_minute: int = 120
    admin_rate_limit_per_minute: int = 120
    max_request_body_bytes: int = 128 * 1024
    api_prefix: str = "/v1"

    model_config = SettingsConfigDict(
        env_prefix="TKAUTO_CONTROL_",
        env_file=Path(__file__).resolve().parents[1] / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    def _load_secret_file(self, value_name: str, file_name: str) -> str:
        inline = str(getattr(self, value_name)).strip()
        file_value = str(getattr(self, file_name)).strip()
        if inline and file_value:
            raise RuntimeError(
                f"Set only one of {value_name} or {file_name}, not both."
            )
        if file_value:
            path = Path(file_value).expanduser().resolve()
            if not path.is_file():
                raise RuntimeError(f"Secret file does not exist: {file_name}")
            inline = path.read_text(encoding="utf-8").strip()
            setattr(self, value_name, inline)
        return inline

    def validate_runtime(self) -> None:
        if self.environment not in {"development", "production"}:
            raise RuntimeError("Environment must be development or production.")
        database_url_file = str(self.database_url_file).strip()
        if database_url_file:
            if self.database_url not in {"", "sqlite:///./control_plane.db"}:
                raise RuntimeError(
                    "Set only one of database_url or database_url_file, not both."
                )
            path = Path(database_url_file).expanduser().resolve()
            if not path.is_file():
                raise RuntimeError("Database URL secret file does not exist.")
            self.database_url = path.read_text(encoding="utf-8").strip()
        if not self.database_url:
            raise RuntimeError("Database URL is missing.")
        if self.environment == "production" and self.database_url.startswith("sqlite"):
            raise RuntimeError(
                "Production control plane must use a managed server database, not SQLite."
            )
        self._load_secret_file("admin_token", "admin_token_file")
        self._load_secret_file("license_key_pepper", "license_key_pepper_file")
        self._load_secret_file("download_grant_secret", "download_grant_secret_file")
        missing = [
            name
            for name, value in {
                "admin_token": self.admin_token,
                "license_key_pepper": self.license_key_pepper,
                "lease_signing_key_id": self.lease_signing_key_id,
                "lease_signing_private_key_path": self.lease_signing_private_key_path,
                "release_signing_key_id": self.release_signing_key_id,
                "release_signing_private_key_path": self.release_signing_private_key_path,
                "artifact_storage_root": self.artifact_storage_root,
                "public_base_url": self.public_base_url,
                "download_grant_secret": self.download_grant_secret,
            }.items()
            if not str(value).strip()
        ]
        if missing:
            raise RuntimeError(
                f"Control-plane secrets/configuration missing: {', '.join(missing)}"
            )
        if any(
            len(value) < 32
            for value in (
                self.admin_token,
                self.license_key_pepper,
                self.download_grant_secret,
            )
        ):
            raise RuntimeError(
                "Admin token, license-key pepper, and download grant secret must each be at least 32 characters."
            )
        key_path = Path(self.lease_signing_private_key_path).expanduser().resolve()
        if not key_path.is_file():
            raise RuntimeError("Lease signing private key file does not exist.")
        release_key_path = (
            Path(self.release_signing_private_key_path).expanduser().resolve()
        )
        if not release_key_path.is_file():
            raise RuntimeError("Release signing private key file does not exist.")
        artifact_root = Path(self.artifact_storage_root).expanduser().resolve()
        if not artifact_root.is_dir():
            raise RuntimeError(
                "Artifact storage root does not exist or is not a directory."
            )
        parsed_base_url = urlsplit(self.public_base_url)
        if (
            parsed_base_url.scheme != "https"
            or not parsed_base_url.hostname
            or parsed_base_url.username is not None
            or parsed_base_url.password is not None
            or parsed_base_url.query
            or parsed_base_url.fragment
        ):
            raise RuntimeError("Public base URL must be a clean HTTPS origin/path.")
        if self.environment == "production" and parsed_base_url.hostname in {
            "localhost",
            "127.0.0.1",
            "::1",
        }:
            raise RuntimeError("Production public base URL cannot use localhost.")
        if not 300 <= self.lease_ttl_seconds <= 7 * 86_400:
            raise RuntimeError("Lease TTL must be between 5 minutes and 7 days.")
        if not 60 <= self.download_grant_ttl_seconds <= 900:
            raise RuntimeError("Download grant TTL must be between 1 and 15 minutes.")
        if self.environment == "production" and (
            key_path == release_key_path
            or key_path.read_bytes() == release_key_path.read_bytes()
        ):
            raise RuntimeError("Lease and release signing keys must be distinct.")
        for name in ("minimum_client_version", "minimum_backend_version"):
            value = str(getattr(self, name))
            if not value or len(value) > 64:
                raise RuntimeError(f"Minimum version is invalid: {name}")
            try:
                _parse_semver(value)
            except ValueError as exc:
                raise RuntimeError(f"Minimum version is invalid: {name}") from exc
        for name in (
            "activation_rate_limit_per_minute",
            "device_rate_limit_per_minute",
            "admin_rate_limit_per_minute",
        ):
            if not 1 <= int(getattr(self, name)) <= 100_000:
                raise RuntimeError(f"Invalid rate limit: {name}")
        if not 4 * 1024 <= self.max_request_body_bytes <= 10 * 1024 * 1024:
            raise RuntimeError("Maximum request body must be between 4 KiB and 10 MiB.")

    @property
    def allowed_hosts(self) -> list[str]:
        configured = [
            host.strip().lower()
            for host in self.trusted_hosts.split(",")
            if host.strip()
        ]
        if configured:
            return configured
        host = urlsplit(self.public_base_url).hostname
        if self.environment == "development":
            return list(
                dict.fromkeys(
                    ["testserver", "localhost", "127.0.0.1", *([host] if host else [])]
                )
            )
        return [host] if host else []
