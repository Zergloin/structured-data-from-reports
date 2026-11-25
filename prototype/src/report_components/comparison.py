"""comparison_comp: compare confirmed snapshots by semantic field identity."""
from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
import math
from typing import Protocol
from .domain import ConfirmedRecord, FieldKind, JsonValue, snapshot
from .value_validation import ConfirmValueStrategy, ValidationMode, ValueValidator


class ComparisonStrategy(Protocol):
    def equivalent(self, left: JsonValue, right: JsonValue, kind: FieldKind) -> bool: ...


class ExactComparisonStrategy:
    def equivalent(self, left: JsonValue, right: JsonValue, kind: FieldKind) -> bool:
        if kind in {FieldKind.NUMBER, FieldKind.INTEGER}:
            return (isinstance(left, (int, float)) and not isinstance(left, bool)
                    and isinstance(right, (int, float)) and not isinstance(right, bool) and left == right)
        return snapshot(left) == snapshot(right)


class NumericToleranceStrategy:
    def __init__(self, absolute: float, relative: float = 0.0) -> None:
        if any(not math.isfinite(v) or v < 0 for v in (absolute, relative)):
            raise ValueError("Tolerance must be finite and nonnegative")
        self._absolute = absolute
        self._relative = relative

    def equivalent(self, left: JsonValue, right: JsonValue, kind: FieldKind) -> bool:
        if kind in {FieldKind.NUMBER, FieldKind.INTEGER}:
            if isinstance(left, bool) or isinstance(right, bool):
                return False
            if isinstance(left, (float, int)) and isinstance(right, (float, int)):
                return math.isclose(left, right, abs_tol=self._absolute, rel_tol=self._relative)
            return False
        return ExactComparisonStrategy().equivalent(left, right, kind)


@dataclass(frozen=True)
class FieldComparison:
    semantic_id: str
    status: str
    values: tuple[JsonValue, ...]


@dataclass(frozen=True)
class ComparisonResult:
    version_ids: tuple[str, ...]
    fields: tuple[FieldComparison, ...]


class RecordComparator:
    def __init__(self, strategy: ComparisonStrategy) -> None:
        self._strategy = strategy

    def set_strategy(self, strategy: ComparisonStrategy) -> None:
        self._strategy = strategy

    def compare(self, records: tuple[ConfirmedRecord, ...]) -> ComparisonResult:
        if len(records) < 2 or len({r.version_id for r in records}) != len(records):
            raise ValueError("Two or more distinct record versions required")
        for record in records:
            if not record.confirmed or not record.version_id or not record.document_version_id or not record.schema.version_id:
                raise ValueError("Only confirmed versioned records are comparable")
            check = ValueValidator(ConfirmValueStrategy()).validate(record.schema, record.values, ValidationMode.CONFIRM)
            if not check.can_confirm:
                raise ValueError("Invalid confirmed snapshot")
        indexes = [{f.semantic_id: f for f in r.schema.fields} for r in records]
        fields: list[FieldComparison] = []
        for semantic_id in sorted(set().union(*(set(x) for x in indexes))):
            definitions = [index.get(semantic_id) for index in indexes]
            values: tuple[JsonValue, ...] = tuple(r.values.get(f.key) if f else None for r, f in zip(records, definitions))
            if any(f is None for f in definitions) or any(v is None for v in values):
                status = "missing"
            else:
                present = [f for f in definitions if f is not None]
                if len({(f.kind, f.unit) for f in present}) != 1:
                    status = "incompatible"
                else:
                    status = "equal" if all(self._strategy.equivalent(a, b, present[0].kind)
                                            for a, b in combinations(values, 2)) else "different"
            fields.append(FieldComparison(semantic_id, status, values))
        return ComparisonResult(tuple(r.version_id for r in records), tuple(fields))
