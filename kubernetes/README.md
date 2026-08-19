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

### 2. Apply Sovereign Services (Manifest or Helm)

**Option A — Direct Manifest:**
```bash
kubectl apply -f k3s-sovereign.yaml
```

**Option B — Production Helm Chart:**
```bash
helm install sovereign-stack ./kubernetes/helm/sovereign-stack \
  --namespace sovereign \
  --create-namespace \
  --set ai.ollama.defaultModel="qwen2.5-coder:7b"
```

### 3. Verify Cluster Health
```bash
kubectl get pods -n sovereign -o wide
helm status sovereign-stack -n sovereign
```
