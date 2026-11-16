"""Minimal JSON Schema validator for plugin.schema.json (no third-party dependency).

Supports the keywords that schema uses: type, enum, required, properties,
additionalProperties, items, minItems, minLength, pattern, anyOf, and local $ref.
"""
from __future__ import annotations

import re

_TYPES = {
    "object": dict,
    "array": list,
    "string": str,
    "boolean": bool,
    "number": (int, float),
}


def _is_type(value, name: str) -> bool:
    if name == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if name == "number" and isinstance(value, bool):
        return False
    return isinstance(value, _TYPES[name])


def validate(instance, schema: dict, root: dict | None = None, path: str = "$") -> list[str]:
    """Return a list of human-readable errors (empty when valid)."""
    root = root or schema
    if "$ref" in schema:
        target = root
        for part in schema["$ref"].removeprefix("#/").split("/"):
            target = target[part]
        merged = {**target, **{k: v for k, v in schema.items() if k not in ("$ref", "description")}}
        return validate(instance, merged, root, path)
    errors: list[str] = []
    if "anyOf" in schema:
        if all(validate(instance, option, root, path) for option in schema["anyOf"]):
            errors.append(f"{path}: does not match any allowed form")
        return errors
    expected = schema.get("type")
    if expected and not _is_type(instance, expected):
        return [f"{path}: expected {expected}, got {type(instance).__name__}"]
    if "enum" in schema and instance not in schema["enum"]:
        errors.append(f"{path}: must be one of {schema['enum']}")
    if isinstance(instance, str):
        if len(instance) < schema.get("minLength", 0):
            errors.append(f"{path}: must not be empty")
        if "pattern" in schema and not re.search(schema["pattern"], instance):
            errors.append(f"{path}: {instance!r} does not match {schema['pattern']}")
    if isinstance(instance, list):
        if len(instance) < schema.get("minItems", 0):
            errors.append(f"{path}: needs at least {schema['minItems']} item(s)")
        if "items" in schema:
            for index, item in enumerate(instance):
                errors += validate(item, schema["items"], root, f"{path}[{index}]")
    if isinstance(instance, dict):
        for name in schema.get("required", []):
            if name not in instance:
                errors.append(f"{path}: missing required field '{name}'")
        properties = schema.get("properties", {})
        extra = schema.get("additionalProperties", True)
        for name, value in instance.items():
            if name in properties:
                errors += validate(value, properties[name], root, f"{path}.{name}")
            elif extra is False:
                errors.append(f"{path}: unknown field '{name}'")
            elif isinstance(extra, dict):
                errors += validate(value, extra, root, f"{path}.{name}")
    return errors
