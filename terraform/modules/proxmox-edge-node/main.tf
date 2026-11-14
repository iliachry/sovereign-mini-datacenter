resource "proxmox_virtual_environment_vm" "this" {
  node_name = var.node_name
  vm_id     = var.vm_id
  name      = var.vm_name

  # PCIe passthrough (hostpci pcie = true) is only valid on the q35 machine type.
  machine = var.enable_pci_gpu_passthrough ? "q35" : null

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
    enabled = var.qemu_guest_agent_enabled
  }

  dynamic "hostpci" {
    for_each = var.enable_pci_gpu_passthrough ? [1] : []
    content {
      device = "hostpci0"
      # Exactly one of id / mapping is set. A raw PCI id can only be attached
      # by root@pam with password auth; API tokens must use a resource mapping.
      id      = var.gpu_mapping == null ? var.pci_gpu_device : null
      mapping = var.gpu_mapping
      pcie    = true
      rombar  = true
    }
  }

  tags = ["sovereign-dc", "talos", "edge-node"]
}
