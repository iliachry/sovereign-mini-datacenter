output "public_keys" {
  description = "Map of public keys per mesh node"
  value       = { for k, v in wireguard_asymmetric_key.node_key : k => v.public_key }
}

output "node_configs" {
  description = "Map of generated WireGuard configuration contents per node"
  value       = local.node_configs
  sensitive   = true
}
