module "talos" {
  source = "../../modules/talos-cluster"

  cluster_name        = var.cluster_name
  cluster_endpoint    = var.cluster_endpoint
  control_plane_nodes = var.control_plane_nodes
  worker_nodes        = var.worker_nodes
  enable_luks_encryption = true
  save_config_locally = true
  config_output_dir   = "${path.module}/_output"
}

provider "helm" {
  kubernetes {
    host                   = module.talos.kubernetes_client_configuration.host
    client_certificate     = base64decode(module.talos.kubernetes_client_configuration.client_certificate)
    client_key             = base64decode(module.talos.kubernetes_client_configuration.client_key)
    cluster_ca_certificate = base64decode(module.talos.kubernetes_client_configuration.ca_certificate)
  }
}

provider "kubernetes" {
  host                   = module.talos.kubernetes_client_configuration.host
  client_certificate     = base64decode(module.talos.kubernetes_client_configuration.client_certificate)
  client_key             = base64decode(module.talos.kubernetes_client_configuration.client_key)
  cluster_ca_certificate = base64decode(module.talos.kubernetes_client_configuration.ca_certificate)
}

module "sovereign_stack" {
  source = "../../modules/k8s-sovereign-stack"

  depends_on   = [module.talos]
  chart_path   = "${path.module}/../../../kubernetes/helm/sovereign-stack"
  domain       = var.domain
  ollama_model = var.ollama_model
  enable_gpu   = true
}
