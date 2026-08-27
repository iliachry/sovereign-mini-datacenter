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
  description = "Allow self-signed certificates on Proxmox API"
  type        = bool
  default     = true
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
