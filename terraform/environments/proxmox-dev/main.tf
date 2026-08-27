provider "proxmox" {
  endpoint = var.proxmox_endpoint
  api_token = var.proxmox_api_token
  insecure = var.proxmox_insecure
}

module "control_plane" {
  source = "../../modules/proxmox-edge-node"

  node_name    = var.target_node
  vm_id        = 801
  vm_name      = "smdc-dev-cp01"
  cores        = 4
  memory_mb    = 8192
  disk_size_gb = 50
}

module "worker_gpu" {
  source = "../../modules/proxmox-edge-node"

  node_name                  = var.target_node
  vm_id                      = 802
  vm_name                    = "smdc-dev-gpu01"
  cores                      = 8
  memory_mb                  = 16384
  disk_size_gb               = 100
  enable_pci_gpu_passthrough = true
  pci_gpu_device             = "0000:01:00.0"
}
