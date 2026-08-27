output "release_name" {
  description = "Helm release name"
  value       = helm_release.sovereign_stack.name
}

output "namespace" {
  description = "Namespace where sovereign stack is deployed"
  value       = helm_release.sovereign_stack.namespace
}

output "status" {
  description = "Helm release status"
  value       = helm_release.sovereign_stack.status
}
