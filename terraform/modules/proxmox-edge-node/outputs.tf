output "vm_id" {
  description = "Proxmox VM identifier"
  value       = proxmox_virtual_environment_vm.this.vm_id
}

output "vm_name" {
  description = "VM name"
  value       = proxmox_virtual_environment_vm.this.name
}

output "mac_addresses" {
  description = "MAC addresses assigned to VM network interfaces"
  value       = proxmox_virtual_environment_vm.this.network_device[*].mac_address
}
