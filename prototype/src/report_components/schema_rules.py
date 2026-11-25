"""Shared pure schema invariants; independent from container strategy classes."""
from __future__ import annotations
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from .domain import JsonValue, SchemaDefinition, ValidationIssue


def common_checks(schema: SchemaDefinition) -> tuple[ValidationIssue, ...]:
    issues: list[ValidationIssue] = []
    obj = schema.schema
    try:
        Draft202012Validator.check_schema(obj)
    except SchemaError:
        issues.append(ValidationIssue("invalid_schema", "$", "Invalid JSON Schema", blocks_draft=True))
    if obj.get("type") != "object":
        issues.append(ValidationIssue("root_type", "$", "Root must be object", blocks_draft=True))
    def walk(value: JsonValue) -> None:
        if isinstance(value, dict):
            ref = value.get("$ref")
            if isinstance(ref, str) and not ref.startswith("#"):
                issues.append(ValidationIssue("external_ref", "$", "External references unsupported", blocks_draft=True))
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)
    walk(obj)
    keys = [f.key for f in schema.fields]
    semantic = [f.semantic_id for f in schema.fields]
    if len(keys) != len(set(keys)) or len(semantic) != len(set(semantic)) or any(not k for k in keys + semantic):
        issues.append(ValidationIssue("duplicate_fields", "$", "Field identifiers must be unique and nonempty", blocks_draft=True))
    properties = obj.get("properties", {})
    if not isinstance(properties, dict) or set(properties) != set(keys):
        issues.append(ValidationIssue("field_set", "$", "Metadata and properties differ", blocks_draft=True))
    else:
        for field in schema.fields:
            prop = properties[field.key]
            kind = prop.get("type") if isinstance(prop, dict) else None
            if kind != field.kind.value:
                issues.append(ValidationIssue("field_type", field.key, "Metadata type differs", blocks_draft=True))
    required = obj.get("required", [])
    if isinstance(required, list) and any(not isinstance(k, str) or k not in keys for k in required):
        issues.append(ValidationIssue("required_field", "$", "Unknown required field", blocks_draft=True))
    return tuple(issues)

