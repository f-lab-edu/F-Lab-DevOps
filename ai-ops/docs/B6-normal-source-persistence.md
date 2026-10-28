# B-06 정상 CI 원본 보관과 모델 보고서 연결

기준: 2026-10-27 KST. 정상 관측 CI의 원본 로그·대응표·규칙 보고서는 `ai-ops-lab/lab-collector`의 `/data/incidents`에 둔다. 이 문서는 해당 폴더를 실습 전용 암호화 EBS PVC로 보관하고, 검증된 정제 영수증을 승인된 v3 모델 캠페인의 보고서 경로에 연결하는 절차다.

## 자료 흐름과 경계

1. 실습 CI가 생성한 아이템·로그는 수집기 안에서만 대조한다. 수집기는 원본 세션과 규칙 보고서를 전용 PVC에 0700/0600 권한으로 보관한다. GitHub artifact는 정제 영수증 한 파일만 가진다.
2. 전달기는 성공한 정상 CI 실행과 artifact를 GitHub에서 검증하고, worker의 별도 영수증 표에 넣는다. worker는 HMAC·현재 Pod binding·설정 해시를 대조해 규칙 요약을 만든다.
3. 모델 연결의 기본값은 꺼짐이다. 새 코드·이미지·정제 view·목적지·예산·기간을 묶은 **새 v3 캠페인**을 검토·승인하고 gateway에 활성화한 뒤에만 worker가 정제 view를 제출한다. 캠페인 승인 시각보다 앞선 CI 관측은 제출하지 않는다.
4. gateway는 기존 서명·캠페인·정책·예산 검사를 거쳐 모델 보고서를 별도 장부에 남긴다. worker는 전송 전에 시도를 기록하고 응답이 불명확하면 `needs_review`로 보존한다. 같은 영수증을 자동 재전송하지 않는다.

정제 영수증의 `rules_ref`는 PVC에 보존된 원래 규칙 보고서 경로다. worker의 `normal_ci_rules_summary`는 정제 view로 만든 별도 요약이며 원래 보고서를 대체하지 않는다. gateway에는 원본 로그·아이템 ID·원본 대응표를 전달하지 않는다. 영속 PVC는 **Pod 재생성 경계**를 넘지만, 인프라 정리 시 PVC/EBS까지 삭제하면 자료도 삭제된다. 재설치 후 과거 영수증을 현재 Pod의 증거로 재사용하지 않는다.

## 기존 `emptyDir`에서 이관

기존 수집기에는 원본 세션이 있으므로 Deployment를 먼저 교체하면 자료가 사라진다. 관리자만 현재 대상·Pod UID·기존 `/data/incidents`의 파일 수/경로/해시를 확인하고, 실습 전용 `lab-collector-source` PVC를 생성해 `Bound`를 확인한다. 같은 namespace의 임시 이관 Pod는 현재 검증된 수집기 digest, 비활성 ServiceAccount, 토큰 마운트 차단, 제한된 UID·읽기 전용 루트로 띄우고 **새 PVC만** `/data`에 붙인다. 기존 Pod에서 새 Pod로 tar 스트림을 직접 전달해 로컬 파일·GitHub artifact·명령 출력에 원본을 남기지 않는다. 이관 Pod에서 전체 파일 목록·내용 해시·0700/0600 권한을 대조한다. 하나라도 다르면 기존 Pod를 유지하고 전환을 중단한다.

검증 후 실습 quota를 PVC 1개로 바꾸고 `lab-collector`의 data 볼륨만 해당 PVC로 교체한다. **암호화 EBS 첫 마운트가 기존 파일의 그룹 권한을 넓힐 수 있으므로** 새 Deployment에는 `fsGroupChangePolicy=OnRootMismatch`를 설정한다. 이관 Pod에서 파일 수·내용·권한을 포함한 해시를 맞춘 뒤 이관 Pod를 제거하고 수집기를 전환한다. 새 Pod에서 원본 폴더 0700·파일 0600을 확인하고, 마운트가 권한을 넓혔다면 소유자·항목 종류를 검사한 뒤 해당 권한을 복구한다. 원본 세션과 규칙 보고서를 다시 읽어 검증한 뒤에만 성공으로 기록한다. 새 Pod UID와 실행 코드/설정 해시로 정상 CI binding을 다시 만들고, 현재 이름으로 제한한 RBAC·GitHub 환경의 `NORMAL_CI_BINDING`·`ai-ci/ci-config`를 함께 갱신한다. relay와 worker는 새 ConfigMap을 읽도록 순서대로 재시작한다. 기존 CI 실행은 옛 Pod의 자료로서 보존하지만 새 binding의 성공 사례로 재접수하지 않는다.

실습 원본의 보관 기한과 용량은 별도 정리 정책으로 관리한다. PVC 1Gi가 가득 차면 수집을 실패로 처리해야 하며, 기존 원본을 임의로 삭제하거나 정제 영수증만으로 복구했다고 표시하지 않는다. 설치·삭제 절차에서는 이 PVC의 생성과 삭제 및 EBS 잔존 여부를 확인한다. PVC 삭제 전에 원본 보존 필요성을 확인한다.

## 2026-10-27 실제 이관 결과

- [PR #96](https://github.com/f-lab-edu/F-Lab-DevOps/pull/96)의 전체 AI 시험 522개와 이미지 검사가 통과했다. [이미지 등록 실행](https://github.com/f-lab-edu/F-Lab-DevOps/actions/runs/40477224644)은 성공했고, `sha256:e56d8f44607bb57b558a5b0a4dc9c5a3f4dd5c32f0aeefdb8c9e674e1022e6bc`의 ECR 검사는 `COMPLETE`·발견 항목 0개였다.
- 기존 수집기의 파일 16개·156,822바이트를 로컬 파일 없이 tar 스트림으로 새 PVC에 옮겼다. 이관 Pod에서 내용·경로·권한을 포함한 결합 SHA-256 `026fc40917cc374fd9fb56b03a25a4289884d240d3c2ebcb98b1aa169628083f`가 기존 Pod와 같았다. 새 `lab-collector-source` 1Gi EBS는 gp3·암호화 true·Bound로 확인했다.
- 첫 EBS 마운트는 파일을 0660, 폴더를 2770으로 넓혀 **권한 포함 결합 해시를 바꿨다**. 따라서 최초 해시를 그대로 보존했다고 주장하지 않는다. 소유자와 파일 종류를 검사한 뒤 0600/0700으로 복구하고 `fsGroupChangePolicy=OnRootMismatch`를 적용했다. 다시 Pod를 재생성해도 내용 기준 트리 SHA-256 `3de8cb25138a5c84c447fb2fa9f5ae54f39e4072665d45155fab45704ea8ba78`와 0600/0700 권한이 유지됐다. 새 Pod에서 기존 원본 세션 2건·규칙 보고서 2건을 파싱·조회했다.
- 최종 Pod UID와 설정·실행 코드 해시로 정상 CI binding을 다시 만들고 전용 RBAC, GitHub 환경 변수, `ai-ci/ci-config`를 갱신했다. worker와 relay는 새 이미지로 Ready 1/1이며 기존 `ci-queue` PVC·GitHub 토큰 Secret은 동일한 식별자를 유지했다. 모델 연결 환경 변수는 설정하지 않았다.
- [새 정상 관측 CI #40479470948](https://github.com/f-lab-edu/F-Lab-DevOps/actions/runs/40479470948)이 성공했다. 영속 PVC에서 원본 세션 3건·규칙 보고서 3건을 읽어 검증했고 모든 파일은 0600, 폴더는 0700이었다. 전달기의 주기 조회가 새 실행을 자동 접수해 worker에 `normal_ci_rules_summary` 상태 `ready`를 남겼다. `external_model_calls=0`이다.
- 이 실측은 **원본의 Pod 재생성 후 보관과 규칙 요약 자동 접수**까지다. v3 모델 제출 코드·모의 시험은 통과했지만 실제 캠페인 활성화·유료 모델 보고서 생성은 수행하지 않았다. 열려 있던 과거 검토 화면은 새 이미지·Pod·binding과 일치하지 않으므로 승인에 사용할 수 없다.

## 모델 활성화 조건

- 수집기 PVC와 새 binding을 실제 배포에서 확인하고, 정상 CI 한 건이 새 Pod에서 성공해야 한다.
- worker의 `AI_NORMAL_MODEL_BRIDGE_ENABLED` 기본값은 `false`다. 기존 열려 있던 검토 화면이나 과거 캠페인 승인을 재사용하지 않는다.
- 검토한 정제 입력, OpenAI 데이터 공유 설정, 24시간/건수/건당·일일 예약액, 저장·보관 조건을 담은 새 검토안을 사용자에게 제시해 별도 승인을 받는다.
- 승인 전에는 gateway·proxy를 켜거나 API 키를 클러스터에 넣지 않는다. 승인 후에도 정상 사례 한 건만 검증하고 보고서 상태·중복·예산·중지·만료를 확인한다.

다음 작업 권장 추론 깊이: **GPT-6 SOL high**.

## 2026-10-28 v3 첫 활성화에서 발견한 설정 경계

사용자가 정상 관측 v3 캠페인 `campaign-e26beec99cd548ffb17dab4cfc0bde73`을 승인했다. 검토한 정상 CI 영수증은 수집기 `ai-ops-lab/lab-collector` 설정 해시 `329843b3...`에 묶여 있었다. 그러나 gateway의 기존 기동 검사는 실패·원복 CI가 사용하는 `ai-ci/ci-config` 해시 `0e4e94df...`와 대조해 `model_probe_config_changed`로 기동을 거부했다. 두 설정은 Argo 대상 네임스페이스가 달라 의도적으로 별개다.

이 캠페인의 새 영수증 제출·토큰 계산·모델 생성은 **0건**이었다. worker의 v3 연결은 켜지 않았다. 캠페인을 개인·클러스터 장부에서 `stopped`로 기록하고 gateway·proxy를 0으로 내린 뒤, 클러스터의 모델 API 키·서비스 토큰 Secret을 제거했다. 기존 장부의 보고서 1건은 유지했다. 이전 승인은 새 이미지나 수정된 입력 계약에 재사용하지 않는다.

후속 수정은 v1/v2가 기존 `ci-config`를 검사하고 v3는 `lab-collector` 설정과 `ci-normal-binding`을 함께 검사하도록 한다. gateway의 GET 권한도 각 ConfigMap 이름으로 한정한다. 수정 이미지 배포 후 새 v3 검토안·별도 승인을 받아 한 건 실측해야 한다.

## 2026-10-28 v3 재시도에서 확인한 Pod 고정 경계

[PR #101](https://github.com/f-lab-edu/F-Lab-DevOps/pull/101)의 설정 경계 수정과 새 이미지 `sha256:9800fe8c…f63b0b`를 적용했다. 사용자가 모델 gateway의 `ai-ops-lab/lab-collector`, `ai-ci/ci-normal-binding` ConfigMap에 대한 `get` 권한과 별도 캠페인 `campaign-31ab324bfc8143fdb7bfa43703b39588`을 승인했다. gateway·proxy와 worker는 기동했으나, [정상 CI #40639505092](https://github.com/f-lab-edu/F-Lab-DevOps/actions/runs/40639505092)는 승인 전에 고정한 `lab-api` Pod가 이미 교체돼 실행 전 거부됐다. 이 캠페인의 새 영수증·모델 전송·예산 예약은 모두 0건이었다. 개인·클러스터 장부에서 캠페인을 중지하고 모델 관련 클러스터 Secret을 제거했으며 gateway·proxy를 0으로 내렸다.

현재 Pod로 `ci-normal-binding` ConfigMap과 GitHub `ai-ops-normal-ci` 환경의 `NORMAL_CI_BINDING`을 갱신했지만, 첫 [재검증 CI #40640261282](https://github.com/f-lab-edu/F-Lab-DevOps/actions/runs/40640261282)는 잠금 취득 뒤 테스트 앱 명령을 실행하지 못했다. CI 결과는 `recovery_unverified`, `external_model_calls=0`이었다. 원인은 `b6-normal-coordinator` Role의 `pods` 조회·`pods/exec` 생성 대상 이름이 여전히 교체 전 Pod에 고정돼 있었기 때문이다. 관리자가 해당 실행의 자기 아이템 파일 부재·DB 행 0개·Redis 응답·앱 준비 상태를 확인하고 동일 cleanup 명령과 기준선 검사를 다시 성공시킨 뒤, 일치하는 잠금 소유자만 해제했다. Role의 두 이름 제한은 현재 Pod로 교체하고 이전 Pod의 exec 권한이 제거됐음을 확인했다. 공유 잠금의 5분 관측 창이 끝난 뒤 정상 CI를 다시 실행해야 한다.

Pod 재생성 후에는 **binding ConfigMap, GitHub 환경 변수, 정상 CI Role의 `pods`·`pods/exec` 대상 이름을 함께 갱신**한다. 세 곳 중 하나라도 이전 Pod를 가리키면 새 실습 CI를 시작하지 않는다. 기존 캠페인의 정확한 결합 해시는 새 binding에 재사용하지 않는다.

5분 관측 창 이후 [정상 CI #40641183042](https://github.com/f-lab-edu/F-Lab-DevOps/actions/runs/40641183042)가 성공했다. 정제 영수증의 binding 해시는 현재 Pod·ConfigMap과 일치하고 `owned_data_removed`, `baseline_unchanged`, `lock_released`가 모두 참이며 모델 호출은 0건이다. 처음에는 전달기·worker가 ConfigMap 갱신 전 binding을 메모리에 유지해 영수증을 거부했다. 두 프로세스를 공유 PVC 사용 순서에 맞춰 재시작한 뒤 worker의 정상 영수증 상태가 `ready`로 자동 접수됐다. 새 샘플로 `campaign-683ca19c2a7341498afa306518de89db`의 비활성 검토안을 준비했으며, 별도 승인 전에는 API 키를 클러스터에 재설치하거나 gateway·proxy를 켜지 않는다.

## 2026-10-28 v3 승인 캠페인 한 건 실측

사용자가 `campaign-683ca19c2a7341498afa306518de89db`(결합 SHA-256 `52d35bce593ab0f61466eb545674ed83c20b38f4802441fb16598eb3bfdc463d`)과 API 선택적 데이터 공유 꺼짐을 확인했다. 승인 기록을 기존 암호화 장부에 추가할 때 첫 입력 사본이 32KiB로 잘려 SQLite 검사가 실패했으므로 병합하지 않았다. `kubectl cp`로 다시 전달한 57,344바이트 파일의 SHA-256과 SQLite 무결성이 원본과 일치함을 확인한 뒤 캠페인·승인 기록 두 행만 추가했다. 기존 보고서 1건은 유지됐다.

[승인 이후 정상 CI #40643171148](https://github.com/f-lab-edu/F-Lab-DevOps/actions/runs/40643171148)가 성공했다. 새 영수증은 `ready`로 자동 접수됐고 모델 전달 기록은 한 건 `submitted`다. gateway 장부에는 해당 캠페인의 outbox 한 건이 `needs_review`·`semantic_review_required`로 저장됐다. 보고서의 `input_hash`는 이 CI 영수증의 `view_hash`와 같고, 출처 실행 ID·보고서 형식 계약·테스트 데이터 원복 세 조건을 다시 확인했다. 보고서는 `analysis_status=partial`, `diagnosis_status=insufficient_evidence`이며 관측 사실 3개·원인 가설 0개·제안 조치 1개를 담는다. 모든 조치는 `not_executed`이고 실제 변경은 없었다. 독립적인 자연어 의미 평가는 사용자가 앞서 건너뛰기로 했으므로 완료로 표시하지 않는다.

이 한 건의 사용량은 입력 1,949토큰·출력 406토큰·캐시 쓰기 1,946토큰, 추정 비용 **$0.008931**이다. 장부는 이 캠페인에 $0.10을 예약해 UTC 해당일 예약 합계가 이전 건 포함 $0.20이 됐다. 한 건 검증 직후 개인·클러스터 장부의 캠페인을 `stopped`로 바꾸고 worker의 모델 연결·토큰 마운트를 제거했다. gateway·proxy는 복제 수 0, 클러스터의 모델 API 키·서비스 토큰 Secret은 삭제됐다. 일반 CI worker·전달기는 각각 Ready 1/1로 복구했다. 암호화 PVC의 보고서와 개인 보관 결과는 유지한다.
