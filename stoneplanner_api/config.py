"""Settings from environment variables. Defaults suit local development."""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path


def _int(name: str, default: int) -> int:
    value = os.environ.get(name, "")
    return int(value) if value.strip() else default


def _bool(name: str, default: bool) -> bool:
    value = os.environ.get(name, "").strip().lower()
    if not value:
        return default
    return value in ("1", "true", "yes", "on")


@dataclass
class Settings:
    # Signs session cookies. Render generates one (render.yaml); a random key
    # per process is used otherwise, which logs everyone out on restart.
    secret_key: str = field(default_factory=lambda: secrets.token_urlsafe(32))
    # Shared access password. Empty means nobody can log in (fail closed).
    app_password: str = ""
    work_dir: Path = Path("/tmp/stoneplanner-cases")
    case_ttl_minutes: int = 120
    max_upload_mb: int = 400
    session_hours: int = 12
    # Modal app and function deployed from modal_app/segment_gpu.py. Modal
    # credentials come from MODAL_TOKEN_ID and MODAL_TOKEN_SECRET.
    modal_app_name: str = "stone-planner-segment"
    modal_function_name: str = "segment_organs"
    frontend_dist: Path | None = None
    cookie_secure: bool = True
    plan_workers: int = 1
    # Local ingest page. Both set turns it on; never set them on Render (the
    # library refuses to start there). KEY_FILE must be outside DATA_DIR.
    data_dir: Path | None = None
    key_file: Path | None = None
    max_ingest_mb: int = 4000

    @property
    def modal_configured(self) -> bool:
        return bool(os.environ.get("MODAL_TOKEN_ID") and os.environ.get("MODAL_TOKEN_SECRET"))

    @classmethod
    def from_env(cls) -> Settings:
        dist = os.environ.get("FRONTEND_DIST", "")
        return cls(
            secret_key=os.environ.get("SECRET_KEY") or secrets.token_urlsafe(32),
            app_password=os.environ.get("APP_PASSWORD", ""),
            work_dir=Path(os.environ.get("WORK_DIR", "/tmp/stoneplanner-cases")),
            case_ttl_minutes=_int("CASE_TTL_MINUTES", 120),
            max_upload_mb=_int("MAX_UPLOAD_MB", 400),
            session_hours=_int("SESSION_HOURS", 12),
            modal_app_name=os.environ.get("MODAL_APP_NAME", "stone-planner-segment"),
            modal_function_name=os.environ.get("MODAL_FUNCTION_NAME", "segment_organs"),
            frontend_dist=Path(dist) if dist else None,
            cookie_secure=_bool("COOKIE_SECURE", True),
            plan_workers=_int("PLAN_WORKERS", 1),
            data_dir=Path(os.environ["DATA_DIR"]) if os.environ.get("DATA_DIR") else None,
            key_file=Path(os.environ["KEY_FILE"]) if os.environ.get("KEY_FILE") else None,
            max_ingest_mb=_int("MAX_INGEST_MB", 4000),
        )
