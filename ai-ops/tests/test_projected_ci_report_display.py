"""CI 모델 보고서 표시가 근거와 시간대를 보존하는지 확인한다."""

import unittest

from ai_ops.contracts import sha256_json
from ai_ops.model_ci_projection import CIViewItem, project_ci_snapshot
from ai_ops.projected_ci_report_display import render_ci_model_html
from ai_ops.reports import format_kst
from test_model_ci_projection import saved_s10_case


class CIModelReportDisplayTest(unittest.TestCase):
    def setUp(self):
        snapshot, settings, identity, history = saved_s10_case()
        self.view = project_ci_snapshot(snapshot, settings, identity, history).view
        self.failure, self.rollback = [item for item in self.view.items
                                       if isinstance(item, CIViewItem)]
        self.report = {
            "input_hash": sha256_json(self.view.model_dump(mode="json")),
            "analysis_status": "partial", "diagnosis_status": "suspected_cause",
            "current_state": "unknown",
            "facts": [
                {"statement": self.failure.observed_at.strftime("%H:%M:%S") +
                 " 후보 배포 Ready 0, 이미지 취득 실패", "evidence_ids": [self.failure.evidence_id]},
                {"statement": self.rollback.observed_at.strftime("%H:%M:%S") +
                 " 기준 버전 Ready 1", "evidence_ids": [self.rollback.evidence_id]},
            ],
            "hypotheses": [{"cause": "이미지 취득 실패가 원인 후보",
                             "supporting_evidence_ids": [self.failure.evidence_id],
                             "contradicting_evidence_ids": []}],
            "limitations": ["원본 이미지·Git SHA는 모델에 제공되지 않았다."],
            "suggested_actions": [{"description": "사용자 경로를 확인한다.",
                                   "execution_status": "not_executed"}],
        }

    def test_cited_utc_clocks_show_kst_and_input_scope(self):
        page = render_ci_model_html(self.report, self.view)
        self.assertIn(format_kst(self.failure.observed_at), page)
        self.assertIn(format_kst(self.rollback.observed_at), page)
        self.assertIn("원문 " + self.failure.observed_at.strftime("%H:%M:%S") + " UTC", page)
        self.assertIn("모델에 전달된 정제 입력", page)
        self.assertIn("원본 CI 자료 전체의 부재를 뜻하지 않습니다", page)
        self.assertIn("사용자 요청 경로 복구나 현재 전체 서비스 정상 상태를 증명하지 않습니다", page)
        self.assertIn("미실행", page)

    def test_unmatched_time_is_not_guessed_and_html_is_escaped(self):
        self.report["facts"][0]["statement"] = "01:02:03 <script>잘못된 시간</script>"
        page = render_ci_model_html(self.report, self.view)
        self.assertIn("01:02:03 [시간대 확인 필요]", page)
        self.assertNotIn("<script>", page)
        self.assertIn("&lt;script&gt;", page)

    def test_unseen_citation_is_rejected(self):
        self.report["facts"][0]["evidence_ids"] = ["ev-view-999"]
        with self.assertRaises(ValueError):
            render_ci_model_html(self.report, self.view)


if __name__ == "__main__":
    unittest.main()
