{{- define "url-shortener.aiOpsLabels" -}}
{{- if .Values.monitoring.aiOps.enabled -}}
{{- $cluster := required "AI 경보를 켜려면 monitoring.aiOps.clusterId가 필요합니다" .Values.monitoring.aiOps.clusterId -}}
{{- $environment := required "AI 경보를 켜려면 monitoring.aiOps.environment가 필요합니다" .Values.monitoring.aiOps.environment -}}
{{- if not (regexMatch "^[a-z0-9]([-a-z0-9]*[a-z0-9])?$" $cluster) }}{{ fail "AI clusterId 형식이 유효하지 않습니다" }}{{ end -}}
{{- if not (has $environment (list "practice" "production")) }}{{ fail "AI environment는 명시적 practice 또는 production이어야 합니다" }}{{ end -}}
ai_ops: enabled
cluster_id: {{ $cluster | quote }}
environment: {{ $environment | quote }}
namespace: {{ .Release.Namespace | quote }}
service: {{ printf "%s-svc" .Release.Name | quote }}
workload: {{ printf "%s-api" .Release.Name | quote }}
{{- end -}}
{{- end -}}
