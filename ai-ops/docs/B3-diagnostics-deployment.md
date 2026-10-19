# B-03 실제 진단 StatefulSet·PVC 배포 검증

최신 자체 관측 상태: 2026-10-19 19:28:15 KST 자체 관측·중지/복구 실환경 검증 완료. PR #36·main e1b30b5의 새 이미지 등록 성공, ECR 검사 전체 0건과 코드 해시 일치를 확인했다. Helm revision 4에서 Prometheus up=1·진단 갱신·수동 수집/보고서 partial 각 1회 증가를 확인했다. AI StatefulSet 0→1 복구 후 동일 PVC·기존/신규 보고서 재조회와 해시·집계 보존, 앱 UID/generation 불변·Ready 2개를 확인했다. 외부 모델은 호출하지 않았다. [관측·중지/복구 절차](B3-observability-and-recovery.md). 아래 이전 이미지·배포 내용은 해당 시점의 이력이다.

최신 상태: 2026-10-19 18:12 KST 실제 수집→규칙 부분 보고서→PVC 저장→JSON/KST 재조회 연결 검증 완료. PR #34·#35 수정이 포함된 main 5d432f5 이미지로 일반 경로를 실행했고 Pod 2개 상태·설정, 배포/Argo·캐시 지표 등 7개 항목을 수집했다. 총 18개 중 available 7·invalid 2·missing 7·stale 2로, 자료 부족은 partial/unknown/insufficient_evidence로 유지했다. 이전 Pod의 저장 보고서도 새 Pod에서 해시 변화 없이 재조회했다. 외부 AI·키·예산 경로는 호출하지 않았고 기존 앱 UID/generation·Ready 2개는 유지됐다. [실제 수집 검증](B2-live-validation.md). 아래 이전 이미지·배포 결과는 해당 시점의 이력이다.

2026-10-19 KST 실제 `ai-ops/ai-ops-0`과 5Gi 암호화 EBS를 배포했다. 대상 앱·Argo 읽기, Secret/다른 namespace 조회 거부, 쓰기·exec 권한 부재, 선택한 통신 경계와 노드 간 EBS 재연결·합성 자료 보존을 확인했다. **같은 Pod의 토큰 자동 교체·교체 후 API 200·최종 배치 원복과 재시작 후 자료 보존까지 완료했다.**

## 무엇을 구현·확인한 단계인가

앞 단계에서 준비한 이미지·권한·정책·저장소를 실제 클러스터에 올리는 작업이다.

1. 계정·클러스터·API 주소·CSI/CNI·StorageClass와 ECR 검사 0건인 digest를 다시 대조한다.
2. 전용 namespace에 restricted Pod 보안 정책을 적용하고 수동 Helm으로 진단 StatefulSet 한 개를 배포한다.
3. 로컬 설정·런북·CA/토큰 가독성·SQLite WAL 검사로 Ready 상태가 되는지 확인한다.
4. 해당 Pod의 토큰으로 고정 대상 읽기와 권한 거부를 검사한다. 앱 쓰기는 실제 실행하지 않고 SelfSubjectAccessReview로 판정한다.
5. 허용된 API·DNS·Prometheus와 차단돼야 할 내부/외부 목적지를 실제 통신으로 대조한다.
6. 합성 SQLite 표식을 PVC에 저장한 뒤 AI Pod만 같은 AZ의 다른 노드로 이동해 같은 EBS의 재연결과 자료 재조회를 확인한다.
7. 같은 Pod 안에서 projected token 파일의 자동 교체와 새 토큰 인증을 관찰한 뒤 임시 배치 설정을 제거한다.

진단 프로세스는 `ai_ops.deployment_check --serve`다. 준비 검사 후 대기하며 경보 수신기·worker·자동 수집·실자료 외부 AI 분석을 시작하지 않는다. Ready는 로컬 준비 상태다. 이 문서의 실제 API·권한·통신 검사는 운영자가 별도 실행한 결과다.

## 배포 구성과 관리 방법

| 항목 | 적용값 |
|---|---|
| 계정·리전·클러스터 | `716174522908` / `ap-northeast-2` / `urlshortener` |
| 방법·namespace·release | 수동 Helm / `ai-ops` / `ai-ops` |
| StatefulSet·Pod | `ai-ops` / `ai-ops-0`, replica 1 |
| 이미지 | `urlshortener-practice-ai-ops@sha256:bbfa1859831b5811d4bfe3057adf25141ed1c66b38c9165d235952059ef286be` |
| 이미지 소스 | main `b6548b82e588b09efee925a2c663ee71d3b91f1d` |
| 설정 | [기본 비활성 입력](../config/b3-values.preflight.yaml) + [명시적 진단 활성 입력](../config/b3-values.diagnostics.yaml) |
| namespace 원본 | [namespace.yaml](../kubernetes/namespace.yaml), restricted `v1.35` |
| projected token | 이 배포는 600초, chart 기본 3600초; schema는 600~3600만 허용 |
| 실행 경계 | UID/GID 10001, rootfs read-only, 모든 capability 제거, privilege escalation 금지 |
| 모델 | `llm_enabled=false`, 외부 모델 키 미주입 |

chart의 토큰 수명 입력과 경계 검사는 이번 로컬 변경이며 이미지 패키지는 바꾸지 않았다. 운영자 검증 스크립트도 AI 도구에 연결하지 않았다. Argo Application은 기존 비활성 placeholder 상태를 유지하고 적용하지 않아 수동 Helm과 소유권이 겹치지 않는다.

재적용 전 실제 대상을 확인하고 다음 두 values를 함께 사용한다. base만 사용하면 `enabled=false`여서 활성 배포 리소스를 제거하는 변경이 될 수 있으므로, 기존 release를 단순 중지하려고 base만 upgrade하지 않는다.

```bash
kubectl config current-context
kubectl apply --dry-run=server -f ai-ops/kubernetes/namespace.yaml
kubectl apply -f ai-ops/kubernetes/namespace.yaml
helm template ai-ops ai-ops/chart --namespace ai-ops \
  --values ai-ops/config/b3-values.preflight.yaml \
  --values ai-ops/config/b3-values.diagnostics.yaml
helm upgrade --install ai-ops ai-ops/chart --namespace ai-ops \
  --values ai-ops/config/b3-values.preflight.yaml \
  --values ai-ops/config/b3-values.diagnostics.yaml --wait --timeout 180s
kubectl -n ai-ops get statefulset,pod,pvc
```

## 실제 읽기·거부 결과

- 지정 Deployment·Service·Argo Application GET: `200`.
- 대상 namespace Secret 목록·다른 namespace Pod 목록 GET: `403`. 응답 본문은 저장하지 않았다.
- 자신의 권한 질의: 대상 Pod 목록/로그 읽기 허용, Deployment patch/delete·Pod create/exec·자신의 namespace Secret·다른 Deployment·클러스터 Node·Role update·추가 토큰 발급 거부.
- UID 10001과 실제 rootfs 쓰기 거부 확인.

쓰기/exec는 [SelfSubjectAccessReview](https://kubernetes.io/docs/reference/access-authn-authz/authorization/)로 검사했고 앱 데이터나 Deployment를 변경하는 요청은 보내지 않았다. 대상 namespace의 Pod·이벤트 목록과 로그는 RBAC만으로 label에 제한되지 않는다. 기존 수집기가 selector·소속 UID를 추가 검사하는 경계를 유지한다.

## 실제 통신 결과

실제 StatefulSet에서 DNS·API Service와 두 Endpoint의 TLS GET·Prometheus 준비 응답을 확인했다. 허용 API는 앞서 검증한 Service/Endpoint 세 `/32`다.

송신 차단은 별도 임시 namespace의 인증정보 없는 TCP 기준 서버와 비교했다. 기준 Pod에서는 다른 namespace Service/Pod와 외부 `1.1.1.1:443` 연결이 성공했고 AI Pod에서는 세 연결이 모두 timeout이었다. 외부 목적지에는 TCP 연결만 시도했고 업무 데이터·인증정보는 전송하지 않았다.

수신 차단은 AI Pod의 임시 8081 TCP 수신기에 대해 loopback 연결이 성공하지만 다른 노드의 기준 Pod 연결은 timeout인 것으로 대조했다. 검사 후 수신기 프로세스를 종료했다. 새 외부 Service·LoadBalancer를 만들지 않았다.

이 결과는 선택한 목적지와 시점의 실제 대조이며 모든 IP·노드·프로토콜에 대한 증명으로 확대하지 않는다. CNI는 여전히 standard 모드여서 Pod 시작 시 정책 반영 전 기본 허용 구간이 있다. 진단 프로세스는 그 구간에 자동 네트워크 조사를 시작하지 않는다.

## EBS·재시작·다른 노드 재연결

| 항목 | 실제 확인값 |
|---|---|
| PVC | `ai-ops/data-ai-ops-0` |
| StorageClass | `ai-ops-gp3-encrypted` |
| EBS | `vol-037d3068cc25c23ef` |
| 크기·종류·암호화 | 5Gi / gp3 / `Encrypted=true` |
| KMS | `arn:aws:kms:ap-northeast-2:716174522908:key/b14ad45b-3895-410f-956b-829d0bf72789` |
| AZ | `ap-northeast-2a` |
| 첫 노드 | `ip-10-0-10-42.ap-northeast-2.compute.internal` |
| 이동한 노드 | `ip-10-0-10-244.ap-northeast-2.compute.internal` |

운영 보고서와 분리한 `.b3-volume-<합성표식>.sqlite3`를 `/data/incidents`에 0600으로 저장했다. WAL·FULL 동기화 commit 후 Pod UID가 바뀌고 동일 EBS의 attachment가 다른 인스턴스로 이동한 것을 AWS에서 대조했다. 새 Pod에서 동일 표식·journal_mode WAL·integrity_check를 확인했다. 기존 앱 노드를 drain하거나 앱 Pod를 재시작하지 않았다.

단일 AZ의 ReadWriteOnce 볼륨이다. 이번 검증은 정상적인 Pod 교체와 같은 AZ의 다른 노드 연결이며 AZ 장애 복구·동시 다중 writer·고가용성·백업 복구를 검증한 것은 아니다. [StatefulSet 저장소·PVC 보존 동작](https://kubernetes.io/docs/concepts/workloads/controllers/statefulset/)을 참고한다.

## 토큰 자동 교체와 검사 근거

[검사 스크립트](../scripts/probe_deployed_diagnostics.py)는 매 요청마다 토큰 파일을 다시 읽고 TLS로 연결한다. 교체 관찰은 같은 Pod에서 파일 내용의 변경만 메모리로 비교하고 원문·해시·JWT claim을 출력하지 않는다. 2026-10-19 17:36:18 KST 같은 Pod UID를 유지한 채 파일 교체를 관찰했고 새 토큰 GET은 200이었다. 관찰 시간은 480초였다. 이후 임시 nodeSelector를 제거한 새 최종 Pod에서도 같은 PVC의 합성 표식·무결성·WAL을 재확인했다.

[검증 JSON](assets/B3-diagnostics-deployment-validation.json)에 Pod/PVC/PV UID, 이미지, UTC 시각, AWS attachment, 권한·통신·저장 검사와 정리 상태를 남긴다. 최종 결과는 모든 검사 통과다. 임시 기준 namespace·수신기를 제거했고 nodeSelector도 원래대로 복원했다. 앱 Ready 2개·기존 UID/generation 유지와 Helm deployed를 확인했다.

## 유지·정리와 남은 구현

진단 Pod와 PVC는 유지하며 준비 검사만 반복한다. StatefulSet 삭제/축소 시 PVC는 Retain이고 PVC를 명시 삭제하면 StorageClass의 Delete 정책으로 EBS가 제거된다. 중지·삭제 전에 보관할 자료와 실제 Volume ID를 확인한다. 설치/정리 가이드에서 Helm release·PVC/EBS·namespace 순서를 따른다.

다음은 실제 수집기의 제한된 운영 자료를 규칙 보고서로 저장·재조회하는 연결 검증이다. 이 배포만으로 B-03 전체, 자동 경보 접수·영속 큐·worker·모델 proxy·에이전트 자체 관측·실자료 AI·승인 후 조치가 완료된 것은 아니다. 다음 작업 추천 추론 깊이는 **GPT-6 SOL high**다.
