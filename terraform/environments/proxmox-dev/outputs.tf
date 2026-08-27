output "control_plane_vm_id" {
  description = "VM ID of the virtual control plane"
  value       = module.control_plane.vm_id
}

output "worker_gpu_vm_id" {
  description = "VM ID of the virtual GPU worker"
  value       = module.worker_gpu.vm_id
}
