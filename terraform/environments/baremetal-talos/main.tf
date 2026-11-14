module "talos" {
  source = "../../modules/talos-cluster"

  cluster_name           = var.cluster_name
  cluster_endpoint       = var.cluster_endpoint
  control_plane_nodes    = var.control_plane_nodes
  worker_nodes           = var.worker_nodes
  gateway                = var.gateway
  network_prefix_length  = var.network_prefix_length
  gpu_installer_image    = var.gpu_installer_image
  gpu_extension_images   = var.gpu_extension_images
  enable_luks_encryption = true
  save_config_locally    = true
  config_output_dir      = "${path.module}/_output"
}

provider "helm" {
  kubernetes {
    host                   = module.talos.kubernetes_client_configuration.host
    client_certificate     = base64decode(module.talos.kubernetes_client_configuration.client_certificate)
    client_key             = base64decode(module.talos.kubernetes_client_configuration.client_key)
    cluster_ca_certificate = base64decode(module.talos.kubernetes_client_configuration.ca_certificate)
  }
}

module "sovereign_stack" {
  source = "../../modules/k8s-sovereign-stack"

  depends_on = [module.talos]
  chart_path = "${path.module}/../../../kubernetes/helm/sovereign-stack"
  domain     = var.domain

  ollama_model = var.ollama_model

  # Cluster add-ons that Talos does not ship and this environment does not
  # install. See "Cluster prerequisites" in terraform/README.md.
  storage_class          = var.storage_class
  ingress_enabled        = var.ingress_enabled
  ingress_class_name     = var.ingress_class_name
  enable_gpu             = var.enable_gpu
  gpu_runtime_class_name = var.gpu_runtime_class_name
}
