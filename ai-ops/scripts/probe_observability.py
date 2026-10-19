"""실제 Pod에서 메트릭·Prometheus·기존 보고서 보존을 본문 반출 없이 확인한다."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
from urllib.parse import urlencode
from urllib.request import urlopen


PROMETHEUS = "http://kube-prometheus-stack-prometheus.monitoring.svc:9090"


def read_bounded(url: str, limit: int) -> bytes:
    with urlopen(url, timeout=5) as response:
        data = response.read(limit + 1)
        if response.status != 200 or len(data) > limit:
            raise ValueError("관측 응답 크기 또는 상태 오류")
        return data


def main() -> int:
    parser = argparse.ArgumentParser(description="실제 자체 관측 및 보고서 보존 확인")
    parser.add_argument("--incident", required=True)
    parser.add_argument("--run", required=True)
    args = parser.parse_args()
    try:
        if not re.fullmatch(r"inc-[0-9a-f]{32}", args.incident) or not re.fullmatch(r"run-[0-9a-f]{32}", args.run):
            raise ValueError("식별자 형식 오류")
        # 본문은 읽어 출력하지 않고 고정 파일의 해시만 대조용으로 반환한다.
        directory = Path("/data/incidents") / args.incident / args.run
        hashes = {}
        for name in ("input.json", "metadata.json", "report.json", "report.md", "collection-audit.json"):
            path = directory / name
            if path.is_symlink() or path.stat().st_mode & 0o777 != 0o600:
                raise ValueError("보고서 파일 권한 오류")
            hashes[name] = hashlib.sha256(path.read_bytes()).hexdigest()
        metrics = read_bounded("http://127.0.0.1:9091/metrics", 16384).decode("ascii")
        values = {}
        for line in metrics.splitlines():
            if not line or line.startswith("#"):
                continue
            key, value = line.rsplit(" ", 1)
            if not re.fullmatch(r'ai_ops_(?:operation_duration_seconds_(?:count|sum)\{stage="(?:diagnostic|collection|report)",result="(?:success|partial|failure)"\}|diagnostic_last_success_timestamp_seconds)', key):
                raise ValueError("예상하지 않은 지표")
            value = float(value)
            if not math.isfinite(value) or value < 0 or key in values:
                raise ValueError("지표 값 오류")
            values[key] = value
        if len(values) != 19 or values['ai_ops_operation_duration_seconds_count{stage="diagnostic",result="success"}'] < 1:
            raise ValueError("진단 지표 부족")
        scrape = {}
        for name, query in (
            ("up", 'up{namespace="ai-ops",service="ai-ops-metrics"}'),
            ("heartbeat", 'ai_ops_diagnostic_last_success_timestamp_seconds{namespace="ai-ops",service="ai-ops-metrics"}'),
            ("collection_partial", 'ai_ops_operation_duration_seconds_count{namespace="ai-ops",service="ai-ops-metrics",stage="collection",result="partial"}'),
            ("report_partial", 'ai_ops_operation_duration_seconds_count{namespace="ai-ops",service="ai-ops-metrics",stage="report",result="partial"}'),
        ):
            response = json.loads(read_bounded(PROMETHEUS + "/api/v1/query?" + urlencode({"query": query}), 262144))
            results = response["data"]["result"]
            if response["status"] != "success" or len(results) != 1:
                raise ValueError("Prometheus 대상 수 오류")
            scrape[name] = float(results[0]["value"][1])
        now = datetime.now(timezone.utc)
        if scrape["up"] != 1 or not 0 <= now.timestamp() - scrape["heartbeat"] < 120:
            raise ValueError("Prometheus 수집 또는 heartbeat 지연")
        print(json.dumps({"observed_at": now.isoformat().replace("+00:00", "Z"),
                          "metrics": values, "prometheus": scrape, "file_sha256": hashes,
                          "passed": True}, sort_keys=True), flush=True)
        return 0
    except Exception as error:
        # 예외 원문·본문·토큰은 내보내지 않는다.
        print(json.dumps({"passed": False, "error_type": type(error).__name__}), flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
