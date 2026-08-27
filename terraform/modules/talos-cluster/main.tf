resource "talos_machine_secrets" "this" {
  talos_version = var.talos_version
}

data "talos_client_configuration" "this" {
  cluster_name         = var.cluster_name
  client_configuration = talos_machine_secrets.this.client_configuration
  endpoints            = [for k, v in var.control_plane_nodes : v.ip]
}

data "talos_machine_configuration" "controlplane" {
  cluster_name     = var.cluster_name
  cluster_endpoint = var.cluster_endpoint
  machine_type     = "controlplane"
  machine_secrets  = talos_machine_secrets.this.machine_secrets
  talos_version    = var.talos_version
  kubernetes_version = var.kubernetes_version
  docs             = false
  examples         = false

  config_patches = [
    yamlencode({
      machine = {
        install = {
          disk = values(var.control_plane_nodes)[0].install_disk
        }
        network = {
          hostname = values(var.control_plane_nodes)[0].hostname
          nameservers = var.nameservers
        }
        systemDiskEncryption = var.enable_luks_encryption ? {
          state = {
            provider = "luks2"
            keys = [{ nodeID = {}, slot = 0 }]
          }
          ephemeral = {
            provider = "luks2"
            keys = [{ nodeID = {}, slot = 0 }]
          }
        } : null
        sysctls = {
          "net.ipv4.ip_forward"          = "1"
          "net.ipv4.tcp_syncookies"      = "1"
          "kernel.randomize_va_space"    = "2"
        }
      }
      cluster = {
        network = {
          cni = {
            name = "flannel"
          }
        }
      }
    })
  ]
}

data "talos_machine_configuration" "worker" {
  for_each = var.worker_nodes

  cluster_name       = var.cluster_name
  cluster_endpoint   = var.cluster_endpoint
  machine_type       = "worker"
  machine_secrets    = talos_machine_secrets.this.machine_secrets
  talos_version      = var.talos_version
  kubernetes_version = var.kubernetes_version
  docs               = false
  examples           = false

  config_patches = [
    yamlencode({
      machine = {
        install = {
          disk = each.value.install_disk
          extensions = each.value.has_gpu ? [
            { image = "ghcr.io/siderolabs/nvidia-container-toolkit:${var.talos_version}" },
            { image = "ghcr.io/siderolabs/nvidia-open-gpu-kernel-modules:${var.talos_version}" }
          ] : []
        }
        kernel = each.value.has_gpu ? {
          modules = [
            { name = "nvidia" },
            { name = "nvidia_uvm" },
            { name = "nvidia_modeset" }
          ]
        } : null
        network = {
          hostname    = each.value.hostname
          nameservers = var.nameservers
        }
        systemDiskEncryption = var.enable_luks_encryption ? {
          state = {
            provider = "luks2"
            keys = [{ nodeID = {}, slot = 0 }]
          }
          ephemeral = {
            provider = "luks2"
            keys = [{ nodeID = {}, slot = 0 }]
          }
        } : null
        nodeLabels = each.value.has_gpu ? {
          "smdc.io/gpu-accelerator" = "true"
          "nvidia.com/gpu.present"   = "true"
        } : {}
      }
    })
  ]
}

resource "talos_machine_configuration_apply" "controlplane" {
  for_each = var.control_plane_nodes

  client_configuration        = talos_machine_secrets.this.client_configuration
  machine_configuration_input = data.talos_machine_configuration.controlplane.machine_configuration
  node                        = each.value.ip
}

resource "talos_machine_configuration_apply" "worker" {
  for_each = var.worker_nodes

  client_configuration        = talos_machine_secrets.this.client_configuration
  machine_configuration_input = data.talos_machine_configuration.worker[each.key].machine_configuration
  node                        = each.value.ip
}

resource "talos_machine_bootstrap" "this" {
  depends_on = [talos_machine_configuration_apply.controlplane]

  client_configuration = talos_machine_secrets.this.client_configuration
  node                 = values(var.control_plane_nodes)[0].ip
}

resource "talos_cluster_kubeconfig" "this" {
  depends_on = [talos_machine_bootstrap.this]

  client_configuration = talos_machine_secrets.this.client_configuration
  node                 = values(var.control_plane_nodes)[0].ip
}

resource "local_file" "talosconfig" {
  count           = var.save_config_locally ? 1 : 0
  content         = data.talos_client_configuration.this.talos_config
  filename        = "${var.config_output_dir}/talosconfig"
  file_permission = "0600"
}

resource "local_file" "kubeconfig" {
  count           = var.save_config_locally ? 1 : 0
  content         = talos_cluster_kubeconfig.this.kubeconfig_raw
  filename        = "${var.config_output_dir}/kubeconfig"
  file_permission = "0600"
}
