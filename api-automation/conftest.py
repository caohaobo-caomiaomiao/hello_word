"""Shared pytest fixtures: configuration, server lifecycle and API clients."""

from __future__ import annotations

import dataclasses
import logging
import os
import threading
import uuid

import pytest
import requests

from api import mock_server
from api.client import ApiClient
from api.config import Settings

LOGGER = logging.getLogger("api.tests")


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("api")
    group.addoption("--base-url", default=None, help="Target API base URL (default: bundled demo API).")
    group.addoption("--api-username", default=None, help="Username used by authenticated tests.")
    group.addoption("--api-password", default=None, help="Password used by authenticated tests.")


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "smoke: fast, critical-path checks")
    config.addinivalue_line("markers", "regression: broader functional coverage")
    config.addinivalue_line("markers", "negative: invalid input and error handling")
    config.addinivalue_line("markers", "contract: response contract and schema checks")


@pytest.fixture(scope="session")
def bundled_api(pytestconfig: pytest.Config) -> bool:
    """True when the suite owns the demo server instead of hitting a real API."""
    return not (pytestconfig.getoption("--base-url") or os.getenv("API_BASE_URL"))


@pytest.fixture(scope="session")
def base_url(pytestconfig: pytest.Config, bundled_api: bool):
    """Start the bundled demo API, or use the explicitly configured endpoint."""
    explicit = pytestconfig.getoption("--base-url") or os.getenv("API_BASE_URL")
    if explicit:
        url = explicit.rstrip("/")
        LOGGER.info("Testing against external API: %s", url)
        yield url
        return

    server = mock_server.create_server()
    thread = threading.Thread(target=server.serve_forever, name="demo-api", daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_address[1]}"
    LOGGER.info("Started bundled demo API at %s", url)
    try:
        yield url
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture(scope="session")
def settings(pytestconfig: pytest.Config, base_url: str) -> Settings:
    values = Settings.from_env(base_url)
    overrides = {}
    if pytestconfig.getoption("--api-username"):
        overrides["username"] = pytestconfig.getoption("--api-username")
    if pytestconfig.getoption("--api-password"):
        overrides["password"] = pytestconfig.getoption("--api-password")
    return dataclasses.replace(values, **overrides) if overrides else values


@pytest.fixture
def client(settings: Settings):
    """Unauthenticated client with the configured retry/timeout policy."""
    api = ApiClient(
        settings.base_url,
        timeout=settings.timeout,
        retries=settings.retries,
        retry_backoff=settings.retry_backoff,
        verify=settings.verify_ssl,
    )
    try:
        yield api
    finally:
        api.close()


@pytest.fixture
def auth_client(client: ApiClient, settings: Settings) -> ApiClient:
    """Client with a valid bearer token already attached."""
    return client.authenticate(settings.username, settings.password)


@pytest.fixture(autouse=True)
def fresh_state(settings: Settings, bundled_api: bool):
    """Restore the demo dataset before every test for deterministic assertions."""
    if bundled_api:
        response = requests.post(f"{settings.base_url}/admin/reset", timeout=settings.timeout)
        response.raise_for_status()
    yield


@pytest.fixture
def user_factory(auth_client: ApiClient):
    """Create unique users on demand, with optional field overrides."""

    def make(**overrides):
        payload = {
            "name": "Test User",
            "email": f"user-{uuid.uuid4().hex[:10]}@example.com",
            **overrides,
        }
        return auth_client.post("/users", json=payload, expected_status=201).json()

    return make
