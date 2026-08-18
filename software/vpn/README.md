# Sovereign Mesh VPN (Headscale)

This module provides a **self-hosted, zero-trust peer-to-peer WireGuard mesh VPN** powered by [Headscale](https://github.com/juanfont/headscale), the open-source control plane for Tailscale.

---

## ?? Why Headscale?

- **Zero-Trust Access:** Connect securely to all sovereign datacenter services without opening public ports.
- **End-to-End Encryption:** Direct peer-to-peer WireGuard tunnels with automatic NAT traversal via built-in DERP relay.
- **No Third-Party Cloud:** Zero telemetry, keys and metadata never leave your hardware.
- **Cross-Platform:** Works natively with official Tailscale client apps on macOS, iOS, Android, Linux, and Windows.

---

## ?? Quick Setup

### 1. Launch Headscale

```bash
docker compose -f software/vpn/docker-compose.vpn.yml up -d
```

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

## ?? Connecting Client Devices

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