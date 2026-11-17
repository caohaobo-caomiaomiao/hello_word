"""Authentication and authorization behaviour."""

from __future__ import annotations

import pytest

from api.schema import validate_schema
from api.schemas import ERROR_SCHEMA, LOGIN_SCHEMA


@pytest.mark.smoke
def test_login_issues_a_bearer_token(client, settings):
    response = (
        client.post(
            "/auth/login",
            json={"username": settings.username, "password": settings.password},
            expected_status=200,
        )
        .expect_json()
        .expect_content_type()
    )
    validate_schema(response.json(), LOGIN_SCHEMA, context="POST /auth/login")
    assert response.json()["token_type"] == "Bearer"
    assert response.json()["token"]


@pytest.mark.negative
def test_login_rejects_wrong_password(client, settings):
    response = client.post(
        "/auth/login",
        json={"username": settings.username, "password": "definitely-wrong"},
        expected_status=401,
    ).expect_json()
    validate_schema(response.json(), ERROR_SCHEMA)
    assert response.header("WWW-Authenticate", "").startswith("Bearer")


@pytest.mark.negative
@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Bearer not-a-real-token"},
        {"Authorization": "Basic dXNlcjpwYXNz"},
    ],
    ids=["missing-token", "invalid-token", "wrong-scheme"],
)
def test_protected_routes_reject_bad_credentials(client, headers):
    response = client.get("/users", headers=headers, expected_status=401).expect_json()
    validate_schema(response.json(), ERROR_SCHEMA)


@pytest.mark.smoke
def test_authenticated_client_can_list_users(auth_client):
    body = auth_client.get("/users", expected_status=200).expect_json().json()
    assert len(body["items"]) == 10
    assert body["total"] == 25
