terraform {
  required_version = ">= 1.6.0"

  required_providers {
    libvirt = {
      source  = "dmacvicar/libvirt"
      # 0.8.x only: this module uses the 0.8 resource schema (size/source on
      # libvirt_volume, cpu/disk/network_interface blocks on libvirt_domain).
      version = "~> 0.8.0"
    }
  }
}
