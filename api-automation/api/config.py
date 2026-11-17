"""Runtime configuration for the API automation suite.

Every value can be overridden with an environment variable, which keeps the
same suite usable on a laptop, in CI and against a staging environment
without code changes.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULT_BASE_URL = "http://127.0.0.1:8000"


def _flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    """Immutable settings snapshot resolved from the environment."""

    base_url: str = DEFAULT_BASE_URL
    timeout: float = 10.0
    retries: int = 2
    retry_backoff: float = 0.2
    verify_ssl: bool = True
    username: str = "admin"
    password: str = "admin123"

    @classmethod
    def from_env(cls, base_url: str | None = None) -> Settings:
        """Build settings, with ``base_url`` taking priority over the environment."""
        return cls(
            base_url=(base_url or os.getenv("API_BASE_URL") or DEFAULT_BASE_URL).rstrip("/"),
            timeout=float(os.getenv("API_TIMEOUT", cls.timeout)),
            retries=int(os.getenv("API_RETRIES", cls.retries)),
            retry_backoff=float(os.getenv("API_RETRY_BACKOFF", cls.retry_backoff)),
            verify_ssl=_flag("API_VERIFY_SSL", cls.verify_ssl),
            username=os.getenv("API_USERNAME", cls.username),
            password=os.getenv("API_PASSWORD", cls.password),
        )
