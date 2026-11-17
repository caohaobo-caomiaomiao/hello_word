"""Resilience: transient failures are retried, unsafe writes are not replayed."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.negative


def test_transient_503_is_retried(auth_client):
    response = auth_client.get(
        "/flaky",
        params={"key": "retry-get", "fail": 2},
        retries=2,
        expected_status=200,
    ).expect_json()
    assert response.header("X-Attempt") == "3", "GET should be replayed until the 3rd attempt succeeds"
    assert response.json()["status"] == "recovered"


def test_retries_are_bounded(auth_client):
    response = auth_client.get(
        "/flaky",
        params={"key": "bounded", "fail": 99},
        retries=2,
        expected_status=503,
    )
    assert response.header("X-Attempt") == "3", "retries must stop at the configured budget"


def test_post_is_not_replayed_automatically(auth_client):
    response = auth_client.post(
        "/flaky",
        params={"key": "retry-post", "fail": 5},
        expected_status=503,
    )
    assert response.header("X-Attempt") == "1", "POST must never be retried automatically"


def test_forced_error_status_is_returned_unchanged(auth_client):
    response = auth_client.get("/status/418", expected_status=418).expect_json()
    assert response.json()["error"]["code"] == "forced_status"
