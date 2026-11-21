{{- define "ph.name" -}}{{ .Release.Name }}-practicehub{{- end -}}
{{- define "ph.labels" -}}
app.kubernetes.io/name: practicehub
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}
{{- define "ph.mongoHost" -}}{{ include "ph.name" . }}-mongo-0.{{ include "ph.name" . }}-mongo:27017{{- end -}}
{{- define "ph.databaseUri" -}}
{{- if .Values.mongo.enabled -}}
mongodb://{{ include "ph.mongoHost" . }}/practicehub?replicaSet=rs0&directConnection=true
{{- else -}}
{{ .Values.externalUri }}
{{- end -}}
{{- end -}}
{{- define "ph.env" -}}
- name: PRACTICEHUB_SECRET
  valueFrom: { secretKeyRef: { name: {{ .Values.secret.existingSecret }}, key: {{ .Values.secret.key }} } }
{{- if or .Values.mongo.enabled .Values.externalUri }}{{/* else PRACTICEHUB_DATABASE comes from hub.envFrom */}}
- name: PRACTICEHUB_DATABASE
  value: {{ include "ph.databaseUri" . | quote }}
{{- end }}
- name: PRACTICEHUB_STATE
  value: /state
- name: PRACTICEHUB_ANCHORS
  value: /anchors/track.anchors
{{- range $k, $v := .Values.hub.env }}
- name: {{ $k }}
  value: {{ $v | quote }}
{{- end }}
{{- end -}}
{{- define "ph.image" -}}{{ required "set image.repository" .Values.image.repository }}:{{ .Values.image.tag }}{{- end -}}
