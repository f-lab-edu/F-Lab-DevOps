"""고정된 실습 대상만 조회하고 임시 로컬 포트 전달을 정리한다.

기존 운영자 kubeconfig를 사용하며 자격 발급·권한 변경·exec·모델 호출은 하지 않는다.
응답 본문과 kubectl 오류에는 민감한 자료가 있을 수 있어 예외에 포함하지 않는다.
"""

from contextlib import contextmanager
import http.client
import json
import os
import re
import selectors
import subprocess
import time

CONTEXT = "arn:aws:eks:ap-northeast-2:764428911194:cluster/urlshortener"
RESOURCES = {
    "application": ("argocd", "applications.argoproj.io", "lab-target"),
    "deployment": ("ai-ops-lab", "deployment", "lab-api"),
    "pods": ("ai-ops-lab", "pods", "-l", "app=lab-api"),
    "replicasets": ("ai-ops-lab", "replicasets", "-l", "app=lab-api"),
    "prometheus": ("monitoring", "pod", "prometheus-kube-prometheus-stack-prometheus-0"),
}
LIMIT = 2 * 1024 * 1024


class CollectionError(ValueError):
    """외부 응답을 노출하지 않는 수집 실패다."""


def _chunks(process, deadline, limit):
    """파이프가 멈추거나 출력이 커져도 무한 대기·메모리 증가를 막는다."""
    total = 0
    with selectors.DefaultSelector() as selector:
        selector.register(process.stdout, selectors.EVENT_READ)
        while time.monotonic() < deadline:
            if not selector.select(min(0.2, max(0, deadline-time.monotonic()))):
                continue
            data = os.read(process.stdout.fileno(), 65536)
            if not data:
                return
            total += len(data)
            if total > limit:
                raise CollectionError("response_too_large")
            yield data
    raise CollectionError("read_timeout")


def _stop(process):
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)
    if process.stdout:
        process.stdout.close()


def _reject_constant(value):
    raise ValueError("유한하지 않은 JSON 값")


class KubectlReader:
    """기존 권한으로 GET과 루프백 포트 전달만 수행하는 로컬 수집기다."""

    def __init__(self):
        self.http_requests = 0
        self.forwards_started = 0
        self.deadline = None

    def _deadline(self, seconds):
        deadline = time.monotonic()+seconds
        if self.deadline is not None:
            deadline = min(deadline, self.deadline)
        if deadline <= time.monotonic():
            raise CollectionError("collection_deadline_exceeded")
        return deadline

    def _command(self, *args):
        return ["kubectl", "--context", CONTEXT, "--request-timeout=8s", *args]

    def get(self, resource):
        namespace, *target = RESOURCES[resource]
        deadline = self._deadline(12)
        process = subprocess.Popen(self._command("-n", namespace, "get", *target, "-o", "json"),
                                   stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        try:
            data = b"".join(_chunks(process, deadline, LIMIT))
            if process.wait(timeout=2) != 0:
                raise CollectionError("kubernetes_read_failed")
            result = json.loads(data, parse_constant=_reject_constant)
            if not isinstance(result, dict):
                raise CollectionError("invalid_kubernetes_response")
            return result
        except (OSError, ValueError, subprocess.TimeoutExpired):
            raise CollectionError("kubernetes_read_failed") from None
        finally:
            _stop(process)

    @contextmanager
    def forward(self, namespace, pod, port):
        """명시적 Pod에만 연결하며 모든 종료 경로에서 하위 프로세스를 닫는다."""
        allowed = (namespace == "ai-ops-lab" and re.fullmatch(r"lab-api-[a-z0-9-]+", pod) and port == 8000
                   or namespace == "monitoring" and pod == RESOURCES["prometheus"][-1] and port == 9090)
        if not allowed:
            raise CollectionError("forward_target_rejected")
        deadline = self._deadline(15)
        process = subprocess.Popen(self._command("-n", namespace, "port-forward", "--address", "127.0.0.1",
                                   "pod/"+pod, ":"+str(port)), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        try:
            buffer = b""
            for chunk in _chunks(process, deadline, 16384):
                buffer += chunk
                found = re.search(rb"Forwarding from 127\.0\.0\.1:(\d+) -> "+str(port).encode()+rb"\b", buffer)
                if found:
                    self.forwards_started += 1
                    yield int(found.group(1))
                    return
            raise CollectionError("forward_not_ready")
        finally:
            _stop(process)

    def http(self, port, path):
        """프록시·리다이렉트 없이 GET 응답만 읽고 본문 대신 해시를 남긴다."""
        if path not in ("/readyz", "/items") and not path.startswith(("/api/v1/query?", "/api/v1/rules?")):
            raise CollectionError("http_path_rejected")
        self.http_requests += 1
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        try:
            deadline = self._deadline(15)
            connection.timeout = min(5, deadline-time.monotonic())
            connection.request("GET", path, headers={"Accept": "application/json", "Accept-Encoding": "identity"})
            response = connection.getresponse()
            if response.getheader("Content-Encoding", "identity") != "identity":
                raise CollectionError("encoded_response_rejected")
            length = response.getheader("Content-Length")
            if length is not None and (not length.isdigit() or int(length) > LIMIT):
                raise CollectionError("response_too_large")
            body = bytearray()
            while True:
                if time.monotonic() >= deadline:
                    raise CollectionError("read_timeout")
                if connection.sock is not None:
                    connection.sock.settimeout(min(5, deadline-time.monotonic()))
                part = response.read1(min(65536, LIMIT+1-len(body)))
                if not part:
                    break
                body.extend(part)
                if len(body) > LIMIT:
                    raise CollectionError("response_too_large")
            try:
                value = json.loads(body, parse_constant=_reject_constant)
            except (ValueError, UnicodeError):
                value = None
            import hashlib
            return response.status, value, hashlib.sha256(body).hexdigest()
        except (OSError, http.client.HTTPException, ValueError):
            raise CollectionError("http_read_failed") from None
        finally:
            connection.close()
