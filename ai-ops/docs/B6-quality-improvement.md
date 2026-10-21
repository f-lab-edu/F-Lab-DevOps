# B-06 로그 분류·부분 보고서 개선

상태: **수정 이미지 배포·기존 보고서 보존·새 v2 보고서 검증 완료**. 로컬 246개·원격 247개 시험과 동일 실환경 증거 비교를 통과한 PR #49 이미지를 사용자 승인 후 같은 PVC에 배포했다. [검증 결과](assets/B6-quality-improvement.json), [배포 확인](assets/B6-deployment-validation.json), [새 조사 실측](assets/B6-operational-demo-v2.json)을 함께 확인한다. 기존 운영 보고서를 덮어쓰지 않았다.

## 확인한 원인

AI Pod 안의 별도 검사 프로세스로 현재 등록 대상의 로그 형태만 집계했다. 두 응답 모두 UTF-8과 행 시각 형식이 유효했으며 허용 진단 표식은 0개였다. 196행/195행의 원문은 Pod 밖에 내보내지 않았다. 기존 정규화는 허용 표식이 없는 로그까지 일반 `invalid_payload`로 합쳐 자료 형식 오류와 구분하기 어려웠다.

## 수정한 처리

- 형식·시각 검사는 통과했지만 허용 진단 필드가 없으면 `missing/no_diagnostic_fields`로 남기고 본문을 저장하지 않는다. 이 상태를 오류 없음·서비스 정상으로 해석하지 않는다.
- UTF-8 실패는 `invalid_log_encoding`, 행 시각 형식 실패는 `invalid_log_timestamp`로 구분한다. 잘림·크기·시각 범위·마스킹·소유 관계와 외부 전송 정책은 유지한다.
- 새 `rules-captured-v2`는 기존 판단 규칙 위에 유효한 Pod 상태·배포 상태·캐시 집계 관측을 추가한다. 부분 자료를 전체 정상으로 확대하지 않으며 모든 사실은 available 증거만 인용한다.
- 기존 `rules-captured-v1` 보고서는 원래 규칙으로 검증해 재조회 호환성을 유지한다. v2 저장 보고서의 사실은 입력 증거에서 다시 계산한 내용과 일치해야 한다. 변조된 정상 단정은 거부한다.

## 검증

전체 Python 시험 246개가 통과했다. 진단 표식 부재와 UTF-8·시각 오류의 분리, 원문·비밀값 미보존, 부분 관측과 상태 유보, v1 보고서 재조회, v2 사실 변조 거부를 포함한다. 최초 전체 실행은 샌드박스의 localhost 소켓 제한으로 TLS/지표 시험이 거부돼, 로컬 소켓이 허용된 환경에서 같은 전체 시험을 실행했다.

기존 실제 bundle `6f3b2143…`을 Pod 안에서 후보 규칙으로 다시 계산했다. 사실 0→3개(두 Pod 상태·두 배포 자료·캐시 집계), 가설 0개이며 `partial/unknown`은 유지됐다. 이는 후보 코드의 동일 입력 비교이며 수정 이미지 배포 성공이나 새 수집 결과가 아니다. 원래 보고서와 입력 파일은 변경하지 않았다.

## 배포 절차

CI 통과 후 main 기반 이미지를 GitHub 필수 사용자 승인으로 등록한다. artifact·ECR 검사·현재 연결 주소를 검증해 같은 PVC와 worker·수신기 overlay로 갱신한 뒤, 기존 v1 보고서 재조회와 새 v2 보고서의 사실·참조·자료 부족 사유를 확인한다. 새 로그가 상한에 도달하면 여전히 truncated로 처리하는 것이 정상이며 이 결과를 no_diagnostic_fields로 억지 변경하지 않는다.

이 개선만으로 독립 장애 시나리오나 라이브 모델 정확도가 검증되지는 않는다. B-06의 운영 평가 범위와 남은 조건은 [평가 문서](B6-operational-evaluation.md)를 따른다.

## 원격 반영과 실제 배포 결과

[PR #49](https://github.com/f-lab-edu/F-Lab-DevOps/pull/49)은 원격 247개·이미지 검사 통과 후 main `91a1608af06a210487bc1cf58db659f1767155db`에 반영했다. 사용자 승인 후 [이미지 등록 실행 39543600322](https://github.com/f-lab-edu/F-Lab-DevOps/actions/runs/39543600322)이 성공했다. artifact·ECR 검사·현재 연결 주소를 확인한 digest `sha256:aa4dabfffb2767e64aed3d9c56ce62c9bef4bdf522d9510ec686a6a0b9d3e190`을 2026-10-21 13:29 KST Helm revision 2에 배포했다.

- 동일 PVC UID `b0236f94-4395-4aaa-91ac-3b2bbe31a78d`를 유지했다. 기존 v1 보고서 3건이 새 코드에서 조회됐고, 각 report/input/collection-audit 파일과 정규화한 보고서 해시는 전후 동일했다.
- 새 요청 1건은 약 13.66초·worker 시도 1회에 `rules-captured-v2` 보고서로 저장됐다. 접수 직후·완료 후 중복 요청도 같은 job을 반환하고 시도를 늘리지 않았다.
- 사실 3개는 Pod 상태·배포 상태·캐시 계수를 설명하며 available 자료만 인용한다. 가설은 0개이고 `partial / insufficient_evidence / unknown`을 유지했다. 첫 v1 측정과 수집 시각이 다르므로 정확도 향상률이나 동일 입력 성능 비교로 해석하지 않는다.
- 이번 30개 자료는 available 7·missing 8·stale 15·invalid 0이다. 두 Pod 로그는 `missing/no_diagnostic_fields`로 분류됐다. 원문을 보존하거나 허용 진단 필드 범위를 확대하지 않았다.
- AI Pod 2/2 Ready·worker probe·Prometheus up=1·보관 정리 enabled=1·overdue=0, 큐 상태 8개 시계열을 확인했다. 앱 Deployment UID/generation과 Ready 2개를 유지했고 `/readyz`·`/items`는 HTTPS 200·TLS 검증 0이다.
- 외부 모델 호출·API 비용은 0이며 captured 외부 전송은 차단됐다. AWS 운영 비용은 이 API 비용에 포함하지 않는다.

이번 로그 분류·부분 관측 설명·보존 검증 묶음은 완료다. 독립 장애 시나리오와 라이브 모델 비교는 아직 미검증이므로 B-06 전체와 B 완료·C 진입은 보류한다. 다음은 독립 실습 대상과 정답 기준·복구 절차를 준비하는 작업이며 추천 추론 깊이는 GPT-6 SOL high다.
