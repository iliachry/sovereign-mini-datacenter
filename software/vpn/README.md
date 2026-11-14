# Sovereign Mesh VPN (Headscale)

This module provides **WireGuard tunnels between enrolled nodes**, coordinated by a self-hosted [Headscale](https://github.com/juanfont/headscale) control server (the open-source implementation of the Tailscale control plane). It is a mesh VPN, not a zero-trust architecture: **no ACL policy is shipped**, so every enrolled node can reach every other enrolled node on every port (see [Access control](#access-control-no-acl-policy-is-shipped)).

---

## Why Headscale?

- **Private access path:** Enrolled devices reach the node over the mesh. The VPN itself needs the Headscale HTTPS endpoint (`DOMAIN_VPN`, through Traefik on 443) and UDP 3478 (STUN) reachable from the internet. Enrolment requires a pre-authenticated key or an operator-approved node key.
- **WireGuard tunnels between enrolled nodes:** Traffic between two nodes is encrypted by WireGuard from one node to the other (the relay cannot read it); this covers the tunnel, not the applications inside it. Direct peer-to-peer tunnels with NAT traversal (STUN) and relay fallback through the DERP server embedded in Headscale — by default the only relay clients are given.
- **No third-party control plane:** Node keys, the node list and the relay run on your own server; with the default configuration no relay map is downloaded from Tailscale Inc.
- **Cross-Platform:** Works natively with official Tailscale client apps on macOS, iOS, Android, Linux, and Windows.

---

## Quick Setup

### 1. Launch Headscale

Edit `software/vpn/config/headscale.yaml` first and set `server_url` to your public
VPN URL (the same host as `DOMAIN_VPN` in `software/.env`). Headscale does not expand
environment variables in this file.

```bash
./software/setup.sh --with-vpn
# or standalone (the core stack's Traefik must be running to reach it over HTTPS):
docker compose --project-directory software \
  -f software/docker-compose.attach.yml -f software/vpn/docker-compose.vpn.yml up -d
```

The image is pinned to `headscale/headscale:0.23.0`; the config file follows that
version's schema. Review both together when upgrading.

#### Relays (DERP): self-hosted by default

`config/headscale.yaml` enables Headscale's embedded DERP relay (region `999`,
"Sovereign DERP") and sets `derp.urls: []` with `auto_update_enabled: false`, so no
third-party relay map is downloaded and relayed traffic never passes through
Tailscale Inc. relays. For it to work:

- `server_url` must be an `https://` URL (DERP requires TLS); Traefik terminates TLS
  for `DOMAIN_VPN` and forwards all paths, including `/derp`, to Headscale.
- **UDP 3478 (STUN)** must be reachable from the internet. The overlay publishes it;
  open it on the host firewall and forward it on the router.

This means a single relay: peers that cannot reach each other directly are cut off
while this node is unreachable. Adding Tailscale's public DERP map is an **opt-in
fallback** — in `headscale.yaml` set
`derp.urls: [https://controlplane.tailscale.com/derpmap/default]` and
`auto_update_enabled: true`. Payloads stay WireGuard-encrypted end to end, but relay
metadata and availability then depend on a third party.

#### DNS: no resolver is pushed by default

`dns.nameservers.global` is empty, so Headscale hands clients no upstream resolver:
MagicDNS still resolves `<host>.sovereign.mesh` between mesh nodes, and every other
name is resolved by the DNS servers of the network each client is on. No public
resolver (Cloudflare, Quad9, ...) is imposed on the fleet. To push resolvers, list
them under `dns.nameservers.global` in `config/headscale.yaml` (clients then use
them instead of their local DNS) — ideally one you run on the mesh — or under
`dns.nameservers.split` for selected domains only, then restart the container:
`docker restart sovereign_headscale`. Headscale 0.23.0 has no
`dns.override_local_dns` key; on a later release keep it `false` while the list is
empty.

#### Access control: no ACL policy is shipped

`config/headscale.yaml` has no `policy` section, and without a policy Headscale allows
all traffic between all enrolled nodes. To restrict it:

1. Write a policy file, for example `software/vpn/config/acl.hujson` (format:
   [Headscale ACL reference](https://headscale.net/stable/ref/acls/)).
2. Mount it in `docker-compose.vpn.yml` next to the config file:
   `- ./vpn/config/acl.hujson:/etc/headscale/acl.hujson:ro`
3. Point Headscale at it in `config/headscale.yaml` (keys of the pinned 0.23 schema;
   check them against the Headscale documentation when you upgrade):
   ```yaml
   policy:
     mode: file
     path: /etc/headscale/acl.hujson
   ```
4. `docker restart sovereign_headscale`, then confirm from a client that a connection
   the policy should deny is refused.

### 2. Create a User

```bash
chmod +x software/vpn/register-node.sh
./software/vpn/register-node.sh create-user admin
```

### 3. Generate a Pre-Authenticated Key

```bash
./software/vpn/register-node.sh create-authkey admin --reusable --expiration 90d
```

---

## Connecting Client Devices

### Linux / Servers
```bash
tailscale up --login-server https://vpn.yourdomain.com --authkey <YOUR_AUTH_KEY>
```

### macOS / Windows
1. Open Tailscale preferences.
2. Under **Custom Login Server**, enter `https://vpn.yourdomain.com`.
3. Click Log In and authenticate or register using the node key in `register-node.sh`:
```bash
./software/vpn/register-node.sh register-node admin <NODE_KEY>
```

### iOS / Android
1. In Tailscale app settings, tap on **Alternate Server**.
2. Enter `https://vpn.yourdomain.com`.
3. Sign in to your sovereign network.