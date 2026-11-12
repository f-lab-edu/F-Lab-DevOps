"""사용자 안내의 호스트 도구 응답을 받아 현재 PR의 영수증을 게시한다.

PR 생성·stdout 출력·사람 리뷰는 안내 전달 확인을 대신하지 않는다.
호스트는 실제 안내 도구의 성공 응답 이후에만 관측한 성공 응답 또는 식별자를 반환한다.
"""

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import socket
import stat
import tempfile
import uuid
from zoneinfo import ZoneInfo

from ai_ops.contracts import _utc_datetime, sha256_json

LIMIT = 16384


def _moment():
    return datetime.now(timezone.utc)


def _socket_path(path, *, existing):
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts or len(os.fsencode(path)) > 90:
        raise ValueError('review_notice_socket_path_rejected')
    if path.is_symlink() or path.parent.is_symlink():
        raise ValueError('review_notice_socket_link_rejected')
    parent = path.parent.stat()
    if parent.st_uid != os.geteuid() or parent.st_mode & 0o077:
        raise ValueError('review_notice_private_directory_required')
    if existing:
        value = path.lstat()
        if not stat.S_ISSOCK(value.st_mode) or value.st_uid != os.geteuid() or value.st_mode & 0o077:
            raise ValueError('review_notice_private_socket_required')
    elif path.exists():
        raise ValueError('review_notice_socket_already_exists')
    return path


def _read(stream):
    raw = stream.readline(LIMIT + 1)
    if len(raw) > LIMIT or not raw.endswith(b'\n'):
        raise ValueError('review_notice_message_rejected')
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError('review_notice_object_required')
    return value


def _write(stream, value):
    raw = json.dumps(value, ensure_ascii=False).encode() + b'\n'
    if len(raw) > LIMIT:
        raise ValueError('review_notice_message_rejected')
    stream.write(raw)
    stream.flush()


def _exchange(path, value, timeout):
    path = _socket_path(path, existing=True)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(timeout)
        client.connect(str(path))
        with client.makefile('rwb') as stream:
            _write(stream, value)
            return _read(stream)


def _request(plan, pull_number, head, nonce):
    if (plan.schema_version != 2 or type(pull_number) is not int or pull_number < 1
            or not isinstance(head, str) or not re.fullmatch('[a-f0-9]{40}', head)
            or not isinstance(nonce, str) or not re.fullmatch('[a-f0-9]{32}', nonce)):
        raise ValueError('review_notice_binding_rejected')
    return {'action': 'notify', 'trial_id': plan.trial_id, 'plan_hash': plan.digest(),
            'pull_number': pull_number, 'head_sha': head, 'nonce': nonce,
            'url': f'https://github.com/joungGo/c03-execution-lab/pull/{pull_number}/files',
            'review_deadline': plan.review_cutoff().isoformat()}


def notification_question(request):
    """실제 request_user_input_async로 전달할 질문이다. 사람 승인을 합성하지 않는다."""
    deadline = _utc_datetime(request['review_deadline']).astimezone(ZoneInfo('Asia/Seoul'))
    return {'questions': [{'title': (
        f"[복구 PR #{request['pull_number']}]({request['url']})의 정확한 head "
        f"`{request['head_sha']}`를 **{deadline:%Y-%m-%d %H:%M:%S KST}까지** "
        'GitHub에서 Review changes → Approve → Submit review로 승인해 주세요. '
        '실제 리뷰 기록과 필수 CI를 별도로 확인한 뒤 병합·동기화합니다.'
    )}]}


def rehearsal_question(request):
    """실제 리뷰 요청과 구분한 호스트 전달 리허설 질문이다."""
    return {'questions': [{'title': (
        'C-04 안내 전달 리허설입니다. 이 메시지가 보이나요? '
        '장애 주입이나 PR 승인·병합 요청은 아닙니다. '
        f"실습 계획 {request['trial_id']}의 알림 전송 응답만 확인합니다."
    ), 'options': ['수신 확인']}]}


def require_bridge(plan, path):
    response = _exchange(path, {'action': 'ping', 'trial_id': plan.trial_id,
                               'plan_hash': plan.digest()}, 2)
    if response != {'ready': True, 'trial_id': plan.trial_id, 'plan_hash': plan.digest()}:
        raise ValueError('review_notice_bridge_not_ready')


def delivery_evidence(value):
    """호스트가 관측한 성공 응답을 보존한다. 제공되지 않은 ID를 만들지 않는다."""
    if set(value) == {'delivery_id'}:
        identifier = value['delivery_id']
        if isinstance(identifier, str) and re.fullmatch(r'[A-Za-z0-9_.:-]{1,160}', identifier):
            return dict(value)
    if set(value) == {'delivery_tool_response'}:
        response = value['delivery_tool_response']
        if isinstance(response, dict) and set(response) == {'accepted'} and response['accepted'] is True:
            return {'delivery_tool_response': {'accepted': True}}
    raise ValueError('review_notice_host_response_rejected')


def validate_delivery(plan, notice, *, pull_number, head):
    request = notice['delivery_request']
    expected = _request(plan, pull_number, head, request['nonce'])
    evidence = {key: notice[key] for key in ('delivery_id', 'delivery_tool_response') if key in notice}
    delivery_evidence(evidence)
    if (request != expected or notice['delivery_request_hash'] != sha256_json(request)
            or notice['delivery_transport'] != 'codex-host-bridge'):
        raise ValueError('review_notice_delivery_rejected')


def deliver_review_notice(plan, ready, path, destination, *, clock=_moment):
    """완료 응답을 받은 현재 시각으로만 기록한다. 실패·지연·기존 파일은 거부한다."""
    destination = Path(destination)
    if destination.exists() or destination.is_symlink():
        raise ValueError('review_notice_receipt_already_exists')
    started = clock()
    latest = plan.review_cutoff() - timedelta(seconds=plan.minimum_review_seconds)
    if not plan.planned_at <= started < latest:
        raise ValueError('insufficient_actual_review_notice_window')
    if ready['trial_id'] != plan.trial_id or ready.get('review_deadline_utc') != plan.review_cutoff().isoformat():
        raise ValueError('review_notice_binding_rejected')
    request = _request(plan, ready['pull_number'], ready['head_sha'], uuid.uuid4().hex)
    if ready['url'] not in (request['url'], request['url'].removesuffix('/files')):
        raise ValueError('review_notice_url_rejected')
    response = _exchange(path, request, (latest-started).total_seconds())
    completed = clock()
    evidence = {key: response[key] for key in ('delivery_id', 'delivery_tool_response') if key in response}
    evidence = delivery_evidence(evidence)
    if (set(response) != {'request_hash', 'acknowledged_at', *evidence}
            or response['request_hash'] != sha256_json(request)
            or not started <= _utc_datetime(response['acknowledged_at']) <= completed):
        raise ValueError('review_notice_delivery_rejected')
    notice = {'trial_id': plan.trial_id, 'plan_hash': plan.digest(),
              'pull_number': ready['pull_number'], 'head_sha': ready['head_sha'],
              'notified_at': completed.isoformat(), 'review_deadline': plan.review_cutoff().isoformat(),
              'delivery_transport': 'codex-host-bridge', 'delivery_request': request,
              'delivery_request_hash': response['request_hash'], **evidence}
    from ai_ops.c_trial_review_window import require_review_notice
    require_review_notice(plan, notice, pull_number=ready['pull_number'], head=ready['head_sha'], now=completed)
    # 같은 파일 시스템에서 완성 파일만 게시하고 동시 작성/기존 영수증을 덮지 않는다.
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', dir=destination.parent, prefix='.review-notice-', delete=False) as f:
            temporary = Path(f.name)
            os.fchmod(f.fileno(), 0o600)
            json.dump(notice, f, ensure_ascii=False)
            f.write('\n'); f.flush(); os.fsync(f.fileno())
        published = clock()
        if not completed <= published <= latest:
            raise ValueError('insufficient_actual_review_notice_window')
        os.link(temporary, destination)
    finally:
        if temporary is not None:
            temporary.unlink()
    return notice


def serve_bridge(plan, path, notify_host, *, clock=_moment, rehearsal=False,
                 resolve_plan=None, standby_until=None):
    """한 실행 전용이며 실제 호스트의 응답을 받기 전에는 전달 성공을 반환하지 않는다."""
    path = _socket_path(path, existing=False)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
        server.bind(str(path)); os.chmod(path, 0o600)
        identity = path.lstat().st_ino
        try:
            server.listen(2); server.settimeout(.5)
            while True:
                if resolve_plan is not None:
                    current = resolve_plan()
                    if current is not None:
                        plan = current
                    elif standby_until is None or clock() >= standby_until:
                        return
                if plan is not None and clock() >= plan.review_cutoff() - timedelta(seconds=plan.minimum_review_seconds):
                    return
                try:
                    client, _ = server.accept()
                except socket.timeout:
                    continue
                with client, client.makefile('rwb') as stream:
                    client.settimeout(2)
                    request = _read(stream)
                    # accept 대기 중 activation이 게시됐을 수 있다. 첫 ping도 새 바인딩을 확인한다.
                    if resolve_plan is not None:
                        current = resolve_plan()
                        if current is not None: plan = current
                    if plan is None:
                        _write(stream, {'ready': False})
                        continue
                    if request == {'action': 'ping', 'trial_id': plan.trial_id, 'plan_hash': plan.digest()}:
                        response = {'ready': True, 'trial_id': plan.trial_id, 'plan_hash': plan.digest()}
                        if rehearsal:
                            response['rehearsal'] = True
                        _write(stream, response)
                        continue
                    expected = _request(plan, request['pull_number'], request['head_sha'], request['nonce'])
                    if rehearsal:
                        expected['action'] = 'rehearse'
                    if request != expected:
                        raise ValueError('review_notice_binding_rejected')
                    question = rehearsal_question(request) if rehearsal else notification_question(request)
                    acknowledged = delivery_evidence(notify_host(question))
                    response = {'request_hash': sha256_json(request),
                                'acknowledged_at': clock().isoformat(), **acknowledged}
                    if rehearsal:
                        # 실제 실행기의 3개 키 응답·notify 요청 계약과 호환되지 않는다.
                        response['rehearsal'] = True
                    _write(stream, response)
                    return
        finally:
            if path.exists() and path.lstat().st_ino == identity:
                path.unlink()
