resource "libvirt_volume" "disk" {
  name   = "${var.domain_name}-root.qcow2"
  pool   = var.pool_name
  size   = var.disk_size_bytes
  source = var.base_image_path != "" ? var.base_image_path : null
  format = "qcow2"
}

resource "libvirt_domain" "this" {
  name   = var.domain_name
  memory = var.memory_mb
  vcpu   = var.vcpu

  cpu {
    mode = "host-passthrough"
  }

  disk {
    volume_id = libvirt_volume.disk.id
  }

  network_interface {
    network_name   = var.network_name
    wait_for_lease = true
  }

  console {
    type        = "pty"
    target_port = "0"
    target_type = "serial"
  }

  graphics {
    type        = "vnc"
    listen_type = "address"
  }
}
