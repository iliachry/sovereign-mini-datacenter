variable "node_name" {
  description = "Target Proxmox node where VM is created"
  type        = string
  default     = "pve-01"
}

variable "vm_id" {
  description = "Proxmox VM identifier"
  type        = number
  default     = 800
}

variable "vm_name" {
  description = "Name of the sovereign virtual machine"
  type        = string
  default     = "smdc-talos-node"
}

variable "cores" {
  description = "Number of CPU cores allocated"
  type        = number
  default     = 8
}

variable "memory_mb" {
  description = "Dedicated RAM in Megabytes"
  type        = number
  default     = 16384
}

variable "disk_size_gb" {
  description = "Storage capacity in Gigabytes"
  type        = number
  default     = 100
}

variable "datastore_id" {
  description = "Proxmox storage pool ID (e.g. local-zfs, nvme-pool)"
  type        = string
  default     = "local-zfs"
}

variable "iso_file_id" {
  description = "Proxmox ISO image ID (e.g. local:iso/talos-metal-amd64.iso)"
  type        = string
  default     = "local:iso/talos-amd64.iso"
}

variable "network_bridge" {
  description = "Network bridge interface (e.g. vmbr0 for 10GbE mesh)"
  type        = string
  default     = "vmbr0"
}

variable "vlan_tag" {
  description = "Optional VLAN tag for network isolation"
  type        = number
  default     = null
}

variable "enable_pci_gpu_passthrough" {
  description = "Enable PCIe device passthrough for dedicated NVIDIA GPU or Jetson"
  type        = bool
  default     = false
}

variable "pci_gpu_device" {
  description = "Raw PCI device ID on the Proxmox host (e.g. 0000:01:00.0). Used only when gpu_mapping is null. Proxmox only lets root@pam with username/password authentication attach a raw PCI id; it is rejected for API tokens."
  type        = string
  default     = "0000:01:00.0"
}

variable "gpu_mapping" {
  description = "Name of a Proxmox cluster resource mapping (Datacenter > Resource Mappings > PCI Devices) for the GPU. When set it is used instead of pci_gpu_device and works with API-token authentication."
  type        = string
  default     = null
}

variable "qemu_guest_agent_enabled" {
  description = "Enable the QEMU guest agent channel. Leave false for a stock Talos ISO, which ships no guest agent (the provider would otherwise wait for an agent that never answers). Set true only for Talos images built with the siderolabs/qemu-guest-agent extension."
  type        = bool
  default     = false
}
