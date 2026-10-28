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

{{/*
Fully qualified ingress host for a given subdomain key (e.g. "ai" -> ai.sovereign.local)
Usage: {{ include "sovereign-stack.host" (dict "sub" "ai" "root" .) }}
*/}}
{{- define "sovereign-stack.host" -}}
{{- printf "%s.%s" .sub .root.Values.global.domain }}
{{- end }}

{{/*
Resolve a base64-encoded secret value with precedence:
  1. explicit value from values.yaml
  2. value already stored in the cluster (lookup)
  3. freshly generated random value
Usage: {{ include "sovereign-stack.secretValue" (dict "explicit" "" "existing" "<b64>" "length" 32) }}
*/}}
{{- define "sovereign-stack.secretValue" -}}
{{- if .explicit }}
{{- .explicit | b64enc }}
{{- else if .existing }}
{{- .existing }}
{{- else }}
{{- randAlphaNum (int .length) | b64enc }}
{{- end }}
{{- end }}
