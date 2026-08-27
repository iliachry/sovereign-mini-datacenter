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
  description = "PCI device ID on Proxmox host (e.g. 0000:01:00.0)"
  type        = string
  default     = "0000:01:00.0"
}
