"""Data-driven create checks; payloads live in tests/data/users.json."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from api.schema import validate_schema
from api.schemas import USER_SCHEMA

pytestmark = pytest.mark.regression

DATA_FILE = Path(__file__).parent / "data" / "users.json"
USERS = json.loads(DATA_FILE.read_text(encoding="utf-8"))


@pytest.mark.parametrize("payload", USERS, ids=[user["email"] for user in USERS])
def test_create_user_from_data_file(auth_client, payload):
    created = auth_client.post("/users", json=payload, expected_status=201).expect_json().json()
    validate_schema(created, USER_SCHEMA, context=f"created user {payload['email']}")
    assert created["name"] == payload["name"]
    assert created["email"] == payload["email"].lower()
    assert created["role"] == payload.get("role", "viewer")
