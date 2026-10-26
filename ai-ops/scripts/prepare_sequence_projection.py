"""실습 수집기 내부 snapshot과 검증한 조회 기록을 결합해 v3만 반출한다."""

import argparse
import hashlib
import json
from pathlib import Path
import re

from ai_ops.b6_sequence_capture import RequestObservation, SequenceLabClient, bind_reads
from ai_ops.b6_lab import CONTEXT
from ai_ops.contracts import canonical_json_bytes, sha256_json
from ai_ops.model_projection import SourceIdentity
from ai_ops.model_sequence_projection import parse_sequence_projection, probe_log_hash
from ai_ops.path_policy import cli_paths, write_private_json
from prepare_model_projection import ROOT, command, current_identity


def exporter_hash():
    """실행할 v1·v3 소스와 이 exporter를 해시에 결합한다."""
    files = {name: ROOT / "src/ai_ops" / name for name in
             ("model_projection.py", "model_sequence_projection.py")}
    files["sequence_exporter"] = Path(__file__)
    return sha256_json({name: hashlib.sha256(path.read_bytes()).hexdigest()
                        for name, path in files.items()})


def validate_capture(capture):
    """완료·원복·잠금 해제를 확인한 실제 수집 결과만 받는다."""
    if (capture.get("status") != "captured" or capture.get("external_model_calls") != 0 or
            capture.get("recovery") != {"owned_data_removed": True, "baseline_unchanged": True} or
            capture.get("lock_release", {}).get("released") is not True):
        raise ValueError("sequence capture incomplete")
    reads = bind_reads([RequestObservation.model_validate(o) for o in capture["observations"]],
                       capture["raw_logs"].encode(), capture["item_id"])
    if ([r.model_dump(mode="json") for r in reads] != capture["reads"] or
            probe_log_hash(reads) != capture["log_source_hash"] or
            hashlib.sha256(capture["raw_logs"].encode()).hexdigest() != capture["raw_log_sha256"]):
        raise ValueError("sequence capture changed")
    for key, pattern in (("incident_id", r"inc-[a-f0-9]{32}"), ("run_id", r"run-[a-f0-9]{32}"),
                         ("bundle_hash", r"[a-f0-9]{64}")):
        if not re.fullmatch(pattern, capture["investigation"][key]):
            raise ValueError("sequence incident rejected")
    return reads


def exporter_program(capture, current):
    """원본 snapshot은 출력하지 않고 검증한 모듈을 메모리에서만 실행한다."""
    program = "import json,sys,types\ntry:\n"
    for name in ("model_projection", "model_sequence_projection"):
        source = (ROOT / f"src/ai_ops/{name}.py").read_text()
        program += (f" m=types.ModuleType('ai_ops.{name}');sys.modules[m.__name__]=m\n"
                    f" exec(compile({source!r},'<sequence-export>','exec'),m.__dict__)\n")
    program += """ from pathlib import Path
 from ai_ops.b_contracts import load_b_settings
 from ai_ops.incidents import IncidentStore
 from ai_ops.collection import CapturedSnapshot
 from ai_ops.model_projection import SourceIdentity,project_snapshot
 from ai_ops.model_sequence_projection import ProbeRecord,project_probe
 from ai_ops.contracts import sha256_json
"""
    program += f" capture=json.loads({json.dumps(capture)!r})\n current=json.loads({json.dumps(current)!r})\n"
    program += """ settings=load_b_settings(*(Path('/etc/ai-ops')/n for n in ('bindings.json','runtime.json','metric-queries.json')))
 event=capture['investigation']
 record=IncidentStore(Path('/data/incidents')).show(event['incident_id'],event['run_id'])
 if record.collection_audit is None or record.bundle.bundle_hash!=event['bundle_hash']: raise ValueError('snapshot mismatch')
 app=capture['baseline']['app']
 identity=SourceIdentity(cluster_fingerprint=current['cluster_fingerprint'],deployment_uid=app['deployment_uid'],
     config_hash=settings.config_hash,incident_id=event['incident_id'],run_id=event['run_id'],exporter_sha256=current['exporter_hash'])
 snapshot=CapturedSnapshot(record.bundle,record.collection_audit)
 base=project_snapshot(snapshot,settings,identity)
 probe=ProbeRecord(source_bundle_hash=base.source_bundle_hash,source_audit_hash=base.source_audit_hash,
     config_hash=settings.config_hash,deployment_uid=app['deployment_uid'],pod_uid=app['pod_uid'],
     image_reference_hash=sha256_json(app['image']),window=base.view.window,recorded_at=capture['recorded_at'],
     item_id=capture['item_id'],reads=capture['reads'],log_source_hash=capture['log_source_hash'],raw_log_sha256=capture['raw_log_sha256'])
 result=project_probe(base,probe,snapshot,settings,raw_logs=capture['raw_logs'].encode(),log_pod_uid=app['pod_uid'],logs_truncated=False)
 print(result.model_dump_json())
except Exception:
 print('sequence_export_rejected');sys.exit(1)
"""
    return program


def prepare_export(capture, *, execute=command, client=None):
    """현재 대상·수집기 digest를 전후에 대조하고 원본을 로컬로 반출하지 않는다."""
    validate_capture(capture)
    client = client or SequenceLabClient()
    image = capture["baseline"]["lab"]["lab-collector"]["image"]
    if not re.fullmatch(r"716174522908\.dkr\.ecr\.ap-northeast-2\.amazonaws\.com/urlshortener-practice-ai-ops@sha256:[a-f0-9]{64}", image):
        raise ValueError("sequence collector image rejected")
    before = current_identity(image, execute=execute)
    if client.pin_app() != capture["baseline"]["app"] or before["deployment_uid"] != capture["baseline"]["app"]["deployment_uid"]:
        raise ValueError("sequence current app changed")
    current = {**before, "exporter_hash": exporter_hash()}
    raw = execute(["kubectl", "--context", CONTEXT,
        "-n", "ai-ops-lab", "exec", "-i", before["pod_name"], "-c", "collector", "--", "python", "-"],
        data=exporter_program(capture, current).encode(), limit=256*1024)
    result = parse_sequence_projection(raw)
    identity = result.base.identity
    if (current_identity(image, execute=execute) != before or client.pin_app() != capture["baseline"]["app"] or
            identity.exporter_sha256 != current["exporter_hash"] or
            identity.cluster_fingerprint != before["cluster_fingerprint"] or
            identity.deployment_uid != before["deployment_uid"] or
            identity.incident_id != capture["investigation"]["incident_id"] or
            identity.run_id != capture["investigation"]["run_id"] or
            result.base.source_bundle_hash != capture["investigation"]["bundle_hash"] or
            result.probe.item_id != capture["item_id"] or
            result.probe.pod_uid != capture["baseline"]["app"]["pod_uid"] or
            result.probe.raw_log_sha256 != capture["raw_log_sha256"] or
            result.probe.log_source_hash != capture["log_source_hash"] or
            result.probe.image_reference_hash != sha256_json(capture["baseline"]["app"]["image"]) or
            result.probe.reads != validate_capture(capture)):
        raise ValueError("sequence export binding changed")
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description="실습 조회 기록과 내부 snapshot의 v3 정제 반출; API 호출 없음")
    parser.add_argument("--capture", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = cli_paths(parser, parser.parse_args(argv), {"capture": "input", "output": "output"})
    try:
        if args.capture.stat().st_size > 128*1024:
            raise ValueError("sequence capture too large")
        result = prepare_export(json.loads(args.capture.read_bytes()))
        write_private_json(args.output, result.model_dump(mode="json"))
        print(json.dumps({"policy": result.view.policy_version, "view_hash": result.view_hash,
                          "items": len(result.view.items), "external_model_calls": 0}))
        return 0
    except Exception:
        parser.exit(1, "v3 정제 반출 실패: 원본 출처·현재 Pod·수집기 이미지·해시·시각을 확인하세요.\n")


if __name__ == "__main__":
    raise SystemExit(main())
