"""int_flow: exchange policy; API owns jobs, retries and outbox."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Protocol, TypeAlias
from .domain import ConfirmedRecord


class RetryableError(Exception):
    """A failed operation whose repetition is known to be safe."""


class ManualReconciliation(Exception):
    """Unknown external outcome, with no safe automatic repetition."""


@dataclass(frozen=True)
class ExternalDocument:
    external_id: str
    version: str
    content: bytes
    checksum: str


@dataclass(frozen=True)
class ImportRequest:
    job_id: str
    external_id: str
    external_version: str
    schema_version: str | None = None


@dataclass(frozen=True)
class ExportRequest:
    job_id: str
    export_id: str
    records: tuple[ConfirmedRecord, ...]


IntegrationRequest: TypeAlias = ImportRequest | ExportRequest


@dataclass(frozen=True)
class ExecutionLease:
    request: IntegrationRequest
    attempt: int
    token: int
    terminal_state: str | None = None


@dataclass(frozen=True)
class ProcessingResult:
    state: str
    ack: bool
    detail: str


class SystemApi(Protocol):
    def claim(self, job_id: str, operation: str) -> ExecutionLease: ...
    def register_import(self, lease: ExecutionLease, document: ExternalDocument) -> str: ...
    def delivery_started(self, lease: ExecutionLease, export_id: str) -> bool: ...
    def finish(self, lease: ExecutionLease, state: str, detail: str) -> None: ...
    def schedule_retry(self, lease: ExecutionLease, detail: str) -> None: ...


class ArchivePort(Protocol):
    def fetch(self, external_id: str, version: str) -> ExternalDocument: ...


class AnalyticsPort(Protocol):
    @property
    def supports_idempotency(self) -> bool: ...
    def status(self, export_id: str) -> str: ...
    def send(self, export_id: str, records: tuple[ConfirmedRecord, ...]) -> None: ...


class IntegrationStrategy(Protocol):
    @property
    def operation(self) -> str: ...
    def execute(self, lease: ExecutionLease, api: SystemApi) -> str: ...


class ImportStrategy:
    def __init__(self, archive: ArchivePort) -> None:
        self._archive = archive

    @property
    def operation(self) -> str:
        return "import"

    def execute(self, lease: ExecutionLease, api: SystemApi) -> str:
        request = lease.request
        if not isinstance(request, ImportRequest):
            raise ValueError("Import request required")
        document = self._archive.fetch(request.external_id, request.external_version)
        if (document.external_id != request.external_id or document.version != request.external_version
            or hashlib.sha256(document.content).hexdigest() != document.checksum):
            raise ValueError("Archive version or checksum mismatch")
        return api.register_import(lease, document)


class ExportStrategy:
    def __init__(self, analytics: AnalyticsPort) -> None:
        self._analytics = analytics

    @property
    def operation(self) -> str:
        return "export"

    def execute(self, lease: ExecutionLease, api: SystemApi) -> str:
        request = lease.request
        if not isinstance(request, ExportRequest):
            raise ValueError("Export request required")
        if (not request.export_id or not request.records
            or len({r.version_id for r in request.records}) != len(request.records)
            or any(not r.confirmed or not r.version_id or not r.document_version_id or not r.schema.version_id for r in request.records)):
            raise ValueError("Confirmed distinct snapshots required")
        # The durable intent must precede the external side effect.
        previous = api.delivery_started(lease, request.export_id)
        if previous:
            state = self._analytics.status(request.export_id)
            if state == "delivered":
                return request.export_id
            if state == "unknown" and not self._analytics.supports_idempotency:
                raise ManualReconciliation("Cannot determine prior delivery")
        try:
            self._analytics.send(request.export_id, request.records)
        except TimeoutError as exc:
            state = self._analytics.status(request.export_id)
            if state == "delivered":
                return request.export_id
            if state == "absent" or self._analytics.supports_idempotency:
                raise RetryableError("Delivery can be retried safely") from exc
            raise ManualReconciliation("Unknown delivery outcome") from exc
        return request.export_id


class IntegrationContext:
    def __init__(self, strategy: IntegrationStrategy, api: SystemApi, max_attempts: int = 3) -> None:
        if max_attempts < 1:
            raise ValueError("Positive retry limit required")
        self._strategy = strategy
        self._api = api
        self._max_attempts = max_attempts

    def set_strategy(self, strategy: IntegrationStrategy) -> None:
        self._strategy = strategy

    def process(self, job_id: str) -> ProcessingResult:
        try:
            lease = self._api.claim(job_id, self._strategy.operation)
        except (ConnectionError, ValueError) as exc:
            return ProcessingResult("unavailable", False, str(exc))
        if lease.terminal_state:
            return ProcessingResult(lease.terminal_state, True, "Already committed")
        try:
            detail = self._strategy.execute(lease, self._api)
            state = "succeeded"
        except RetryableError as exc:
            detail = str(exc)
            if lease.attempt < self._max_attempts:
                try:
                    self._api.schedule_retry(lease, detail)
                    return ProcessingResult("retry_scheduled", True, detail)
                except (ConnectionError, ValueError) as api_error:
                    return ProcessingResult("unavailable", False, str(api_error))
            state = "failed"
        except ManualReconciliation as exc:
            detail, state = str(exc), "manual"
        except ValueError as exc:
            detail, state = str(exc), "failed"
        except ConnectionError as exc:
            return ProcessingResult("unavailable", False, str(exc))
        try:
            self._api.finish(lease, state, detail)
            return ProcessingResult(state, True, detail)
        except (ConnectionError, ValueError) as exc:
            return ProcessingResult("unavailable", False, str(exc))


@dataclass
class _Job:
    request: IntegrationRequest
    attempt: int = 0
    token: int = 0
    state: str = "pending"
    detail: str = ""


class MemorySystemApi:
    """Fixture for atomic API operations; process-local memory is not a database."""
    def __init__(self) -> None:
        self._jobs: dict[str, _Job] = {}
        self._imports: dict[tuple[str, str], ExternalDocument] = {}
        self._deliveries: dict[str, str] = {}
        self.outbox: list[tuple[str, str]] = []
        self.extraction_jobs: list[tuple[str, str]] = []
        self.available = True

    def register_job(self, request: IntegrationRequest) -> None:
        if not request.job_id or request.job_id in self._jobs:
            raise ValueError("Job must be new and nonempty")
        if isinstance(request, ExportRequest) and any(isinstance(j.request, ExportRequest)
                and j.request.export_id == request.export_id for j in self._jobs.values()):
            raise ValueError("Export identity is already registered")
        self._jobs[request.job_id] = _Job(request)

    def claim(self, job_id: str, operation: str) -> ExecutionLease:
        if not self.available:
            raise ConnectionError("API unavailable")
        if job_id not in self._jobs:
            raise ValueError("Unknown job")
        job = self._jobs[job_id]
        expected = "import" if isinstance(job.request, ImportRequest) else "export"
        if operation != expected or job.state == "running":
            raise ValueError("Wrong operation or active lease")
        if job.state in {"succeeded", "failed", "manual"}:
            return ExecutionLease(job.request, job.attempt, job.token, job.state)
        job.attempt += 1
        job.token += 1
        job.state = "running"
        return ExecutionLease(job.request, job.attempt, job.token)

    def _check(self, lease: ExecutionLease) -> _Job:
        if not self.available:
            raise ConnectionError("API unavailable")
        job = self._jobs[lease.request.job_id]
        if job.token != lease.token or job.state != "running":
            raise ValueError("Stale lease")
        return job

    def expire_lease(self, job_id: str) -> None:
        job = self._jobs[job_id]
        job.token += 1
        job.state = "retry"

    def register_import(self, lease: ExecutionLease, document: ExternalDocument) -> str:
        job = self._check(lease)
        request = job.request
        if not isinstance(request, ImportRequest):
            raise ValueError("Import lease required")
        key = (document.external_id, document.version)
        previous = self._imports.get(key)
        if previous and previous.checksum != document.checksum:
            raise ValueError("Same external version has different content")
        document_id = "doc:" + ":".join(key)
        if not previous:
            self._imports[key] = document
        if request.schema_version and (document_id, request.schema_version) not in self.extraction_jobs:
            self.extraction_jobs.append((document_id, request.schema_version))
            self.outbox.append(("extraction", document_id + ":" + request.schema_version))
        return document_id

    def delivery_started(self, lease: ExecutionLease, export_id: str) -> bool:
        self._check(lease)
        owner = self._deliveries.get(export_id)
        if owner and owner != lease.request.job_id:
            raise ValueError("Export identity belongs to another job")
        self._deliveries[export_id] = lease.request.job_id
        return owner is not None

    def finish(self, lease: ExecutionLease, state: str, detail: str) -> None:
        job = self._check(lease)
        job.state, job.detail = state, detail

    def schedule_retry(self, lease: ExecutionLease, detail: str) -> None:
        job = self._check(lease)
        job.state, job.detail = "retry", detail
        self.outbox.append(("integration", lease.request.job_id))


class MemoryArchive:
    def __init__(self, document: ExternalDocument, failures: int = 0) -> None:
        self._document = document
        self._failures = failures

    def fetch(self, external_id: str, version: str) -> ExternalDocument:
        if self._failures:
            self._failures -= 1
            raise RetryableError("Archive temporarily unavailable")
        return self._document


class MemoryAnalytics:
    def __init__(self, idempotent: bool = True, status_supported: bool = True, timeout_after_send: bool = False) -> None:
        self._idempotent = idempotent
        self._status_supported = status_supported
        self._timeout = timeout_after_send
        self.sent: dict[str, tuple[ConfirmedRecord, ...]] = {}
        self.send_calls = 0

    @property
    def supports_idempotency(self) -> bool:
        return self._idempotent

    def status(self, export_id: str) -> str:
        if not self._status_supported:
            return "unknown"
        return "delivered" if export_id in self.sent else "absent"

    def send(self, export_id: str, records: tuple[ConfirmedRecord, ...]) -> None:
        self.send_calls += 1
        if export_id not in self.sent or not self._idempotent:
            self.sent[export_id] = records
        if self._timeout:
            self._timeout = False
            raise TimeoutError("Response lost after accepting data")
