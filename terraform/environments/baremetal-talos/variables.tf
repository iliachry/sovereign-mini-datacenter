variable "cluster_name" {
  description = "Cluster identifier"
  type        = string
  default     = "sovereign-mini-dc"
}

variable "cluster_endpoint" {
  description = "Virtual IP or hostname for the Kubernetes control plane endpoint"
  type        = string
  default     = "https://10.0.0.10:6443"
}

variable "control_plane_nodes" {
  description = "Map of control plane nodes"
  type = map(object({
    ip           = string
    hostname     = string
    install_disk = string
    interface    = optional(string, "10gbe0")
  }))
  default = {
    "cp-01" = {
      ip           = "10.0.0.10"
      hostname     = "smdc-cp-01"
      install_disk = "/dev/nvme0n1"
      interface    = "10gbe0"
    }
  }
}

variable "worker_nodes" {
  description = "Map of worker nodes"
  type = map(object({
    ip           = string
    hostname     = string
    install_disk = string
    has_gpu      = optional(bool, false)
    interface    = optional(string, "10gbe0")
  }))
  default = {
    "worker-gpu-01" = {
      ip           = "10.0.0.20"
      hostname     = "smdc-worker-gpu-01"
      install_disk = "/dev/nvme0n1"
      has_gpu      = true
      interface    = "10gbe0"
    }
    "worker-gpu-02" = {
      ip           = "10.0.0.21"
      hostname     = "smdc-worker-gpu-02"
      install_disk = "/dev/nvme0n1"
      has_gpu      = true
      interface    = "10gbe0"
    }
  }
}

variable "domain" {
  description = "Base domain name for services"
  type        = string
  default     = "sovereign.local"
}

variable "ollama_model" {
  description = "Default local AI model"
  type        = string
  default     = "qwen2.5-coder:7b"
}
