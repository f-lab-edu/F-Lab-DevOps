"""인증된 경보를 정확한 등록 대상·시작 시각에 묶어 영속 접수한다."""

from __future__ import annotations

from datetime import datetime, timezone
import re

from ai_ops.b_contracts import BSettings
from ai_ops.contracts import Target, sha256_json
from ai_ops.evidence import _json_object
from ai_ops.job_queue import JobQueue, QueueFull
from ai_ops.job_queue import utc

MAX_BODY = 65536
MAX_ALERTS = 32
MAX_ALERT_EPISODES = 10000
ALERT_NAMES = frozenset(('HighErrorRate', 'CacheHitRateLow', 'CacheOperationErrorHigh',
                         'DBQueryLatencyHigh', 'APIRequestLatencyHigh'))
TARGET_LABELS = ('cluster_id', 'namespace', 'workload', 'service', 'environment')


class RejectedAlert(ValueError):
    """전송 형식은 맞지만 조사 허용 범위 밖인 경보다."""


def alert_time(value: str) -> tuple[str, float]:
    """RFC3339의 최대 나노초 정밀도를 중복 키에 보존하고 UTC로 정규화한다."""
    if not isinstance(value, str):
        raise ValueError('경보 시각 형식이 유효하지 않습니다')
    match = re.fullmatch(r'(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d{1,9}))?(Z|[+-]\d{2}:\d{2})', value)
    if match is None:
        raise ValueError('경보 시각 형식이 유효하지 않습니다')
    offset = match[3]
    if offset != 'Z' and (int(offset[1:3]) > 23 or int(offset[4:6]) > 59):
        raise ValueError('경보 시각 오프셋이 유효하지 않습니다')
    moment = datetime.fromisoformat(match[1] + ('+00:00' if offset == 'Z' else offset)).astimezone(timezone.utc)
    fraction = (match[2] or '').ljust(9, '0')
    return moment.strftime('%Y-%m-%dT%H:%M:%S') + '.' + fraction + 'Z', moment.timestamp() + int(fraction) / 1_000_000_000


def _labels(value) -> dict:
    if not isinstance(value, dict) or not 1 <= len(value) <= 64:
        raise ValueError('경보 label 구조가 유효하지 않습니다')
    for key, item in value.items():
        if (not isinstance(key, str) or not re.fullmatch(r'[a-zA-Z_][a-zA-Z0-9_]{0,127}', key)
                or not isinstance(item, str) or len(item) > 1024
                or any(ord(char) < 32 or ord(char) == 127 for char in item)):
            raise ValueError('경보 label 값이 유효하지 않습니다')
    return value


class AlertInbox:
    """원문 없이 대상·시각·해시·결과 참조만 남기며 전송 묶음을 원자 접수한다."""

    def __init__(self, queue: JobQueue, settings: BSettings, *, cluster_id: str):
        if cluster_id not in {binding.target.cluster_id for binding in settings.bindings.bindings}:
            raise ValueError('발신 클러스터가 등록되지 않았습니다')
        self.queue = queue
        self.settings = settings
        self.cluster_id = cluster_id
        with queue.transaction() as db:
            db.execute("CREATE TABLE IF NOT EXISTS alert_episodes (episode_key TEXT PRIMARY KEY, identity_hash TEXT NOT NULL, target_hash TEXT NOT NULL, alertname TEXT NOT NULL, starts_at TEXT NOT NULL, ends_at TEXT, status TEXT NOT NULL CHECK(status IN ('firing','resolved')), job_id TEXT, received_at REAL NOT NULL, updated_at REAL NOT NULL)")

    def prepare(self, raw: bytes) -> list[dict]:
        if not raw or len(raw) > MAX_BODY:
            raise ValueError('경보 본문 크기가 유효하지 않습니다')
        doc = _json_object(raw)
        if (doc.get('version') != '4' or doc.get('receiver') != 'ai-investigation'
                or doc.get('status') not in ('firing', 'resolved')
                or type(doc.get('truncatedAlerts', 0)) is not int or doc.get('truncatedAlerts', 0) != 0
                or not isinstance(doc.get('alerts'), list) or not 1 <= len(doc['alerts']) <= MAX_ALERTS):
            raise ValueError('경보 전송 형식이 유효하지 않습니다')
        now = self.queue.now()
        prepared = []
        for item in doc['alerts']:
            try:
                prepared.append(self._prepare_alert(item, now))
            except RejectedAlert:
                prepared.append({'status':'rejected'})
        return prepared

    def _prepare_alert(self, item, now: float) -> dict:
        if not isinstance(item, dict) or item.get('status') not in ('firing', 'resolved'):
            raise ValueError('개별 경보 상태가 유효하지 않습니다')
        labels = _labels(item.get('labels'))
        if labels.get('ai_ops') != 'enabled' or labels.get('alertname') not in ALERT_NAMES:
            raise RejectedAlert('조사 허용 경보가 아닙니다')
        try:
            target = Target.model_validate({key: labels.get(key) for key in TARGET_LABELS})
            binding = self.settings.bindings.resolve(target)
        except ValueError:
            raise RejectedAlert('등록되지 않은 조사 대상입니다') from None
        if target.cluster_id != self.cluster_id:
            raise RejectedAlert('인증 발신자의 클러스터와 다릅니다')
        route, operation = labels.get('route'), labels.get('operation')
        if route is not None and route not in binding.routes or operation is not None and operation not in binding.operations:
            raise RejectedAlert('등록하지 않은 조사 차원입니다')
        # 수집용 URL·selector·PromQL과 질문은 경보 본문에서 받지 않는다.
        self.settings.metrics.render('api_latency', binding, dimension=route)
        self.settings.metrics.render('db_latency', binding, dimension=operation)
        fingerprint = item.get('fingerprint')
        if not isinstance(fingerprint, str) or not re.fullmatch(r'[0-9a-f]{16}', fingerprint):
            raise ValueError('경보 fingerprint가 유효하지 않습니다')
        starts, starts_number = alert_time(item.get('startsAt'))
        if starts_number < 0 or starts_number > now + 300:
            raise ValueError('경보 시작 시각이 유효하지 않습니다')
        ends = None
        if item['status'] == 'resolved':
            ends, ends_number = alert_time(item.get('endsAt'))
            if ends < starts or ends_number > now + 300:
                raise ValueError('경보 종료 시각이 유효하지 않습니다')
        elif 'endsAt' in item:
            # 발생 중 endsAt은 Alertmanager의 만료 예정 시각일 수 있다.
            alert_time(item['endsAt'])
        episode = sha256_json({'source': 'alertmanager', 'cluster_id': self.cluster_id,
                               'fingerprint': fingerprint, 'starts_at': starts})
        payload = {'target': binding.alias,
                   'question': f"{labels['alertname']} 경보와 관련된 현재 관측 사실·원인 후보·부족한 자료를 읽기 전용으로 확인한다.",
                   'config_hash': self.settings.config_hash, 'route': route, 'operation': operation, 'mode': 'rules'}
        return {'key': episode, 'identity': self.queue.engine.request_fingerprint({'labels': labels}),
            'target_hash': sha256_json(target.model_dump(mode='json')), 'name': labels['alertname'],
            'starts': starts, 'ends': ends, 'status': item['status'], 'payload': payload}

    def accept(self, raw: bytes) -> dict:
        items = self.prepare(raw)
        result = {'accepted': 0, 'duplicate': 0, 'resolved': 0, 'suppressed': 0, 'rejected': 0}
        now = self.queue.now()
        # 같은 전송 안에서는 해제를 먼저 반영해 순서가 뒤집힌 재전송도 새 조사를 막는다.
        items.sort(key=lambda item: item['status'] != 'resolved')
        with self.queue.transaction() as db:
            for item in items:
                if item['status'] == 'rejected':
                    result['rejected'] += 1
                    continue
                row = db.execute('SELECT * FROM alert_episodes WHERE episode_key=?', (item['key'],)).fetchone()
                if row and (row['identity_hash'] != item['identity'] or row['target_hash'] != item['target_hash']):
                    raise ValueError('같은 경보 키에 다른 식별 정보가 있습니다')
                if row is None:
                    if db.execute('SELECT count(*) FROM alert_episodes').fetchone()[0] >= MAX_ALERT_EPISODES:
                        raise QueueFull('경보 중복 장부가 가득 찼습니다')
                    job = None
                    if item['status'] == 'firing':
                        fingerprint = self.queue.engine.request_fingerprint({'payload': item['payload'], 'source': 'alertmanager'})
                        job, _ = self.queue._submit(db, payload=item['payload'], fingerprint=fingerprint,
                                                  episode_key=item['key'], now=now)
                    db.execute('INSERT INTO alert_episodes VALUES (?,?,?,?,?,?,?,?,?,?)',
                        (item['key'], item['identity'], item['target_hash'], item['name'], item['starts'], item['ends'],
                         item['status'], job, now, now))
                    result['accepted' if job else 'resolved'] += 1
                elif item['status'] == 'resolved':
                    # 해제는 조사/앱 복구 성공 판정이나 재분석 요청이 아니다.
                    ends = max(filter(None, (row['ends_at'], item['ends'])))
                    db.execute("UPDATE alert_episodes SET status='resolved',ends_at=?,updated_at=? WHERE episode_key=?",
                               (ends, now, item['key']))
                    result['resolved'] += 1
                elif row['status'] == 'resolved':
                    result['suppressed'] += 1
                else:
                    # 연결 보고서가 보관 만료돼도 동일 startsAt의 경보를 재조사하지 않는다.
                    result['duplicate'] += 1
        return result


def show_alert(queue: JobQueue, job_id: str) -> dict:
    """운영자 CLI에 경보 시각·접수·시도와 결과 참조만 제공한다."""
    job = queue.show(job_id)
    with queue.transaction() as db:
        exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='alert_episodes'").fetchone()
        row = db.execute('SELECT alertname,starts_at,ends_at,status,received_at,updated_at FROM alert_episodes WHERE job_id=?', (job_id,)).fetchone() if exists else None
        if row is None:
            raise ValueError('경보와 연결된 조사 요청이 아닙니다')
        attempts = db.execute('SELECT generation,started_at,finished_at,state,reason FROM attempts WHERE job_id=? ORDER BY generation',(job_id,)).fetchall()
    return {'job':job,'alert':{'alertname':row['alertname'],'starts_at':row['starts_at'],'ends_at':row['ends_at'],
        'status':row['status'],'received_at':utc(row['received_at']),'updated_at':utc(row['updated_at'])},
        'attempts':[{'generation':item['generation'],'started_at':utc(item['started_at']),
                     'finished_at':utc(item['finished_at']) if item['finished_at'] is not None else None,
                     'state':item['state'],'reason':item['reason']} for item in attempts]}
