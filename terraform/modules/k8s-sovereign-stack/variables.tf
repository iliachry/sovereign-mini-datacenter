variable "release_name" {
  description = "Helm release name"
  type        = string
  default     = "sovereign-stack"
}

variable "namespace" {
  description = "Kubernetes namespace for the sovereign datacenter stack"
  type        = string
  default     = "sovereign"
}

variable "create_namespace" {
  description = "Whether to create the namespace if it does not exist"
  type        = bool
  default     = true
}

variable "chart_path" {
  description = "Filesystem path to sovereign-stack Helm chart"
  type        = string
  default     = "./kubernetes/helm/sovereign-stack"
}

variable "domain" {
  description = "Base domain name for sovereign services"
  type        = string
  default     = "sovereign.local"
}

variable "ollama_model" {
  description = "Ollama model pulled in the background after the container starts (best effort; empty string disables the pre-pull)"
  type        = string
  default     = "qwen2.5-coder:7b"
}

variable "storage_class" {
  description = "StorageClass used by every PersistentVolumeClaim of the chart. The cluster must already have a dynamic provisioner for it (k3s ships `local-path`; Talos and vanilla Kubernetes ship none). An empty string selects the cluster's default StorageClass."
  type        = string
  default     = "local-path"
}

variable "ingress_enabled" {
  description = "Create the chart's Ingress. Requires an ingress controller that is already installed in the cluster (this module installs none)."
  type        = bool
  default     = true
}

variable "ingress_class_name" {
  description = "IngressClass of the pre-installed ingress controller (k3s ships `traefik`). An empty string selects the cluster's default IngressClass."
  type        = string
  default     = "traefik"
}

variable "enable_gpu" {
  description = "Request nvidia.com/gpu for Ollama. Requires NVIDIA drivers, the NVIDIA container runtime and the NVIDIA device plugin to be present in the cluster already (this module installs none); otherwise the pod stays Pending and the release times out."
  type        = bool
  default     = true
}

variable "gpu_runtime_class_name" {
  description = "RuntimeClass for the AI pod (e.g. `nvidia`). Leave empty when the NVIDIA runtime is the container runtime default or when enable_gpu is false."
  type        = string
  default     = ""
}

variable "gpu_limit" {
  description = "Number of GPUs allocated to Ollama"
  type        = string
  default     = "1"
}

variable "simulate_telemetry" {
  description = "Serve simulated power/thermal telemetry. Keep true: hardware mode (false) requires an ESP32 telemetry bridge or a serial device, and the chart does not wire either yet, so with false the exporter has no real measurements to report."
  type        = bool
  default     = true
}

variable "custom_values" {
  description = "Additional raw YAML or map values to pass to Helm release"
  type        = list(string)
  default     = []
}
