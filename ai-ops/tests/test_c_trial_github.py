"""병합 App 세션의 한 저장소 범위·정확한 head·자격 회수를 시험한다."""

from datetime import datetime, timedelta, timezone
import unittest

from ai_ops.c_trial_github import APP_ID, BOT_ID, INSTALLATION_ID, PERMISSIONS, GitHubMergeSession


class Session(GitHubMergeSession):
    def __init__(self):
        super().__init__()
        self.calls = []
        self.repo_count = 1

    def _jwt(self):
        return "synthetic-jwt"

    def _api(self, method, path, credential, payload=None):
        self.calls.append((method, path))
        if path == "/app":
            return {"id": APP_ID, "slug": "c03-execution-lab-merge",
                    "owner": {"login": "joungGo"}, "permissions": PERMISSIONS}
        if path == f"/app/installations/{INSTALLATION_ID}":
            return {"id": INSTALLATION_ID, "app_id": APP_ID, "account": {"login": "joungGo"},
                    "repository_selection": "selected", "permissions": PERMISSIONS}
        if path.endswith("/access_tokens"):
            self.asserted_scope = payload
            return {"token": "synthetic-installation-token", "permissions": PERMISSIONS,
                    "expires_at": (datetime.now(timezone.utc)+timedelta(minutes=30)).isoformat()}
        if path.startswith("/installation/repositories"):
            return {"total_count": self.repo_count,
                    "repositories": [{"id": 1452572723, "full_name": "joungGo/c03-execution-lab"}]}
        if path.startswith("/users/"):
            return {"id": BOT_ID, "type": "Bot"}
        if path == "/installation/token":
            return {}
        if path.endswith("/merge"):
            self.merge_payload = payload
            return {"merged": True, "sha": "e"*40}
        raise AssertionError(path)


class GitHubSessionTest(unittest.TestCase):
    def test_registration_issues_no_installation_token(self):
        session = Session()
        session.verify_registration()
        self.assertIsNone(session.token)
        self.assertTrue(all(method == "GET" for method, _ in session.calls))

    def test_exact_merge_uses_one_repo_and_revokes_on_exit(self):
        session = Session()
        with session as client:
            result = client.merge({"method": "PUT", "endpoint": "repos/joungGo/c03-execution-lab/pulls/8/merge",
                "write_enabled": False, "body": {"sha": "a"*40, "merge_method": "squash",
                    "merge_action": "direct_merge", "bypass_rules": False}})
            self.assertTrue(result["merged"])
            self.assertEqual(client.identity["actor_id"], BOT_ID)
            self.assertEqual(session.asserted_scope["repository_ids"], [1452572723])
            self.assertEqual(set(session.merge_payload), {"sha", "merge_method"})
        self.assertIsNone(session.token)
        self.assertIn(("DELETE", "/installation/token"), session.calls)

    def test_scope_mismatch_and_exception_revoke_new_token(self):
        session = Session()
        session.repo_count = 2
        with self.assertRaises(ValueError):
            with session:
                self.fail("scope should be rejected")
        self.assertIsNone(session.token)
        self.assertIn(("DELETE", "/installation/token"), session.calls)

    def test_other_repo_or_bypass_is_refused_before_put(self):
        session = Session()
        with session as client:
            with self.assertRaises(ValueError):
                client.merge({"method": "PUT", "endpoint": "repos/other/repo/pulls/8/merge", "write_enabled": False})
        self.assertFalse(any(method == "PUT" for method, _ in session.calls))
        self.assertIsNone(session.token)
        session = Session()
        with self.assertRaisesRegex(RuntimeError, "synthetic"):
            with session:
                raise RuntimeError("synthetic failure")
        self.assertIsNone(session.token)
