# WF-03 경보 공지·사건 참조 후보

이 폴더는 로컬 검토용이다. 기존 `../values.yaml`과 Application의 valueFiles는 변경하지 않는다. `prepare.py`는 기존 설정을 검증한 뒤 후보JSON(YAML 호환)을 새 파일로 쓴다. Helm/kubectl/Slack을 실행하지 않는다.

```sh
<venv>/bin/python cluster-addons/prometheus/wf03/prepare.py \
  --values cluster-addons/prometheus/values.yaml \
  --source-alias lab-seoul --investigation-channel '#ai-ops' \
  --output /tmp/wf03-candidate-values.json
WF03_AMTOOL=/tmp/wf03-tools/amtool <venv>/bin/python -m unittest discover \
  -s cluster-addons/prometheus/wf03 -p 'test_*.py' -v
```

`lab-seoul`은 예시다. 실제 연결 전 AI 서버의 source_alias/source_id/cluster_id와 맞춰야 한다. 조사 채널은 표시용이며 Incoming Webhook 목적지를 덮어쓰거나 Slack 권한을 확대하지 않는다.

기존 Watchdog/heartbeat·억제·group/repeat·Secret 경로와TLS를 보존한다. 후보는 기존 ai_ops=enabled 자동조사 분기를 제거하고 Slack continue 분기와 ai-events 사건접수를 독립 구성한다. `/v1/events`는 AI receiver의 명시적인 두 event-source 옵션이 필요하다. 수신기의 기본 경로는 기존 `/v1/alerts`로 유지된다. 기존 worker/DB 보존·장부 전환과 정책 차이를 검토하지 않고 이 후보를 배포하지 않는다.

템플릿은 원인 추정 대신 제한된 경보 사실·나노초UTC·발생 참조를 표시한다. 자유형 annotation/URL은 제외하고 mrkdwn·link_names를 끈다. 예상 밖 묶음은3개 표시 상한과 생략을 명시한다. `al1/alias/fingerprint/startsAt`은 조사 인가나 Slack 전달 성공 증거가 아니다.

고정 Helm83.7.0의 Alertmanagerv0.32.0 amtool로 config·routes·실제 Go template을 검사했다. 집중8개 통과; 도구 없이 skip된 검사는 검증 완료가 아니다. AI 쪽 TLS/동일참조 연결·전체회귀 결과는 AI 저장소 `ai-ops/docs/incident-notice-local-20261118.md` 및 검증JSON을 따른다. 실제 Slack 전송/클러스터/Alertmanager 실전송 재시도는 미실행이다.

사용한 upstream: [고정 chart values](https://github.com/prometheus-community/helm-charts/blob/kube-prometheus-stack-83.7.0/charts/kube-prometheus-stack/values.yaml), [Alertmanagerv0.32.0](https://github.com/prometheus/alertmanager/releases/tag/v0.32.0). 기존 시간·승인·원복 조건은 변경하지 않는다. 다음은 양쪽 코드 검토·CI/main 반영이다.

## 검토 보완 · 2026-11-18

기본 Slack fallback은 custom title과 별도로 전체 CommonLabels를 다시 렌더링한다. 실제 amtool로 이를 재현하고 fallback을 고정 문구로, title_link를 빈 값으로 명시했다. 검토하지 않은 pretext 등 추가 Slack 템플릿과 tplConfig/stringConfig/useExistingSecret 대체 설정은 후보 생성을 거절한다. 본문 제한만 통과하고 다른 필드로 원문이 전달되는 경로를 닫았다.

추가 회귀를 포함한 플랫폼 10개 시험이 통과했다. `.github/workflows/ai-ops-ci.yml`에 고정 Alertmanager 0.32.0·배포파일 SHA256·해시 잠금 PyYAML을 사용하는 `WF03 notice contracts`를 추가하고 기존 필수 C03 Main PR Gate에 연결했다. 기존 배포 values/Application·시간·승인·원복은 유지한다. CI는 템플릿 렌더/라우팅 검사만 수행하며 Slack이나 클러스터에 연결하지 않는다.

공식 기본값 근거: [default.tmpl](https://github.com/prometheus/alertmanager/blob/v0.32.0/template/default.tmpl), [Slack config defaults](https://github.com/prometheus/alertmanager/blob/v0.32.0/config/notifiers.go). 전체 검토·병합 결과는 AI 저장소 `ai-ops/docs/incident-notice-review-20261118.md`와 양쪽 HANDOFF에서 이어 기록한다.

## WF11 실제 전환 · 2026-11-25

WF11(실환경 전 구간 검증) 결정에 따라 **배포 values를 전환했다.**

- **결정 내용:**
  - WF10만 namespace·이름 `ai-ops`로 설치한다.
  - 발신원 alias는 `eks-seoul`이다.
  - 경보 공지는 기존 Webhook 채널에 그대로 보낸다.
- **원본 보존:** 전환 전 `values.yaml`을 `legacy-values.yaml`로 그대로 보존했다.
- **생성 방식:** `../values.yaml`의 Alertmanager `config`는 아래 명령으로 생성한 결과다.
  ```
  prepare.py --values legacy-values.yaml --source-alias eks-seoul --investigation-channel '#ai-ops' --event-service ai-ops-events.ai-ops.svc
  ```
- **정합성 강제:** `test_live_values_equal_the_generated_transition`이 생성 결과와의 일치를 강제한다. values의 다른 부분과 `alertmanagerSpec`은 바꾸지 않았다.
- **동작 변화:**
  - Watchdog 외 모든 경보는 기존 Slack 공지(continue)와 `ai-events`(`https://ai-ops-events.ai-ops.svc:9443/v1/events`)로 함께 간다.
  - TLS `server_name`은 같은 Service 이름이다.
  - 예전 `ai_ops=enabled`→`/v1/alerts` 분기는 없어졌다.
- **새 인자:** `prepare.py --event-service`는 `<service>.<namespace>.svc` 형식만 받는다. 기본값은 예전 `ai-ops-webhook.ai-ops.svc`이며, 기존 후보 시험과 호환된다.
- **amtool 검사:** 실제 배포 설정도 amtool로 검사한다(`LiveAlertmanagerTest`, CI 고정 0.32.0). 대상은 `check-config`, 그리고 Watchdog·`LabDeploymentUnavailable`·`HighErrorRate` 경로다.
- **배포 시점:** 이 values는 Argo가 main에서 가져가므로 다음 설치 때 적용된다.
- **AI 쪽 연결 조건:**
  - WF10이 `ai-ops/ai-ops`로 배포돼야 한다.
  - `ai-ops-alertmanager-auth` Secret(token, ca.crt)이 WF10 사건 수신기의 토큰·CA와 같아야 한다.
- **더 이상 맞지 않는 도구:** 예전 `/v1/alerts` 분기를 추가하던 AI 쪽 `prepare_alertmanager.py`/`build_routing`은 이 values와 맞지 않는다. 재설치 때 쓰지 않는다.
