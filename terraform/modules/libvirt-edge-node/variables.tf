variable "domain_name" {
  description = "Name of the Libvirt/KVM virtual machine"
  type        = string
  default     = "smdc-dev-node"
}

variable "vcpu" {
  description = "Number of virtual CPUs"
  type        = number
  default     = 4
}

variable "memory_mb" {
  description = "Memory in Megabytes"
  type        = number
  default     = 8192
}

variable "pool_name" {
  description = "Libvirt storage pool name"
  type        = string
  default     = "default"
}

variable "disk_size_bytes" {
  description = "Disk capacity in bytes"
  type        = number
  default     = 53687091200 # 50 GiB
}

variable "base_image_path" {
  description = "Local path or URL to Talos / Ubuntu raw/qcow2 image"
  type        = string
  default     = ""
}

variable "network_name" {
  description = "Libvirt virtual network name (e.g. default, bridge)"
  type        = string
  default     = "default"
}
