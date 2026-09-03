from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv


def _positive_int(name: str, default: int) -> int:
    value = int(os.getenv(name, str(default)))
    if value < 1:
        raise ValueError(f"{name} must be positive")
    return value


def _boolean(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be true or false")


@dataclass(frozen=True, slots=True)
class Settings:
    app_name: str = "Invisible Browser Studio"
    environment: str = "development"
    runtime: str = "simulated"
    host: str = "127.0.0.1"
    port: int = 8010
    scheduler_workers: int = 4
    max_queued_jobs: int = 256
    max_sessions: int = 64
    batch_max_jobs: int = 50
    batch_max_concurrency: int = 8
    rotation_timeout_seconds: float = 20.0
    rotation_max_attempts: int = 3
    rotation_settle_seconds: float = 2.0
    max_upload_bytes: int = 2 * 1024 * 1024 * 1024
    upload_root: Path = Path(".runtime/uploads")
    database_path: Path | None = None
    credentials_key_path: Path | None = None
    credentials_key: str = field(default="", repr=False)
    cors_origins: tuple[str, ...] = field(
        default_factory=lambda: (
            "http://127.0.0.1:5184",
            "http://localhost:5184",
        )
    )
    stream_jpeg_quality: int = 85
    stream_interval_seconds: float = 0.08
    stream_max_width: int = 1024
    extension_paths: tuple[Path, ...] = field(default_factory=tuple)
    extensions_required: bool = True
    omocaptcha_api_key: str = field(default="", repr=False)
    omocaptcha_extension_uuid: str = "d6105ea0-8d34-41ab-85a7-2eb0c66d55bb"

    @classmethod
    def from_env(cls) -> Settings:
        load_dotenv(Path.cwd() / ".env", override=False)
        cors = tuple(
            item.strip()
            for item in os.getenv(
                "IBS_CORS_ORIGINS",
                "http://127.0.0.1:5184,http://localhost:5184",
            ).split(",")
            if item.strip()
        )
        extension_paths = tuple(
            Path(item.strip())
            for item in os.getenv("IBS_EXTENSION_PATH", "").split(";")
            if item.strip()
        )
        runtime = os.getenv("IBS_RUNTIME", "simulated").strip().lower()
        if runtime not in {"simulated", "invisible"}:
            raise ValueError("IBS_RUNTIME must be simulated or invisible")
        stream_quality = int(os.getenv("IBS_STREAM_JPEG_QUALITY", "85"))
        if not 40 <= stream_quality <= 95:
            raise ValueError("IBS_STREAM_JPEG_QUALITY must be between 40 and 95")
        stream_interval = float(os.getenv("IBS_STREAM_INTERVAL_SECONDS", "0.08"))
        if not 0.05 <= stream_interval <= 5:
            raise ValueError("IBS_STREAM_INTERVAL_SECONDS must be between 0.05 and 5")
        stream_max_width = _positive_int("IBS_STREAM_MAX_WIDTH", 1024)
        if not 640 <= stream_max_width <= 2560:
            raise ValueError("IBS_STREAM_MAX_WIDTH must be between 640 and 2560")
        return cls(
            environment=os.getenv("IBS_ENVIRONMENT", "development"),
            runtime=runtime,
            host=os.getenv("IBS_HOST", "127.0.0.1"),
            port=_positive_int("IBS_PORT", 8010),
            scheduler_workers=_positive_int("IBS_SCHEDULER_WORKERS", 4),
            max_queued_jobs=_positive_int("IBS_MAX_QUEUED_JOBS", 256),
            max_sessions=_positive_int("IBS_MAX_SESSIONS", 64),
            batch_max_jobs=_positive_int("IBS_BATCH_MAX_JOBS", 50),
            batch_max_concurrency=_positive_int("IBS_BATCH_MAX_CONCURRENCY", 8),
            rotation_timeout_seconds=float(os.getenv("IBS_ROTATION_TIMEOUT_SECONDS", "20")),
            rotation_max_attempts=_positive_int("IBS_ROTATION_MAX_ATTEMPTS", 3),
            rotation_settle_seconds=float(os.getenv("IBS_ROTATION_SETTLE_SECONDS", "2")),
            max_upload_bytes=_positive_int("IBS_MAX_UPLOAD_BYTES", 2 * 1024 * 1024 * 1024),
            upload_root=Path(os.getenv("IBS_UPLOAD_ROOT", ".runtime/uploads")),
            database_path=Path(
                os.getenv("IBS_DATABASE_PATH", ".runtime/control-plane.sqlite3")
            ),
            credentials_key_path=Path(
                os.getenv("IBS_CREDENTIALS_KEY_PATH", ".runtime/credentials.key")
            ),
            credentials_key=os.getenv("IBS_CREDENTIALS_KEY", "").strip(),
            cors_origins=cors,
            stream_jpeg_quality=stream_quality,
            stream_interval_seconds=stream_interval,
            stream_max_width=stream_max_width,
            extension_paths=extension_paths,
            extensions_required=_boolean("IBS_EXTENSIONS_REQUIRED", True),
            omocaptcha_api_key=os.getenv("IBS_OMOCAPTCHA_API_KEY", "").strip(),
            omocaptcha_extension_uuid=os.getenv(
                "IBS_OMOCAPTCHA_EXTENSION_UUID",
                "d6105ea0-8d34-41ab-85a7-2eb0c66d55bb",
            ).strip(),
        )
