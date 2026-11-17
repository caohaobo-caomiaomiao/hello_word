"""A dependency-free demo REST API so the suite runs with no external service.

When ``API_BASE_URL`` (or ``--base-url``) is not set, the test session starts
this server on an ephemeral port.  Point the suite at a real service with the
same contract and everything except ``/admin/reset`` works unchanged.

Endpoints
---------
GET    /health               public health probe
POST   /auth/login           public token endpoint
GET    /users                list + pagination + search   (auth)
POST   /users                create                       (auth)
GET    /users/{id}           read                         (auth)
PUT    /users/{id}           full update                  (auth)
PATCH  /users/{id}           partial update               (auth)
DELETE /users/{id}           delete                       (auth)
GET    /echo                 echo method/query/headers/body
GET    /status/{code}        force an HTTP status for negative tests
GET    /flaky?fail=N         fail N times, then succeed (retry demo)
POST   /admin/reset          restore seed data (demo only)
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import re
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

LOGGER = logging.getLogger("api.mock")

DEFAULT_USERNAME = "admin"
DEFAULT_PASSWORD = "admin123"
DEMO_TOKEN = "demo-token-6b9c1f4e8a2d4c0f"

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
VALID_ROLES = {"admin", "editor", "viewer"}
MAX_PAGE_SIZE = 100
MAX_NAME_LENGTH = 80


# ---------------------------------------------------------------------------
# In-memory state
# ---------------------------------------------------------------------------


class DuplicateEmailError(Exception):
    """Raised when a write would create two users with the same email."""


def _seed_users() -> dict[int, dict[str, Any]]:
    users = {}
    for user_id in range(1, 26):
        users[user_id] = {
            "id": user_id,
            "name": f"User {user_id:02d}",
            "email": f"user{user_id:02d}@example.com",
            "role": "admin" if user_id % 2 == 0 else "viewer",
            "active": user_id % 3 != 0,
        }
    return users


class UserStore:
    """Thread-safe user repository with the demo seed data."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._users: dict[int, dict[str, Any]] = {}
        self._next_id = 1
        self.reset()

    def reset(self) -> None:
        with self._lock:
            self._users = _seed_users()
            self._next_id = max(self._users) + 1

    def all(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(self._users[user_id]) for user_id in sorted(self._users)]

    def get(self, user_id: int) -> dict[str, Any] | None:
        with self._lock:
            user = self._users.get(user_id)
            return dict(user) if user else None

    def find_by_email(self, email: str) -> dict[str, Any] | None:
        with self._lock:
            return self._find_by_email_locked(email.lower())

    def create(self, data: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            if self._find_by_email_locked(data["email"]):
                raise DuplicateEmailError(data["email"])
            user_id = self._next_id
            self._next_id += 1
            user = {"id": user_id, **data}
            self._users[user_id] = user
            return dict(user)

    def replace(self, user_id: int, data: dict[str, Any]) -> dict[str, Any] | None:
        with self._lock:
            if user_id not in self._users:
                return None
            conflict = self._find_by_email_locked(data["email"])
            if conflict and conflict["id"] != user_id:
                raise DuplicateEmailError(data["email"])
            user = {"id": user_id, **data}
            self._users[user_id] = user
            return dict(user)

    def patch(self, user_id: int, changes: dict[str, Any]) -> dict[str, Any] | None:
        with self._lock:
            user = self._users.get(user_id)
            if user is None:
                return None
            candidate = {**user, **changes}
            conflict = self._find_by_email_locked(candidate["email"])
            if conflict and conflict["id"] != user_id:
                raise DuplicateEmailError(candidate["email"])
            self._users[user_id] = candidate
            return dict(candidate)

    def delete(self, user_id: int) -> bool:
        with self._lock:
            return self._users.pop(user_id, None) is not None

    def _find_by_email_locked(self, email: str) -> dict[str, Any] | None:
        for user in self._users.values():
            if user["email"].lower() == email.lower():
                return user
        return None


class FlakyCounter:
    """Counts attempts per key so /flaky can simulate transient failures."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._attempts: dict[str, int] = {}

    def reset(self) -> None:
        with self._lock:
            self._attempts.clear()

    def attempt(self, key: str) -> int:
        with self._lock:
            attempt = self._attempts.get(key, 0) + 1
            self._attempts[key] = attempt
            return attempt


STORE = UserStore()
FLAKY = FlakyCounter()


# ---------------------------------------------------------------------------
# Error type
# ---------------------------------------------------------------------------


class ApiError(Exception):
    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        *,
        details: list[dict[str, str]] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.details = details
        self.headers = headers or {}

    def payload(self) -> dict[str, Any]:
        error: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.details:
            error["details"] = self.details
        return {"error": error}


def _validation_error(message: str, details: list[dict[str, str]]) -> ApiError:
    return ApiError(400, "validation_error", message, details=details)


def _validate_user(body: Any, *, partial: bool) -> dict[str, Any]:
    """Validate a create/update payload and normalise it.

    ``partial=True`` (PATCH) only checks the fields that are present.  A full
    update (PUT) requires ``name`` and ``email``.
    """
    if not isinstance(body, dict):
        raise ApiError(400, "invalid_body", "Request body must be a JSON object.")

    details: list[dict[str, str]] = []
    data: dict[str, Any] = {}

    if not partial or "name" in body:
        name = body.get("name")
        if not isinstance(name, str) or not name.strip():
            details.append({"field": "name", "message": "name must be a non-empty string."})
        elif len(name.strip()) > MAX_NAME_LENGTH:
            details.append({"field": "name", "message": f"name must be at most {MAX_NAME_LENGTH} characters."})
        else:
            data["name"] = name.strip()

    if not partial or "email" in body:
        email = body.get("email")
        if not isinstance(email, str) or not EMAIL_RE.match(email.strip()):
            details.append({"field": "email", "message": "email must be a valid email address."})
        else:
            data["email"] = email.strip().lower()

    if "role" in body:
        role = body["role"]
        if role not in VALID_ROLES:
            details.append({"field": "role", "message": f"role must be one of {sorted(VALID_ROLES)}."})
        else:
            data["role"] = role
    elif not partial:
        data["role"] = "viewer"

    if "active" in body:
        active = body["active"]
        if not isinstance(active, bool):
            details.append({"field": "active", "message": "active must be a boolean."})
        else:
            data["active"] = active
    elif not partial:
        data["active"] = True

    if details:
        raise _validation_error("Request validation failed.", details)
    return data


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------


class DemoApiHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "DemoAPI/1.0"

    # -- plumbing ----------------------------------------------------------

    def log_message(self, fmt: str, *args: Any) -> None:
        LOGGER.debug("%s - %s", self.address_string(), fmt % args)

    def do_GET(self) -> None:
        self._handle("GET")

    def do_POST(self) -> None:
        self._handle("POST")

    def do_PUT(self) -> None:
        self._handle("PUT")

    def do_PATCH(self) -> None:
        self._handle("PATCH")

    def do_DELETE(self) -> None:
        self._handle("DELETE")

    def do_OPTIONS(self) -> None:
        self._handle("OPTIONS")

    def _handle(self, method: str) -> None:
        self._request_id = self.headers.get("X-Request-ID") or uuid.uuid4().hex
        try:
            self._dispatch(method)
        except ApiError as exc:
            self._send_json(exc.status, exc.payload(), headers=exc.headers)
        except Exception:  # pragma: no cover - defensive
            LOGGER.exception("unhandled error while serving %s %s", method, self.path)
            self._send_json(500, {"error": {"code": "internal_error", "message": "Unexpected server error."}})

    def _send_json(self, status: int, payload: Any, *, headers: dict[str, str] | None = None) -> None:
        body = b"" if payload is None else json.dumps(payload).encode("utf-8")
        self._send(status, body, content_type="application/json", headers=headers)

    def _send(
        self,
        status: int,
        body: bytes,
        *,
        content_type: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.send_response(status)
        if body and content_type:
            self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Request-ID", self._request_id)
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _raw_body(self) -> bytes:
        length = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(length) if length else b""

    def _json_body(self) -> Any:
        raw = self._raw_body()
        if not raw:
            return None
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ApiError(400, "invalid_json", "Request body must be valid JSON.") from exc

    # -- routing -----------------------------------------------------------

    def _dispatch(self, method: str) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        query = parse_qs(parsed.query, keep_blank_values=True)

        if method == "OPTIONS":
            self._send(204, b"", headers={"Allow": "GET, POST, PUT, PATCH, DELETE, OPTIONS"})
            return
        if path == "/health":
            self._require_method(method, "GET")
            self._send_json(200, {"status": "ok", "service": "demo-api", "version": "1.0.0"})
            return
        if path == "/auth/login":
            self._require_method(method, "POST")
            self._login()
            return
        if path == "/admin/reset":
            self._require_method(method, "POST")
            STORE.reset()
            FLAKY.reset()
            self._send(204, b"")
            return
        if path == "/echo":
            self._echo(method, parsed)
            return
        if path == "/flaky":
            self._require_method(method, "GET", "POST")
            self._flaky(query)
            return

        match = re.fullmatch(r"/status/(\d{3})", path)
        if match:
            self._require_method(method, "GET")
            self._forced_status(int(match.group(1)))
            return

        if path == "/users":
            if method == "GET":
                self._list_users(query)
            elif method == "POST":
                self._create_user()
            else:
                self._require_method(method, "GET", "POST")
            return

        match = re.fullmatch(r"/users/(\d+)", path)
        if match:
            self._user_by_id(method, int(match.group(1)))
            return

        raise ApiError(404, "not_found", f"No route for {method} {path}.")

    def _require_method(self, method: str, *allowed: str) -> None:
        if method not in allowed:
            raise ApiError(
                405,
                "method_not_allowed",
                f"{method} is not allowed here; use {', '.join(allowed)}.",
                headers={"Allow": ", ".join(allowed)},
            )

    # -- endpoints ---------------------------------------------------------

    def _login(self) -> None:
        body = self._json_body()
        if not isinstance(body, dict):
            raise ApiError(400, "invalid_body", "Request body must be a JSON object.")
        if body.get("username") == DEFAULT_USERNAME and body.get("password") == DEFAULT_PASSWORD:
            self._send_json(
                200,
                {"token": DEMO_TOKEN, "token_type": "Bearer", "expires_in": 3600},
            )
        else:
            raise ApiError(
                401,
                "invalid_credentials",
                "Invalid username or password.",
                headers={"WWW-Authenticate": "Bearer"},
            )

    def _require_auth(self) -> None:
        header = self.headers.get("Authorization") or ""
        if not header.startswith("Bearer "):
            raise ApiError(
                401,
                "unauthorized",
                "A bearer token is required.",
                headers={"WWW-Authenticate": "Bearer"},
            )
        if header[len("Bearer "):].strip() != DEMO_TOKEN:
            raise ApiError(
                401,
                "invalid_token",
                "The bearer token is invalid or expired.",
                headers={"WWW-Authenticate": 'Bearer error="invalid_token"'},
            )

    def _list_users(self, query: dict[str, list[str]]) -> None:
        self._require_auth()
        page = self._positive_int(query, "page", default=1)
        limit = self._positive_int(query, "limit", default=10, maximum=MAX_PAGE_SIZE)
        search = (query.get("search") or [""])[0].strip().lower()

        users = STORE.all()
        if search:
            users = [
                user
                for user in users
                if search in user["name"].lower() or search in user["email"].lower()
            ]

        total = len(users)
        start = (page - 1) * limit
        items = users[start : start + limit]
        self._send_json(
            200,
            {
                "items": items,
                "page": page,
                "limit": limit,
                "total": total,
                "total_pages": math.ceil(total / limit) if total else 0,
                "has_next": start + len(items) < total,
            },
        )

    def _create_user(self) -> None:
        self._require_auth()
        data = _validate_user(self._json_body(), partial=False)
        try:
            user = STORE.create(data)
        except DuplicateEmailError as exc:
            raise ApiError(409, "duplicate_email", f"A user with email {exc.args[0]!r} already exists.") from exc
        self._send_json(201, user, headers={"Location": f"/users/{user['id']}"})

    def _user_by_id(self, method: str, user_id: int) -> None:
        self._require_auth()
        if method == "GET":
            user = STORE.get(user_id)
            if user is None:
                self._raise_missing(user_id)
            self._send_json(200, user)
            return
        if method == "PUT":
            data = _validate_user(self._json_body(), partial=False)
            try:
                user = STORE.replace(user_id, data)
            except DuplicateEmailError as exc:
                raise ApiError(409, "duplicate_email", f"A user with email {exc.args[0]!r} already exists.") from exc
            if user is None:
                self._raise_missing(user_id)
            self._send_json(200, user)
            return
        if method == "PATCH":
            changes = _validate_user(self._json_body(), partial=True)
            try:
                user = STORE.patch(user_id, changes)
            except DuplicateEmailError as exc:
                raise ApiError(409, "duplicate_email", f"A user with email {exc.args[0]!r} already exists.") from exc
            if user is None:
                self._raise_missing(user_id)
            self._send_json(200, user)
            return
        if method == "DELETE":
            if not STORE.delete(user_id):
                self._raise_missing(user_id)
            self._send(204, b"")
            return
        self._require_method(method, "GET", "PUT", "PATCH", "DELETE")

    def _raise_missing(self, user_id: int) -> None:
        raise ApiError(404, "not_found", f"User {user_id} does not exist.")

    def _echo(self, method: str, parsed: Any) -> None:
        body: Any = None
        raw = self._raw_body()
        if raw:
            try:
                body = json.loads(raw)
            except (json.JSONDecodeError, UnicodeDecodeError):
                body = raw.decode("utf-8", "replace")
        query = {
            key: values[0] if len(values) == 1 else values
            for key, values in parse_qs(parsed.query, keep_blank_values=True).items()
        }
        self._send_json(
            200,
            {
                "method": method,
                "path": parsed.path,
                "query": query,
                "headers": dict(self.headers.items()),
                "json": body,
            },
        )

    def _forced_status(self, status: int) -> None:
        if not 400 <= status <= 599:
            raise _validation_error(
                "status must be between 400 and 599.",
                [{"field": "code", "message": "status must be between 400 and 599."}],
            )
        self._send_json(
            status,
            {"error": {"code": "forced_status", "message": f"Requested status {status}."}},
        )

    def _flaky(self, query: dict[str, list[str]]) -> None:
        try:
            fail = int((query.get("fail") or ["0"])[0])
        except ValueError as exc:
            raise _validation_error(
                "fail must be a non-negative integer.",
                [{"field": "fail", "message": "fail must be a non-negative integer."}],
            ) from exc
        if fail < 0:
            raise _validation_error(
                "fail must be a non-negative integer.",
                [{"field": "fail", "message": "fail must be a non-negative integer."}],
            )
        key = (query.get("key") or ["default"])[0]
        attempt = FLAKY.attempt(key)
        if attempt <= fail:
            self._send_json(
                503,
                {"error": {"code": "transient_failure", "message": f"Simulated failure {attempt} of {fail}."}},
                headers={"Retry-After": "0", "X-Attempt": str(attempt)},
            )
        else:
            self._send_json(
                200,
                {"status": "recovered", "attempt": attempt},
                headers={"X-Attempt": str(attempt)},
            )

    def _positive_int(
        self,
        query: dict[str, list[str]],
        name: str,
        *,
        default: int,
        maximum: int | None = None,
    ) -> int:
        raw = (query.get(name) or [None])[0]
        if raw is None or raw == "":
            return default
        try:
            value = int(raw)
        except (TypeError, ValueError):
            value = 0
        if value < 1 or (maximum is not None and value > maximum):
            bound = f"between 1 and {maximum}" if maximum is not None else "a positive integer"
            message = f"{name} must be {bound}."
            raise _validation_error(message, [{"field": name, "message": message}])
        return value


# ---------------------------------------------------------------------------
# Server bootstrap
# ---------------------------------------------------------------------------


class DemoApiServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def handle_error(self, request: Any, client_address: Any) -> None:
        # A retried request can close its connection while the previous
        # response is still being written; log that instead of a traceback.
        LOGGER.exception("error while handling a request from %s", client_address)


def create_server(host: str = "127.0.0.1", port: int = 0) -> DemoApiServer:
    return DemoApiServer((host, port), DemoApiHandler)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run the demo API used by the test suite.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s | %(message)s")
    server = create_server(args.host, args.port)
    host, port = server.server_address[:2]
    print(f"Demo API listening on http://{host}:{port}")
    print(f"Demo credentials: {DEFAULT_USERNAME} / {DEFAULT_PASSWORD}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:  # pragma: no cover - interactive use
        pass
    finally:
        server.server_close()


if __name__ == "__main__":  # pragma: no cover - manual entry point
    main()
