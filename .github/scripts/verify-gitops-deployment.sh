#!/usr/bin/env bash
set -euo pipefail

: "${ARGOCD_SERVER:?ARGOCD_SERVER is required}"
: "${ARGOCD_AUTH_TOKEN:?ARGOCD_AUTH_TOKEN is required}"
: "${ARGOCD_APPLICATION:?ARGOCD_APPLICATION is required}"
: "${GITOPS_COMMIT:?GITOPS_COMMIT is required}"
: "${SMOKE_TEST_BASE_URL:?SMOKE_TEST_BASE_URL is required}"

argocd_cli="${ARGOCD_CLI:-${RUNNER_TEMP:?RUNNER_TEMP is required}/argocd}"
test -x "${argocd_cli}"

server="${ARGOCD_SERVER#https://}"
argocd_options=(--server "${server}" --auth-token "${ARGOCD_AUTH_TOKEN}" --grpc-web)
if [[ "${ARGOCD_SERVER}" == http://* ]]; then
  server="${ARGOCD_SERVER#http://}"
  argocd_options=(--server "${server}" --auth-token "${ARGOCD_AUTH_TOKEN}" --grpc-web --plaintext)
fi

timeout="${ARGOCD_WAIT_TIMEOUT:-600}"
[[ "${timeout}" =~ ^[1-9][0-9]*$ ]] || { echo "ARGOCD_WAIT_TIMEOUT must be a positive integer" >&2; exit 1; }
deadline=$((SECONDS + timeout))
applied=false

# --sync/--health만으로는 이전 revision의 성공을 새 배포의 성공으로 오인할 수 있다.
while (( SECONDS < deadline )); do
  if app_json="$("${argocd_cli}" "${argocd_options[@]}" app get "${ARGOCD_APPLICATION}" --refresh -o json)"; then
    IFS='|' read -r revision sync_status health_status < <(
      printf '%s' "${app_json}" | python3 -c '
import json, sys
status = json.load(sys.stdin).get("status", {})
sync = status.get("sync", {})
health = status.get("health", {})
print("|".join((sync.get("revision", ""), sync.get("status", ""), health.get("status", ""))))
')
    if [[ "${revision}" == "${GITOPS_COMMIT}" && "${sync_status}" == "Synced" && "${health_status}" == "Healthy" ]]; then
      applied=true
      break
    fi
    echo "Waiting for ${ARGOCD_APPLICATION}: revision=${revision}, sync=${sync_status}, health=${health_status}"
  fi
  sleep 10
done

if [[ "${applied}" != true ]]; then
  echo "Argo CD did not apply healthy revision ${GITOPS_COMMIT} within ${timeout}s" >&2
  exit 1
fi

base_url="${SMOKE_TEST_BASE_URL%/}"
while (( SECONDS < deadline )); do
  if ready_json="$(curl --fail --silent --show-error --max-time 10 "${base_url}/readyz")" &&
     items_json="$(curl --fail --silent --show-error --max-time 10 "${base_url}/items")" &&
     printf '%s' "${ready_json}" | python3 -c 'import json,sys; assert json.load(sys.stdin).get("status") == "ready"' &&
     printf '%s' "${items_json}" | python3 -c 'import json,sys; assert isinstance(json.load(sys.stdin), list)'; then
    echo "Verified Argo CD revision ${GITOPS_COMMIT}, /readyz and /items"
    exit 0
  fi
  sleep 10
done

echo "Smoke test did not pass within ${timeout}s" >&2
exit 1
