output "kubeconfig_raw" {
  description = "Raw kubeconfig content for connecting to the Kubernetes cluster"
  value       = talos_cluster_kubeconfig.this.kubeconfig_raw
  sensitive   = true
}

output "talosconfig" {
  description = "Client talosconfig configuration"
  value       = data.talos_client_configuration.this.talos_config
  sensitive   = true
}

output "kubernetes_client_configuration" {
  description = "Kubernetes client connection credentials extracted from kubeconfig"
  value       = talos_cluster_kubeconfig.this.kubernetes_client_configuration
  sensitive   = true
}

output "control_plane_ips" {
  description = "IP addresses of all control plane nodes"
  value       = [for node in var.control_plane_nodes : node.ip]
}

output "worker_ips" {
  description = "IP addresses of all worker nodes"
  value       = [for node in var.worker_nodes : node.ip]
}
