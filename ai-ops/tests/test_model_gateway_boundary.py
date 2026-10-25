"""모델 gateway와 proxy가 원문·임의 목적지·재전송을 거부하는지 검사한다."""

import copy
import hashlib
import socket
import unittest
from unittest.mock import patch

from ai_ops.contracts import canonical_json_bytes, sha256_json
from ai_ops.model_egress_proxy import parse_connect, public_addresses
from ai_ops.model_gateway import validate_request
from ai_ops.model_gateway_transport import GatewayOpenAITransport, _TunnelConnection
from test_model_projection import example_projection


KEY = b"test-model-client-" + b"x" * 40
API_KEY = "test-api-" + "x" * 40


class GatewayBoundaryTests(unittest.TestCase):
    def test_only_valid_sanitized_view_and_hash(self):
        view = example_projection().view.model_dump(mode="json")
        body = {"view": view, "view_hash": sha256_json(view)}
        raw = canonical_json_bytes(body)
        self.assertEqual(validate_request(raw, "Bearer " + KEY.decode(), KEY).target_alias,
                         "service-1")
        for mutate in (lambda d: d.update(snapshot={"secret": "no"}),
                       lambda d: d.update(view_hash="0" * 64),
                       lambda d: d["view"].update(raw_log="no")):
            changed = copy.deepcopy(body)
            mutate(changed)
            with self.assertRaises(ValueError):
                validate_request(canonical_json_bytes(changed), "Bearer " + KEY.decode(), KEY)
        with self.assertRaises(ValueError):
            validate_request(raw, "Bearer wrong", KEY)
        with self.assertRaises(ValueError):
            validate_request(b"x" * 32769, "Bearer " + KEY.decode(), KEY)

    def test_connect_target_and_public_dns_only(self):
        parse_connect(b"CONNECT api.openai.com:443 HTTP/1.1\r\nHost: api.openai.com:443\r\n\r\n")
        parse_connect(b"CONNECT api.openai.com:443 HTTP/1.0\r\nHost: api.openai.com:443\r\n\r\n")
        for raw in (b"CONNECT 1.2.3.4:443 HTTP/1.1\r\nHost: 1.2.3.4:443\r\n\r\n",
                    b"CONNECT api.openai.com:80 HTTP/1.1\r\nHost: api.openai.com:80\r\n\r\n",
                    b"CONNECT api.openai.com:443 HTTP/1.1\r\nHost: api.openai.com:443\r\nX: a\r\n\r\n",
                    b"CONNECT api.openai.com:443 HTTP/1.0\r\nHost: api.openai.com:443\r\nX: a\r\n\r\n",
                    b"CONNECT api.openai.com:443 HTTP/1.0\r\n\r\n"):
            with self.assertRaises(ValueError):
                parse_connect(raw)
        def resolver(_host, _port, *, family, type):
            self.assertEqual(family, socket.AF_INET)
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("1.1.1.1", 443))]
        self.assertEqual(public_addresses(resolver)[0][4][0], "1.1.1.1")
        def private(_host, _port, *, family, type):
            return resolver(_host, _port, family=family, type=type) + [
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.1", 443))]
        with self.assertRaises(ValueError):
            public_addresses(private)

    def test_tunnel_destination_and_no_auto_retry(self):
        connection = _TunnelConnection(timeout=5)
        self.assertEqual(connection.host, "model-egress-proxy.ai-model.svc.cluster.local")
        self.assertEqual(connection._tunnel_host, "api.openai.com")
        self.assertEqual(connection._tunnel_port, 443)
        with patch("ai_ops.model_gateway_transport.http.client.HTTPConnection.connect") as connect, patch(
                "ai_ops.model_gateway_transport.ssl.create_default_context") as context:
            raw_socket = object()
            connection.sock = raw_socket
            connection.connect()
            connect.assert_called_once()
            context.return_value.wrap_socket.assert_called_once_with(raw_socket,
                server_hostname="api.openai.com")

        class Response:
            status = 200
            def __init__(self): self.body = b'{"ok":true}'
            def getheader(self, _name, default=None): return default
            def read1(self, size):
                part, self.body = self.body[:size], self.body[size:]
                return part

        class FakeConnection:
            sock = None
            def __init__(self): self.calls = 0
            def request(self, *args, **kwargs): self.calls += 1
            def getresponse(self): return Response()
            def close(self): pass

        fake = FakeConnection()
        fingerprint = hashlib.sha256(API_KEY.encode()).hexdigest()
        with patch("ai_ops.model_gateway_transport.read_cluster_credential", return_value=API_KEY), patch(
                "ai_ops.model_gateway_transport._TunnelConnection", return_value=fake):
            transport = GatewayOpenAITransport(fingerprint)
            self.assertEqual(transport("/v1/responses", {}, timeout=5), {"ok": True})
            with self.assertRaises(ValueError):
                transport("/v1/responses", {}, timeout=5)
            with self.assertRaises(ValueError):
                transport("/evil", {}, timeout=5)
        self.assertEqual(fake.calls, 1)

    def test_stopped_campaign_blocks_before_key_read_or_network(self):
        def stopped():
            raise ValueError("campaign_stopped")
        transport = GatewayOpenAITransport("0" * 64, before_request=stopped)
        with patch("ai_ops.model_gateway_transport.read_cluster_credential") as credential, patch(
                "ai_ops.model_gateway_transport._TunnelConnection") as connection:
            with self.assertRaisesRegex(ValueError, "campaign_stopped"):
                transport("/v1/responses", {}, timeout=5)
            credential.assert_not_called()
            connection.assert_not_called()
