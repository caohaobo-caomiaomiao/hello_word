"""Response contracts that are easy to break and expensive to debug."""

from __future__ import annotations

import pytest

from api.schema import validate_schema
from api.schemas import ERROR_SCHEMA, USER_LIST_SCHEMA, USER_SCHEMA

pytestmark = pytest.mark.contract


def test_user_list_matches_schema(auth_client):
    response = auth_client.get("/users", expected_status=200).expect_json()
    validate_schema(response.json(), USER_LIST_SCHEMA, context="GET /users")


def test_single_user_matches_schema(auth_client):
    response = auth_client.get("/users/1", expected_status=200).expect_json()
    validate_schema(response.json(), USER_SCHEMA, context="GET /users/1")


def test_error_envelope_is_consistent(auth_client):
    response = auth_client.get("/users/999999", expected_status=404).expect_json()
    validate_schema(response.json(), ERROR_SCHEMA, context="GET /users/999999")
    assert response.json()["error"]["code"] == "not_found"


def test_request_id_is_echoed(auth_client):
    response = auth_client.get("/health", expected_status=200)
    assert response.header("X-Request-ID") == response.request_id


def test_echo_returns_query_headers_and_body(auth_client):
    response = auth_client.post(
        "/echo",
        params={"source": "pytest"},
        json={"hello": "world"},
        headers={"X-Trace": "contract-test"},
        expected_status=200,
    ).expect_json()
    body = response.json()
    assert body["method"] == "POST"
    assert body["query"]["source"] == "pytest"
    assert body["headers"]["X-Trace"] == "contract-test"
    assert body["json"] == {"hello": "world"}
