{{/*
앱 이름 생성 (Chart 이름 또는 nameOverride 사용)
*/}}
{{- define "url-shortener.name" -}}
{{- .Values.nameOverride | default .Chart.Name | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
전체 릴리즈 이름 생성
*/}}
{{- define "url-shortener.fullname" -}}
{{- printf "%s-%s" .Release.Name (include "url-shortener.name" .) | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
공통 레이블 (모든 리소스에 적용)
*/}}
{{- define "url-shortener.labels" -}}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version }}
app.kubernetes.io/name: {{ include "url-shortener.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
셀렉터 레이블 (Deployment, Service에서 Pod 선택에 사용)
*/}}
{{- define "url-shortener.selectorLabels" -}}
app.kubernetes.io/name: {{ include "url-shortener.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}
