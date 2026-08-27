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
        domain = var.domain
      }
      ai = {
        enabled = true
        ollama = {
          defaultModel = var.ollama_model
          resources = var.enable_gpu ? {
            limits = {
              "nvidia.com/gpu" = var.gpu_limit
            }
          } : null
        }
      }
      telemetry = {
        powerExporter = {
          simulate = var.simulate_telemetry
        }
      }
    })
  ], var.custom_values)
}
