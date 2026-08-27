terraform {
  required_version = ">= 1.6.0"

  required_providers {
    wireguard = {
      source  = "OJFord/wireguard"
      version = ">= 0.3.0, < 1.0.0"
    }
  }
}
