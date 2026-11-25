from dataclasses import replace
import unittest
from report_components.fixtures import sample_schema, sample_document, sample_candidates
from report_components.extraction import (ExtractionContext, WholeSchemaChunkStrategy, GroupedFieldChunkStrategy,
    MemoryCheckpoints, ScriptedModel, ByteTokenCounter, ModelLimits, ParsedDocument, SourceFragment, Candidate)


class ExtractionTests(unittest.TestCase):
    def setUp(self):
        self.schema, self.doc = sample_schema(), sample_document()
        self.counter, self.limits = ByteTokenCounter(), ModelLimits(1800, 200, 100)
        self.model, self.saved = ScriptedModel(sample_candidates()), MemoryCheckpoints()
        self.context = ExtractionContext(WholeSchemaChunkStrategy(), self.model, self.saved, self.counter, self.limits)

    def test_dynamic_grouping_preserves_values(self):
        first = self.context.run("a", self.doc, self.schema)
        calls = self.model.calls
        self.context.set_strategy(GroupedFieldChunkStrategy())
        second = self.context.run("b", self.doc, self.schema)
        self.assertEqual(first.values_json, second.values_json)
        self.assertGreater(self.model.calls - calls, calls)
        self.assertFalse(second.confirmed)

    def test_every_field_gets_full_long_document(self):
        doc = ParsedDocument("long", (SourceFragment("x", "Текст отчёта. " * 300),))
        plan = GroupedFieldChunkStrategy().build_plan(doc, self.schema, self.limits, self.counter)
        self.assertGreater(len(plan.units), 2)
        for field in ("tasks", "cost"):
            chunks = sorted((u for u in plan.units if field in u.fields), key=lambda u: u.start)
            self.assertEqual("".join(u.text for u in chunks), doc.fragments[0].text)
        self.assertTrue(all(u.input_cost + self.limits.output + self.limits.reserve <= self.limits.context for u in plan.units))

    def test_resume_skips_completed_requests(self):
        model = ScriptedModel(sample_candidates(), fail_on_call=2)
        context = ExtractionContext(WholeSchemaChunkStrategy(), model, self.saved, self.counter, self.limits)
        with self.assertRaises(TimeoutError): context.run("job", self.doc, self.schema)
        result = context.run("job", self.doc, self.schema)
        self.assertEqual(model.calls, 3)
        self.assertFalse(result.issues)

    def test_completed_repeat_does_not_call_model(self):
        self.context.run("a", self.doc, self.schema)
        calls = self.model.calls
        self.context.run("a", self.doc, self.schema)
        self.assertEqual(self.model.calls, calls)

    def test_changed_input_needs_new_job(self):
        self.context.run("a", self.doc, self.schema)
        with self.assertRaises(ValueError): self.context.run("a", replace(self.doc, version_id="v2"), self.schema)

    def test_strategy_change_needs_new_job(self):
        self.context.run("a", self.doc, self.schema)
        self.context.set_strategy(GroupedFieldChunkStrategy())
        with self.assertRaises(ValueError): self.context.run("a", self.doc, self.schema)

    def test_false_quote_rejected(self):
        class Liar:
            model_id = "liar"
            def extract(self, unit): return (replace(sample_candidates()[0], quote="999"),)
        context = ExtractionContext(WholeSchemaChunkStrategy(), Liar(), self.saved, self.counter, self.limits)
        with self.assertRaises(ValueError): context.run("a", self.doc, self.schema)
        self.assertIsNone(self.saved.load("a", "0"))

    def test_conflicting_candidates_not_silently_selected(self):
        candidates = sample_candidates() + (replace(sample_candidates()[0], value_json="13"),)
        context = ExtractionContext(WholeSchemaChunkStrategy(), ScriptedModel(candidates), self.saved, self.counter, self.limits)
        result = context.run("a", self.doc, self.schema)
        self.assertIn("conflict:tasks", result.issues)
        self.assertEqual(len(result.candidates), 3)

    def test_missing_values_stay_explicit(self):
        context = ExtractionContext(WholeSchemaChunkStrategy(), ScriptedModel(()), self.saved, self.counter, self.limits)
        self.assertIn("missing:cost", context.run("a", self.doc, self.schema).issues)

    def test_oversized_schema_fails_before_model(self):
        context = ExtractionContext(WholeSchemaChunkStrategy(), self.model, self.saved, self.counter, ModelLimits(130, 30, 20))
        with self.assertRaises(ValueError): context.run("a", self.doc, self.schema)
        self.assertEqual(self.model.calls, 0)

    def test_replacement_cannot_skip_source_coverage(self):
        class Incomplete:
            def build_plan(self, document, schema, limits, counter):
                plan = WholeSchemaChunkStrategy().build_plan(document, schema, limits, counter)
                return replace(plan, units=plan.units[:1])
        self.context.set_strategy(Incomplete())
        with self.assertRaises(ValueError): self.context.run("a", self.doc, self.schema)

    def test_invalid_limits_and_group_size(self):
        with self.assertRaises(ValueError): ModelLimits(100, 80, 30)
        with self.assertRaises(ValueError): GroupedFieldChunkStrategy(0)

    def test_replacement_cannot_change_field_definition(self):
        from report_components.extraction import payload
        class WrongDefinition:
            def build_plan(self, document, schema, limits, counter):
                plan = WholeSchemaChunkStrategy().build_plan(document, schema, limits, counter)
                bad = replace(plan.units[0], request_schema='{"type":"object","properties":{}}')
                bad = replace(bad, input_cost=counter.count(payload(bad.text,bad.fields,bad.request_schema)))
                return replace(plan, units=(bad, *plan.units[1:]))
        self.context.set_strategy(WrongDefinition())
        with self.assertRaises(ValueError): self.context.run("a", self.doc, self.schema)

    def test_changed_model_needs_new_job(self):
        self.context.run("a", self.doc, self.schema)
        class NewModel(ScriptedModel):
            @property
            def model_id(self): return "new-model"
        context = ExtractionContext(WholeSchemaChunkStrategy(), NewModel(sample_candidates()), self.saved, self.counter, self.limits)
        with self.assertRaises(ValueError): context.run("a", self.doc, self.schema)
