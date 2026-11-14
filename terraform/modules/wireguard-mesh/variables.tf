variable "mesh_subnet" {
  description = "Overlay mesh network CIDR. Every node's mesh_ip must fall inside it (enforced by a precondition)."
  type        = string
  default     = "10.42.0.0/16"
}

variable "listen_port" {
  description = "WireGuard UDP listen port"
  type        = number
  default     = 51820
}

variable "nodes" {
  description = <<-EOT
    Map of mesh node definitions.
      mesh_ip     - overlay address of the node in CIDR form (e.g. 10.42.0.1/32); must be inside mesh_subnet.
      endpoint    - public "host:port" other peers dial; omit for nodes behind NAT.
      allowed_ips - EXTRA CIDRs routed to this node by its peers (e.g. a LAN behind it).
                    The node's own mesh address (/32) is always included automatically.
                    WireGuard maps a prefix to exactly one peer, so these CIDRs must not
                    overlap between nodes; never list the whole mesh_subnet here.
  EOT
  type = map(object({
    mesh_ip              = string
    endpoint             = optional(string)
    allowed_ips          = optional(list(string), [])
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
