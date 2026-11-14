resource "helm_release" "sovereign_stack" {
  name             = var.release_name
  chart            = var.chart_path
  namespace        = var.namespace
  create_namespace = var.create_namespace
  wait             = true
  timeout          = 600

  values = concat([
    yamlencode({
      global = {
        domain       = var.domain
        storageClass = var.storage_class
      }
      ai = {
        enabled = true
        ollama = {
          defaultModel     = var.ollama_model
          runtimeClassName = var.gpu_runtime_class_name
          gpu = {
            enabled = var.enable_gpu
            count   = var.gpu_limit
          }
        }
      }
      ingress = {
        enabled   = var.ingress_enabled
        className = var.ingress_class_name
      }
      telemetry = {
        powerExporter = {
          simulate  = var.simulate_telemetry
          bridgeUrl = var.telemetry_bridge_url
        }
      }
    })
  ], var.custom_values)
}
