variable "cluster_name" {
  description = "Name of the Sovereign Kubernetes cluster"
  type        = string
  default     = "sovereign-cluster"
}

variable "cluster_endpoint" {
  description = "Virtual IP or hostname for the Kubernetes control plane endpoint (e.g. https://10.0.0.10:6443)"
  type        = string
}

variable "kubernetes_version" {
  description = "Kubernetes version to install"
  type        = string
  default     = "v1.31.0"
}

variable "talos_version" {
  description = "Talos OS version"
  type        = string
  default     = "v1.9.0"
}

variable "control_plane_nodes" {
  description = "Map of control plane nodes with IP, hostname, and install disk"
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
  description = "Map of worker nodes with IP, hostname, install disk, and GPU capability"
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
  }
}

variable "enable_luks_encryption" {
  description = "Enable LUKS2 system and ephemeral disk encryption"
  type        = bool
  default     = true
}

variable "gateway" {
  description = "Default network gateway"
  type        = string
  default     = "10.0.0.1"
}

variable "nameservers" {
  description = "List of upstream DNS nameservers"
  type        = list(string)
  default     = ["1.1.1.1", "9.9.9.9"]
}

variable "save_config_locally" {
  description = "Save generated talosconfig and kubeconfig to local disk"
  type        = bool
  default     = true
}

variable "config_output_dir" {
  description = "Directory path where local talosconfig and kubeconfig will be saved"
  type        = string
  default     = "./_output"
}
