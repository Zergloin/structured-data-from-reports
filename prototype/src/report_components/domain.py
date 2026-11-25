"""Immutable snapshots; no HTTP, ORM or broker dependencies."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import json
from typing import TypeAlias, cast

JsonValue: TypeAlias = "None | bool | int | float | str | list[JsonValue] | dict[str, JsonValue]"


def snapshot(value: JsonValue) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def read_object(value: str) -> dict[str, JsonValue]:
    parsed: object = json.loads(value)
    if not isinstance(parsed, dict):
        raise ValueError("Expected JSON object")
    return cast(dict[str, JsonValue], parsed)


class FieldKind(str, Enum):
    STRING = "string"
    INTEGER = "integer"
    NUMBER = "number"
    BOOLEAN = "boolean"


@dataclass(frozen=True)
class FieldDefinition:
    key: str
    semantic_id: str
    kind: FieldKind
    description: str = ""
    unit: str | None = None


@dataclass(frozen=True)
class SchemaDefinition:
    schema_id: str
    revision: int
    schema_json: str
    fields: tuple[FieldDefinition, ...]
    version_id: str | None = None

    @property
    def schema(self) -> dict[str, JsonValue]:
        return read_object(self.schema_json)


@dataclass(frozen=True)
class ConfirmedRecord:
    version_id: str
    document_version_id: str
    schema: SchemaDefinition
    values_json: str
    confirmed: bool = True

    @property
    def values(self) -> dict[str, JsonValue]:
        return read_object(self.values_json)


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    path: str
    message: str
    error: bool = True
    blocks_draft: bool = False
