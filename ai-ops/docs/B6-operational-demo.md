# B-06 읽기 조사 운영 데모

이 절차는 운영자 Kubernetes 인증으로 실제 수집 요청 한 건을 추가한다. 앱·DB·Redis·권한·이미지를 변경하지 않는다. 같은 PVC의 worker가 보고서를 작성하며 외부 모델은 호출하지 않는다.

## 용어와 흐름

- 조사 요청: “현재 서비스 상태와 부족한 자료를 확인해 달라”는 작업이다.
- job: 이 요청의 접수·시도·완료 상태를 추적하는 큐 항목이다.
- 증거 묶음: 읽어 온 상태·로그·지표를 선별·정제하고 시간과 해시를 고정한 입력이다.
- 보고서: 관측 사실·원인 후보·부족한 자료·확인 제안을 분리한 결과다. 현재 배포에서는 규칙 기반으로 만든다.
- partial: 일부 자료가 부족한 상태로 결과를 저장했다는 뜻이다. 서비스 장애·원인 확정·해결 완료를 뜻하지 않는다.

**현재 identity·모델 비활성 확인 → 권한 검사 → PVC 큐 접수 → 동일 요청 재접수 → worker 수집·보고서 저장 → 결과 조회 → 완료 후 중복 확인 → 외부 전송 차단·앱 불변 확인** 순서다.

## 실행

잠긴 의존성이 설치된 프로젝트 Python 환경과 `kubectl`을 사용한다. context는 서울 리전의 `urlshortener`여야 하고, AI Pod 2/2 Ready와 기존 worker가 필요하다. 출력은 새 파일이어야 한다.

```bash
B6_DIR="$HOME/.local/share/url-shortener-ai-ops/b6"
mkdir -p "$B6_DIR"
chmod 700 "$B6_DIR"
PYTHONPATH=ai-ops/src python ai-ops/scripts/run_operational_demo.py   --output "$B6_DIR/operational-demo-$(date -u +%Y%m%dT%H%M%SZ).json"
```

원문 보고서는 출력하지 않는다. 결과 JSON의 job·incident·run ID를 확인한다. 보고서를 직접 열어야 한다면 운영 권한을 가진 터미널에서 다음을 사용한다. `/data/incidents`는 반드시 명시한다.

```bash
kubectl -n ai-ops exec ai-ops-0 -c diagnostics -- python -m ai_ops.cli show   --store /data/incidents --incident <incident_id> --run <run_id>
```

partial 보고서도 조회되지만 종료 코드는 1이다. 이를 무조건 접수 실패로 해석하거나 같은 요청을 새 키로 반복 생성하지 않는다. 세부 상태는 `queue-show --store /data/incidents --job <job_id>`로 확인한다. 보고서 원문은 비공개 운영 자료이므로 Git·외부 모델에 붙여 넣지 않는다.

## 이번 결과와 재현의 범위

2026-10-21 실제 요청은 약 12.92초에 partial로 저장됐다. 30개 입력 중 available 7개, 원인 후보 0개이며 정상/장애를 단정하지 않았다. [실측 기록](assets/B6-operational-demo.json)을 참고한다.

스크립트 재실행은 새 요청 한 건을 만들며 새 정확도 표본이나 실제 장애 실험을 자동 생성하지 않는다. 같은 결과를 재검토할 때는 위 조회 명령을 사용한다. 실패 시 결과 JSON과 큐 상태를 확인하고 앱을 재시작하거나 PVC를 삭제하지 않는다. 내부 보고서는 생성 후 7일 보관 정책을 따른다.
