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
  description = "Default Ollama LLM model to pre-fetch/load"
  type        = string
  default     = "qwen2.5-coder:7b"
}

variable "enable_gpu" {
  description = "Allocate NVIDIA GPU resources to AI workloads"
  type        = bool
  default     = true
}

variable "gpu_limit" {
  description = "Number of GPUs allocated to Ollama"
  type        = string
  default     = "1"
}

variable "simulate_telemetry" {
  description = "Run simulated power/thermal telemetry if hardware serial is disconnected"
  type        = bool
  default     = false
}

variable "custom_values" {
  description = "Additional raw YAML or map values to pass to Helm release"
  type        = list(string)
  default     = []
}
