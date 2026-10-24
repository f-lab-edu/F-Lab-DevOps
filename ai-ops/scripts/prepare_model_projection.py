"""고정 실습 Pod 내부에서만 저장 snapshot을 선별하고 정제 결과만 반출한다."""

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess

from ai_ops.b6_lab import CONTEXT, PURPOSE
from ai_ops.contracts import canonical_json_bytes, sha256_json
from ai_ops.model_projection import SourceIdentity, parse_projection
from ai_ops.path_policy import cli_paths
from ai_ops.model_comparison import ComparisonStore
from ai_ops.projected_model_engine import CallPlan


ROOT = Path(__file__).resolve().parents[1]


def command(args, *, data=None, limit=512*1024):
    """출력은 메모리로만 받고 오류 원문·자격은 출력하지 않는다."""
    result = subprocess.run(args, input=data, capture_output=True, timeout=60)
    if result.returncode or len(result.stdout) > limit:
        raise ValueError("projection_export_command_failed")
    return result.stdout


def current_identity(expected_image, *, execute=command):
    """고정 context·현재 클러스터·Deployment·수집기 이미지와 소유 표식을 검사한다."""
    def read(args): return json.loads(execute(args))
    if execute(["kubectl", "config", "current-context"]).decode().strip() != CONTEXT:
        raise ValueError("projection_context_rejected")
    cluster = read(["aws", "eks", "describe-cluster", "--name", "urlshortener", "--region", "ap-northeast-2"])["cluster"]
    if cluster["arn"] != CONTEXT or cluster["status"] != "ACTIVE":
        raise ValueError("projection_cluster_rejected")
    def get(kind, name):
        selection = ["-l", "app=lab-collector"] if kind == "pods" else [name]
        return read(["kubectl", "--context", CONTEXT, "-n", "ai-ops-lab", "get", kind, *selection, "-o", "json"])
    namespace = get("namespace", "ai-ops-lab")
    if any(namespace["metadata"].get("labels", {}).get(k) != v for k, v in PURPOSE.items()):
        raise ValueError("projection_namespace_rejected")
    app, collector = get("deployment", "lab-api"), get("deployment", "lab-collector")
    for deployment in (app, collector):
        if (any(deployment["metadata"].get("labels", {}).get(k) != v for k, v in PURPOSE.items())
                or deployment["spec"]["replicas"] != 1 or deployment["status"].get("readyReplicas") != 1):
            raise ValueError("projection_lab_deployment_rejected")
    containers = collector["spec"]["template"]["spec"]["containers"]
    if (len(containers) != 1 or containers[0]["name"] != "collector" or containers[0]["image"] != expected_image
            or collector["status"].get("readyReplicas") != 1):
        raise ValueError("projection_collector_image_rejected")
    pods = get("pods", "-l=app=lab-collector")["items"]
    if len(pods) != 1:
        raise ValueError("projection_collector_pod_rejected")
    pod = pods[0]
    owners = [x for x in pod["metadata"].get("ownerReferences", []) if x.get("controller") is True]
    if len(owners) != 1 or owners[0].get("kind") != "ReplicaSet":
        raise ValueError("projection_collector_owner_rejected")
    rs = get("replicaset", owners[0]["name"])
    controllers = [x for x in rs["metadata"].get("ownerReferences", []) if x.get("controller") is True]
    if (rs["metadata"]["uid"] != owners[0]["uid"] or len(controllers) != 1
            or any(controllers[0].get(k) != v for k, v in {"kind": "Deployment", "name": "lab-collector",
                                                          "uid": collector["metadata"]["uid"]}.items())):
        raise ValueError("projection_collector_owner_rejected")
    statuses = pod["status"].get("containerStatuses", [])
    if len(statuses) != 1 or not statuses[0]["ready"] or not statuses[0]["imageID"].endswith(expected_image.split("@", 1)[1]):
        raise ValueError("projection_collector_digest_rejected")
    kube = read(["kubectl", "--context", CONTEXT, "config", "view", "--minify", "--raw", "-o", "json"])
    conn = kube["clusters"][0]["cluster"]
    if conn.get("server") != cluster["endpoint"] or conn.get("certificate-authority-data") != cluster["certificateAuthority"]["data"]:
        raise ValueError("projection_cluster_connection_rejected")
    return {"cluster_fingerprint": sha256_json({"arn": cluster["arn"], "endpoint": cluster["endpoint"],
                        "ca": cluster["certificateAuthority"]["data"]}),
            "deployment_uid": app["metadata"]["uid"], "deployment_generation": app["metadata"]["generation"],
            "collector_uid": collector["metadata"]["uid"], "collector_generation": collector["metadata"]["generation"],
            "pod_uid": pod["metadata"]["uid"],
            "pod_name": pod["metadata"]["name"]}


def exporter_program(module_source, identity):
    """새 exporter는 메모리에서만 실행한다. 컨테이너 파일·설정·이미지를 변경하지 않는다."""
    return ("import json,sys,types\ntry:\n"
        " m=types.ModuleType('ai_ops.model_projection');sys.modules[m.__name__]=m\n"
        " exec(compile(" + repr(module_source) + ",'<approved-projection>','exec'),m.__dict__)\n"
        " from pathlib import Path\n from ai_ops.b_contracts import load_b_settings\n"
        " from ai_ops.incidents import IncidentStore\n from ai_ops.collection import CapturedSnapshot\n"
        " settings=load_b_settings(*(Path('/etc/ai-ops')/n for n in ('bindings.json','runtime.json','metric-queries.json')))\n"
        " identity=m.SourceIdentity.model_validate_json(" + repr(json.dumps(identity)) + ")\n"
        " record=IncidentStore(Path('/data/incidents')).show(identity.incident_id,identity.run_id)\n"
        " if record.collection_audit is None: raise ValueError('snapshot required')\n"
        " result=m.project_snapshot(CapturedSnapshot(record.bundle,record.collection_audit),settings,identity)\n"
        " print(result.model_dump_json())\n"
        "except Exception:\n print('projection_export_rejected');sys.exit(1)\n")


def exporter_hash():
    """운영자가 검토한 exporter 두 파일의 고정 해시를 계산한다."""
    source = (ROOT / "src/ai_ops/model_projection.py").read_bytes()
    return sha256_json({"projection": hashlib.sha256(source).hexdigest(),
                       "exporter": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()})


def prepare_export(identity: SourceIdentity, expected_image, *, execute=command):
    if not re.fullmatch(r"716174522908\.dkr\.ecr\.ap-northeast-2\.amazonaws\.com/urlshortener-practice-ai-ops@sha256:[a-f0-9]{64}", expected_image):
        raise ValueError("projection_export_image_rejected")
    source = (ROOT / "src/ai_ops/model_projection.py").read_text()
    code_hash = exporter_hash()
    if identity.exporter_sha256 != code_hash:
        raise ValueError("projection_export_code_rejected")
    before = current_identity(expected_image, execute=execute)
    if identity.cluster_fingerprint != before["cluster_fingerprint"] or identity.deployment_uid != before["deployment_uid"]:
        raise ValueError("projection_export_identity_rejected")
    raw = execute(["kubectl", "--context", CONTEXT, "-n", "ai-ops-lab", "exec", "-i", before["pod_name"],
                   "-c", "collector", "--", "python", "-"],
                  data=exporter_program(source, identity.model_dump(mode="json")).encode(), limit=256*1024)
    result = parse_projection(raw)
    if result.identity != identity or current_identity(expected_image, execute=execute) != before:
        raise ValueError("projection_export_changed_during_read")
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description="저장된 실습 조사 한 건의 정제 입력만 반출; API 호출 없음")
    parser.add_argument("--identity", type=Path, required=True, help="검토한 SourceIdentity JSON")
    parser.add_argument("--collector-image", required=True)
    parser.add_argument("--plan", type=Path, required=True)
    args = cli_paths(parser, parser.parse_args(argv), {"identity": "input", "plan": "input"})
    try:
        if args.identity.stat().st_size > 8192 or args.plan.stat().st_size > 8192:
            raise ValueError()
        identity = SourceIdentity.model_validate_json(args.identity.read_bytes())
        plan = CallPlan.model_validate_json(args.plan.read_bytes())
        result = prepare_export(identity, args.collector_image)
        identifier = ComparisonStore().prepare(result, plan)
        print(json.dumps({"id": identifier, "state": "prepared_or_existing", "external_model_calls": 0}))
        return 0
    except Exception:
        parser.exit(1, "정제 입력 반출 실패: 대상·출처·감사·코드·이미지·경로를 확인하세요.\n")


if __name__ == "__main__":
    raise SystemExit(main())
