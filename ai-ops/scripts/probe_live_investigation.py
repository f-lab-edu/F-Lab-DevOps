"""실제 수집·규칙 보고서·재조회를 검사하되 운영 본문은 PVC 밖으로 출력하지 않는다."""

from __future__ import annotations

import argparse
from collections import Counter
import contextlib
import datetime
import hashlib
import io
import json
import os
from pathlib import Path
import re
import traceback
from unittest.mock import patch

from ai_ops import cli
from ai_ops.b_contracts import load_b_settings
from ai_ops.data_policy import ModelDataDenied, ensure_external_model_allowed
from ai_ops.incidents import IncidentStore
from ai_ops.reports import format_kst


ROOT = Path('/data/incidents')
QUESTION = '등록 앱의 현재 상태와 최근 오류·배포 관련 근거 및 부족한 자료를 읽기 전용으로 확인한다.'


def private_json(path: Path, value: dict) -> None:
    """운영자 전용 파일만 쓰고 기존 실행 자료는 덮어쓰지 않는다."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as stream:
        os.fchmod(stream.fileno(), 0o600)
        json.dump(value, stream, ensure_ascii=False, indent=2)


def inspect_record(incident: str, run: str) -> dict:
    """본문·로그·질문은 내보내지 않고 검사 통과 여부와 고정 상태 코드만 반환한다."""
    store = IncidentStore(ROOT)
    record = store.show(incident, run)
    assert record.report is not None and record.bundle is not None and record.collection_audit is not None
    assert record.report.execution.engine == 'rules-captured-v1'
    assert record.report.execution.mode == 'rules'
    assert record.bundle.target.environment == 'production'
    assert all(item.provenance.kind == 'captured' for item in record.bundle.evidence)
    settings = load_b_settings(Path('/etc/ai-ops/bindings.json'), Path('/etc/ai-ops/runtime.json'), Path('/etc/ai-ops/metric-queries.json'))
    assert settings.runtime.llm_enabled is False
    try:
        ensure_external_model_allowed(record.bundle)
    except ModelDataDenied:
        model_denied = True
    else:
        model_denied = False
    assert model_denied
    run_dir = ROOT / incident / run
    names = ['input.json', 'metadata.json', 'report.json', 'report.md', 'collection-audit.json']
    hashes = {name: hashlib.sha256((run_dir / name).read_bytes()).hexdigest() for name in names}
    permission_ok = all((run_dir / name).stat().st_mode & 0o777 == 0o600 for name in names)
    permission_ok = permission_ok and ROOT.stat().st_mode & 0o777 == 0o700 and run_dir.stat().st_mode & 0o777 == 0o700
    outputs = {}
    expected_code = 0 if record.metadata['status'] == 'completed' else 1
    for fmt in ('markdown', 'json'):
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream), contextlib.redirect_stderr(io.StringIO()):
            code = cli.main(['show', '--store', str(ROOT), '--incident', incident, '--run', run, '--format', fmt])
        assert code == expected_code
        outputs[fmt] = stream.getvalue()
    report_json = json.loads(outputs['json'])
    if record.metadata['status'] == 'partial':
        assert report_json['status'] == 'partial'
        report_json = report_json['report']
    assert report_json == record.report.model_dump(mode='json')
    assert outputs['markdown'] == record.markdown
    assert format_kst(record.request.requested_at) in outputs['markdown']
    assert 'KST' in outputs['markdown']
    assert all(hashlib.sha256((run_dir / name).read_bytes()).hexdigest() == value for name, value in hashes.items())
    audit = record.collection_audit
    all_times = [record.request.requested_at, record.bundle.created_at, audit.started_at, audit.completed_at,
                 *[value for item in record.bundle.evidence for value in (item.observed_at, item.collected_at) if value is not None]]
    assert all(value.utcoffset() == datetime.timedelta(0) for value in all_times)
    if record.metadata['status'] == 'partial':
        assert record.metadata['failure_reason'] == 'collection_incomplete'
        assert record.report.analysis_status == 'partial'
    return {'incident_id': incident, 'run_id': run, 'status': record.metadata['status'],
            'failure_reason': record.metadata.get('failure_reason'), 'collection_status': audit.collection_status,
            'engine': record.report.execution.engine, 'current_state': record.report.current_state,
            'diagnosis_status': record.report.diagnosis_status, 'analysis_status': record.report.analysis_status,
            'evidence_count': len(record.bundle.evidence), 'evidence_status_counts': dict(Counter(item.status for item in record.bundle.evidence)),
            'audit_reason_counts': dict(Counter(entry.reason for entry in audit.entries)),
            'entries': [{'evidence_id': entry.evidence_id, 'status': entry.status, 'reason': entry.reason,
                         'time_field': entry.time_field, 'observed_offset': entry.observed_offset} for entry in audit.entries],
            'expected_pods': audit.workload_expected_pods, 'raw_bytes_read': audit.raw_bytes_read,
            'redaction_count': sum(item.redaction_summary.count for item in record.bundle.evidence),
            'log_event_projection_count': sum(entry.diagnostic_projection for entry in audit.entries),
            'duration_seconds': (audit.completed_at-audit.started_at).total_seconds(), 'bundle_hash': record.bundle.bundle_hash,
            'file_sha256': hashes, 'private_permissions': permission_ok, 'show_matches_stored': True,
            'utc_internal': True, 'kst_markdown': True, 'external_model_denied': model_denied,
            'ai_budget_file_absent': not (ROOT / 'ai-budget.json').exists(),
            'passed': permission_ok}


def main() -> int:
    parser = argparse.ArgumentParser(description='PVC 내 실제 수집과 규칙 보고서 재조회 검증')
    parser.add_argument('--mode', choices=('collect', 'verify'), required=True)
    parser.add_argument('--request-id', default='')
    parser.add_argument('--incident', default='')
    parser.add_argument('--run', default='')
    args = parser.parse_args()
    os.umask(0o077)
    diagnostics = {}
    original_collect = cli.collect_snapshot
    original_investigate = IncidentStore.investigate_snapshot
    def observed(name, function):
        def call(*values, **options):
            try:
                result = function(*values, **options)
                diagnostics[name] = {'returned': True}
                return result
            except Exception as error:
                cause = error.__context__
                diagnostics[name] = {'cause_type': type(cause).__name__ if cause else None,
                    'cause_fields': [{'loc': [str(v) for v in item['loc']], 'type': item['type']} for item in cause.errors(include_input=False)] if hasattr(cause, 'errors') else [],
                    'known_integrity_errors': [text for text in ('bundle hash mismatch', 'evidence payload hash mismatch', 'observation cannot follow collection', 'duplicate evidence ID') if text in str(cause)],
                    'returned': False, 'error_type': type(error).__name__,
                    'frames': [{'file': Path(frame.filename).name, 'function': frame.name, 'line': frame.lineno}
                               for frame in traceback.extract_tb(error.__traceback__) if 'ai_ops' in frame.filename],
                    'fields': [{'loc': [str(v) for v in item['loc']], 'type': item['type']}
                               for item in error.errors(include_input=False)] if hasattr(error, 'errors') else []}
                raise
        return call
    try:
        if args.mode == 'collect':
            if not re.fullmatch(r'b2-live-[0-9a-z-]{1,80}', args.request_id):
                raise ValueError('검증 요청 ID 형식 오류')
            now = datetime.datetime.now(datetime.timezone.utc)
            retention = {'request_id': args.request_id, 'created_at': now.isoformat().replace('+00:00', 'Z'),
                         'delete_after': (now+datetime.timedelta(days=7)).isoformat().replace('+00:00', 'Z'),
                         'scope': 'sanitized_input_report_collection_audit_and_request_index',
                         'owner': 'repository_operator', 'automatic_deletion_enabled': False}
            private_json(ROOT / 'retention' / (args.request_id + '.json'), retention)
            output = io.StringIO()
            # 규칙 실행이 키·모델·비용 경로에 진입하면 즉시 실패시킨다.
            with patch('ai_ops.cli.collect_snapshot', observed('collect_snapshot', original_collect)), \
                 patch.object(IncidentStore, 'investigate_snapshot', observed('investigate_snapshot', original_investigate)), \
                 patch('ai_ops.cli.api_key_from_environment', side_effect=AssertionError('key_read_forbidden')), \
                 patch('ai_ops.cli.analyze_ai', side_effect=AssertionError('model_call_forbidden')), \
                 patch.object(IncidentStore, 'load_api_key', side_effect=AssertionError('key_read_forbidden')), \
                 patch.object(IncidentStore, '_reserve_ai_budget', side_effect=AssertionError('budget_forbidden')), \
                 contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()):
                code = cli.main(['collect-investigate', '--bindings', '/etc/ai-ops/bindings.json',
                    '--runtime', '/etc/ai-ops/runtime.json', '--metric-queries', '/etc/ai-ops/metric-queries.json',
                    '--target', 'url-shortener-production', '--store', str(ROOT), '--question', QUESTION,
                    '--request-id', args.request_id, '--route', '/items', '--operation', 'select_all', '--mode', 'rules'])
            incident = re.search(r'사건 ID: (inc-[0-9a-f]{32})', output.getvalue())
            run = re.search(r'실행 ID: (run-[0-9a-f]{32})', output.getvalue())
            if code not in (0, 1) or incident is None or run is None:
                raise ValueError('수집 CLI 저장 완료 없음')
            result = inspect_record(incident.group(1), run.group(1))
            result['retention'] = retention
            result['cli_exit_code'] = code
            result['provider_key_and_budget_guards_not_called'] = True
            private_json(ROOT / 'retention' / (args.request_id + '-result.json'), result)
        else:
            result = inspect_record(args.incident, args.run)
        print(json.dumps({'record_type': 'ai_ops_live_rules_validation', 'observed_at': datetime.datetime.now(datetime.timezone.utc).isoformat().replace('+00:00', 'Z'), **result}, ensure_ascii=False), flush=True)
        return 0 if result['passed'] else 1
    except Exception as error:
        # 실제 운영 자료가 포함될 수 있는 예외 원문·본문·traceback은 내보내지 않는다.
        print(json.dumps({'record_type': 'ai_ops_live_rules_validation', 'passed': False, 'error_type': type(error).__name__, 'diagnostics': diagnostics}), flush=True)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
