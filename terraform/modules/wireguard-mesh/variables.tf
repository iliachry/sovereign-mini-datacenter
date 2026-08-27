variable "mesh_subnet" {
  description = "Overlay mesh network CIDR"
  type        = string
  default     = "10.42.0.0/16"
}

variable "listen_port" {
  description = "WireGuard UDP listen port"
  type        = number
  default     = 51820
}

variable "nodes" {
  description = "Map of mesh node definitions"
  type = map(object({
    mesh_ip           = string
    endpoint          = optional(string)
    allowed_ips       = optional(list(string), ["10.42.0.0/16"])
    persistent_keepalive = optional(number, 25)
  }))
  default = {
    "node-01" = {
      mesh_ip  = "10.42.0.1/32"
      endpoint = "203.0.113.10:51820"
    }
    "node-02" = {
      mesh_ip  = "10.42.0.2/32"
      endpoint = "203.0.113.20:51820"
    }
  }
}
