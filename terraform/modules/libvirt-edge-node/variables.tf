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
  description = "Capacity in bytes of the blank root disk. Only used when base_image_path is empty; a disk created from base_image_path has the size of that image."
  type        = number
  default     = 53687091200 # 50 GiB
}

variable "base_image_path" {
  description = "Local path or URL to a bootable Talos / Ubuntu qcow2 disk image. If left empty the VM gets a blank disk and no boot media (this module attaches no ISO), so it will not boot an OS until you install one by other means."
  type        = string
  default     = ""
}

variable "network_name" {
  description = "Libvirt virtual network name (e.g. default, bridge)"
  type        = string
  default     = "default"
}

variable "wait_for_lease" {
  description = "Block until the VM obtains a DHCP lease on a libvirt-managed network. Keep false when base_image_path is empty (nothing boots, so no lease ever appears) or when network_name is a bridge without libvirt DHCP; ip_addresses is then empty."
  type        = bool
  default     = false
}
