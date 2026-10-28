"""Run `python -m report_components.demo all` or select a team role."""
from __future__ import annotations
from dataclasses import replace
import json
import sys
from .domain import JsonValue
from .fixtures import sample_schema, sample_record, sample_document, sample_candidates, external_document
from .comparison import RecordComparator, ExactComparisonStrategy, NumericToleranceStrategy
from .schema_validation import SchemaValidator, DraftSchemaStrategy, PublishSchemaStrategy
from .value_validation import ValueValidator, DraftValueStrategy, ConfirmValueStrategy, ValidationMode
from .extraction import (ExtractionContext, WholeSchemaChunkStrategy, GroupedFieldChunkStrategy,
                         ScriptedModel, MemoryCheckpoints, ByteTokenCounter, ModelLimits)
from .integrations import (MemorySystemApi, MemoryArchive, MemoryAnalytics, IntegrationContext,
                           ImportStrategy, ExportStrategy, ImportRequest, ExportRequest)


def run(role: str) -> dict[str, JsonValue]:
    schema = sample_schema()
    if role == "teamlead":
        comparison = RecordComparator(ExactComparisonStrategy())
        records = (sample_record(), sample_record("r2", 1500.2))
        before: list[JsonValue] = [f.status for f in comparison.compare(records).fields]
        comparison.set_strategy(NumericToleranceStrategy(0.5))
        return {"exact": before, "tolerance": [f.status for f in comparison.compare(records).fields]}
    if role == "backend":
        model = ScriptedModel(sample_candidates())
        extraction = ExtractionContext(WholeSchemaChunkStrategy(), model, MemoryCheckpoints(), ByteTokenCounter(), ModelLimits(1800, 200, 100))
        first = extraction.run("extract-1", sample_document(), schema)
        first_calls = model.calls
        extraction.set_strategy(GroupedFieldChunkStrategy())
        second = extraction.run("extract-2", sample_document(), schema)
        return {"whole_calls": first_calls, "grouped_calls": model.calls - first_calls,
                "same_values": first.values_json == second.values_json, "confirmed": second.confirmed,
                "values": json.loads(second.values_json)}
    if role == "analyst":
        incomplete = replace(schema, fields=tuple(replace(f, description="") for f in schema.fields))
        schemas = SchemaValidator(DraftSchemaStrategy())
        draft = schemas.validate(incomplete).accepted
        schemas.set_strategy(PublishSchemaStrategy())
        return {"draft": draft, "incomplete_publish": schemas.validate(incomplete).accepted,
                "complete_publish": schemas.validate(schema).accepted}
    if role == "techdoc":
        values = ValueValidator(DraftValueStrategy())
        bad: dict[str, JsonValue] = {"tasks": None, "cost": 1500}
        draft_result = values.validate(schema, bad, ValidationMode.DRAFT)
        values.set_strategy(ConfirmValueStrategy())
        return {"draft_saved": draft_result.can_save,
                "incomplete_confirmed": values.validate(schema, bad, ValidationMode.CONFIRM).can_confirm,
                "corrected_confirmed": values.validate(schema, {"tasks": 12, "cost": 1500}, ValidationMode.CONFIRM).can_confirm}
    if role == "devops":
        api = MemorySystemApi()
        api.register_job(ImportRequest("import-1", "archive-1", "1", "schema-v1"))
        context = IntegrationContext(ImportStrategy(MemoryArchive(external_document(), failures=1)), api)
        retry = context.process("import-1")
        imported = context.process("import-1")
        api.register_job(ExportRequest("export-1", "delivery-1", (sample_record(),)))
        analytics = MemoryAnalytics(timeout_after_send=True)
        context.set_strategy(ExportStrategy(analytics))
        exported = context.process("export-1")
        return {"first_import": retry.state, "second_import": imported.state,
                "export": exported.state, "send_calls": analytics.send_calls,
                "outbox": [str(c) for c in api.outbox], "ack": exported.ack}
    raise ValueError("Unknown role")


def main() -> None:
    selected = sys.argv[1] if len(sys.argv) > 1 else "all"
    roles = ("teamlead", "backend", "analyst", "techdoc", "devops") if selected == "all" else (selected,)
    print(json.dumps({role: run(role) for role in roles}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
