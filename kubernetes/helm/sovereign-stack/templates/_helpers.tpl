{{/*
Expand the name of the chart.
*/}}
{{- define "sovereign-stack.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
*/}}
{{- define "sovereign-stack.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}

{{/*
Common labels
*/}}
{{- define "sovereign-stack.labels" -}}
helm.sh/chart: {{ include "sovereign-stack.name" . }}-{{ .Chart.Version | replace "+" "_" }}
{{ include "sovereign-stack.selectorLabels" . }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels
*/}}
{{- define "sovereign-stack.selectorLabels" -}}
app.kubernetes.io/name: {{ include "sovereign-stack.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}
