from dataclasses import replace
import unittest
from report_components.comparison import RecordComparator, ExactComparisonStrategy, NumericToleranceStrategy
from report_components.domain import FieldKind, snapshot
from report_components.fixtures import sample_record


class ComparisonTests(unittest.TestCase):
    def setUp(self):
        self.context = RecordComparator(ExactComparisonStrategy())

    def test_dynamic_strategy_changes_result(self):
        records = (sample_record(), sample_record("r2", 1500.2))
        self.assertEqual(self.context.compare(records).fields[1].status, "different")
        self.context.set_strategy(NumericToleranceStrategy(0.5))
        self.assertEqual(self.context.compare(records).fields[1].status, "equal")

    def test_rejects_unconfirmed(self):
        with self.assertRaises(ValueError):
            self.context.compare((sample_record(), replace(sample_record("r2"), confirmed=False)))

    def test_rejects_duplicate_versions(self):
        with self.assertRaises(ValueError): self.context.compare((sample_record(), sample_record()))

    def test_semantic_identity_allows_renamed_keys(self):
        r = sample_record("r2")
        obj = r.schema.schema
        obj["properties"]["expenses"] = obj["properties"].pop("cost")
        obj["required"] = ["tasks", "expenses"]
        fields = tuple(replace(f, key="expenses") if f.key == "cost" else f for f in r.schema.fields)
        r = replace(r, schema=replace(r.schema, fields=fields, schema_json=snapshot(obj)), values_json=snapshot({"tasks": 12, "expenses": 1500}))
        self.assertTrue(all(f.status == "equal" for f in self.context.compare((sample_record(), r)).fields))

    def test_unit_mismatch_is_incompatible(self):
        r = sample_record("r2")
        r = replace(r, schema=replace(r.schema, fields=tuple(replace(f, unit="USD") if f.key == "cost" else f for f in r.schema.fields)))
        self.assertEqual(self.context.compare((sample_record(), r)).fields[1].status, "incompatible")

    def test_tolerance_checks_all_pairs(self):
        self.context.set_strategy(NumericToleranceStrategy(1.0))
        records = tuple(sample_record(str(i), n) for i, n in enumerate((1.0, 0.4, 1.6)))
        self.assertEqual(self.context.compare(records).fields[1].status, "different")
        self.assertEqual(self.context.compare(tuple(reversed(records))).fields[1].status, "different")

    def test_boolean_is_not_numeric(self):
        self.assertFalse(NumericToleranceStrategy(1).equivalent(True, 1, FieldKind.INTEGER))

    def test_invalid_tolerance_rejected(self):
        for value in (-1, float("nan"), float("inf")):
            with self.subTest(value=value), self.assertRaises(ValueError): NumericToleranceStrategy(value)

    def test_missing_optional_value(self):
        a, b = sample_record(), sample_record("r2")
        obj = a.schema.schema
        obj["required"] = ["tasks"]
        schema = replace(a.schema, schema_json=snapshot(obj))
        a = replace(a, schema=schema, values_json=snapshot({"tasks": 12}))
        b = replace(b, schema=schema)
        self.assertEqual(self.context.compare((a, b)).fields[1].status, "missing")
