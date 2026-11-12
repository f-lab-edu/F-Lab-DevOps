"""실제 읽기 증거를 갱신하고 마감 전 준비한 같은 프로세스만 자동 기동한다."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
import os
import shutil
from pathlib import Path
import stat
import threading
import time

from ai_ops.contracts import _utc_datetime


class RefreshingProbe:
    """조회 시작 시각을 보존한다. 오류·정지·시계 변경은 과거 성공으로 덮지 않는다."""

    def __init__(self, plan, read, *, clock=None, monotonic=time.monotonic,
                 interval=2, max_age=10, publish=None):
        self.plan, self.read = plan, read
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.monotonic, self.interval, self.max_age = monotonic, interval, max_age
        self.publish = publish
        self.lock, self.stop = threading.Lock(), threading.Event()
        self.value, self.started_mono, self.thread = None, None, None
        self.error_type = None

    def refresh(self):
        started, tick = self.clock(), self.monotonic()
        try:
            value = self.read(self.plan)
            elapsed = self.monotonic()-tick
            wall = (self.clock()-started).total_seconds()
            if not 0 <= elapsed <= self.max_age or abs(wall-elapsed) > 1:
                raise ValueError("조회 지연 또는 시계 변경")
            value = {**value, "plan_hash": self.plan.digest(), "checked_at": started.isoformat()}
            if self.publish:
                self.publish(value)
            with self.lock:
                self.value, self.started_mono, self.error_type = value, tick, None
        except Exception as exc:
            # 실패 직후 성공 캐시를 폐기한다. 원본 예외 본문/자격은 보존하지 않는다.
            with self.lock:
                self.value, self.started_mono, self.error_type = None, None, type(exc).__name__

    def require(self, plan):
        with self.lock:
            value, tick = deepcopy(self.value), self.started_mono
        if value is None or tick is None or value.get("plan_hash") != plan.digest():
            raise ValueError("현재 실행의 실제 읽기 증거가 없습니다")
        elapsed = self.monotonic()-tick
        wall = (self.clock()-_utc_datetime(value["checked_at"])).total_seconds()
        if not 0 <= elapsed <= self.max_age or not 0 <= wall <= self.max_age or abs(wall-elapsed) > 1:
            raise ValueError("실제 읽기 증거가 만료됐거나 시계가 변경됐습니다")
        return True

    def _loop(self):
        while not self.stop.is_set():
            self.refresh()
            self.stop.wait(self.interval)

    def start(self):
        if self.thread is not None:
            raise ValueError("조회 갱신은 한 번만 기동합니다")
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()
        return self

    def close(self):
        self.stop.set()
        if self.thread is not None:
            self.thread.join(timeout=20)
            if self.thread.is_alive():
                raise ValueError("읽기 갱신 프로세스 종료를 확인하지 못했습니다")


def schedule_trial(executor, authorization, *, sleep=time.sleep, monotonic=time.monotonic,
                   on_prepared=None):
    """마감까지 준비하고 30초 전 자동 진입한다. 이 함수에는 외부 쓰기가 없다."""
    plan = executor.plan
    executor._authorize(authorization)
    # 최초 시계 기준도 고정해 suspend/시각 점프에 과거 준비를 재사용하지 않는다.
    start, tick = executor._moment(), monotonic()
    while executor.prepared_at is None:
        now = executor._moment()
        if now >= plan.approval_cutoff:
            raise ValueError("준비 마감 전에 실제 조건을 확인하지 못했습니다")
        try:
            executor.prepare(authorization)
        except (ValueError, OSError):
            if executor._moment() >= plan.approval_cutoff:
                raise ValueError("준비 검사 중 마감을 넘었습니다") from None
            sleep(min(1, (plan.approval_cutoff-executor._moment()).total_seconds()))
    if on_prepared:
        on_prepared(executor.prepared_at)
    launch_at = plan.planned_at-timedelta(seconds=30)
    while True:
        now = executor._moment()
        if abs((now-start).total_seconds()-(monotonic()-tick)) > 1:
            raise ValueError("자동 기동 대기 중 시계 변경 또는 절전이 감지됐습니다")
        executor.transport.require_guards(plan)
        executor.transport.require_images(plan)
        remaining = (launch_at-now).total_seconds()
        if remaining <= 0:
            if (now-launch_at).total_seconds() > 1:
                raise ValueError("자동 기동 시각을 놓쳤습니다")
            return
        sleep(min(.25, remaining))


def verify_guard_sources(root, expected_hashes):
    """원격 main의 네 파일을 로컬 실제 감시와 대조한다. 별도 main 묶음을 사용한다."""
    root = Path(root).resolve(strict=True)
    needed = {".github/workflows/ai-ops-c2-rollback.yml", "ai-ops/scripts/run_c2_rollback_guard.py",
              "ai-ops/src/ai_ops/c2_rollback_guard.py", "ai-ops/src/ai_ops/b6_coordination.py"}
    if set(expected_hashes) != needed:
        raise ValueError("원복 소스 계약이 다릅니다")
    for name, expected in expected_hashes.items():
        path = root/name
        if (path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root)
                or hashlib.sha256(path.read_bytes()).hexdigest() != expected):
            raise ValueError("로컬/원격 원복 감시 소스가 다릅니다")
    spec = importlib.util.spec_from_file_location("c04_checked_guard", root/"ai-ops/src/ai_ops/c2_rollback_guard.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.validate_plan


def validate_operator_socket(root):
    """macOS sun_path보다 여유 있는 전용 소켓 경로를 준비 전에 검사한다."""
    root = Path(root)
    if not root.is_absolute() or root.is_symlink():
        raise ValueError("전용 소켓 절대 경로가 필요합니다")
    info = root.stat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077:
        raise ValueError("소켓 폴더는 사용자 전용이어야 합니다")
    path = root/"operator.sock"
    if len(os.fsencode(path)) > 100:
        raise ValueError("운영자 소켓 경로가 너무 깁니다")
    return path


def prepare_operator_kubeconfig(config, *, context, aws_path=None):
    """기존 aws와 같은 파일인 절대 경로만 정규화한다. 다른 자격/실행기는 거절한다."""
    if (config.get('current-context') != context
            or any(len(config.get(key, [])) != 1 for key in ('users', 'contexts', 'clusters'))):
        raise ValueError('operator_kubeconfig_context_rejected')
    user = config['users'][0]['user']
    if set(user) != {'exec'}:
        raise ValueError('operator_kubeconfig_identity_rejected')
    trusted = aws_path or shutil.which('aws')
    command = user['exec'].get('command')
    if not trusted or not isinstance(command, str):
        raise ValueError('operator_kubeconfig_aws_command_rejected')
    trusted = Path(trusted).resolve(strict=True)
    actual = trusted if command == 'aws' else Path(command)
    if (not actual.is_absolute() or not actual.is_file()
            or actual.resolve(strict=True) != trusted or not os.access(trusted, os.X_OK)):
        raise ValueError('operator_kubeconfig_aws_command_rejected')
    prepared = deepcopy(config)
    prepared['users'][0]['user']['exec']['command'] = 'aws'
    prepared['contexts'][0]['context']['namespace'] = 'argocd'
    return prepared
