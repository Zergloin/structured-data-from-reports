from dataclasses import replace
import unittest
from report_components.domain import snapshot
from report_components.fixtures import sample_schema
from report_components.value_validation import ValueValidator, DraftValueStrategy, ConfirmValueStrategy, ValidationMode


class ValueTests(unittest.TestCase):
    def setUp(self):
        self.context = ValueValidator(DraftValueStrategy())
        self.schema = sample_schema()

    def test_dynamic_confirmation_policy(self):
        values = {"tasks": None, "cost": 1500}
        self.assertTrue(self.context.validate(self.schema, values, ValidationMode.DRAFT).can_save)
        self.context.set_strategy(ConfirmValueStrategy())
        self.assertFalse(self.context.validate(self.schema, values, ValidationMode.CONFIRM).can_confirm)
        self.assertTrue(self.context.validate(self.schema, {"tasks": 12, "cost": 1500}, ValidationMode.CONFIRM).can_confirm)

    def test_unknown_fields_block_draft(self):
        self.assertFalse(self.context.validate(self.schema, {"surprise": 1}, ValidationMode.DRAFT).can_save)

    def test_boolean_is_not_integer(self):
        self.context.set_strategy(ConfirmValueStrategy())
        self.assertFalse(self.context.validate(self.schema, {"tasks": True, "cost": 1500}, ValidationMode.CONFIRM).can_confirm)

    def test_wrong_operation_rejected(self):
        with self.assertRaises(ValueError): self.context.validate(self.schema, {}, ValidationMode.CONFIRM)

    def test_invalid_schema_blocks_draft(self):
        bad = replace(self.schema, schema_json=snapshot({"type": "array"}))
        self.assertFalse(self.context.validate(bad, {}, ValidationMode.DRAFT).can_save)

    def test_no_confirmation_bypass_with_custom_policy(self):
        class Unsafe:
            mode = ValidationMode.CONFIRM
            def accepts(self, issues): return True
        context = ValueValidator(Unsafe())
        self.assertFalse(context.validate(self.schema, {}, ValidationMode.CONFIRM).can_confirm)

    def test_result_keeps_value_snapshot(self):
        values = {"tasks": 12, "cost": 1500}
        result = self.context.validate(self.schema, values, ValidationMode.DRAFT)
        values["tasks"] = 99
        self.assertIn('"tasks": 12', result.values_json)

    def test_draft_never_confirms(self):
        result = self.context.validate(self.schema, {"tasks": 12, "cost": 1500}, ValidationMode.DRAFT)
        self.assertTrue(result.can_save)
        self.assertFalse(result.can_confirm)
