"""extractor_comp: bounded requests, complete coverage and resumable extraction."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Protocol
from .domain import JsonValue, SchemaDefinition, snapshot, read_object
from .schema_rules import common_checks


@dataclass(frozen=True)
class SourceFragment:
    fragment_id: str
    text: str


@dataclass(frozen=True)
class ParsedDocument:
    version_id: str
    fragments: tuple[SourceFragment, ...]


@dataclass(frozen=True)
class ModelLimits:
    context: int
    output: int
    reserve: int

    def __post_init__(self) -> None:
        if min(self.context, self.output, self.reserve) < 0 or self.output + self.reserve >= self.context:
            raise ValueError("Invalid context budget")


class TokenCounter(Protocol):
    def count(self, text: str) -> int: ...


class ByteTokenCounter:
    """Demonstration unit: UTF-8 bytes, NOT an actual model tokenizer."""
    def count(self, text: str) -> int:
        return len(text.encode("utf-8"))


@dataclass(frozen=True)
class ExtractionUnit:
    unit_id: str
    fragment_id: str
    start: int
    end: int
    text: str
    fields: tuple[str, ...]
    request_schema: str
    input_cost: int


@dataclass(frozen=True)
class ExtractionPlan:
    document_version: str
    schema_version: str
    units: tuple[ExtractionUnit, ...]
    fingerprint: str


def payload(text: str, fields: tuple[str, ...], request_schema: str) -> str:
    return snapshot({"instruction": "Extract values with literal source spans", "text": text,
                     "fields": list(fields), "schema": request_schema})


def plan_groups(document: ParsedDocument, schema: SchemaDefinition, limits: ModelLimits,
                counter: TokenCounter, groups: tuple[tuple[str, ...], ...]) -> ExtractionPlan:
    if not schema.version_id or not document.version_id or common_checks(schema):
        raise ValueError("Valid versioned inputs required")
    if len({f.fragment_id for f in document.fragments}) != len(document.fragments) or any(not f.fragment_id for f in document.fragments):
        raise ValueError("Source fragment identities must be unique")
    properties = schema.schema.get("properties")
    assert isinstance(properties, dict)
    units: list[ExtractionUnit] = []
    budget = limits.context - limits.output - limits.reserve
    for group in groups:
        selected: dict[str, JsonValue] = {key: properties[key] for key in group}
        request_schema = snapshot({"type": "object", "properties": selected, "additionalProperties": False})
        if counter.count(payload("", group, request_schema)) >= budget:
            raise ValueError("Schema alone exceeds request budget")
        for fragment in document.fragments:
            start = 0
            while start < len(fragment.text):
                # Count the complete serialized request, including escaping and schema.
                lo, hi = start + 1, len(fragment.text)
                end = start
                while lo <= hi:
                    mid = (lo + hi) // 2
                    cost = counter.count(payload(fragment.text[start:mid], group, request_schema))
                    if cost <= budget:
                        end, lo = mid, mid + 1
                    else:
                        hi = mid - 1
                if end == start:
                    raise ValueError("One source character cannot fit the request")
                text = fragment.text[start:end]
                unit = ExtractionUnit(str(len(units)), fragment.fragment_id, start, end, text, group,
                                      request_schema, counter.count(payload(text, group, request_schema)))
                units.append(unit)
                start = end
    serial = repr((document, schema, limits, units))
    return ExtractionPlan(document.version_id, schema.version_id, tuple(units), hashlib.sha256(serial.encode()).hexdigest())


class ExtractionStrategy(Protocol):
    def build_plan(self, document: ParsedDocument, schema: SchemaDefinition,
                   limits: ModelLimits, counter: TokenCounter) -> ExtractionPlan: ...


class WholeSchemaChunkStrategy:
    def build_plan(self, document: ParsedDocument, schema: SchemaDefinition,
                   limits: ModelLimits, counter: TokenCounter) -> ExtractionPlan:
        return plan_groups(document, schema, limits, counter, (tuple(f.key for f in schema.fields),))


class GroupedFieldChunkStrategy:
    def __init__(self, group_size: int = 1) -> None:
        if group_size < 1:
            raise ValueError("Positive group size required")
        self._group_size = group_size

    def build_plan(self, document: ParsedDocument, schema: SchemaDefinition,
                   limits: ModelLimits, counter: TokenCounter) -> ExtractionPlan:
        keys = tuple(f.key for f in schema.fields)
        groups = tuple(keys[i:i + self._group_size] for i in range(0, len(keys), self._group_size))
        return plan_groups(document, schema, limits, counter, groups)


@dataclass(frozen=True)
class Candidate:
    field: str
    value_json: str
    fragment_id: str
    start: int
    end: int
    quote: str


class ModelPort(Protocol):
    @property
    def model_id(self) -> str: ...
    def extract(self, unit: ExtractionUnit) -> tuple[Candidate, ...]: ...


class CheckpointPort(Protocol):
    def bind(self, job_id: str, fingerprint: str) -> None: ...
    def load(self, job_id: str, unit_id: str) -> tuple[Candidate, ...] | None: ...
    def save(self, job_id: str, unit_id: str, candidates: tuple[Candidate, ...]) -> None: ...


@dataclass(frozen=True)
class ExtractionResult:
    document_version: str
    schema_version: str
    values_json: str
    candidates: tuple[Candidate, ...]
    issues: tuple[str, ...]
    confirmed: bool = False


class ExtractionContext:
    def __init__(self, strategy: ExtractionStrategy, model: ModelPort, checkpoints: CheckpointPort,
                 counter: TokenCounter, limits: ModelLimits, prompt_version: str = "v1") -> None:
        self._strategy = strategy
        self._model = model
        self._checkpoints = checkpoints
        self._counter = counter
        self._limits = limits
        self._prompt_version = prompt_version
        self._running = False

    def set_strategy(self, strategy: ExtractionStrategy) -> None:
        if self._running:
            raise RuntimeError("Cannot change an executing plan")
        self._strategy = strategy

    def _validate_plan(self, plan: ExtractionPlan, document: ParsedDocument, schema: SchemaDefinition) -> None:
        if not document.version_id or not schema.version_id or not schema.fields or common_checks(schema):
            raise ValueError("Extraction requires a valid published schema with fields")
        if len({f.fragment_id for f in document.fragments}) != len(document.fragments):
            raise ValueError("Duplicate source fragments")
        if plan.document_version != document.version_id or plan.schema_version != schema.version_id:
            raise ValueError("Plan version mismatch")
        if len({u.unit_id for u in plan.units}) != len(plan.units):
            raise ValueError("Duplicate work units")
        sources = {f.fragment_id: f.text for f in document.fragments}
        keys = {f.key for f in schema.fields}
        for unit in plan.units:
            source = sources.get(unit.fragment_id)
            if source is None or not (0 <= unit.start < unit.end <= len(source)) or source[unit.start:unit.end] != unit.text:
                raise ValueError("Invalid source range")
            if not unit.fields or not set(unit.fields) <= keys:
                raise ValueError("Invalid fields")
            request = read_object(unit.request_schema)
            full_properties = schema.schema.get("properties")
            assert isinstance(full_properties, dict)
            if request.get("properties") != {key: full_properties[key] for key in unit.fields}:
                raise ValueError("Request schema does not match planned fields")
            cost = self._counter.count(payload(unit.text, unit.fields, unit.request_schema))
            if cost != unit.input_cost or cost + self._limits.output + self._limits.reserve > self._limits.context:
                raise ValueError("Exceeded request budget")
        for field in keys:
            for fragment_id, text in sources.items():
                cursor = 0
                spans = sorted((u.start, u.end) for u in plan.units if u.fragment_id == fragment_id and field in u.fields)
                for start, end in spans:
                    if start > cursor:
                        raise ValueError("Incomplete source coverage")
                    cursor = max(cursor, end)
                if cursor != len(text):
                    raise ValueError("Incomplete source coverage")

    def run(self, job_id: str, document: ParsedDocument, schema: SchemaDefinition) -> ExtractionResult:
        if self._running or not job_id:
            raise ValueError("Invalid or concurrently used context")
        plan = self._strategy.build_plan(document, schema, self._limits, self._counter)
        self._validate_plan(plan, document, schema)
        fingerprint = hashlib.sha256(repr((plan, self._model.model_id, self._prompt_version)).encode()).hexdigest()
        self._checkpoints.bind(job_id, fingerprint)
        self._running = True
        candidates: list[Candidate] = []
        try:
            for unit in plan.units:
                batch = self._checkpoints.load(job_id, unit.unit_id)
                if batch is None:
                    batch = self._model.extract(unit)
                    for item in batch:
                        if (item.field not in unit.fields or item.fragment_id != unit.fragment_id
                            or not (unit.start <= item.start < item.end <= unit.end)
                            or unit.text[item.start - unit.start:item.end - unit.start] != item.quote):
                            raise ValueError("Invalid evidence returned by model")
                        # Reject invalid or non-finite JSON before checkpointing.
                        import json
                        value = json.loads(item.value_json)
                        snapshot(value)
                    self._checkpoints.save(job_id, unit.unit_id, batch)
                candidates.extend(batch)
        finally:
            self._running = False
        unique = tuple(dict.fromkeys(candidates))
        values: dict[str, JsonValue] = {}
        issues: list[str] = []
        import json
        for field in schema.fields:
            found = {snapshot(json.loads(c.value_json)) for c in unique if c.field == field.key}
            values[field.key] = json.loads(next(iter(found))) if len(found) == 1 else None
            if len(found) != 1:
                issues.append(("missing:" if not found else "conflict:") + field.key)
        return ExtractionResult(document.version_id, schema.version_id or "", snapshot(values), unique, tuple(issues))


class MemoryCheckpoints:
    def __init__(self) -> None:
        self._plans: dict[str, str] = {}
        self._units: dict[tuple[str, str], tuple[Candidate, ...]] = {}

    def bind(self, job_id: str, fingerprint: str) -> None:
        previous = self._plans.setdefault(job_id, fingerprint)
        if previous != fingerprint:
            raise ValueError("Changed inputs require a new job")

    def load(self, job_id: str, unit_id: str) -> tuple[Candidate, ...] | None:
        return self._units.get((job_id, unit_id))

    def save(self, job_id: str, unit_id: str, candidates: tuple[Candidate, ...]) -> None:
        if (job_id, unit_id) in self._units and self._units[job_id, unit_id] != candidates:
            raise ValueError("Checkpoint is immutable")
        self._units[job_id, unit_id] = candidates


class ScriptedModel:
    """Explicit fixture adapter, not a real language model."""
    def __init__(self, candidates: tuple[Candidate, ...], fail_on_call: int | None = None) -> None:
        self._candidates = candidates
        self._fail_on_call = fail_on_call
        self.calls = 0

    @property
    def model_id(self) -> str:
        return "fixture-model-v1"

    def extract(self, unit: ExtractionUnit) -> tuple[Candidate, ...]:
        self.calls += 1
        if self.calls == self._fail_on_call:
            raise TimeoutError("Simulated model timeout")
        return tuple(c for c in self._candidates if c.field in unit.fields and c.fragment_id == unit.fragment_id
                     and unit.start <= c.start < c.end <= unit.end)
