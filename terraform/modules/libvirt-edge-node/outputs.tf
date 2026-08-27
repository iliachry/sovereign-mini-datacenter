output "domain_id" {
  description = "Libvirt domain identifier"
  value       = libvirt_domain.this.id
}

output "domain_name" {
  description = "Domain name"
  value       = libvirt_domain.this.name
}

output "ip_addresses" {
  description = "Assigned IP addresses"
  value       = libvirt_domain.this.network_interface[0].addresses
}
