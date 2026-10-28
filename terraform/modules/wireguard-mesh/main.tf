resource "wireguard_asymmetric_key" "node_key" {
  for_each = var.nodes
}

locals {
  # Every unordered pair of nodes shares exactly one PresharedKey. The pair key
  # is the sorted "<a>|<b>" tuple so that node_a -> node_b and node_b -> node_a
  # both resolve to the same symmetric key (WireGuard requires PSKs to match on
  # both ends of a tunnel).
  peer_pairs = toset(flatten([
    for node_name in keys(var.nodes) : [
      for peer_name in keys(var.nodes) :
      join("|", sort([node_name, peer_name])) if peer_name != node_name
    ]
  ]))
}

resource "wireguard_preshared_key" "pair_key" {
  for_each = local.peer_pairs
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
          preshared_key        = wireguard_preshared_key.pair_key[join("|", sort([node_name, peer_name]))].key
          allowed_ips          = join(", ", peer_val.allowed_ips)
          endpoint             = peer_val.endpoint
          persistent_keepalive = peer_val.persistent_keepalive
        } if peer_name != node_name
      ]
    })
  }
}
