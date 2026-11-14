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
  description = <<-EOT
    Map of control plane nodes.
      ip           - address Terraform reaches the node on; it must already answer there in
                     maintenance mode (e.g. via a DHCP reservation) when the config is applied.
      hostname     - node hostname.
      install_disk - disk Talos is installed to.
      interface    - kernel name of the NIC to configure statically with ip/network_prefix_length
                     and a default route via var.gateway (list the real names with
                     `talosctl -n <ip> get links --insecure`). Leave unset to keep DHCP.
  EOT
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
  description = "Map of worker nodes. Same fields as control_plane_nodes plus has_gpu, which labels the node and (only when NVIDIA drivers are supplied through gpu_installer_image or gpu_extension_images) loads the NVIDIA kernel modules."
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
  }
}

variable "enable_luks_encryption" {
  description = "Enable LUKS2 system and ephemeral disk encryption"
  type        = bool
  default     = true
}

variable "gateway" {
  description = "Default IPv4 gateway. Used for the default route of every node that sets `interface` (static addressing)."
  type        = string
  default     = "10.0.0.1"
}

variable "network_prefix_length" {
  description = "Prefix length of the node subnet (e.g. 24 for 10.0.0.0/24). Combined with each node's ip for static addressing when `interface` is set."
  type        = number
  default     = 24

  validation {
    condition     = var.network_prefix_length >= 1 && var.network_prefix_length <= 32
    error_message = "network_prefix_length must be between 1 and 32."
  }
}

variable "gpu_installer_image" {
  description = "Talos installer image that already contains the NVIDIA system extensions for GPU workers, generated with Talos Image Factory (https://factory.talos.dev) for exactly var.talos_version, e.g. factory.talos.dev/installer/<schematic-id>:<talos-version>. This is the supported way to ship extensions. Null leaves the stock installer in place (no NVIDIA drivers)."
  type        = string
  default     = null
}

variable "gpu_extension_images" {
  description = "Full, digest- or tag-pinned system extension image references written to machine.install.extensions on GPU workers (legacy mechanism, deprecated by Talos in favour of gpu_installer_image). Extension tags are NOT the Talos version: copy the exact references for your Talos release from Talos Image Factory (https://factory.talos.dev) or the siderolabs/extensions release notes. Default: none."
  type        = list(string)
  default     = []
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
