"""worker와 분리된 운영자 프로세스에서 완료된 실습 CI만 검증해 큐에 전달한다."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import signal
import threading

from ai_ops.b_contracts import load_b_settings
from ai_ops.ci_history import GitHubReader, LAB_RUNS_PATH, _positive
from ai_ops.contracts import _utc_datetime
from ai_ops.evidence import _json_object
from ai_ops.job_queue import JobQueue
from ai_ops.lab_ci_history import verify_lab_ci_history
from ai_ops.path_policy import cli_paths


POLL_SECONDS = 60
RECENT_SECONDS = 900


class CIRelay:
    """GitHub 인증은 이 프로세스에서만 사용하고 큐에는 검증 기록만 전달한다."""

    def __init__(self, queue, settings, *, reader=None, clock=None):
        self.queue = queue
        self.settings = settings
        self.reader = reader or GitHubReader()
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def submit_run(self, run_id: int, expected_head: str) -> dict:
        history = verify_lab_ci_history(run_id, expected_head, reader=self.reader)
        job_id, duplicate = self.queue.submit_ci(self.settings, history)
        return {"job_id": job_id, "duplicate": duplicate, "state": self.queue.show(job_id)["state"]}

    def scan_once(self) -> dict:
        """최근 완료 목록 20개만 확인한다. 과거·목록 제한은 명시하고 최신으로 바꾸지 않는다."""
        listing = _json_object(self.reader.get(LAB_RUNS_PATH))
        runs, count = listing.get("workflow_runs"), listing.get("total_count")
        if (type(count) is not int or count < 0 or not isinstance(runs, list) or len(runs) != min(count, 20)
                or any(not isinstance(run, dict) for run in runs)):
            raise ValueError("CI discovery listing rejected")
        now = _utc_datetime(self.clock())
        result = {"inspected": len(runs), "submitted": 0, "duplicates": 0, "skipped": 0,
                  "rejected": 0, "listing_limited": count > 20}
        for run in runs:
            try:
                updated = _utc_datetime(run["updated_at"])
                if not 0 <= (now - updated).total_seconds() <= RECENT_SECONDS or run.get("conclusion") != "failure":
                    result["skipped"] += 1
                    continue
                receipt = self.submit_run(_positive(run.get("id")), run.get("head_sha"))
                result["duplicates" if receipt["duplicate"] else "submitted"] += 1
            except Exception:
                # 개별 API 실패·잘못된 기록은 큐에 넣지 않고 다른 실행 검증은 계속한다.
                result["rejected"] += 1
        return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="완료된 실습 CI를 별도 gh 인증으로 검증해 비공개 worker 큐에 전달")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--store", type=Path, required=True)
    parser.add_argument("--run-id", type=int, help="과거 기록을 포함해 명시한 실행 한 건만 검증")
    parser.add_argument("--expected-head", help="명시 실행의 검토한 전체 소스 SHA")
    parser.add_argument("--watch", action="store_true", help="프로세스가 실행 중인 동안 60초 간격으로 최근 완료 기록 조회")
    args = cli_paths(parser, parser.parse_args(argv), {"config": "directory", "store": "store"})
    if (args.run_id is None) != (args.expected_head is None) or args.watch and args.run_id is not None:
        parser.error("명시 실행은 run-id와 expected-head를 함께 지정하고 watch와 분리해야 합니다")
    os.umask(0o077)
    stopped = threading.Event()
    if args.watch:
        signal.signal(signal.SIGTERM, lambda *_: stopped.set())
        signal.signal(signal.SIGINT, lambda *_: stopped.set())
    try:
        settings = load_b_settings(args.config / "bindings.json", args.config / "runtime.json", args.config / "metric-queries.json")
        token_file = os.environ.get("AI_CI_GITHUB_TOKEN_FILE")
        reader = None
        if token_file:
            from ai_ops.ci_token_reader import TokenGitHubReader
            reader = TokenGitHubReader(token_file)
        relay = CIRelay(JobQueue(args.store), settings, reader=reader)
        if args.run_id is not None:
            print(json.dumps(relay.submit_run(args.run_id, args.expected_head)), flush=True)
            return 0
        while not stopped.is_set():
            try:
                receipt = relay.scan_once()
                print(json.dumps(receipt), flush=True)
                code = 1 if receipt["rejected"] else 0
            except Exception:
                print('{"error":"ci_scan_unavailable"}', flush=True)
                code = 1
            if not args.watch:
                return code
            stopped.wait(POLL_SECONDS)
        return 0
    except Exception:
        # 인증값·API 응답·원본 관측·예외 문자열을 출력하지 않는다.
        print('{"error":"ci_relay_failed"}', flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
