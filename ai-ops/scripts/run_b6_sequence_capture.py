"""고정 GitOps 실습 대상의 조회 순서를 소유자 전용 폴더에 수집한다."""

import argparse
import os
import uuid

from ai_ops.b6_sequence_capture import SequenceLabClient, capture_sequence
from ai_ops.path_policy import PERSONAL_ROOT, permitted_path, write_private_json


def main(argv=None):
    parser = argparse.ArgumentParser(description="실습 자기 아이템 한 건의 GET·counter·같은 Pod 로그 수집; 모델 호출 없음")
    parser.add_argument("action", choices=("capture",))
    parser.parse_args(argv)
    run_id = uuid.uuid4().hex
    try:
        root = permitted_path(PERSONAL_ROOT / "normal-sequence-observations", "store")
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if root.stat().st_uid != os.getuid() or root.stat().st_mode & 0o077:
            raise ValueError("probe private root required")
        folder = permitted_path(root / ("s2-" + run_id), "store")
        folder.mkdir(mode=0o700, exist_ok=False)
        checkpoints = 0
        def save(value):
            nonlocal checkpoints
            checkpoints += 1
            write_private_json(folder / f"checkpoint-{checkpoints}.json", value)
        result = capture_sequence(SequenceLabClient(), run_id=run_id, checkpoint=save)
        write_private_json(folder / "result.json", result)
        print("조회 순서 수집: " + result["status"])
        print("비공개 결과: " + str(folder / "result.json"))
        return 0 if result["status"] == "captured" else 1
    except Exception:
        parser.exit(1, "조회 순서 수집 실패: 실습 상태·소유 Pod·공유 잠금·비공개 경로를 확인하세요. 원문 오류는 출력하지 않습니다.\n")


if __name__ == "__main__":
    raise SystemExit(main())
