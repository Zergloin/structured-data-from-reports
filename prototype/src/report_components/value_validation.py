"""validation_comp: draft save and confirmation are different operations."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol
from jsonschema import Draft202012Validator
from .domain import JsonValue, SchemaDefinition, ValidationIssue, read_object, snapshot
from .schema_rules import common_checks


class ValidationMode(str, Enum):
    DRAFT = "draft"
    CONFIRM = "confirm"


class ValuePolicy(Protocol):
    @property
    def mode(self) -> ValidationMode: ...
    def accepts(self, issues: tuple[ValidationIssue, ...]) -> bool: ...


class DraftValueStrategy:
    @property
    def mode(self) -> ValidationMode:
        return ValidationMode.DRAFT

    def accepts(self, issues: tuple[ValidationIssue, ...]) -> bool:
        return not any(i.blocks_draft for i in issues)


class ConfirmValueStrategy:
    @property
    def mode(self) -> ValidationMode:
        return ValidationMode.CONFIRM

    def accepts(self, issues: tuple[ValidationIssue, ...]) -> bool:
        return not any(i.error for i in issues)


@dataclass(frozen=True)
class ValueValidationResult:
    values_json: str
    mode: ValidationMode
    can_save: bool
    can_confirm: bool
    issues: tuple[ValidationIssue, ...]


class ValueValidator:
    def __init__(self, policy: ValuePolicy) -> None:
        self._policy = policy

    def set_strategy(self, policy: ValuePolicy) -> None:
        self._policy = policy

    def validate(self, schema: SchemaDefinition, values: dict[str, JsonValue], operation: ValidationMode) -> ValueValidationResult:
        if operation != self._policy.mode:
            raise ValueError("Operation does not match strategy")
        frozen = snapshot(values)
        issues = list(common_checks(schema))
        if not issues:
            try:
                for error in Draft202012Validator(schema.schema).iter_errors(read_object(frozen)):
                    path = "$" + "".join(f"/{part}" for part in error.absolute_path)
                    issues.append(ValidationIssue(str(error.validator), path, error.message,
                                                  blocks_draft=error.validator == "additionalProperties"))
            except Exception as exc:
                # Schema reference failures must never turn into accepted data.
                issues.append(ValidationIssue("schema_resolution", "$", type(exc).__name__, blocks_draft=True))
        result = tuple(issues)
        accepted = self._policy.accepts(result)
        safe_save = accepted and not any(i.blocks_draft for i in result)
        can_confirm = safe_save and operation == ValidationMode.CONFIRM and not any(i.error for i in result)
        return ValueValidationResult(frozen, operation, safe_save, can_confirm, result)
