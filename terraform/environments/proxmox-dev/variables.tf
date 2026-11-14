variable "proxmox_endpoint" {
  description = "Proxmox VE API endpoint URL (e.g. https://10.0.0.5:8006/)"
  type        = string
  default     = "https://10.0.0.5:8006/"
}

variable "proxmox_api_token" {
  description = "Proxmox API token (e.g. root@pam!token_name=uuid)"
  type        = string
  default     = ""
  sensitive   = true
}

variable "proxmox_insecure" {
  description = "Skip TLS verification of the Proxmox API certificate. Only set true for a lab host that still uses the self-signed certificate."
  type        = bool
  default     = false
}

variable "target_node" {
  description = "Target Proxmox node"
  type        = string
  default     = "pve-01"
}

variable "cluster_name" {
  description = "Cluster identifier"
  type        = string
  default     = "smdc-proxmox-dev"
}

variable "gpu_mapping" {
  description = "Name of the Proxmox PCI resource mapping (Datacenter > Resource Mappings) that exposes the GPU to the worker VM. Required because API tokens cannot pass through a raw PCI id."
  type        = string
}
