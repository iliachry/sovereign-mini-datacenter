resource "wireguard_asymmetric_key" "node_key" {
  for_each = var.nodes
}

resource "wireguard_preshared_key" "preshared_key" {
  for_each = var.nodes
}

locals {
  # Build full peer mesh configuration for each node
  node_configs = {
    for node_name, node_val in var.nodes : node_name => templatefile("${path.module}/templates/wg.conf.tpl", {
      private_key = wireguard_asymmetric_key.node_key[node_name].private_key
      address     = node_val.mesh_ip
      listen_port = var.listen_port
      peers = [
        for peer_name, peer_val in var.nodes : {
          public_key           = wireguard_asymmetric_key.node_key[peer_name].public_key
          preshared_key        = wireguard_preshared_key.preshared_key[peer_name].key
          allowed_ips          = join(", ", peer_val.allowed_ips)
          endpoint             = peer_val.endpoint
          persistent_keepalive = peer_val.persistent_keepalive
        } if peer_name != node_name
      ]
    })
  }
}
