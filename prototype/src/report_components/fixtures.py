"""Deterministic common inputs shared by tests and demonstration."""
from __future__ import annotations
import hashlib
from .domain import ConfirmedRecord, FieldDefinition, FieldKind, SchemaDefinition, snapshot
from .extraction import Candidate, ParsedDocument, SourceFragment
from .integrations import ExternalDocument


def sample_schema() -> SchemaDefinition:
    return SchemaDefinition("report", 1, snapshot({"type": "object", "properties": {
        "tasks": {"type": "integer", "description": "Количество завершённых задач"},
        "cost": {"type": "number", "description": "Расходы в рублях"}},
        "required": ["tasks", "cost"], "additionalProperties": False}),
        (FieldDefinition("tasks", "completed_tasks", FieldKind.INTEGER, "Завершённые задачи", "count"),
         FieldDefinition("cost", "total_cost", FieldKind.NUMBER, "Общие расходы", "RUB")), "schema-v1")


def sample_record(version_id: str = "r1", cost: float = 1500.0) -> ConfirmedRecord:
    return ConfirmedRecord(version_id, "doc:" + version_id, sample_schema(), snapshot({"tasks": 12, "cost": cost}))


def sample_document() -> ParsedDocument:
    return ParsedDocument("doc-v1", (SourceFragment("p1", "Завершено 12 задач."),
                                     SourceFragment("p2", "Расходы составили 1500 рублей.")))


def sample_candidates() -> tuple[Candidate, ...]:
    a, b = sample_document().fragments
    return (Candidate("tasks", "12", a.fragment_id, a.text.index("12"), a.text.index("12") + 2, "12"),
            Candidate("cost", "1500", b.fragment_id, b.text.index("1500"), b.text.index("1500") + 4, "1500"))


def external_document() -> ExternalDocument:
    content = "\n".join(f.text for f in sample_document().fragments).encode()
    return ExternalDocument("archive-1", "1", content, hashlib.sha256(content).hexdigest())
