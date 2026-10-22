"""전달기 자격의 API 범위·다운로드 인증 분리와 크기 제한을 검증한다."""
import io
import tempfile
from pathlib import Path
import unittest
from unittest.mock import Mock
from urllib.error import HTTPError

from ai_ops.ci_token_reader import TokenGitHubReader
from ai_ops.ci_history import PREFIX


class TokenReaderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.file = Path(self.tmp.name) / 'token'
        self.file.write_text('github_pat_' + 'a' * 30)
        self.file.chmod(0o600)
        self.reader = TokenGitHubReader(self.file)
        self.reader.opener = Mock()

    def response(self, data):
        response = Mock()
        response.__enter__ = Mock(return_value=io.BytesIO(data))
        response.__exit__ = Mock(return_value=False)
        return response

    def test_signed_download_never_receives_token(self):
        error = HTTPError('https://api.github.com', 302, '', {'Location': 'https://example.blob.core.windows.net/file?sig=test'}, None)
        self.reader.opener.open.side_effect = [error, self.response(b'PK')]
        self.assertEqual(self.reader.get(PREFIX + '/artifacts/123/zip'), b'PK')
        first, second = [x.args[0] for x in self.reader.opener.open.call_args_list]
        self.assertTrue(first.has_header('Authorization'))
        self.assertFalse(second.has_header('Authorization'))

    def test_untrusted_redirect_rejected(self):
        for url in ['http://example.blob.core.windows.net/a', 'https://evil.test/a', 'https://api.github.com/a', 'https://user@example.blob.core.windows.net/a']:
            self.reader.opener.open.side_effect = HTTPError('https://api.github.com', 302, '', {'Location': url}, None)
            with self.assertRaises(ValueError): self.reader.get(PREFIX + '/artifacts/123/zip')

    def test_scope_rejected_before_credential_read(self):
        self.file.unlink()
        with self.assertRaises(ValueError): self.reader.get('repos/other/repo/actions/runs/1')
        self.reader.opener.open.assert_not_called()

    def test_oversized_response_rejected(self):
        self.reader.opener.open.return_value = self.response(b'12345')
        with self.assertRaises(ValueError): self.reader.get(PREFIX + '/runs/123', max_bytes=4)

    def test_writable_credential_rejected(self):
        self.file.chmod(0o666)
        with self.assertRaises(ValueError): self.reader.get(PREFIX + '/runs/123')
        self.reader.opener.open.assert_not_called()
