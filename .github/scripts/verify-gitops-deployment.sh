#!/usr/bin/env bash
set -euo pipefail

: "${ARGOCD_SERVER:?ARGOCD_SERVER 값이 필요합니다}"
: "${ARGOCD_AUTH_TOKEN:?ARGOCD_AUTH_TOKEN 값이 필요합니다}"
: "${ARGOCD_APPLICATION:?ARGOCD_APPLICATION 값이 필요합니다}"
: "${GITOPS_COMMIT:?GITOPS_COMMIT 값이 필요합니다}"
if [[ "${ARGOCD_SERVER}" != https://* ]]; then
  echo "ARGOCD_AUTH_TOKEN을 전송하려면 ARGOCD_SERVER에 HTTPS 주소를 사용해야 합니다" >&2
  exit 1
fi
argocd_cli="${ARGOCD_CLI:-${RUNNER_TEMP:?RUNNER_TEMP 값이 필요합니다}/argocd}"
test -x "${argocd_cli}"

server="${ARGOCD_SERVER#https://}"
argocd_options=(--server "${server}" --auth-token "${ARGOCD_AUTH_TOKEN}" --grpc-web)

timeout="${ARGOCD_WAIT_TIMEOUT:-600}"
[[ "${timeout}" =~ ^[1-9][0-9]*$ ]] || { echo "ARGOCD_WAIT_TIMEOUT은 양의 정수여야 합니다" >&2; exit 1; }
deadline=$((SECONDS + timeout))
verified=false

# 해당 커밋의 동기화와 PostSync 검증 Job이 모두 성공할 때까지 기다린다.
while (( SECONDS < deadline )); do
  if app_json="$("${argocd_cli}" "${argocd_options[@]}" app get "${ARGOCD_APPLICATION}" --refresh -o json)"; then
    IFS='|' read -r revision sync_status health_status operation_revision operation_phase < <(
      printf '%s' "${app_json}" | python3 -c '
import json, sys
status = json.load(sys.stdin).get("status", {})
sync = status.get("sync", {})
health = status.get("health", {})
operation = status.get("operationState") or {}
sync_result = operation.get("syncResult") or {}
print("|".join((sync.get("revision", ""), sync.get("status", ""), health.get("status", ""), sync_result.get("revision", ""), operation.get("phase", ""))))
')
    if [[ "${revision}" == "${GITOPS_COMMIT}" && "${sync_status}" == "Synced" && "${health_status}" == "Healthy" && "${operation_revision}" == "${GITOPS_COMMIT}" && "${operation_phase}" == "Succeeded" ]]; then
      verified=true
      break
    fi
    if [[ "${operation_revision}" == "${GITOPS_COMMIT}" && ( "${operation_phase}" == "Failed" || "${operation_phase}" == "Error" ) ]]; then
      echo "${GITOPS_COMMIT}의 Argo CD 동기화 또는 PostSync 검증이 실패했습니다" >&2
      exit 1
    fi
    echo "${ARGOCD_APPLICATION} 동기화 대기 중: 커밋=${revision}, 동기화=${sync_status}, 상태=${health_status}, 작업=${operation_phase}"
  fi
  remaining=$((deadline - SECONDS))
  if (( remaining > 0 )); then
    if (( remaining < 10 )); then
      sleep "${remaining}"
    else
      sleep 10
    fi
  fi
done

if [[ "${verified}" != true ]]; then
  echo "${timeout}초 안에 ${GITOPS_COMMIT}의 Argo CD 동기화와 내부 검증이 완료되지 않았습니다" >&2
  exit 1
fi

echo "Argo CD 커밋 ${GITOPS_COMMIT}과 내부 /readyz, /items 검증이 완료됐습니다"
