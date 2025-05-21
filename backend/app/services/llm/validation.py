"""Minimal, dependency-free JSON-schema validation for LLM output.

Only the subset used by ``prompts.OBSERVATION_SCHEMA`` is implemented, which
keeps the backend free of an extra runtime dependency while still rejecting
malformed model output deterministically.
"""

from __future__ import annotations

import json
import re
from typing import Any

_JSON_BLOCK = re.compile(r"\{.*\}", re.S)


class SchemaValidationError(ValueError):
    def __init__(self, message: str, path: str = "$"):
        super().__init__(f"{path}: {message}")
        self.path = path


def extract_json(text: str) -> dict[str, Any]:
    """Parse a JSON object from raw model text (tolerates code fences/prose)."""
    candidate = (text or "").strip()
    if candidate.startswith("```"):
        candidate = re.sub(r"^```[a-zA-Z]*\n?", "", candidate)
        candidate = re.sub(r"\n?```$", "", candidate).strip()
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        match = _JSON_BLOCK.search(candidate)
        if not match:
            raise SchemaValidationError("Response did not contain a JSON object.") from None
        try:
            parsed = json.loads(match.group(0))
        except json.JSONDecodeError as exc:
            raise SchemaValidationError(f"Invalid JSON ({exc.msg}).") from exc
    if not isinstance(parsed, dict):
        raise SchemaValidationError("Top-level JSON value must be an object.")
    return parsed


def _check_type(value: Any, expected: Any, path: str) -> None:
    types = expected if isinstance(expected, list) else [expected]
    ok = False
    for t in types:
        if (
            (t == "object" and isinstance(value, dict))
            or (t == "array" and isinstance(value, list))
            or (t == "string" and isinstance(value, str))
            or (t == "number" and isinstance(value, (int, float)) and not isinstance(value, bool))
            or (t == "integer" and isinstance(value, int) and not isinstance(value, bool))
            or (t == "boolean" and isinstance(value, bool))
            or (t == "null" and value is None)
        ):
            ok = True
    if not ok:
        raise SchemaValidationError(f"expected type {expected}, got {type(value).__name__}", path)


def validate(instance: Any, schema: dict[str, Any], path: str = "$") -> None:
    """Validate ``instance`` against the supported JSON-schema subset."""
    if "type" in schema:
        _check_type(instance, schema["type"], path)
    if "enum" in schema and instance not in schema["enum"]:
        raise SchemaValidationError(f"value {instance!r} not in {schema['enum']}", path)
    if isinstance(instance, str):
        if "maxLength" in schema and len(instance) > schema["maxLength"]:
            raise SchemaValidationError(f"string longer than {schema['maxLength']}", path)
        if "minLength" in schema and len(instance) < schema["minLength"]:
            raise SchemaValidationError(f"string shorter than {schema['minLength']}", path)
    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            raise SchemaValidationError(f"value below minimum {schema['minimum']}", path)
        if "maximum" in schema and instance > schema["maximum"]:
            raise SchemaValidationError(f"value above maximum {schema['maximum']}", path)
    if isinstance(instance, list):
        if "maxItems" in schema and len(instance) > schema["maxItems"]:
            raise SchemaValidationError(f"more than {schema['maxItems']} items", path)
        if "minItems" in schema and len(instance) < schema["minItems"]:
            raise SchemaValidationError(f"fewer than {schema['minItems']} items", path)
        item_schema = schema.get("items")
        if item_schema:
            for index, item in enumerate(instance):
                validate(item, item_schema, f"{path}[{index}]")
    if isinstance(instance, dict):
        for key in schema.get("required", []):
            if key not in instance:
                raise SchemaValidationError(f"missing required property '{key}'", path)
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            extra = set(instance) - set(properties)
            if extra:
                raise SchemaValidationError(f"unexpected properties {sorted(extra)}", path)
        for key, value in instance.items():
            if key in properties:
                validate(value, properties[key], f"{path}.{key}")


def parse_and_validate(text: str, schema: dict[str, Any]) -> dict[str, Any]:
    payload = extract_json(text)
    validate(payload, schema)
    return payload
