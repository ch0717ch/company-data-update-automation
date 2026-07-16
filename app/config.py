from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on", "y"}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _headers_from_env() -> dict[str, str]:
    raw = os.getenv("SOURCE_HEADERS_JSON", "{}").strip() or "{}"
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("SOURCE_HEADERS_JSON은 올바른 JSON 객체여야 합니다.") from exc
    if not isinstance(parsed, dict):
        raise ValueError("SOURCE_HEADERS_JSON은 JSON 객체여야 합니다.")
    return {str(key): str(value) for key, value in parsed.items()}


@dataclass(frozen=True)
class Settings:
    app_host: str = field(default_factory=lambda: os.getenv("APP_HOST", "127.0.0.1"))
    app_port: int = field(default_factory=lambda: _env_int("APP_PORT", 8765))
    app_reload: bool = field(default_factory=lambda: _env_bool("APP_RELOAD", False))
    open_browser: bool = field(default_factory=lambda: _env_bool("OPEN_BROWSER", True))
    database_path: Path = field(
        default_factory=lambda: _resolve_path(os.getenv("DATABASE_PATH", "data/hiseoul_companies.db"))
    )

    source_base_url: str = field(
        default_factory=lambda: os.getenv("SOURCE_BASE_URL", "https://hiseoul.sba.kr").rstrip("/")
    )
    source_list_path: str = field(
        default_factory=lambda: os.getenv("SOURCE_LIST_PATH", "/Pages/AccountList.aspx/GetData")
    )
    source_detail_path: str = field(
        default_factory=lambda: os.getenv("SOURCE_DETAIL_PATH", "/Pages/AccountDetail.aspx/GetData")
    )
    source_cookie: str = field(default_factory=lambda: os.getenv("SOURCE_COOKIE", "lang=KOR"))
    source_api_key: str = field(default_factory=lambda: os.getenv("SOURCE_API_KEY", ""))
    source_api_key_header: str = field(
        default_factory=lambda: os.getenv("SOURCE_API_KEY_HEADER", "X-API-Key")
    )
    source_headers: dict[str, str] = field(default_factory=_headers_from_env)
    source_verify_tls: bool = field(default_factory=lambda: _env_bool("SOURCE_VERIFY_TLS", True))
    source_timeout_seconds: int = field(
        default_factory=lambda: _env_int("SOURCE_TIMEOUT_SECONDS", 45)
    )
    source_max_concurrency: int = field(
        default_factory=lambda: max(1, _env_int("SOURCE_MAX_CONCURRENCY", 4))
    )
    source_max_details: int = field(
        default_factory=lambda: max(1, _env_int("SOURCE_MAX_DETAILS", 100))
    )
    source_bulk_max_details: int = field(
        default_factory=lambda: max(1, _env_int("SOURCE_BULK_MAX_DETAILS", 2000))
    )
    source_request_delay_ms: int = field(
        default_factory=lambda: max(0, _env_int("SOURCE_REQUEST_DELAY_MS", 80))
    )
    allow_empty_source_search: bool = field(
        default_factory=lambda: _env_bool("ALLOW_EMPTY_SOURCE_SEARCH", False)
    )


def _resolve_path(value: str) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else (BASE_DIR / path).resolve()


settings = Settings()
