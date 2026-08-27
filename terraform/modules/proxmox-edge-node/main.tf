resource "proxmox_virtual_environment_vm" "this" {
  node_name = var.node_name
  vm_id     = var.vm_id
  name      = var.vm_name

  cpu {
    cores = var.cores
    type  = "host"
  }

  memory {
    dedicated = var.memory_mb
  }

  disk {
    datastore_id = var.datastore_id
    interface    = "scsi0"
    size         = var.disk_size_gb
    file_format  = "raw"
    ssd          = true
    discard      = "on"
  }

  cdrom {
    file_id = var.iso_file_id
  }

  network_device {
    bridge  = var.network_bridge
    model   = "virtio"
    vlan_id = var.vlan_tag
  }

  operating_system {
    type = "l26"
  }

  agent {
    enabled = true
  }

  dynamic "hostpci" {
    for_each = var.enable_pci_gpu_passthrough ? [1] : []
    content {
      device = "hostpci0"
      id     = var.pci_gpu_device
      pcie   = true
      rombar = true
    }
  }

  tags = ["sovereign-dc", "talos", "edge-node"]
}
