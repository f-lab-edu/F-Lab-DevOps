"""운영 데모가 모델 활성화와 예상 밖 권한에서 조사 접수를 차단하는지 확인한다."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location(
    "operational_demo", Path(__file__).parents[1] / "scripts/run_operational_demo.py")
demo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(demo)


class OperationalDemoTest(unittest.TestCase):
    def test_enabled_model_stops_before_submission(self):
        pod = {"status": {"containerStatuses": [{"ready": True}, {"ready": True}]}}
        config = {"data": {"runtime.json": '{"llm_enabled":true}'}}
        with patch.object(demo, "command", return_value=SimpleNamespace(stdout=demo.CONTEXT)), \
                patch.object(demo, "kube_json", side_effect=[pod, config]), \
                patch.object(demo, "cli") as submit:
            with self.assertRaisesRegex(ValueError, "외부 모델 비활성"):
                demo.evaluate({})
            submit.assert_not_called()

    def test_unexpected_secret_permission_is_rejected(self):
        # 허용 조회 세 개 뒤에 Secret 읽기가 허용되는 잘못된 identity를 재현한다.
        with patch.object(demo, "command", return_value=SimpleNamespace(stdout="yes", returncode=0)):
            with self.assertRaisesRegex(ValueError, "권한 범위"):
                demo.permissions()

    def test_authorization_error_is_not_interpreted_as_denial(self):
        with patch.object(demo, "command", return_value=SimpleNamespace(stdout="", returncode=1)):
            with self.assertRaisesRegex(ValueError, "확인할 수 없습니다"):
                demo.permissions()


if __name__ == "__main__":
    unittest.main()
