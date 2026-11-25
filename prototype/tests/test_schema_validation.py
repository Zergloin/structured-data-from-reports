from dataclasses import replace
import unittest
from report_components.domain import snapshot, FieldKind
from report_components.fixtures import sample_schema
from report_components.schema_validation import SchemaValidator, DraftSchemaStrategy, PublishSchemaStrategy


class SchemaTests(unittest.TestCase):
    def setUp(self):
        self.schema = sample_schema()
        self.context = SchemaValidator(DraftSchemaStrategy())

    def test_dynamic_publication_policy(self):
        schema = replace(self.schema, fields=tuple(replace(f, description="") for f in self.schema.fields))
        self.assertTrue(self.context.validate(schema).accepted)
        self.context.set_strategy(PublishSchemaStrategy())
        self.assertFalse(self.context.validate(schema).accepted)
        self.assertTrue(self.context.validate(self.schema).accepted)

    def test_duplicate_semantic_identity(self):
        fields = (self.schema.fields[0], replace(self.schema.fields[1], semantic_id="completed_tasks"))
        self.assertFalse(self.context.validate(replace(self.schema, fields=fields)).accepted)

    def test_unknown_required_field(self):
        obj = self.schema.schema
        obj["required"] = ["unknown"]
        self.assertFalse(self.context.validate(replace(self.schema, schema_json=snapshot(obj))).accepted)

    def test_external_references_forbidden(self):
        obj = self.schema.schema
        obj["$ref"] = "https://example.invalid/schema"
        result = self.context.validate(replace(self.schema, schema_json=snapshot(obj)))
        self.assertIn("external_ref", [i.code for i in result.issues])

    def test_field_type_must_match_metadata(self):
        fields = (replace(self.schema.fields[0], kind=FieldKind.STRING), self.schema.fields[1])
        self.assertFalse(self.context.validate(replace(self.schema, fields=fields)).accepted)

    def test_publication_requires_closed_schema(self):
        self.context.set_strategy(PublishSchemaStrategy())
        obj = self.schema.schema
        obj["additionalProperties"] = True
        self.assertFalse(self.context.validate(replace(self.schema, schema_json=snapshot(obj))).accepted)

    def test_snapshot_is_immutable(self):
        obj = self.schema.schema
        obj["type"] = "array"
        self.assertEqual(self.schema.schema["type"], "object")
