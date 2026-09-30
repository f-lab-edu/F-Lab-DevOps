"""로컬 사건·실행 상태와 정제 입력·규칙 보고서를 원자적으로 보존한다."""

from __future__ import annotations

import fcntl
import hashlib
import hmac
import json
import os
import re
import stat
import tempfile
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterator

from ai_ops.contracts import (
    AnalysisReport, EvidenceBundle, IncidentRequest, TargetRegistry,
    canonical_json_bytes, parse_evidence_bundle_json, parse_incident_request_json,
    sha256_json,
)
from ai_ops.evidence import create_sanitized_incident_request, load_evidence_bundle, sanitize_question
from ai_ops.reports import render_markdown, validate_report
from ai_ops.rules import analyze_rules


PACKAGE_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = PACKAGE_ROOT.parent
ID_PATTERN = re.compile(r"(?:inc|run)-[0-9a-f]{32}\Z")
ACTIVE_STATES = {"accepted", "validating", "analyzing"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _private_directory(path: Path) -> None:
    if path.is_symlink():
        raise ValueError("storage directory cannot be a symlink")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.stat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
        raise ValueError("storage directory must be owned by the user and private (0700)")


def _atomic_write(path: Path, content: bytes) -> None:
    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".tmp-", delete=False) as output:
            temporary = output.name
            os.fchmod(output.fileno(), 0o600)
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None and os.path.exists(temporary):
            os.unlink(temporary)


def _write_json(path: Path, value: dict) -> None:
    _atomic_write(path, (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8"))


def _read_json(path: Path, limit: int = 5 * 1024 * 1024) -> dict:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > limit:
        raise ValueError("stored file is missing or unsafe")
    value = json.loads(path.read_bytes())
    if not isinstance(value, dict):
        raise ValueError("stored JSON must be an object")
    return value


@dataclass(frozen=True)
class RunRecord:
    metadata: dict
    request: IncidentRequest | None = None
    bundle: EvidenceBundle | None = None
    report: AnalysisReport | None = None
    markdown: str | None = None

    @property
    def incident_id(self) -> str:
        return self.metadata["incident_id"]

    @property
    def run_id(self) -> str:
        return self.metadata["run_id"]


class RunStopped(Exception):
    def __init__(self, incident_id: str, run_id: str, status: str):
        super().__init__(f"{status}: {incident_id}/{run_id}")
        self.incident_id = incident_id
        self.run_id = run_id
        self.status = status


class IncidentStore:
    """한 저장소의 동시 요청을 잠그고 중단된 실행을 실패로 복원한다."""

    def __init__(self, root: Path):
        root = root.expanduser().absolute()
        if root.resolve().is_relative_to(REPO_ROOT.resolve()):
            raise ValueError("runtime storage must be outside the repository")
        _private_directory(root)
        _private_directory(root / "requests")
        self.root = root
        self._fingerprint_key = self._load_fingerprint_key()

    def _load_fingerprint_key(self) -> bytes:
        """원본 질문을 저장하지 않고 재전송 내용을 비교할 개인 저장소 키를 준비한다."""
        fd = os.open(self.root / ".fingerprint-key", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
                raise ValueError("fingerprint key must be a private regular file")
            if info.st_size == 0:
                os.write(fd, os.urandom(32))
                os.fsync(fd)
            if os.fstat(fd).st_size != 32:
                raise ValueError("fingerprint key has invalid size")
            os.lseek(fd, 0, os.SEEK_SET)
            return os.read(fd, 32)
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    @contextmanager
    def _locked(self) -> Iterator[None]:
        lock_fd = os.open(self.root / ".lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            self._reconcile()
            yield
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            os.close(lock_fd)

    def _run_dir(self, incident_id: str, run_id: str) -> Path:
        if not ID_PATTERN.fullmatch(incident_id) or not incident_id.startswith("inc-"):
            raise ValueError("invalid incident ID")
        if not ID_PATTERN.fullmatch(run_id) or not run_id.startswith("run-"):
            raise ValueError("invalid run ID")
        incident_dir = self.root / incident_id
        run_dir = incident_dir / run_id
        if incident_dir.is_symlink() or run_dir.is_symlink():
            raise ValueError("stored run path cannot be a symlink")
        return run_dir

    def _index_path(self, request_id: str) -> Path:
        return self.root / "requests" / (sha256_json(request_id) + ".json")

    def _transition(self, run_dir: Path, metadata: dict, status: str, reason: str | None = None) -> None:
        moment = _utc_text(_now())
        metadata["status"] = status
        metadata["updated_at"] = moment
        metadata["failure_reason"] = reason
        metadata["history"].append({"status": status, "at": moment})
        _write_json(run_dir / "metadata.json", metadata)

    def _reconcile(self) -> None:
        for incident_dir in self.root.glob("inc-*"):
            if incident_dir.is_symlink() or not incident_dir.is_dir() or not ID_PATTERN.fullmatch(incident_dir.name):
                continue
            for run_dir in incident_dir.glob("run-*"):
                if run_dir.is_symlink() or not run_dir.is_dir() or not ID_PATTERN.fullmatch(run_dir.name):
                    continue
                metadata_path = run_dir / "metadata.json"
                if not metadata_path.exists():
                    continue
                metadata = _read_json(metadata_path)
                if metadata.get("incident_id") != incident_dir.name or metadata.get("run_id") != run_dir.name:
                    raise ValueError("stored metadata path mismatch")
                if metadata.get("status") in ACTIVE_STATES:
                    self._transition(run_dir, metadata, "failed", "interrupted")
                index_path = self._index_path(metadata["request_id"])
                if not index_path.exists():
                    _write_json(index_path, {
                        "request_id": metadata["request_id"], "fingerprint": metadata["fingerprint"],
                        "incident_id": incident_dir.name, "run_id": run_dir.name,
                    })

    def _read_record(self, incident_id: str, run_id: str) -> RunRecord:
        run_dir = self._run_dir(incident_id, run_id)
        metadata = _read_json(run_dir / "metadata.json")
        if metadata.get("incident_id") != incident_id or metadata.get("run_id") != run_id:
            raise ValueError("stored metadata does not match requested run")
        if metadata["status"] != "completed":
            return RunRecord(metadata=metadata)
        input_data = _read_json(run_dir / "input.json")
        request = parse_incident_request_json(canonical_json_bytes(input_data["request"]))
        bundle = parse_evidence_bundle_json(canonical_json_bytes(input_data["bundle"]))
        report = AnalysisReport.model_validate(_read_json(run_dir / "report.json"))
        validate_report(report, request, bundle, run_id)
        markdown = render_markdown(report, request, bundle)
        return RunRecord(metadata=metadata, request=request, bundle=bundle, report=report, markdown=markdown)

    def show(self, incident_id: str, run_id: str) -> RunRecord:
        with self._locked():
            return self._read_record(incident_id, run_id)

    def investigate(
        self, *, bundle_root: Path, registry: TargetRegistry, question: str,
        request_id: str, analyzer: Callable[[IncidentRequest, EvidenceBundle, str], AnalysisReport] = analyze_rules,
    ) -> RunRecord:
        bundle = load_evidence_bundle(bundle_root, registry)
        cleaned_question, _ = sanitize_question(question)
        fingerprint = hmac.new(self._fingerprint_key, canonical_json_bytes({
            "request_id": request_id, "mode": "rules", "question": question,
            "sanitized_question": cleaned_question,
            "target": bundle.target.model_dump(mode="json"),
            "window": bundle.window.model_dump(mode="json"), "bundle_hash": bundle.bundle_hash,
        }), hashlib.sha256).hexdigest()
        with self._locked():
            index_path = self._index_path(request_id)
            if index_path.exists():
                index = _read_json(index_path)
                if index.get("request_id") != request_id or index.get("fingerprint") != fingerprint:
                    raise ValueError("request_id was already used with different input")
                return self._read_record(index["incident_id"], index["run_id"])

            request, question_redaction = create_sanitized_incident_request(
                request_id=request_id, target=bundle.target, question=question,
                window=bundle.window, bundle=bundle, registry=registry, requested_at=_now(),
            )
            run_id = "run-" + uuid.uuid4().hex
            run_dir = self._run_dir(request.incident_id, run_id)
            _private_directory(run_dir.parent)
            run_dir.mkdir(mode=0o700)
            moment = _utc_text(_now())
            metadata = {
                "schema_version": 1, "incident_id": request.incident_id, "run_id": run_id,
                "request_id": request_id, "fingerprint": fingerprint,
                "input_hash": bundle.bundle_hash, "mode": "rules", "status": "accepted",
                "created_at": moment, "updated_at": moment, "failure_reason": None,
                "history": [{"status": "accepted", "at": moment}],
            }
            _write_json(run_dir / "input.json", {
                "request": request.model_dump(mode="json"),
                "bundle": bundle.model_dump(mode="json"),
                "question_redaction": question_redaction.model_dump(mode="json"),
            })
            _write_json(run_dir / "metadata.json", metadata)
            _write_json(index_path, {
                "request_id": request_id, "fingerprint": fingerprint,
                "incident_id": request.incident_id, "run_id": run_id,
            })
            try:
                self._transition(run_dir, metadata, "validating")
                self._transition(run_dir, metadata, "analyzing")
                started = time.perf_counter()
                report = analyzer(request, bundle, run_id)
                report.execution.duration_ms = int((time.perf_counter() - started) * 1000)
                validate_report(report, request, bundle, run_id)
                markdown = render_markdown(report, request, bundle)
                _write_json(run_dir / "report.json", report.model_dump(mode="json"))
                _atomic_write(run_dir / "report.md", markdown.encode("utf-8"))
                self._transition(run_dir, metadata, "completed")
            except KeyboardInterrupt:
                self._transition(run_dir, metadata, "cancelled", "user_cancelled")
                raise RunStopped(request.incident_id, run_id, "cancelled") from None
            except Exception:
                self._transition(run_dir, metadata, "failed", "rules_error")
                raise RunStopped(request.incident_id, run_id, "failed") from None
            return RunRecord(metadata=metadata, request=request, bundle=bundle,
                             report=report, markdown=markdown)
