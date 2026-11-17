"""Smoke checks for the API's health surface."""

from __future__ import annotations

import pytest

from api.schema import validate_schema
from api.schemas import HEALTH_SCHEMA

pytestmark = pytest.mark.smoke


def test_health_reports_ok(client):
    response = client.get("/health").expect_status(200).expect_json().expect_content_type()
    validate_schema(response.json(), HEALTH_SCHEMA, context="GET /health")
    assert response.json()["status"] == "ok"


def test_health_responds_quickly(client):
    response = client.get("/health").expect_status(200)
    assert response.elapsed < 1.0, f"health check took {response.elapsed:.2f}s"
