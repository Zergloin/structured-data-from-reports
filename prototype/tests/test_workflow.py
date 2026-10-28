import unittest
from report_components.fixtures import sample_schema, sample_document, sample_candidates, external_document, sample_record
from report_components.domain import ConfirmedRecord, read_object
from report_components.schema_validation import SchemaValidator, PublishSchemaStrategy
from report_components.value_validation import ValueValidator, ConfirmValueStrategy, ValidationMode
from report_components.comparison import RecordComparator, ExactComparisonStrategy
from report_components.extraction import (ExtractionContext, WholeSchemaChunkStrategy, ScriptedModel,
    MemoryCheckpoints, ByteTokenCounter, ModelLimits)
from report_components.integrations import (MemorySystemApi, MemoryArchive, MemoryAnalytics, IntegrationContext,
    ImportRequest, ExportRequest, ImportStrategy, ExportStrategy)


class WorkflowTests(unittest.TestCase):
    def test_import_extract_verify_compare_export_share_versions(self):
        schema = sample_schema()
        self.assertTrue(SchemaValidator(PublishSchemaStrategy()).validate(schema).accepted)
        api = MemorySystemApi()
        api.register_job(ImportRequest("i1", "archive-1", "1", schema.version_id))
        context = IntegrationContext(ImportStrategy(MemoryArchive(external_document())), api)
        self.assertTrue(context.process("i1").ack)
        document_id, schema_id = api.extraction_jobs[0]
        from dataclasses import replace
        document = replace(sample_document(), version_id=document_id)
        result = ExtractionContext(WholeSchemaChunkStrategy(), ScriptedModel(sample_candidates()), MemoryCheckpoints(),
            ByteTokenCounter(), ModelLimits(1800, 200, 100)).run("x1", document, schema)
        self.assertEqual(result.schema_version, schema_id)
        self.assertEqual(result.document_version, document_id)
        self.assertFalse(result.confirmed)
        checked = ValueValidator(ConfirmValueStrategy()).validate(schema, read_object(result.values_json), ValidationMode.CONFIRM)
        self.assertTrue(checked.can_confirm)
        # Explicit user confirmation creates the confirmed version outside validation_comp.
        confirmed = ConfirmedRecord("record-1", document_id, schema, checked.values_json)
        compared = RecordComparator(ExactComparisonStrategy()).compare((confirmed, sample_record("record-2")))
        self.assertTrue(all(f.status == "equal" for f in compared.fields))
        api.register_job(ExportRequest("e1", "delivery-1", (confirmed,)))
        receiver = MemoryAnalytics()
        context.set_strategy(ExportStrategy(receiver))
        self.assertEqual(context.process("e1").state, "succeeded")
        self.assertEqual(receiver.sent["delivery-1"][0].document_version_id, document_id)
