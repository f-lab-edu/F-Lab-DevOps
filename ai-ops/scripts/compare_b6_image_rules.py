"""실습 Pod의 저장 증거에 후보 규칙만 적용하고 원본 불변·본문 없는 비교를 기록한다."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess

from ai_ops.b6_lab import CONTEXT, NAMESPACE
from ai_ops.path_policy import cli_paths, write_private_json


PROGRAM = r'''
import hashlib,json,sys,types
from pathlib import Path
from ai_ops.incidents import IncidentStore
from ai_ops.contracts import canonical_json_bytes
from ai_ops.reports import validate_report
from ai_ops.data_policy import ensure_external_model_allowed,ModelDataDenied
root=Path('/data/incidents')
store=IncidentStore(root)
incident,run=sys.argv[1:]
def hashes():
    return {str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest()
        for pattern in ('inc-*/run-*/report.json','inc-*/run-*/input.json','inc-*/run-*/collection-audit.json')
        for p in sorted(root.glob(pattern))}
before=hashes()
records=[]
for p in sorted(root.glob('inc-*/run-*/report.json')):
    r=store.show(p.parent.parent.name,p.parent.name)
    assert r.report is not None
    records.append(r)
record=store.show(incident,run)
assert record.bundle.target.namespace=='ai-ops-lab' and record.bundle.target.environment=='practice'
assert record.report.execution.engine=='rules-captured-v2'
source=sys.stdin.read(131073)
assert len(source.encode())<=131072
module=types.ModuleType('candidate_captured_rules')
exec(compile(source,'candidate_captured_rules.py','exec'),module.__dict__)
fields=('analysis_status','diagnosis_status','current_state','facts','hypotheses','missing_evidence',
        'recommended_checks','suggested_actions','limitations')
legacy_checked=0
for r in records:
    if r.report.execution.engine not in ('rules-captured-v1','rules-captured-v2'):
        continue
    analyzer=module.analyze_snapshot_rules_v1 if r.report.execution.engine=='rules-captured-v1' else module.analyze_snapshot_rules_v2
    expected=analyzer(r.request,r.bundle,r.run_id,r.collection_audit)
    assert all(getattr(expected,f)==getattr(r.report,f) for f in fields)
    legacy_checked+=1
candidate=module.analyze_snapshot_rules(record.request,record.bundle,record.run_id,record.collection_audit)
validate_report(candidate,record.request,record.bundle,record.run_id)
assert candidate.execution.engine=='rules-captured-v3' and candidate.execution.mode=='rules'
assert candidate.diagnosis_status=='suspected_cause' and candidate.current_state=='unknown'
assert candidate.analysis_status==record.report.analysis_status=='partial'
assert len(candidate.hypotheses)==1 and len(candidate.facts)>len(record.report.facts)
available={e.evidence_id for e in record.bundle.evidence if e.status=='available'}
assert all(set(f.evidence_ids)<=available for f in candidate.facts)
assert all(set(h.supporting_evidence_ids)<=available for h in candidate.hypotheses)
assert all(a.execution_status=='not_executed' for a in candidate.suggested_actions)
try:
    ensure_external_model_allowed(record.bundle)
except ModelDataDenied:
    blocked=True
else:
    blocked=False
assert blocked
after=hashes()
assert before==after
print(json.dumps({'status':'passed','scope':'candidate_rules_on_same_saved_bundle_no_deployment',
 'source_sha256':hashlib.sha256(source.encode()).hexdigest(),'incident_id':incident,'run_id':run,
 'bundle_hash':record.bundle.bundle_hash,'old_engine':record.report.execution.engine,'new_engine':candidate.execution.engine,
 'old_facts':len(record.report.facts),'new_facts':len(candidate.facts),
 'old_hypotheses':len(record.report.hypotheses),'new_hypotheses':len(candidate.hypotheses),
 'old_diagnosis':record.report.diagnosis_status,'new_diagnosis':candidate.diagnosis_status,
 'analysis_status':candidate.analysis_status,'current_state':candidate.current_state,
 'new_fact_evidence_ids':[f.evidence_ids for f in candidate.facts[len(record.report.facts):]],
 'new_hypothesis_evidence_ids':[h.supporting_evidence_ids for h in candidate.hypotheses],
 'old_report_sha256':hashlib.sha256(canonical_json_bytes(record.report.model_dump(mode='json'))).hexdigest(),
 'candidate_report_sha256':hashlib.sha256(canonical_json_bytes(candidate.model_dump(mode='json'))).hexdigest(),
 'unchanged_stored_file_hashes':before,'legacy_reports_checked':legacy_checked,
 'saved_report_unchanged':True,'all_citations_available':True,'external_model_blocked':blocked,
 'external_model_calls':0,'external_model_cost_usd':0,'deployment_changed':False}))
'''


def main():
    parser = argparse.ArgumentParser(description="저장된 B-06 실습 증거의 규칙 비교. 수집·저장·배포·모델 호출 없음")
    parser.add_argument("--incident", required=True)
    parser.add_argument("--run", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = cli_paths(parser, parser.parse_args(), {"output": "output"})
    if not re.fullmatch(r"inc-[a-f0-9]{32}", args.incident) or not re.fullmatch(r"run-[a-f0-9]{32}", args.run):
        parser.error("저장된 사건·실행 ID 형식이 다릅니다")
    source = (Path(__file__).resolve().parents[1] / "src/ai_ops/rules.py").read_text()
    result = subprocess.run(["kubectl", "--context", CONTEXT, "-n", NAMESPACE, "exec", "-i", "deployment/lab-collector",
        "-c", "collector", "--", "python", "-c", PROGRAM, args.incident, args.run], input=source,
        capture_output=True, text=True, timeout=90)
    if result.returncode:
        write_private_json(args.output, {"status": "failed", "reason": "saved_bundle_comparison_failed"})
        print("저장 증거 후보 비교 실패. 원문 오류는 출력하지 않았습니다.")
        return 1
    data = json.loads(result.stdout)
    if data["source_sha256"] != hashlib.sha256(source.encode()).hexdigest():
        raise ValueError("비교한 규칙 소스가 다릅니다")
    data["verified_at"] = datetime.now(timezone.utc).isoformat()
    write_private_json(args.output, data)
    print("저장된 동일 증거의 후보 v3 비교·기존 자료 불변 확인 완료. 배포는 변경하지 않았습니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
