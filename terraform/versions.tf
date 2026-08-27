terraform {
  required_version = ">= 1.6.0"

  required_providers {
    talos = {
      source  = "siderolabs/talos"
      version = "~> 0.7.0"
    }
    helm = {
      source  = "hashicorp/helm"
      version = "~> 2.16.0"
    }
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = "~> 2.35.0"
    }
    proxmox = {
      source  = "bpg/proxmox"
      version = "~> 0.70.0"
    }
    libvirt = {
      source  = "dmacvicar/libvirt"
      version = "~> 0.8.1"
    }
    wireguard = {
      source  = "OJFord/wireguard"
      version = "~> 0.3.1"
    }
    local = {
      source  = "hashicorp/local"
      version = "~> 2.5.0"
    }
    tls = {
      source  = "hashicorp/tls"
      version = "~> 4.0.0"
    }
  }
}
