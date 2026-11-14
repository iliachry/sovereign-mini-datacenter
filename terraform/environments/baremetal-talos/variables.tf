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
  description = "Map of control plane nodes. Each node must already be reachable on `ip` in Talos maintenance mode (e.g. DHCP reservation). Set `interface` to the real NIC name to pin ip/network_prefix_length statically; leave it unset to stay on DHCP."
  type = map(object({
    ip           = string
    hostname     = string
    install_disk = string
    interface    = optional(string)
  }))
  default = {
    "cp-01" = {
      ip           = "10.0.0.10"
      hostname     = "smdc-cp-01"
      install_disk = "/dev/nvme0n1"
    }
  }
}

variable "worker_nodes" {
  description = "Map of worker nodes (same fields as control_plane_nodes, plus has_gpu)"
  type = map(object({
    ip           = string
    hostname     = string
    install_disk = string
    has_gpu      = optional(bool, false)
    interface    = optional(string)
  }))
  default = {
    "worker-gpu-01" = {
      ip           = "10.0.0.20"
      hostname     = "smdc-worker-gpu-01"
      install_disk = "/dev/nvme0n1"
      has_gpu      = true
    }
    "worker-gpu-02" = {
      ip           = "10.0.0.21"
      hostname     = "smdc-worker-gpu-02"
      install_disk = "/dev/nvme0n1"
      has_gpu      = true
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

variable "gateway" {
  description = "Default IPv4 gateway for nodes that set `interface`"
  type        = string
  default     = "10.0.0.1"
}

variable "network_prefix_length" {
  description = "Prefix length of the node subnet (24 for 10.0.0.0/24)"
  type        = number
  default     = 24
}

variable "gpu_installer_image" {
  description = "Talos installer image with the NVIDIA extensions baked in for GPU workers, built with Talos Image Factory (https://factory.talos.dev) for the Talos version in use. Null installs the stock image without NVIDIA drivers."
  type        = string
  default     = null
}

variable "gpu_extension_images" {
  description = "Legacy alternative to gpu_installer_image: exact NVIDIA system extension image references (take them from Talos Image Factory / the siderolabs/extensions release notes; the tag is not the Talos version)."
  type        = list(string)
  default     = []
}

# ── Cluster prerequisites that Talos does not ship ───────────────────────────
# This environment installs NO storage provisioner, ingress controller or
# NVIDIA device plugin. The defaults below therefore keep the features that
# depend on them switched off or pointed at something you must install first.

variable "storage_class" {
  description = "StorageClass for the stack's PersistentVolumeClaims. REQUIRED PREREQUISITE: install a dynamic provisioner on the Talos cluster and name its StorageClass here, otherwise every PVC stays Pending and the Helm release times out. Empty string = cluster default StorageClass."
  type        = string
}

variable "ingress_enabled" {
  description = "Create the Ingress. Enable only after installing an ingress controller on the cluster."
  type        = bool
  default     = false
}

variable "ingress_class_name" {
  description = "IngressClass of the ingress controller you installed (used when ingress_enabled = true)"
  type        = string
  default     = ""
}

variable "enable_gpu" {
  description = "Request nvidia.com/gpu for Ollama. Enable only after the GPU workers run an image with the NVIDIA extensions (gpu_installer_image) and the NVIDIA device plugin is deployed."
  type        = bool
  default     = false
}

variable "gpu_runtime_class_name" {
  description = "RuntimeClass for the AI pod, e.g. `nvidia` once you have created that RuntimeClass per the Talos NVIDIA guide"
  type        = string
  default     = ""
}
