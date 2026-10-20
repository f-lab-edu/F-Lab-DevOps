# B-05 내부 TLS·인증 Secret의 설치와 회전

이 절차는 설치 자동화 담당자가 수행한다. 외부 API 키나 공개 인증기관을 사용하지 않는다. 전체 재설치에서는 **모니터링 Application을 적용하기 전에** 두 Secret을 준비한다. Alertmanager가 먼저 참조하는 인증 Secret이 없으면 Pod 시작과 기존 Watchdog 전달까지 지연될 수 있다.

## 새 클러스터의 최초 준비

AWS 계정 `716174522908`·리전 `ap-northeast-2`·클러스터 `urlshortener`를 확인하고 main의 AI namespace 원본과 monitoring namespace를 먼저 준비한다. 기존 클러스터에 동일 이름 Secret이 하나라도 있으면 아래 신규 생성 명령을 반복하지 않는다. 이름·소유 labels·키 이름·서버 인증서 만료와 CA 일치 여부를 값 비출력 방식으로 확인한다. 부분 생성·서로 다른 CA·소유자 불명 상태라면 설치를 멈추고 조정한다.

```sh
kubectl apply -f ai-ops/kubernetes/namespace.yaml
kubectl get namespace monitoring
kubectl -n ai-ops get secret ai-ops-webhook-identity --ignore-not-found -o name
kubectl -n monitoring get secret ai-ops-alertmanager-auth --ignore-not-found -o name
```

조회가 정상 완료되고 둘 다 없는 새 설치에만 실행한다. 전용 개인 폴더를 준비하고 셸 추적을 끈다. 인증값이나 Secret JSON을 로그·Git·artifact에 넣지 않는다. 서버 인증서는 90일, CA는 365일이며 실제 만료를 기록한다. 호스트 OpenSSL의 EC 명시 매개변수와 일부 TLS 클라이언트의 호환 문제가 있어 RSA를 명시한다. 검증 우회 옵션은 사용하지 않는다.

```sh
set +x
umask 077
mkdir -p "$HOME/.local/share/url-shortener-ai-ops"
AI_TLS_DIR=$(mktemp -d "$HOME/.local/share/url-shortener-ai-ops/b5-tls.XXXXXX")
chmod 700 "$AI_TLS_DIR"
openssl req -x509 -newkey rsa:3072 -nodes -days 365 \
  -subj /CN=urlshortener-ai-ops-internal-ca \
  -keyout "$AI_TLS_DIR/ca.key" -out "$AI_TLS_DIR/ca.crt"
openssl req -newkey rsa:2048 -nodes \
  -subj /CN=ai-ops-webhook.ai-ops.svc \
  -keyout "$AI_TLS_DIR/tls.key" -out "$AI_TLS_DIR/server.csr"
cat > "$AI_TLS_DIR/server.ext" <<'EOF'
subjectAltName=DNS:ai-ops-webhook.ai-ops.svc,DNS:ai-ops-webhook.ai-ops.svc.cluster.local
basicConstraints=critical,CA:FALSE
keyUsage=critical,digitalSignature
extendedKeyUsage=serverAuth
EOF
openssl x509 -req -in "$AI_TLS_DIR/server.csr" \
  -CA "$AI_TLS_DIR/ca.crt" -CAkey "$AI_TLS_DIR/ca.key" -CAcreateserial \
  -days 90 -sha256 -extfile "$AI_TLS_DIR/server.ext" -out "$AI_TLS_DIR/tls.crt"
openssl rand -hex 32 | tr -d '\n' > "$AI_TLS_DIR/token"
openssl verify -CAfile "$AI_TLS_DIR/ca.crt" "$AI_TLS_DIR/tls.crt"
openssl x509 -in "$AI_TLS_DIR/tls.crt" -noout -enddate
kubectl -n ai-ops create secret generic ai-ops-webhook-identity \
  --from-file=token="$AI_TLS_DIR/token" \
  --from-file=tls.crt="$AI_TLS_DIR/tls.crt" --from-file=tls.key="$AI_TLS_DIR/tls.key"
kubectl -n monitoring create secret generic ai-ops-alertmanager-auth \
  --from-file=token="$AI_TLS_DIR/token" --from-file=ca.crt="$AI_TLS_DIR/ca.crt"
kubectl -n ai-ops label secret ai-ops-webhook-identity \
  app.kubernetes.io/part-of=ai-ops app.kubernetes.io/managed-by=ai-ops-operator
kubectl -n monitoring label secret ai-ops-alertmanager-auth \
  app.kubernetes.io/part-of=ai-ops app.kubernetes.io/managed-by=ai-ops-operator
```

두 Secret의 token이 같고 서버 인증서가 클라이언트 CA로 검증되는지 비출력 비교한다. CA 개인키는 클러스터에 올리지 않는다. 수신기 Secret은 chart에서 0440·fsGroup 10001로 마운트하고 worker에는 마운트하지 않는다. 공개 Ingress·LoadBalancer는 생성하지 않는다. 이 단계 뒤 기존 설치 가이드의 모니터링을 설치하고, 앱·Prometheus 준비와 승인 이미지 검증 후 AI Helm에 `b4-values.worker.yaml`과 `b5-values.receiver.yaml`을 함께 적용한다. 이미지 등록 소스에 `alert_receiver.py`가 없으면 중지한다.

## 회전과 완료 확인

만료 최소 30일 전에 회전을 계획한다. 현재는 인증서 만료를 자동 갱신하거나 별도 경보로 알리는 기능이 없으므로 설치·운영 기록에 확인일과 만료일을 남긴다. TLS 서버는 시작 때 인증서를 읽으므로 Secret 교체만으로 완료되지 않는다. 같은 CA로 서버 인증서를 재발급한다면 서버 Secret을 갱신하고 AI StatefulSet을 순차 재시작한 뒤 실제 CA·호스트명 검증을 다시 수행한다. worker는 같은 PVC로 복귀해야 한다.

CA도 바꾸는 경우 새·기존 CA를 함께 신뢰하는 클라이언트 CA 묶음을 먼저 배포해 Alertmanager의 파일 갱신·TLS 연결을 확인한다. 새 서버 인증서로 교체·수신기 재시작 후 실제 전달이 성공하면 기존 CA를 제거한다. 임시 `insecure_skip_verify`나 인증 제거로 우회하지 않는다. token 회전도 서버와 클라이언트 파일 반영 시차 동안 401·재시도가 생길 수 있으므로 짧은 점검 창에서 두 Secret 갱신과 실제 전달 확인을 함께 수행한다. AI 오류 동안에도 Slack·Watchdog 경로를 유지한다.

전체 정리는 AI Helm/PVC/EBS를 먼저 제거하고 AI namespace의 수신기 Secret을 함께 제거한다. monitoring의 `ai-ops-alertmanager-auth`는 **모니터링 종료 단계에서** 삭제한다. Alertmanager가 사용 중일 때 인증 Secret을 먼저 삭제하지 않는다. 로컬 개인 폴더는 AWS 정리 대상이 아니며 별도 보관 정책을 따른다.
