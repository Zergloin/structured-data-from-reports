from dataclasses import replace
import unittest
from report_components.fixtures import external_document, sample_record
from report_components.integrations import (MemorySystemApi, MemoryArchive, MemoryAnalytics, ImportRequest, ExportRequest,
    ImportStrategy, ExportStrategy, IntegrationContext)


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.api = MemorySystemApi()
        self.request = ImportRequest("import", "archive-1", "1", "schema-v1")
        self.api.register_job(self.request)

    def test_dynamic_import_to_export(self):
        context = IntegrationContext(ImportStrategy(MemoryArchive(external_document())), self.api)
        self.assertEqual(context.process("import").state, "succeeded")
        self.api.register_job(ExportRequest("export", "e1", (sample_record(),)))
        receiver = MemoryAnalytics()
        context.set_strategy(ExportStrategy(receiver))
        self.assertTrue(context.process("export").ack)
        self.assertEqual(receiver.send_calls, 1)

    def test_retry_goes_through_api_outbox(self):
        context = IntegrationContext(ImportStrategy(MemoryArchive(external_document(), failures=1)), self.api)
        result = context.process("import")
        self.assertTrue(result.ack)
        self.assertEqual(result.state, "retry_scheduled")
        self.assertIn(("integration", "import"), self.api.outbox)
        self.assertEqual(context.process("import").state, "succeeded")

    def test_import_dedup_and_single_extraction_registration(self):
        strategy = ImportStrategy(MemoryArchive(external_document()))
        context = IntegrationContext(strategy, self.api)
        context.process("import")
        self.api.register_job(replace(self.request, job_id="import2"))
        context.process("import2")
        self.assertEqual(len(self.api.extraction_jobs), 1)
        self.assertEqual(sum(x[0] == "extraction" for x in self.api.outbox), 1)

    def test_changed_checksum_rejected(self):
        document = replace(external_document(), checksum="wrong")
        self.assertEqual(IntegrationContext(ImportStrategy(MemoryArchive(document)), self.api).process("import").state, "failed")

    def test_wrong_external_version_rejected(self):
        document = replace(external_document(), version="other")
        self.assertEqual(IntegrationContext(ImportStrategy(MemoryArchive(document)), self.api).process("import").state, "failed")

    def test_api_failure_never_acks(self):
        self.api.available = False
        result = IntegrationContext(ImportStrategy(MemoryArchive(external_document())), self.api).process("import")
        self.assertFalse(result.ack)

    def test_unknown_job_never_created_or_acked(self):
        context = IntegrationContext(ImportStrategy(MemoryArchive(external_document())), self.api)
        self.assertFalse(context.process("unknown").ack)
        self.assertFalse(self.api.outbox)

    def test_wrong_strategy_does_not_claim_job(self):
        context = IntegrationContext(ExportStrategy(MemoryAnalytics()), self.api)
        self.assertFalse(context.process("import").ack)
        context.set_strategy(ImportStrategy(MemoryArchive(external_document())))
        self.assertEqual(context.process("import").state, "succeeded")

    def test_stale_lease_cannot_finish(self):
        lease = self.api.claim("import", "import")
        self.api.expire_lease("import")
        with self.assertRaises(ValueError): self.api.finish(lease, "succeeded", "old worker")

    def test_retry_limit_is_bounded(self):
        context = IntegrationContext(ImportStrategy(MemoryArchive(external_document(), failures=9)), self.api, max_attempts=2)
        self.assertEqual(context.process("import").state, "retry_scheduled")
        self.assertEqual(context.process("import").state, "failed")

    def export_context(self, receiver, record=None):
        self.api.register_job(ExportRequest("export", "e1", (record or sample_record(),)))
        return IntegrationContext(ExportStrategy(receiver), self.api)

    def test_unconfirmed_record_cannot_be_exported(self):
        receiver = MemoryAnalytics()
        context = self.export_context(receiver, replace(sample_record(), confirmed=False))
        self.assertEqual(context.process("export").state, "failed")
        self.assertEqual(receiver.send_calls, 0)

    def test_status_reconciles_lost_response(self):
        receiver = MemoryAnalytics(timeout_after_send=True)
        context = self.export_context(receiver)
        self.assertEqual(context.process("export").state, "succeeded")
        self.assertEqual(context.process("export").state, "succeeded")
        self.assertEqual(receiver.send_calls, 1)

    def test_unknown_outcome_requires_manual_reconciliation(self):
        receiver = MemoryAnalytics(idempotent=False, status_supported=False, timeout_after_send=True)
        context = self.export_context(receiver)
        self.assertEqual(context.process("export").state, "manual")
        self.assertEqual(context.process("export").state, "manual")
        self.assertEqual(receiver.send_calls, 1)

    def test_idempotency_allows_safe_retry(self):
        receiver = MemoryAnalytics(status_supported=False, timeout_after_send=True)
        context = self.export_context(receiver)
        self.assertEqual(context.process("export").state, "retry_scheduled")
        self.assertEqual(context.process("export").state, "succeeded")
        self.assertEqual(len(receiver.sent), 1)

    def test_lost_api_response_after_delivery_does_not_duplicate_send(self):
        api = self.api
        class Receiver(MemoryAnalytics):
            def send(self, export_id, records):
                super().send(export_id, records)
                api.available = False
        receiver = Receiver()
        context = self.export_context(receiver)
        self.assertFalse(context.process("export").ack)
        api.available = True
        api.expire_lease("export")
        self.assertEqual(context.process("export").state, "succeeded")
        self.assertEqual(receiver.send_calls, 1)
