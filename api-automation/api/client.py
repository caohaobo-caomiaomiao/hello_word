"""Observable HTTP client shared by the whole suite.

``ApiClient`` wraps :class:`requests.Session` and adds the plumbing every API
test needs:

* bearer-token authentication;
* one structured log line per request with method, status and duration;
* bounded retries with exponential backoff for transient failures
  (5xx/429 and connection errors) on replay-safe methods;
* fluent assertions on the resulting :class:`ApiResponse`.
"""

from __future__ import annotations

import json
import logging
import random
import time
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

import requests

LOGGER = logging.getLogger("api.client")

#: HTTP methods that may be replayed safely when a request fails.
RETRYABLE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "PUT", "DELETE"})

#: Statuses that usually mean "try again", not "the API is broken".
RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})

_UNSET = object()


class ApiRequestError(AssertionError):
    """A request failed or did not match the expectation encoded in a test."""

    def __init__(self, message: str, *, response: ApiResponse | None = None) -> None:
        super().__init__(message)
        self.response = response


def _resolve_path(data: Any, path: str) -> Any:
    """Resolve a dotted path such as ``items[0].id`` against nested data."""
    current = data
    for token in path.split("."):
        if not token:
            continue
        name = token
        while "[" in name:
            head, _, remainder = name.partition("[")
            index, _, name = remainder.partition("]")
            if head:
                current = _lookup(current, head, path)
            try:
                current = current[int(index)]
            except (IndexError, KeyError, TypeError, ValueError) as exc:
                raise ApiRequestError(f"JSON path {path!r} does not resolve (bad index {index!r}).") from exc
        if name:
            current = _lookup(current, name, path)
    return current


def _lookup(data: Any, key: str, path: str) -> Any:
    try:
        return data[key]
    except (KeyError, TypeError, IndexError) as exc:
        raise ApiRequestError(f"JSON path {path!r} does not resolve (missing key {key!r}).") from exc


@dataclass
class ApiResponse:
    """A request/response pair enriched with the metadata tests assert on."""

    status_code: int
    headers: Mapping[str, str]
    text: str
    elapsed: float
    url: str
    request_id: str
    method: str
    _body: Any = field(default=_UNSET, repr=False)

    @property
    def ok(self) -> bool:
        return 100 <= self.status_code < 400

    def json(self) -> Any:
        """Return the parsed body, raising a useful error for non-JSON responses."""
        if self._body is _UNSET:
            try:
                self._body = json.loads(self.text) if self.text else None
            except json.JSONDecodeError as exc:
                raise ApiRequestError(
                    f"{self.method} {self.url} did not return JSON "
                    f"(status {self.status_code}, body starts with {self.text[:200]!r}).",
                    response=self,
                ) from exc
        return self._body

    def header(self, name: str, default: str | None = None) -> str | None:
        """Case-insensitive header lookup (header names are case-insensitive)."""
        lowered = name.lower()
        for key, value in self.headers.items():
            if key.lower() == lowered:
                return value
        return default

    def expect_status(self, *statuses: int | Iterable[int]) -> ApiResponse:
        """Assert the status code, accepting several alternatives."""
        allowed: set[int] = set()
        for status in statuses:
            if isinstance(status, int):
                allowed.add(status)
            else:
                allowed.update(status)
        if not allowed:
            raise ValueError("expect_status() needs at least one status code.")
        if self.status_code not in allowed:
            raise ApiRequestError(
                f"expected HTTP {sorted(allowed)} from {self.method} {self.url}, "
                f"got {self.status_code} with body {self._preview()}",
                response=self,
            )
        return self

    def expect_json(self) -> ApiResponse:
        """Assert the response parses as JSON and return it for chaining."""
        self.json()
        return self

    def expect_content_type(self, expected: str = "application/json") -> ApiResponse:
        actual = (self.header("Content-Type") or "").split(";")[0].strip().lower()
        if actual != expected.lower():
            raise ApiRequestError(
                f"expected Content-Type {expected!r} from {self.method} {self.url}, got {actual!r}",
                response=self,
            )
        return self

    def expect_header(self, name: str, expected: str | None = None) -> ApiResponse:
        actual = self.header(name)
        if actual is None:
            raise ApiRequestError(f"expected header {name!r} on {self.method} {self.url}, but it is missing.", response=self)
        if expected is not None and actual != expected:
            raise ApiRequestError(
                f"expected header {name!r} == {expected!r} on {self.method} {self.url}, got {actual!r}",
                response=self,
            )
        return self

    def json_path(self, path: str) -> Any:
        """Return a nested value, e.g. ``"items[0].email"``."""
        return _resolve_path(self.json(), path)

    def expect_json_path(self, path: str, expected: Any) -> ApiResponse:
        actual = self.json_path(path)
        if actual != expected:
            raise ApiRequestError(
                f"expected {path!r} == {expected!r} on {self.method} {self.url}, got {actual!r}",
                response=self,
            )
        return self

    def _preview(self, limit: int = 400) -> str:
        text = " ".join(self.text.split())
        return text[:limit] + ("..." if len(text) > limit else "")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<ApiResponse {self.method} {self.url} -> {self.status_code} in {self.elapsed * 1000:.1f}ms>"


class ApiClient:
    """Small, opinionated HTTP client for API tests."""

    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = 10.0,
        retries: int = 2,
        retry_backoff: float = 0.2,
        verify: bool = True,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.retries = max(0, retries)
        self.retry_backoff = max(0.0, retry_backoff)
        self.verify = verify
        self._token: str | None = None
        self._session = requests.Session()
        self._session.headers.update(
            {
                "Accept": "application/json",
                "User-Agent": "api-automation/1.0",
            }
        )
        if headers:
            self._session.headers.update(headers)

    # -- lifecycle ---------------------------------------------------------

    def close(self) -> None:
        self._session.close()

    def __enter__(self) -> ApiClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # -- authentication ----------------------------------------------------

    @property
    def token(self) -> str | None:
        return self._token

    def set_token(self, token: str | None) -> ApiClient:
        self._token = token
        return self

    def clear_token(self) -> ApiClient:
        self._token = None
        return self

    def authenticate(self, username: str, password: str, *, path: str = "/auth/login") -> ApiClient:
        """Log in and keep the returned bearer token for later requests."""
        response = self.request(
            "POST",
            path,
            json={"username": username, "password": password},
            expected_status=200,
        )
        token = response.json().get("token") if isinstance(response.json(), dict) else None
        if not token:
            raise ApiRequestError("login response did not contain a token", response=response)
        return self.set_token(token)

    # -- requests ----------------------------------------------------------

    def request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json: Any = None,
        data: Any = None,
        headers: Mapping[str, str] | None = None,
        expected_status: int | Iterable[int] | None = None,
        timeout: float | None = None,
        retries: int | None = None,
    ) -> ApiResponse:
        """Send a request with logging, bounded retries and optional assertions."""
        method = method.upper()
        url = path if path.startswith(("http://", "https://")) else f"{self.base_url}/{path.lstrip('/')}"

        request_headers = dict(headers or {})
        if self._token and not any(name.lower() == "authorization" for name in request_headers):
            request_headers["Authorization"] = f"Bearer {self._token}"
        request_headers.setdefault("X-Request-ID", uuid.uuid4().hex)

        allowed_attempts = 1 + (self.retries if retries is None else max(0, retries))
        last_error: Exception | None = None

        for attempt in range(1, allowed_attempts + 1):
            started = time.perf_counter()
            try:
                raw = self._session.request(
                    method,
                    url,
                    params=params,
                    json=json,
                    data=data,
                    headers=request_headers,
                    timeout=timeout if timeout is not None else self.timeout,
                    verify=self.verify,
                )
            except requests.RequestException as exc:
                elapsed = time.perf_counter() - started
                LOGGER.warning(
                    "%s %s failed after %.1f ms (attempt %d/%d): %s",
                    method, url, elapsed * 1000, attempt, allowed_attempts, exc,
                )
                last_error = exc
                if method in RETRYABLE_METHODS and attempt < allowed_attempts:
                    self._sleep(attempt)
                    continue
                raise ApiRequestError(f"{method} {url} failed: {exc}") from exc

            elapsed = raw.elapsed.total_seconds()
            LOGGER.info(
                "%s %s -> %d in %.1f ms (attempt %d/%d)",
                method, url, raw.status_code, elapsed * 1000, attempt, allowed_attempts,
            )
            response = ApiResponse(
                status_code=raw.status_code,
                headers=dict(raw.headers),
                text=raw.text,
                elapsed=elapsed,
                url=raw.url,
                request_id=request_headers["X-Request-ID"],
                method=method,
            )

            if (
                raw.status_code in RETRYABLE_STATUSES
                and method in RETRYABLE_METHODS
                and attempt < allowed_attempts
            ):
                LOGGER.warning(
                    "%s %s returned %d, retrying (attempt %d/%d)",
                    method, url, raw.status_code, attempt + 1, allowed_attempts,
                )
                self._sleep(attempt, raw.headers.get("Retry-After"))
                continue

            if expected_status is not None:
                response.expect_status(expected_status)
            return response

        raise ApiRequestError(f"{method} {url} exhausted {allowed_attempts} attempts: {last_error}")

    def get(self, path: str, **kwargs: Any) -> ApiResponse:
        return self.request("GET", path, **kwargs)

    def post(self, path: str, **kwargs: Any) -> ApiResponse:
        return self.request("POST", path, **kwargs)

    def put(self, path: str, **kwargs: Any) -> ApiResponse:
        return self.request("PUT", path, **kwargs)

    def patch(self, path: str, **kwargs: Any) -> ApiResponse:
        return self.request("PATCH", path, **kwargs)

    def delete(self, path: str, **kwargs: Any) -> ApiResponse:
        return self.request("DELETE", path, **kwargs)

    # -- internals ---------------------------------------------------------

    def _sleep(self, attempt: int, retry_after: str | None = None) -> None:
        delay = self.retry_backoff * (2 ** (attempt - 1))
        if retry_after:
            try:
                delay = max(delay, float(retry_after))
            except ValueError:
                pass
        time.sleep(delay + random.uniform(0, self.retry_backoff / 2))
