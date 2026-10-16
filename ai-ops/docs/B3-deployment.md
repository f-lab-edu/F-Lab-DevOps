# B-03 첫 묶음 — 배포 진단 기반

2026-10-16 기준 **진행 중**이다. 독립 이미지 정의, 진단 모드 chart, 읽기 RBAC, 통신 정책, PVC와 수동 GitOps 정의를 작성했다. 후속 로컬 검증에서 Docker를 시작해 linux/amd64 이미지 실제 빌드·기본 실행까지 통과했다. 원격 CI 결과는 아래 Actions 링크에서 확인하며 실제 배포·권한/통신/PVC 재연결은 미검증이다. B-02의 실환경 대기와 A5/A7 미완료도 유지한다.

## 목적과 처리 순서

1. `Dockerfile`이 잠긴 의존성·Python 소스·검증된 런북·고정 지표 목록만 패키징한다. 앱과 같은 Python 3.11.16-slim digest를 사용하고 UID/GID 10001로 실행한다. 키·사이트·평가 결과·실제 자료는 포함하지 않는다. 빌드 문맥은 `ai-ops/`다.
2. chart는 기본 `enabled: false`라 리소스를 생성하지 않는다. 활성 렌더링에는 image digest, 정확한 대상·연결, DNS/Prometheus Pod label, API 도착 IPv4 /32, StorageClass가 필요하다. `values.validation.yaml`은 `.invalid` 주소·문서용 IP·가상 digest/StorageClass를 쓰는 **렌더링 전용** 자료이며 배포하지 않는다.
3. 활성 상태는 **diagnostics 모드의 StatefulSet 한 Pod·한 컨테이너·한 replica**다. `python -m ai_ops.deployment_check --serve`는 네트워크나 AI를 호출하지 않고 로컬 검사 뒤 대기한다. startup/readiness도 같은 로컬 검사다. 수신기·worker·queue가 동작하거나 서비스가 정상이라고 표시하는 health endpoint가 아니다. headless Service는 StatefulSet 식별용이며 외부 수신 포트는 없다.
4. 로컬 검사는 B 설정·연결 참조·CA 파싱·token 파일 가독성·세 런북 해시·저장소 쓰기와 임시 SQLite WAL commit/재읽기를 확인한다. 기존 사건·예산 장부를 열지 않으며 검사 DB는 제거한다. CA/token 원문·예외 메시지는 출력하지 않고 projected token은 매번 다시 읽는다. 임시 WAL 확인은 실제 큐 구현·재시작 복구 시험이 아니다.
5. 운영자가 별도로 승인·설정한 뒤 Pod 안의 기존 `collect-investigate --mode rules`를 수동 실행할 수 있다. 결과는 `/data/incidents`에 저장하고 같은 경로의 `show`로 조회한다. 자동 조사·외부 AI 전송은 활성화하지 않는다.

## 파일별 역할

| 파일 | 역할 |
|---|---|
| [Dockerfile](../Dockerfile), [.dockerignore](../.dockerignore) | 고정 베이스·해시 잠금 의존성·최소 빌드 문맥·non-root 실행 |
| [chart/](../chart/), [values.yaml](../chart/values.yaml), [values.schema.json](../chart/values.schema.json) | 기본 비활성, diagnostics/한 replica, 설정 계약·필수 값 검사 |
| [statefulset.yaml](../chart/templates/statefulset.yaml) | 보안 실행·읽기 전용 설정·projected token/CA·임시 공간·5Gi PVC |
| [rbac.yaml](../chart/templates/rbac.yaml) | 대상 namespace와 Argo Application의 읽기 Role/RoleBinding |
| [networkpolicy.yaml](../chart/templates/networkpolicy.yaml) | 수신 전체 차단과 DNS/API/Prometheus egress만 허용 |
| [deployment_check.py](../src/ai_ops/deployment_check.py) | 로컬 설정·런북·임시 WAL 검사·SIGTERM 종료 |
| [check_deployment.py](../scripts/check_deployment.py) | Helm 결과·계약 복사본·정확한 RBAC·통신·보안/PVC·GitOps 경계 검사 |
| [전용 AppProject](../../cluster-addons/ai-ops/project.yaml), [Application](../../cluster-addons/ai-ops/application.yaml) | 리소스 종류·namespace 소유 범위, 수동/비활성 동기화 정의 |
| [AI 전용 CI](../../.github/workflows/ai-ops-ci.yml) | Python+Helm 정적 검사 뒤 이미지 빌드·통신 없는 읽기 전용 실행. push/PR 후 확인 필요 |

`values.schema.json`의 B 설정 정의는 현재 `BindingRegistry`·`BRuntime`의 JSON Schema를 포함한다. Python 계약 변경 때 해당 정의와 `$defs`도 갱신해야 하며 정적 검사가 서로 다른 복사본을 거부한다. chart의 `files/metric-queries.json`도 원본 catalog와 일치해야 한다. 처음에는 Kubernetes의 지정 API URL·projected CA/token과 내부 무인증 HTTP Prometheus만 지원한다. HTTPS/별도 인증 Prometheus는 마운트·권한·네트워크 설계를 보완한 뒤 사용한다.

## 권한·통신·저장 범위

- ServiceAccount와 Pod 모두 자동 token 마운트를 끄고 진단 컨테이너에만 1시간 projected token과 `kube-root-ca.crt`를 마운트한다. root filesystem은 읽기 전용, capability 전부 제거, privilege escalation 금지, RuntimeDefault seccomp를 적용한다. [Kubernetes ServiceAccount 안내](https://kubernetes.io/docs/concepts/security/service-accounts/)
- 앱 namespace에서는 지정 Deployment·Service의 get, Pod·ReplicaSet·이벤트 get/list와 Pod 로그 get만 허용한다. Argo namespace에서는 지정 Application get만 허용한다. Secret·exec/attach/portforward·쓰기·삭제·cluster 권한은 주지 않는다. 동적 Pod 목록은 namespace 범위이므로 **RBAC만으로 다른 앱의 읽기를 label 단위 격리한 것은 아니다.** B-02 수집기의 selector·소유 UID 검사를 함께 사용한다.
- Pod 수신은 모두 차단한다. 송신은 지정 DNS Pod의 UDP/TCP 53, 확인한 API 도착 IPv4 /32의 TCP 443, 지정 namespace+Pod label의 Prometheus 포트만 허용한다. FQDN 기반 NetworkPolicy나 인터넷 전체 허용을 사용하지 않는다. 모델 proxy와 ingress는 아직 없고 AI 외부 송신도 허용하지 않는다. 다른 허용 정책과 CNI·주소 변환의 실제 영향은 실환경 거부 시험이 필요하다. [Kubernetes NetworkPolicy 안내](https://kubernetes.io/docs/concepts/services-networking/network-policies/)
- PVC는 ReadWriteOnce, 기본 5Gi, 명시 StorageClass이며 삭제·scale 시 Retain이다. `fsGroup=10001`로 데이터 영역에 접근하고 사건 디렉터리는 0700이다. SQLite WAL은 같은 노드 로컬 파일시스템을 전제로 한다. 한 Pod/PVC는 고가용성이 아니며 CSI provisioner·암호화·WaitForFirstConsumer·AZ·detach/attach·재연결을 확인해야 한다. 영속 디스크에서의 임시 WAL 검사는 보관/삭제 정책을 대신하지 않는다.
- 전용 Argo 프로젝트는 명시한 세 namespace와 여덟 리소스 종류만 허용하며 cluster 리소스는 허용하지 않는다. Application의 source revision은 미확정 placeholder, chart는 비활성, 자동 sync/CreateNamespace도 없다. 실제 source SHA·image digest·설정을 검토하고 namespace를 별도 준비해야 한다. 이 정의는 아직 설치하지 않았다.

## 서버 없이 확인하기

Python 3.11+와 고정 의존성, Helm, Ruby의 표준 `json`·`yaml` 모듈이 필요하다. Ruby는 개발용 manifest 읽기에만 쓰며 실행 이미지 의존성이 아니다. 검증 환경은 Python 3.12.14/Pydantic 2.13.5, Helm 4.1.3이다.

```sh
PYTHONPATH=ai-ops/src python3 ai-ops/scripts/check_deployment.py
PYTHONPATH=ai-ops/src python3 -m unittest discover -s ai-ops/tests -p 'test_*.py' -v
# Docker 데몬을 사용할 수 있는 환경에서 실행한다. push·배포는 하지 않는다.
docker build --tag ai-ops:local ai-ops
docker run --rm --network none --read-only --cap-drop ALL \
  --security-opt no-new-privileges --tmpfs /tmp:rw,noexec,nosuid,size=64m ai-ops:local --help
```

로컬에서 전체 **130건**을 통과했다(새 진단 검사 5건 포함). 기본/검증용 Helm lint·template, 비활성 리소스 0개, 활성 리소스 10개, 잘못된 설정 10종 거부, 권한 확대·replica 증가·통신 대상 확대 변조 3종 검출을 확인했다. YAML 구조와 프로젝트의 보안 조건을 검사하는 것이며 Kubernetes API 서버의 schema/admission 검증이나 실제 정책 집행 검증은 아니다. 기존 합성 자료/계약 검사는 CI에서 유지한다. 첫 구현 때는 Docker 데몬이 꺼져 빌드하지 못했으나 후속 로컬 검증에서 실제 빌드·실행을 통과했다. 아래 후속 기록과 Actions 결과를 구분해 확인한다.

## 실제 환경이 준비된 뒤의 수동 확인

먼저 별도 신뢰 values에 실제 대상·연결·Pod label·API 도착 IP·StorageClass·image digest를 채우고 검토한다. ConfigMap에는 설정과 인증 파일 경로만 넣으며 실제 token·키를 넣지 않는다. Kubernetes API의 schema/admission 검사를 수행하고, namespace·기존 리소스 소유·CNI·CSI 조건을 확인한 뒤에만 배포한다. Argo는 chart가 포함된 검토 커밋의 전체 SHA를 지정해야 한다. 검증용 `values.validation.yaml`을 운영 values로 복사해 바로 적용하지 않는다.

배포 뒤 Pod의 로컬 준비 검사와 실제 API 읽기는 별도 실행이다. 다음 명령은 **미실행 사용 예시**다. exec는 운영자 자격증명으로 수행하며 조사 ServiceAccount에는 exec 권한을 주지 않는다.

```sh
# 설정·런북·로컬 저장만 검사한다. API 연결 성공을 뜻하지 않는다.
kubectl -n ai-ops exec ai-ops-0 -c diagnostics -- python -m ai_ops.deployment_check
# 실제 읽기는 환경·권한·데이터 수집 범위를 확인한 뒤 수동 요청한다.
kubectl -n ai-ops exec ai-ops-0 -c diagnostics -- python -m ai_ops collect-investigate \
  --bindings /etc/ai-ops/bindings.json --runtime /etc/ai-ops/runtime.json \
  --metric-queries /etc/ai-ops/metric-queries.json --target url-shortener-practice \
  --store /data/incidents --mode rules --request-id practice-snapshot-001 \
  --question '수집 시점의 장애 징후와 부족한 근거를 정리해줘'
```

새 수집마다 요청 ID를 바꾼다. 출력된 사건·실행 ID를 동일 store의 `show`에 지정한다. 성공 보고서뿐 아니라 수집 partial·권한 거부·누락 사유·UTC/KST도 확인한다. 실제 ServiceAccount의 허용 get/log와 Secret·exec·쓰기·다른 namespace 거부, DNS/API/Prometheus 이외 통신 거부를 각각 시험한다. PVC 재연결은 실제 저장한 정제 보고서가 Pod 재생성 뒤에도 같은 해시로 조회되는지 별도 확인한다. 이 절차를 실행한 기록은 아직 없다.

## 다음 B-03 묶음과 실환경 확인

독립 ECR·수동 이미지 digest 등록 경로와 CI 실패 검증 직후/롤백 전·후 정제된 배포 관측 artifact 보존 코드를 두 번째 묶음에서 구현했다. [이미지·관측 보존 안내](B3-image-and-observations.md)에 현재 검증과 미검증 범위를 기록했다. 실제 AWS 적용·등록·artifact 원격 보존은 실행 전이다. 기존 앱 CI의 실패·롤백 동작을 유지한다. 이후 실제 실습 대상·CA/token·metric label/scrape 주기·API 도착 IP·CNI·StorageClass를 확인하고 이미지 빌드/Pod 배포, 허용 읽기·Secret/exec/쓰기/다른 namespace/인터넷 접근 거부, token 교체·PVC 재연결을 검증한다. 모델 proxy·TLS 수신·자체 metrics·수신기/worker·queue는 아직 연결하지 않았고 차례대로 구현한다. 비용을 발생시키는 인프라 생성·원격 배포·경보 활성화는 이번 작업에서 실행하지 않았다.

## 후속 로컬 이미지 검증 — 2026-10-16

Docker Desktop 29.2.1에서 `docker build --platform linux/amd64 --tag ai-ops:b03-local ai-ops`를 실행해 통과했다. 네트워크 차단·read-only rootfs·capability 제거·no-new-privileges·64MiB 임시 쓰기 영역을 적용한 컨테이너에서 CLI, Python 3.11.16/Pydantic 2.13.5, UID 10001, 런북 세 개의 해시를 확인했다.

합성 S1 입력을 read-only로 마운트해 규칙 조사→보고서 저장→재조회·KST 표시와 모의 연결값의 로컬 배포 진단/SQLite WAL도 통과했다. API·AI·AWS는 호출하지 않았다. 첫 추가 확인 명령은 Markdown 함수 인자를 잘못 지정해 실패했으나 검증 명령을 고친 재실행은 통과했고 제품 코드는 수정하지 않았다.

로컬 이미지 config ID는 `sha256:422d949300ea4c84de24ac6b6b09dd714558900009466b3de5d359d3cb0a15e6`이다. ECR registry digest나 배포 완료를 뜻하지 않는다. 전체 Python 142건과 Helm 정적 검사도 같은 작업 트리에서 통과했다. 원격 실행 결과는 [현재 작업 브랜치 AI CI](https://github.com/f-lab-edu/F-Lab-DevOps/actions/workflows/ai-ops-ci.yml?query=branch%3Acodex%2Fai-incident-copilot)에서 확인한다. 독립 이미지 등록 workflow·AWS 적용·ECR push·실환경 artifact 보존은 이번 검증에 포함하지 않는다.
