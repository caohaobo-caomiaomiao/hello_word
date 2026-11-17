"""Pagination and filtering invariants."""

from __future__ import annotations

import pytest

from api.schema import validate_schema
from api.schemas import USER_LIST_SCHEMA

pytestmark = pytest.mark.regression


def test_default_pagination(auth_client):
    body = auth_client.get("/users", expected_status=200).expect_json().json()
    validate_schema(body, USER_LIST_SCHEMA, context="GET /users")
    assert body["page"] == 1
    assert body["limit"] == 10
    assert len(body["items"]) == 10
    assert body["total"] == 25
    assert body["total_pages"] == 3
    assert body["has_next"] is True


@pytest.mark.parametrize(
    "page,expected_ids",
    [
        (1, list(range(1, 11))),
        (2, list(range(11, 21))),
        (3, list(range(21, 26))),
    ],
)
def test_pages_partition_the_dataset(auth_client, page, expected_ids):
    body = auth_client.get("/users", params={"page": page, "limit": 10}, expected_status=200).json()
    assert [user["id"] for user in body["items"]] == expected_ids


def test_last_page_has_no_next(auth_client):
    body = auth_client.get("/users", params={"page": 3}, expected_status=200).json()
    assert body["has_next"] is False
    assert len(body["items"]) == 5


def test_page_beyond_the_end_is_empty(auth_client):
    body = auth_client.get("/users", params={"page": 99}, expected_status=200).json()
    assert body["items"] == []
    assert body["total"] == 25
    assert body["has_next"] is False


def test_search_is_case_insensitive(auth_client):
    lower = auth_client.get("/users", params={"search": "user 2", "limit": 100}, expected_status=200).json()
    upper = auth_client.get("/users", params={"search": "USER 2", "limit": 100}, expected_status=200).json()
    assert lower["total"] == upper["total"] == 6
    assert [user["id"] for user in lower["items"]] == list(range(20, 26))
    assert [user["id"] for user in upper["items"]] == list(range(20, 26))


def test_pagination_has_no_duplicates_or_gaps(auth_client):
    seen: list[int] = []
    page = 1
    while True:
        body = auth_client.get("/users", params={"page": page, "limit": 7}, expected_status=200).json()
        seen.extend(user["id"] for user in body["items"])
        if not body["has_next"]:
            break
        page += 1
    assert seen == list(range(1, 26))


@pytest.mark.negative
@pytest.mark.parametrize(
    "params,field",
    [
        ({"page": 0}, "page"),
        ({"page": "abc"}, "page"),
        ({"limit": 0}, "limit"),
        ({"limit": 101}, "limit"),
        ({"limit": "ten"}, "limit"),
    ],
)
def test_invalid_pagination_is_rejected(auth_client, params, field):
    body = auth_client.get("/users", params=params, expected_status=400).json()
    assert body["error"]["code"] == "validation_error"
    assert any(detail["field"] == field for detail in body["error"]["details"])
