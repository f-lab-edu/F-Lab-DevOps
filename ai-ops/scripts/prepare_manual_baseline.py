"""A7 v3 원본 증거만 담은 수동 분석 화면을 로컬에 생성한다."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from pathlib import Path
from ai_ops.path_policy import cli_paths


ROOT = Path(__file__).resolve().parents[1]
SUITE = ROOT / "evals/fixtures/holdout-v3/suite.json"
TEMPLATE = Path(__file__).with_name("manual_baseline_template.html")
LOCAL_SITE = ROOT / "site"


def build_packet(suite_path: Path = SUITE) -> dict:
    """고정 원본 파일의 해시를 확인하고 정답·AI 출력 없이 사건을 묶는다."""
    suite = json.loads(suite_path.read_text(encoding="utf-8"))
    if suite.get("suite_id") != "a7-holdout-v3" or len(suite.get("cases", [])) != 16:
        raise ValueError("A7 v3 입력 16건이 필요합니다")
    cases = []
    for row in suite["cases"]:
        case_id = row["case_id"]
        if row["bundle"] != case_id or not case_id.startswith("case-"):
            raise ValueError("사건 경로가 일치하지 않습니다")
        bundle_dir = suite_path.parent / case_id
        manifest_bytes = (bundle_dir / "manifest.json").read_bytes()
        if hashlib.sha256(manifest_bytes).hexdigest() != row["manifest_sha256"]:
            raise ValueError(f"{case_id}: manifest 해시가 다릅니다")
        manifest = json.loads(manifest_bytes)
        if manifest["target"]["environment"] != "practice":
            raise ValueError("합성 연습 대상만 허용합니다")
        evidence = []
        for item in manifest["entries"]:
            ref = item["source_ref"]
            if ref.startswith("/") or any(part in ("", ".", "..") for part in ref.split("/")):
                raise ValueError(f"{case_id}: 안전하지 않은 증거 경로")
            raw = None
            if item["status"] == "available":
                raw_path = bundle_dir.joinpath(*ref.split("/"))
                if raw_path.is_symlink() or not raw_path.resolve().is_relative_to(bundle_dir.resolve()):
                    raise ValueError(f"{case_id}: 증거 파일 경로가 잘못됐습니다")
                raw_bytes = raw_path.read_bytes()
                if hashlib.sha256(raw_bytes).hexdigest() != item["raw_sha256"]:
                    raise ValueError(f"{case_id}: {ref} 해시가 다릅니다")
                raw = raw_bytes.decode("utf-8")
            evidence.append({
                "evidence_id": item["evidence_id"],
                "kind": item["kind"],
                "source_ref": ref,
                "observed_at": item["observed_at"],
                "collected_at": item["collected_at"],
                "status": item["status"],
                "missing_reason": item["missing_reason"],
                "raw": raw,
            })
        cases.append({
            "case_id": case_id,
            "target": manifest["target"],
            "window": manifest["window"],
            "evidence": evidence,
        })
    if len({case["case_id"] for case in cases}) != 16:
        raise ValueError("사건 ID가 중복됐습니다")
    return {"schema_version": 1, "suite_hash": suite["suite_hash"], "cases": cases}


def render(packet: dict, template: str) -> str:
    """증거 내용이 HTML 스크립트 경계를 벗어나지 않게 삽입한다."""
    marker = "__MANUAL_BASELINE_DATA__"
    if template.count(marker) != 1:
        raise ValueError("화면의 자료 삽입 위치가 잘못됐습니다")
    data = json.dumps(packet, ensure_ascii=False, separators=(",", ":"))
    data = data.replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")
    return template.replace(marker, data)


def main() -> None:
    parser = argparse.ArgumentParser(description="A7 v3 사람 분석용 원본 증거 화면 생성")
    parser.add_argument("--output", type=Path, default=LOCAL_SITE / "a7-manual-baseline.html")
    args = cli_paths(parser, parser.parse_args(), {'output': 'html'})
    output = args.output.resolve()
    if output.parent != LOCAL_SITE.resolve() or output.suffix != ".html":
        parser.error("출력은 ai-ops/site의 HTML 파일이어야 합니다")
    packet = build_packet()
    html = render(packet, TEMPLATE.read_text(encoding="utf-8"))
    fd, temporary = tempfile.mkstemp(prefix=".manual-baseline-", suffix=".html", dir=LOCAL_SITE)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(html)
        os.chmod(temporary, 0o600)
        os.replace(temporary, output)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    print(f"A7 원본 증거 화면 16건: {output}")


if __name__ == "__main__":
    main()
