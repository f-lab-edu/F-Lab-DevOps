"""Pydantic 데이터 계약에서 공개 JSON 스키마 파일을 생성한다."""

import json
from pathlib import Path

from ai_ops.contracts import AnalysisReport, EvidenceBundle, IncidentRequest


SCHEMAS = {
    "incident-request": IncidentRequest,
    "evidence-bundle": EvidenceBundle,
    "analysis-report": AnalysisReport,
}


def main() -> None:
    destination = Path(__file__).resolve().parents[1] / "schemas"
    destination.mkdir(exist_ok=True)
    for name, model in SCHEMAS.items():
        (destination / f"{name}.schema.json").write_text(
            json.dumps(model.model_json_schema(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )


if __name__ == "__main__":
    main()
