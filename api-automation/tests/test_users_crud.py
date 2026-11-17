"""Full CRUD lifecycle for /users, including status codes and response headers."""

from __future__ import annotations

import pytest

from api.schema import validate_schema
from api.schemas import ERROR_SCHEMA, USER_SCHEMA

pytestmark = pytest.mark.regression


def test_create_read_update_patch_delete_roundtrip(auth_client):
    created = auth_client.post(
        "/users",
        json={"name": "Ada Lovelace", "email": "ada@example.com", "role": "editor", "active": True},
        expected_status=201,
    )
    user = created.expect_json().json()
    validate_schema(user, USER_SCHEMA, context="POST /users")
    user_id = user["id"]
    assert created.header("Location") == f"/users/{user_id}"
    assert (user["name"], user["email"], user["role"], user["active"]) == (
        "Ada Lovelace",
        "ada@example.com",
        "editor",
        True,
    )

    fetched = auth_client.get(f"/users/{user_id}", expected_status=200).expect_json().json()
    assert fetched == user

    replaced = auth_client.put(
        f"/users/{user_id}",
        json={"name": "Ada Byron", "email": "ada.byron@example.com", "role": "admin", "active": False},
        expected_status=200,
    ).expect_json().json()
    validate_schema(replaced, USER_SCHEMA, context="PUT /users/{id}")
    assert replaced["id"] == user_id
    assert replaced["name"] == "Ada Byron"
    assert replaced["email"] == "ada.byron@example.com"
    assert replaced["role"] == "admin"
    assert replaced["active"] is False

    patched = auth_client.patch(f"/users/{user_id}", json={"active": True}, expected_status=200).expect_json().json()
    assert patched["active"] is True
    assert patched["name"] == "Ada Byron", "PATCH must not reset fields it did not receive"

    auth_client.delete(f"/users/{user_id}", expected_status=204)
    auth_client.get(f"/users/{user_id}", expected_status=404)


def test_created_user_is_searchable(auth_client, user_factory):
    created = user_factory(name="Grace Hopper", email="grace.hopper@example.com")
    listing = auth_client.get("/users", params={"search": "grace.hopper"}).expect_status(200).json()
    assert listing["total"] == 1
    assert listing["items"][0]["id"] == created["id"]


def test_duplicate_email_is_rejected(auth_client, user_factory):
    user_factory(email="duplicate@example.com")
    response = auth_client.post(
        "/users",
        json={"name": "Someone Else", "email": "duplicate@example.com"},
        expected_status=409,
    ).expect_json()
    validate_schema(response.json(), ERROR_SCHEMA, context="POST /users duplicate email")
    assert response.json()["error"]["code"] == "duplicate_email"


@pytest.mark.negative
def test_put_requires_a_complete_payload(auth_client, user_factory):
    created = user_factory(email="put-complete@example.com")
    response = auth_client.put(
        f"/users/{created['id']}",
        json={"name": "Only A Name"},
        expected_status=400,
    ).expect_json()
    assert response.json()["error"]["code"] == "validation_error"
    assert any(detail["field"] == "email" for detail in response.json()["error"]["details"])


@pytest.mark.negative
def test_patching_a_missing_user_returns_404(auth_client):
    response = auth_client.patch("/users/999999", json={"active": False}, expected_status=404).expect_json()
    assert response.json()["error"]["code"] == "not_found"
