{{- define "ai-ops.labels" -}}
app.kubernetes.io/name: ai-ops
app.kubernetes.io/instance: {{ .Release.Name | quote }}
app.kubernetes.io/component: diagnostics
app.kubernetes.io/managed-by: {{ .Release.Service | quote }}
{{- end -}}
{{- define "ai-ops.selector" -}}
app.kubernetes.io/name: ai-ops
app.kubernetes.io/instance: {{ .Release.Name | quote }}
{{- end -}}
{{- define "ai-ops.validate" -}}
{{- if .Values.enabled -}}
{{- if ne .Release.Namespace "ai-ops" }}{{ fail "배포 namespace는 ai-ops여야 합니다" }}{{ end -}}
{{- if gt (len .Release.Name) 40 }}{{ fail "release 이름은 40자 이하여야 합니다" }}{{ end -}}
{{- $_ := required "image.repository가 필요합니다" .Values.image.repository -}}
{{- $_ := required "고정 image.digest가 필요합니다" .Values.image.digest -}}
{{- $_ := required "확인한 StorageClass가 필요합니다" .Values.persistence.storageClassName -}}
{{- $bindings := required "등록 대상 설정이 필요합니다" .Values.config.bindings.bindings -}}
{{- if ne (len $bindings) 1 }}{{ fail "첫 배포는 등록 대상 하나만 허용합니다" }}{{ end -}}
{{- $binding := first $bindings -}}
{{- if not (has $binding.target.environment (list "practice" "production")) }}{{ fail "명시적으로 등록한 practice 또는 production 환경만 허용합니다" }}{{ end -}}
{{- if or (eq $binding.target.namespace "ai-ops") (eq $binding.argo.namespace "ai-ops") (eq $binding.target.namespace $binding.argo.namespace) }}{{ fail "조사·대상·Argo namespace는 분리해야 합니다" }}{{ end -}}
{{- if not (hasKey .Values.config.runtime "llm_enabled") }}{{ fail "모델 비활성 설정이 필요합니다" }}{{ end -}}
{{- if .Values.config.runtime.llm_enabled }}{{ fail "이번 배포는 모델을 활성화할 수 없습니다" }}{{ end -}}
{{- $connections := required "연결 설정이 필요합니다" .Values.config.runtime.connections -}}
{{- if ne (len $connections) 2 }}{{ fail "첫 진단 배포는 Kubernetes와 Prometheus 연결 하나씩만 허용합니다" }}{{ end -}}
{{- $kube := required "Kubernetes 연결 별칭이 필요합니다" (index $connections $binding.kubernetes_connection) -}}
{{- $prom := required "Prometheus 연결 별칭이 필요합니다" (index $connections $binding.prometheus_connection) -}}
{{- if or (ne $kube.kind "kubernetes") (ne $kube.base_url "https://kubernetes.default.svc:443") (ne $kube.token_file "/var/run/ai-ops/kubernetes/token") (ne $kube.ca_file "/var/run/ai-ops/kubernetes/ca.crt") }}{{ fail "Kubernetes 연결은 지정한 API·projected token·CA 경로를 사용해야 합니다" }}{{ end -}}
{{- if or (ne $prom.kind "prometheus") (not (hasPrefix "http://" $prom.base_url)) $prom.token_file $prom.ca_file }}{{ fail "첫 진단 배포의 Prometheus는 내부 HTTP·무인증 연결만 지원합니다" }}{{ end -}}
{{- $_ := required "확인한 API 도착 IP가 필요합니다" .Values.network.apiServerCIDRs -}}
{{- $_ := required "확인한 DNS Pod label이 필요합니다" .Values.network.dns.podLabels -}}
{{- $_ := required "확인한 Prometheus Pod label이 필요합니다" .Values.network.prometheus.podLabels -}}
{{- end -}}
{{- end -}}
