"""CI의 배포·롤백 검증 뒤 Argo 자료를 메모리에서 선별해 고정 JSON만 남긴다."""

from __future__ import annotations

import json
import os
import re
import selectors
import signal
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit


MAX_RESPONSE = 1024 * 1024
REPOSITORY = "f-lab-edu/F-Lab-DevOps"
REPO_URL = "https://github.com/" + REPOSITORY + ".git"
CHART_PATH = "url-shortener/url-shortener-chart"


def read_argo(env: dict[str, str], timeout: float = 8) -> tuple[str, bytes | None]:
    """응답은 1MiB·8초로 제한하고 stderr·임시 원문 파일을 만들지 않는다."""
    parsed = urlsplit(env.get("ARGOCD_SERVER", ""))
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.path not in ("", "/") or parsed.query or parsed.fragment or not env.get("ARGOCD_AUTH_TOKEN")):
        return "unconfigured", None
    cli = env.get("ARGOCD_CLI") or str(Path(env.get("RUNNER_TEMP", "/tmp")) / "argocd")
    try:
        process = subprocess.Popen([cli, "--server", parsed.netloc, "--grpc-web", "app", "get", "url-shortener", "--refresh", "-o", "json"],
                                   stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env, start_new_session=True)
    except (OSError, ValueError):
        return "read_failed", None
    try:
        data = bytearray()
        deadline = time.monotonic() + timeout
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not selector.select(remaining):
                    return "timeout", None
                chunk = os.read(process.stdout.fileno(), min(8192, MAX_RESPONSE + 1 - len(data)))
                if not chunk:
                    break
                data.extend(chunk)
                if len(data) > MAX_RESPONSE:
                    return "too_large", None
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return "timeout", None
        if process.wait(timeout=remaining) != 0:
            return "read_failed", None
        return "available", bytes(data)
    except (OSError, subprocess.TimeoutExpired):
        return "timeout", None
    finally:
        # 후손이 stdout을 붙잡는 경우도 수집 기한 뒤 정리한다.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()
        process.stdout.close()


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate field")
        result[key] = value
    return result


def _sha(value):
    if value in (None, ""):
        return None
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{40}", value):
        raise ValueError("invalid revision")
    return value


def _time(value):
    if not value:
        return None
    if not isinstance(value, str) or len(value) > 40:
        raise ValueError("invalid timestamp")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("missing timezone")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def build_record(env: dict[str, str], raw: bytes | None, read_status: str) -> dict:
    """허용한 식별자·시각·상태만 남기며 실패 이유도 고정 코드로 한정한다."""
    stage, result = env["OBS_STAGE"], env["OBS_RESULT"]
    if stage not in {"deployment_verification", "rollback_verification"} or result not in {"success", "failure"}:
        raise ValueError("invalid phase")
    if env.get("GITHUB_REPOSITORY") != REPOSITORY:
        raise ValueError("unexpected repository")
    for key in ("GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT"):
        if not re.fullmatch(r"[1-9][0-9]{0,19}", env.get(key, "")):
            raise ValueError("invalid run identity")
    image_repo = env["OBS_IMAGE_REPOSITORY"]
    if not re.fullmatch(r"[0-9]{12}\.dkr\.ecr\.[a-z]{2}-[a-z]+-[0-9]\.amazonaws\.com/urlshortener", image_repo):
        raise ValueError("unexpected app image repository")
    image = env.get("OBS_EXPECTED_IMAGE") or None
    # 현 CI의 SHA/release 태그와 초기 v1만 허용해 임의 문자열이 artifact에 실리지 않게 한다.
    image_pattern = re.escape(image_repo) + r"(?::(?:sha-[0-9a-f]{7}(?:[0-9a-f]{33})?|release-[0-9a-f]{40}|v1)|@sha256:[0-9a-f]{64})"
    if image is not None and not re.fullmatch(image_pattern, image):
        raise ValueError("invalid expected image")
    record = {
        "schema_version": 1, "record_type": "deployment_observation", "repository": REPOSITORY,
        "run_id": env["GITHUB_RUN_ID"], "run_attempt": env["GITHUB_RUN_ATTEMPT"],
        "source_revision": _sha(env.get("GITHUB_SHA")), "stage": stage, "verification_result": result,
        "verification_started_at": _time(env.get("OBS_STARTED_AT")),
        "verification_finished_at": _time(env.get("OBS_FINISHED_AT")),
        "observed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "expected_revision": _sha(env.get("OBS_EXPECTED_REVISION")), "expected_image": image,
        "application": "url-shortener", "namespace": "url-shortener", "argo_namespace": "argocd",
        "collection_status": "unavailable", "reason": "read_failed", "observed": None,
    }
    if read_status != "available":
        if read_status not in {"unconfigured", "read_failed", "timeout", "too_large"}:
            raise ValueError("invalid read status")
        record["reason"] = read_status
        return record
    try:
        if raw is None or len(raw) > MAX_RESPONSE:
            raise ValueError("invalid response size")
        doc = json.loads(raw, object_pairs_hook=_object, parse_constant=lambda _: (_ for _ in ()).throw(ValueError("invalid number")))
        metadata, spec, status = doc["metadata"], doc["spec"], doc["status"]
        source, destination = spec["source"], spec["destination"]
        if (metadata["name"] != "url-shortener" or metadata["namespace"] != "argocd"
                or source["repoURL"] != REPO_URL or source["path"] != CHART_PATH
                or destination["namespace"] != "url-shortener" or destination["server"] != "https://kubernetes.default.svc"
                or spec.get("sources")):
            record["reason"] = "scope_mismatch"
            return record
        sync, health = status.get("sync", {}), status.get("health", {})
        operation = status.get("operationState") or {}
        sync_result = operation.get("syncResult") or {}
        sync_state, health_state, phase = sync.get("status", "Unknown"), health.get("status", "Unknown"), operation.get("phase")
        if (sync_state not in {"Synced", "OutOfSync", "Unknown"}
                or health_state not in {"Healthy", "Degraded", "Missing", "Progressing", "Suspended", "Unknown"}
                or phase not in {None, "Succeeded", "Failed", "Error", "Running", "Terminating"}):
            raise ValueError("invalid observed state")
        images = status.get("summary", {}).get("images", [])
        if not isinstance(images, list) or len(images) > 16:
            raise ValueError("invalid observed images")
        kept = []
        for value in images:
            if not isinstance(value, str):
                raise ValueError("invalid image")
            if re.fullmatch(image_pattern, value):
                kept.append(value)
        record["observed"] = {"revision": _sha(sync.get("revision")), "sync_status": sync_state,
            "health_status": health_state, "operation_revision": _sha(sync_result.get("revision")),
            "operation_phase": phase, "reconciled_at": _time(status.get("reconciledAt")),
            "images": sorted(set(kept)), "images_omitted": len(images) - len(kept)}
        record.update(collection_status="available", reason=None)
        return record
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError):
        record["reason"] = "invalid_payload"
        return record


def write_record(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open("w", encoding="utf-8") as stream:
        os.chmod(path, 0o600)
        json.dump(record, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


if __name__ == "__main__":
    try:
        environment = dict(os.environ)
        status, payload = read_argo(environment)
        write_record(Path(environment["OBS_OUTPUT"]), build_record(environment, payload, status))
    except Exception:
        # 인증값·원시 응답·CLI 오류를 로그에 남기지 않는다.
        raise SystemExit("배포 관측 기록 생성 실패: 원래 배포·복구 결과를 확인하세요.") from None
