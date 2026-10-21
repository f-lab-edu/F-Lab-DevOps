"""실습 S1~S4의 단일 실행을 잠그고 이전 5분 창과 복구 기록을 확인한다."""

import argparse
import fcntl
import json
from pathlib import Path
import time
import uuid

from ai_ops.b6_scenarios import LabClient, execute
from ai_ops.path_policy import cli_paths, permitted_path, write_private_json


def main():
    parser = argparse.ArgumentParser(description="고정 실습 namespace의 캐시·이미지 실패·DB 세션 지연 재현·복구")
    parser.add_argument("--scenario", choices=("S1", "S2", "S3", "S4"), required=True)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = cli_paths(parser, parser.parse_args(), {"state_dir": "store", "output": "output"})
    checkpoint = permitted_path(args.output.with_name(args.output.stem + ".ground-truth.json"), "output")
    recovery = permitted_path(args.output.with_name(args.output.stem + ".recovery-plan.json"), "output")
    args.state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    if args.state_dir.stat().st_mode & 0o077:
        parser.error("시나리오 제어 폴더는 현재 사용자 전용이어야 합니다")
    # 고정 파일의 링크는 따라가지 않는다. 실습 실행은 같은 제어 폴더에서 직렬 수행한다.
    import os
    fd = os.open(args.state_dir / ".scenario-lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            parser.error("다른 시나리오가 실행 중입니다")
        state = args.state_dir / "control.json"
        if state.is_symlink():
            parser.error("시나리오 제어 파일은 링크일 수 없습니다")
        if state.exists():
            previous = json.loads(state.read_text())
            if previous.get("active") or not previous.get("recovery_complete"):
                parser.error("이전 실행의 복구를 먼저 확인해야 합니다")
            wait = previous["not_before"] - time.time()
            if wait > 0:
                parser.error(f"이전 지표 창이 남아 있습니다. {wait:.0f}초 뒤 다시 실행하세요")
        def save(value):
            descriptor = os.open(state, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
            with os.fdopen(descriptor, "w") as stream:
                json.dump(value, stream)
                stream.flush()
                os.fsync(stream.fileno())
        run_id = uuid.uuid4().hex
        save({"active": True, "recovery_complete": False, "scenario": args.scenario, "run_id": run_id,
              "output": str(args.output), "checkpoint": str(checkpoint), "recovery_plan": str(recovery)})
        result = execute(args.scenario, LabClient(), run_id=run_id,
                         event=lambda message: print(message, flush=True),
                         checkpoint=lambda value: write_private_json(checkpoint, value),
                         recovery_plan=lambda value: write_private_json(recovery, value))
        result["previous_window_guard"] = True
        write_private_json(args.output, result)
        save({"active": False, "recovery_complete": all(result["cleanup"].values()),
              "scenario": args.scenario, "not_before": time.time() + 305, "result": str(args.output)})
        print("실습 시나리오 결과: " + result["status"])
        return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
