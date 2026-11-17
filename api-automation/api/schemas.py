"""JSON Schema definitions for the demo API's response contracts."""

from __future__ import annotations

HEALTH_SCHEMA = {
    "type": "object",
    "required": ["status", "service", "version"],
    "properties": {
        "status": {"type": "string", "const": "ok"},
        "service": {"type": "string", "minLength": 1},
        "version": {"type": "string", "pattern": r"^\d+\.\d+\.\d+$"},
    },
    "additionalProperties": False,
}

LOGIN_SCHEMA = {
    "type": "object",
    "required": ["token", "token_type", "expires_in"],
    "properties": {
        "token": {"type": "string", "minLength": 16},
        "token_type": {"type": "string", "const": "Bearer"},
        "expires_in": {"type": "integer", "minimum": 1},
    },
    "additionalProperties": False,
}

USER_SCHEMA = {
    "type": "object",
    "required": ["id", "name", "email", "role", "active"],
    "properties": {
        "id": {"type": "integer", "minimum": 1},
        "name": {"type": "string", "minLength": 1, "maxLength": 80},
        "email": {"type": "string", "format": "email"},
        "role": {"type": "string", "enum": ["admin", "editor", "viewer"]},
        "active": {"type": "boolean"},
    },
    "additionalProperties": False,
}

USER_LIST_SCHEMA = {
    "type": "object",
    "required": ["items", "page", "limit", "total", "total_pages", "has_next"],
    "properties": {
        "items": {"type": "array", "items": USER_SCHEMA},
        "page": {"type": "integer", "minimum": 1},
        "limit": {"type": "integer", "minimum": 1, "maximum": 100},
        "total": {"type": "integer", "minimum": 0},
        "total_pages": {"type": "integer", "minimum": 0},
        "has_next": {"type": "boolean"},
    },
    "additionalProperties": False,
}

ERROR_SCHEMA = {
    "type": "object",
    "required": ["error"],
    "properties": {
        "error": {
            "type": "object",
            "required": ["code", "message"],
            "properties": {
                "code": {"type": "string", "minLength": 1},
                "message": {"type": "string", "minLength": 1},
                "details": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["field", "message"],
                        "properties": {
                            "field": {"type": "string"},
                            "message": {"type": "string"},
                        },
                    },
                },
            },
            "additionalProperties": False,
        }
    },
    "additionalProperties": False,
}
