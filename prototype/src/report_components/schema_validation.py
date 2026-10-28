"""schema_validation_comp: schema checks and replaceable lifecycle policy."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from .domain import SchemaDefinition, ValidationIssue
from .schema_rules import common_checks


class SchemaPolicy(Protocol):
    @property
    def mode(self) -> str: ...
    def check(self, schema: SchemaDefinition) -> tuple[ValidationIssue, ...]: ...


class DraftSchemaStrategy:
    @property
    def mode(self) -> str:
        return "draft"

    def check(self, schema: SchemaDefinition) -> tuple[ValidationIssue, ...]:
        return tuple(ValidationIssue("description", f.key, "Description is incomplete", error=False)
                     for f in schema.fields if not f.description.strip())


class PublishSchemaStrategy:
    @property
    def mode(self) -> str:
        return "publish"

    def check(self, schema: SchemaDefinition) -> tuple[ValidationIssue, ...]:
        issues: list[ValidationIssue] = []
        if not schema.schema_id or schema.revision < 1 or not schema.fields:
            issues.append(ValidationIssue("identity", "$", "Identity, revision and fields are required"))
        if schema.schema.get("additionalProperties") is not False:
            issues.append(ValidationIssue("open_schema", "$", "Unknown fields must be forbidden"))
        for f in schema.fields:
            if not f.description.strip():
                issues.append(ValidationIssue("description", f.key, "Description is required"))
            if f.unit and f.kind.value not in {"number", "integer"}:
                issues.append(ValidationIssue("unit", f.key, "Units require numeric fields"))
        return tuple(issues)


@dataclass(frozen=True)
class SchemaValidationResult:
    mode: str
    accepted: bool
    issues: tuple[ValidationIssue, ...]


class SchemaValidator:
    def __init__(self, policy: SchemaPolicy) -> None:
        self._policy = policy

    def set_strategy(self, policy: SchemaPolicy) -> None:
        self._policy = policy

    def validate(self, schema: SchemaDefinition) -> SchemaValidationResult:
        issues = common_checks(schema) + self._policy.check(schema)
        return SchemaValidationResult(self._policy.mode, not any(i.error for i in issues), issues)
