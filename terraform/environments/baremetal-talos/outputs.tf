output "kubeconfig_path" {
  description = "Path to generated kubeconfig"
  value       = "${path.module}/_output/kubeconfig"
}

output "talosconfig_path" {
  description = "Path to generated talosconfig"
  value       = "${path.module}/_output/talosconfig"
}

output "helm_release_status" {
  description = "Status of sovereign-stack Helm release"
  value       = module.sovereign_stack.status
}
