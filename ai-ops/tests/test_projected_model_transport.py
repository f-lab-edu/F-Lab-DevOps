"""실제 네트워크 없이 live 전송의 TLS·경로·키·승인·예산 분리를 검사한다."""

import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from ai_ops.model_comparison import ComparisonStore, rules_projection
from ai_ops.projected_model_engine import CallPlan, request_templates
from ai_ops.projected_model_transport import LiveOpenAITransport, read_credential
from test_model_projection import example_plan, example_projection
from test_model_comparison import FakeTransport
from test_live_collectors import AT


KEY = "test-credential-" + "x"*40
FINGERPRINT = hashlib.sha256(KEY.encode()).hexdigest()


class Response:
    def __init__(self, body=b'{"ok":true}', status=200, headers=None):
        self.status = status; self.body = body; self.headers = headers or {}

    def getheader(self, key, default=None):
        return self.headers.get(key, default)

    def read1(self, size):
        head, self.body = self.body[:size], self.body[size:]
        return head


class Connection:
    def __init__(self, response):
        self.response = response; self.requests = []; self.closed = False; self.sock = Mock()

    def request(self, *args, **kwargs):
        self.requests.append((args, kwargs))

    def getresponse(self):
        return self.response

    def close(self):
        self.closed = True


def live_plan():
    value = example_plan().model_dump(mode="json")
    value.update(execution_mode="live", credential_fingerprint=FINGERPRINT)
    value["price"].update(cache_write_per_million=2.5, cached_input_per_million=0.2)
    return CallPlan.model_validate_json(json.dumps(value))


class FakeLive(FakeTransport):
    comparison_mode = "live"
    expected_credential = FINGERPRINT

    def __init__(self, report):
        super().__init__(report)
        self.attempts = {"/v1/responses/input_tokens": 0, "/v1/responses": 0}

    def __call__(self, path, body, *, timeout):
        self.attempts[path] += 1
        return super().__call__(path, body, timeout=timeout)


class ProjectedTransportTest(unittest.TestCase):
    def test_only_fixed_tls_host_and_header_credential_no_environment_proxy(self):
        connection = Connection(Response())
        transport = LiveOpenAITransport(FINGERPRINT)
        with patch("ai_ops.projected_model_transport.read_credential", return_value=KEY), patch(
                "ai_ops.projected_model_transport.http.client.HTTPSConnection", return_value=connection) as factory:
            result = transport("/v1/responses", {"model": "gpt-6-sol"}, timeout=10)
        self.assertEqual(result, {"ok": True})
        self.assertEqual(factory.call_args.args, ("api.openai.com", 443))
        args, kwargs = connection.requests[0]
        self.assertEqual(args, ("POST", "/v1/responses"))
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer "+KEY)
        self.assertNotIn(KEY.encode(), kwargs["body"])
        self.assertTrue(connection.closed)
        self.assertIsNotNone(factory.call_args.kwargs["context"])

    def test_unknown_path_changed_key_and_duplicate_request_block_before_connection(self):
        with patch("ai_ops.projected_model_transport.read_credential", return_value=KEY), patch(
                "ai_ops.projected_model_transport.http.client.HTTPSConnection") as factory:
            for path, fingerprint in [("https://evil.invalid/", FINGERPRINT), ("/v1/responses", "a"*64)]:
                with self.assertRaises(ValueError):
                    LiveOpenAITransport(fingerprint)(path, {}, timeout=10)
            factory.assert_not_called()
        connection = Connection(Response())
        with patch("ai_ops.projected_model_transport.read_credential", return_value=KEY), patch(
                "ai_ops.projected_model_transport.http.client.HTTPSConnection", return_value=connection) as factory:
            transport = LiveOpenAITransport(FINGERPRINT)
            transport("/v1/responses", {}, timeout=10)
            with self.assertRaises(ValueError): transport("/v1/responses", {}, timeout=10)
            self.assertEqual(factory.call_count, 1)

    def test_redirect_encoded_oversize_invalid_json_rejected_without_retry(self):
        for response in (Response(status=302, headers={"Location": "https://evil.invalid/"}),
                         Response(headers={"Content-Encoding": "gzip"}),
                         Response(headers={"Content-Length": "262145"}),
                         Response(body=b"x"*262145), Response(body=b"not-json")):
            connection = Connection(response)
            with patch("ai_ops.projected_model_transport.read_credential", return_value=KEY), patch(
                    "ai_ops.projected_model_transport.http.client.HTTPSConnection", return_value=connection) as factory:
                with self.assertRaisesRegex(ValueError, "projection_http_request_failed"):
                    LiveOpenAITransport(FINGERPRINT)("/v1/responses", {}, timeout=10)
                self.assertEqual(factory.call_count, 1)
                self.assertTrue(connection.closed)

    def test_local_key_permissions_symlinks_and_whitespace_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory).resolve()/"key"
            path.write_text(KEY); path.chmod(0o600)
            self.assertEqual(read_credential(path), KEY)
            path.chmod(0o644)
            with self.assertRaises(ValueError): read_credential(path)
            path.chmod(0o600); path.write_text(KEY+"\n")
            with self.assertRaises(ValueError): read_credential(path)
            alias = path.with_name("alias"); alias.symlink_to(path)
            with self.assertRaises(ValueError): read_credential(alias)

    def test_mock_approval_never_authorizes_live_transport_and_modes_have_separate_ledgers(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"AI_OPS_FILES_ROOT": str(Path(directory).resolve())}):
            store = ComparisonStore(Path(directory).resolve()/"store", now=lambda: AT)
            projection = example_projection()
            mock_id = store.prepare(projection, example_plan())
            live_id = store.prepare(projection, live_plan())
            self.assertNotEqual(mock_id, live_id)
            store.approve(mock_id, binding_hash=store.preview(mock_id)["binding_hash"], actor="operator")
            transport = FakeLive(rules_projection(projection.view).model_dump(mode="json"))
            with self.assertRaises(ValueError): store.run(mock_id, transport=transport)
            with self.assertRaisesRegex(ValueError, "retention_ack"):
                store.approve(live_id, binding_hash=store.preview(live_id)["binding_hash"], actor="operator")
            self.assertFalse(transport.calls)
            store.approve(live_id, binding_hash=store.preview(live_id)["binding_hash"], actor="operator",
                          acknowledge_provider_retention=True, confirm_optional_data_sharing_off=True)
            result = store.run(live_id, transport=transport)
            self.assertEqual(result["external_request_attempts"]["/v1/responses"], 1)
            self.assertEqual(result["transport_kind"], "live")
            self.assertTrue((store.root/"budget-live.json").exists())
            self.assertFalse((store.root/"budget.json").exists())
            self.assertEqual(store._read(store.root/"budget-live.json")["execution_mode"], "live")

    def test_live_plan_requires_explicit_price_and_credential_binding(self):
        value = example_plan().model_dump(mode="json"); value["execution_mode"] = "live"
        with self.assertRaises(ValueError): CallPlan.model_validate_json(json.dumps(value))
        body = request_templates(example_projection().view, live_plan())["/v1/responses"]
        self.assertEqual(body["service_tier"], "default")
        self.assertNotIn("credential_fingerprint", body)

    def test_live_ack_requires_account_confirmation_and_preview_is_purged(self):
        from datetime import timedelta
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"AI_OPS_FILES_ROOT": str(Path(directory).resolve())}):
            now = [AT]
            store = ComparisonStore(Path(directory).resolve()/"store", now=lambda: now[0])
            identifier = store.prepare(example_projection(), live_plan())
            with self.assertRaisesRegex(ValueError, "account_data_controls"):
                store.approve(identifier, binding_hash=store.preview(identifier)["binding_hash"], actor="operator",
                              acknowledge_provider_retention=True)
            preview = store.write_preview(identifier)
            self.assertEqual(preview.stat().st_mode & 0o077, 0)
            self.assertIn("&quot;", preview.read_text())
            now[0] += timedelta(days=8)
            store.purge(apply=True)
            self.assertFalse(preview.exists())


if __name__ == "__main__":
    unittest.main()
