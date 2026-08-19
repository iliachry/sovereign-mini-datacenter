# Sovereign Mini Datacenter — Bare-Metal Kubernetes & Talos Linux

This directory provides enterprise-grade declarative manifests for deploying the Sovereign Mini Datacenter on **Talos Linux** (immutable, security-hardened, API-only Linux) with **K3s / Vanilla Kubernetes** and NVIDIA GPU acceleration.

---

## 🚀 Quick Deployment

### 1. Flash Talos Linux to Compute Nodes
```bash
# Generate Talos configuration
talosctl gen config sovereign-cluster https://10.0.0.10:6443

# Apply machine config to node
talosctl apply-config --insecure --nodes 10.0.0.10 --file talos-config.yaml
```

### 2. Apply Sovereign Services
```bash
kubectl apply -f k3s-sovereign.yaml
```

### 3. Verify Cluster Health
```bash
kubectl get pods -n sovereign -o wide
```
