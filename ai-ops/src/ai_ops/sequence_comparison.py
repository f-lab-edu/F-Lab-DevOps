"""v3 일회 비교를 별도 보존하고 기존 로컬 일일 예산을 공유한다."""

from datetime import timedelta
import hashlib
from pathlib import Path

from ai_ops.contracts import _utc_datetime, canonical_json_bytes, sha256_json
from ai_ops.model_comparison import ComparisonStore, DEFAULT_ROOT, utc_text
from ai_ops.model_sequence_projection import parse_sequence_projection
from ai_ops.path_policy import PERSONAL_ROOT
from ai_ops.projected_model_engine import CallPlan
from ai_ops.projected_sequence_model_engine import (SEQUENCE_PROMPT_VERSION,
    request_sequence_templates, rules_sequence_projection, validate_sequence_report)


def implementation_hash():
    """상속한 승인·전송 코드와 v3 정제·프롬프트를 모두 결합한다."""
    root = Path(__file__).parent
    names = ("sequence_comparison.py", "model_sequence_projection.py",
             "projected_sequence_model_engine.py", "model_comparison.py",
             "model_projection.py", "projected_model_engine.py", "projected_model_transport.py")
    return sha256_json({name: hashlib.sha256((root/name).read_bytes()).hexdigest() for name in names})


class SequenceComparisonStore(ComparisonStore):
    """기존 보존 파일과 키를 분리하고 예약 장부만 기존 도구와 공유한다."""

    def __init__(self, root=PERSONAL_ROOT/"sequence-comparisons", *, now=None, tick=None,
                 budget_root=DEFAULT_ROOT):
        super().__init__(root, now=now, tick=tick)
        self.budget_store = ComparisonStore(budget_root, now=now, tick=tick)
        if self.root == self.budget_store.root:
            raise ValueError("sequence storage must be separate")

    def _reserve(self, now, mode="mock"):
        with self.budget_store.locked():
            self.budget_store._reserve(now, mode)

    def prepare(self, projection, plan):
        projection = parse_sequence_projection(canonical_json_bytes(projection.model_dump(mode="json")))
        plan = CallPlan.model_validate_json(plan.model_dump_json())
        templates = request_sequence_templates(projection.view, plan)
        bound = {"projection": projection.model_dump(mode="json"), "plan": plan.model_dump(mode="json"),
            "templates_hash": sha256_json(templates), "implementation_hash": implementation_hash(),
            "prompt_version": SEQUENCE_PROMPT_VERSION}
        digest = sha256_json(bound)
        identifier = "cmp-" + digest[:32]
        now = _utc_datetime(self.now())
        record = {**bound, "id": identifier, "binding_hash": digest, "state": "prepared",
            "created_at": utc_text(now), "expires_at": utc_text(now+timedelta(days=7)),
            "rules": rules_sequence_projection(projection.view).model_dump(mode="json")}
        with self.locked():
            path = self._path(identifier)
            if path.exists():
                if self._read(path).get("binding_hash") != digest:
                    raise ValueError("sequence identity conflict")
                return identifier
            self._write(path, record)
        return identifier

    def _validated(self, identifier):
        record = self._read(self._path(identifier))
        if record.get("id") != identifier or record.get("state") not in {
                "prepared", "approved", "sending", "failed", "needs_review"}:
            raise ValueError("sequence record rejected")
        projection = parse_sequence_projection(canonical_json_bytes(record["projection"]))
        plan = CallPlan.model_validate_json(canonical_json_bytes(record["plan"]))
        templates = request_sequence_templates(projection.view, plan)
        bound = {key: record[key] for key in ("projection", "plan", "templates_hash",
                                              "implementation_hash", "prompt_version")}
        if (record["prompt_version"] != SEQUENCE_PROMPT_VERSION or
                record["implementation_hash"] != implementation_hash() or
                record["templates_hash"] != sha256_json(templates) or
                record["binding_hash"] != sha256_json(bound) or
                record["rules"] != rules_sequence_projection(projection.view).model_dump(mode="json")):
            raise ValueError("sequence code or input changed")
        if "model_report" in record:
            validate_sequence_report(record["model_report"], projection.view)
        return record, projection, plan, templates
