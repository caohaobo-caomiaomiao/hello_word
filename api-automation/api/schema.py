"""Assertion helpers that turn schema violations into readable test failures."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

_FORMAT_CHECKER = FormatChecker()


def validate_schema(instance: Any, schema: Mapping[str, Any], *, context: str = "response") -> Any:
    """Validate ``instance`` and raise a readable ``AssertionError`` on mismatch.

    ``context`` describes what is being validated (for example
    ``"GET /users"``) so a failure points straight at the offending endpoint.
    """
    validator = Draft202012Validator(schema, format_checker=_FORMAT_CHECKER)
    errors = sorted(validator.iter_errors(instance), key=lambda error: list(error.absolute_path))
    if not errors:
        return instance

    lines = []
    for error in errors:
        location = "/".join(str(part) for part in error.absolute_path) or "<root>"
        lines.append(f"  - {location}: {error.message}")
    raise AssertionError(f"{context} does not match the expected schema:\n" + "\n".join(lines))
