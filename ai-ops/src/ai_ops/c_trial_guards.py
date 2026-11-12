"""실제 준비 로그의 최초 관찰과 현재 감시 프로세스·원격 실행을 읽기 검증한다."""

from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import stat
import subprocess

from ai_ops.contracts import _utc_datetime
from ai_ops.c_trial_schedule import verify_guard_sources
from ai_ops.path_policy import permitted_path


def private_json(path):
    path = permitted_path(path, "input")
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) & 0o077 or info.st_nlink != 1 or info.st_size > 65536):
        raise ValueError("현재 사용자 전용 실행 입력이 필요합니다")
    return json.loads(path.read_bytes())


def _github(path):
    result = subprocess.run(["gh", "api", path], capture_output=True, check=True, timeout=5)
    if len(result.stdout) > 2*1024*1024:
        raise ValueError("감시 원본 응답이 너무 큽니다")
    return json.loads(result.stdout)


class GuardReader:
    def __init__(self, anchor_path, *, github=_github):
        self.anchor_path, self.github = anchor_path, github

    def __call__(self, plan):
        anchor = private_json(self.anchor_path)
        if (anchor.get("plan_hash") != plan.digest()
                or anchor.get("actual_ready_markers_verified") is not True
                or anchor.get("workflow_main") != plan.workflow_main
                or _utc_datetime(anchor["rollback_deadline"]) != plan.rollback_deadline
                or _utc_datetime(anchor["ready_at"]) >= plan.approval_cutoff):
            raise ValueError("실제 양쪽 준비가 계획의 마감 전에 확인되지 않았습니다")
        root = permitted_path(anchor["guard_source_root"], "directory")
        validate = verify_guard_sources(root, anchor["guard_code_sha256"])
        local_plan = private_json(Path(anchor["local_plan"]))
        expected = {"schema_version": 2, "target": "urlshortener/ai-ops-lab/lab-api/practice",
            "source_repository": plan.repository, "application_uid": plan.application_uid,
            "deployment_uid": plan.deployment_uid, "baseline_revision": plan.normal_revision,
            "failure_revision": plan.failure_revision, "normal_image": plan.normal_image,
            "failure_image": plan.failure_image, "deadline_utc": local_plan.get("deadline_utc")}
        if local_plan != expected or _utc_datetime(local_plan["deadline_utc"]) != plan.rollback_deadline:
            raise ValueError("로컬 감시 계획이 다릅니다")
        # 실제 schema2 validator도 호출하되 준비 마감과 기한을 가진 계획 시각에 대조한다.
        validate(local_plan, now=plan.planned_at)
        pid = anchor["local_pid"]
        if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
            raise ValueError("로컬 감시 PID가 유효하지 않습니다")
        os.kill(pid, 0)
        process = subprocess.run(["ps", "-p", str(pid), "-o", "command="],
            capture_output=True, check=True, timeout=2).stdout.decode()
        if (str(root/"ai-ops/scripts/run_c2_rollback_guard.py") not in process
                or str(anchor["local_plan"]) not in process):
            raise ValueError("현재 로컬 감시 실행 경로가 다릅니다")
        log = Path(anchor["local_ready_log"])
        # 이 로그는 비밀값을 쓰지 않는 고정 감시의 사용자 전용 로컬 파일이다.
        if not log.resolve().is_relative_to(Path(anchor["local_plan"]).parent.resolve()):
            raise ValueError("감시 로그가 실행 폴더 밖에 있습니다")
        info = log.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) & 0o077 or info.st_size > 65536
                or "C-02 별도 원복 감시 준비 완료" not in log.read_text()):
            raise ValueError("실제 로컬 준비 로그가 없습니다")
        run_id = anchor["remote_run_id"]
        if not isinstance(run_id, int) or isinstance(run_id, bool) or run_id <= 0:
            raise ValueError("원격 감시 run ID가 유효하지 않습니다")
        prefix = "repos/f-lab-edu/F-Lab-DevOps/actions/runs/"+str(run_id)
        with ThreadPoolExecutor(max_workers=2) as pool:
            run_future = pool.submit(self.github, prefix)
            jobs_future = pool.submit(self.github, prefix+"/jobs?per_page=100")
            remote, jobs = run_future.result(), jobs_future.result()
        if (remote.get("id") != run_id or remote.get("status") != "in_progress"
                or remote.get("event") != "workflow_dispatch" or remote.get("workflow_id") != 401385940
                or remote.get("head_branch") != "main" or remote.get("head_sha") != plan.workflow_main
                or jobs.get("total_count") != len(jobs.get("jobs", []))
                or not any(job.get("status") == "in_progress" and any(
                    step.get("name") == "별도 기한 원복과 정상 상태 검증" and step.get("status") == "in_progress"
                    for step in job.get("steps", [])) for job in jobs["jobs"])):
            raise ValueError("현재 원격 감시가 실행 준비 상태가 아닙니다")
        return {"remote_run_id": run_id, "local_pid": pid, "ready_at": anchor["ready_at"],
                "workflow_main": plan.workflow_main, "actual_ready_markers_verified": True}
