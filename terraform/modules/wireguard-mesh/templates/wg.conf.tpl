[Interface]
PrivateKey = ${private_key}
Address = ${address}
ListenPort = ${listen_port}

%{ for peer in peers ~}
[Peer]
PublicKey = ${peer.public_key}
PresharedKey = ${peer.preshared_key}
AllowedIPs = ${peer.allowed_ips}
%{ if peer.endpoint != null ~}
Endpoint = ${peer.endpoint}
%{ endif ~}
%{ if peer.persistent_keepalive != null ~}
PersistentKeepalive = ${peer.persistent_keepalive}
%{ endif ~}

%{ endfor ~}
