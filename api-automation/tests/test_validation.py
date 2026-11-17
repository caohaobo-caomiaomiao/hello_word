"""Input validation and error-envelope behaviour."""

from __future__ import annotations

import pytest

from api.schema import validate_schema
from api.schemas import ERROR_SCHEMA

pytestmark = pytest.mark.negative

INVALID_PAYLOADS = [
    pytest.param({}, id="empty-object"),
    pytest.param({"name": "", "email": "valid@example.com"}, id="empty-name"),
    pytest.param({"name": "x" * 81, "email": "valid@example.com"}, id="name-too-long"),
    pytest.param({"name": "Valid", "email": "not-an-email"}, id="malformed-email"),
    pytest.param({"name": "Valid", "email": "valid@example.com", "role": "superuser"}, id="unknown-role"),
    pytest.param({"name": "Valid", "email": "valid@example.com", "active": "yes"}, id="non-boolean-active"),
    pytest.param({"name": 42, "email": "valid@example.com"}, id="non-string-name"),
]


@pytest.mark.parametrize("payload", INVALID_PAYLOADS)
def test_create_rejects_invalid_payload(auth_client, payload):
    response = auth_client.post("/users", json=payload, expected_status=400).expect_json()
    validate_schema(response.json(), ERROR_SCHEMA, context="POST /users invalid payload")
    assert response.json()["error"]["code"] == "validation_error"
    assert response.json()["error"]["details"], "validation errors must identify the offending fields"


def test_malformed_json_is_rejected(auth_client):
    response = auth_client.post(
        "/users",
        data="{definitely not json",
        headers={"Content-Type": "application/json"},
        expected_status=400,
    ).expect_json()
    validate_schema(response.json(), ERROR_SCHEMA, context="POST /users malformed JSON")
    assert response.json()["error"]["code"] == "invalid_json"


def test_unknown_route_returns_404(client):
    response = client.get("/does-not-exist", expected_status=404).expect_json()
    validate_schema(response.json(), ERROR_SCHEMA, context="GET /does-not-exist")
    assert response.json()["error"]["code"] == "not_found"


def test_unsupported_method_returns_405(client):
    response = client.request("DELETE", "/health", expected_status=405)
    assert response.header("Allow") == "GET"
