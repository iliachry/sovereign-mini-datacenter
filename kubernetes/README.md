# Sovereign Mini Datacenter — Bare-Metal Kubernetes & Talos Linux

This directory provides enterprise-grade declarative manifests for deploying the Sovereign Mini Datacenter on **Talos Linux** (immutable, security-hardened, API-only Linux) with **K3s / Vanilla Kubernetes** and NVIDIA GPU acceleration.

---

## 🚀 Quick Deployment

### 1. Flash Talos Linux to Compute Nodes

`talos-config.yaml` is a **machine-config patch**, not a complete machine config: it holds no cluster secrets. Edit the values marked `EXAMPLE` (install disk, NIC name, addresses) to match your hardware, then let `talosctl` generate the secrets and merge the patch into the control-plane config:

```bash
# Generate controlplane.yaml, worker.yaml and talosconfig (patch applied to the control plane only)
talosctl gen config sovereign-cluster https://10.0.0.10:6443 \
  --config-patch-control-plane @talos-config.yaml

# Apply the GENERATED config to the node while it is in maintenance mode
# (--nodes is the address the node currently answers on, e.g. its DHCP lease)
talosctl apply-config --insecure --nodes 10.0.0.10 --file controlplane.yaml

# Bootstrap etcd once, then fetch the kubeconfig
talosctl --talosconfig ./talosconfig --endpoints 10.0.0.10 --nodes 10.0.0.10 bootstrap
talosctl --talosconfig ./talosconfig --endpoints 10.0.0.10 --nodes 10.0.0.10 kubeconfig
```

Keep the generated `controlplane.yaml`, `worker.yaml` and `talosconfig` out of version control: they contain the cluster secrets.

### Cluster prerequisites

Neither the manifest nor the Helm chart installs cluster add-ons. Before deploying, the cluster must provide:

| Prerequisite | Needed for | k3s | Talos / vanilla Kubernetes |
| :--- | :--- | :--- | :--- |
| StorageClass with a dynamic provisioner | every PersistentVolumeClaim | `local-path` built in | install one, then set `global.storageClass` |
| Ingress controller | the chart's Ingress | Traefik built in | install one and set `ingress.className`, or `ingress.enabled=false` |
| NVIDIA drivers, container runtime and device plugin | `nvidia.com/gpu` for Ollama | install the toolkit and device plugin | install the Talos NVIDIA extensions and device plugin, or `ai.ollama.gpu.enabled=false` |

Without them the pods stay `Pending`. `k3s-sovereign.yaml` targets k3s (default StorageClass, `nvidia` RuntimeClass).

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

`ai.ollama.defaultModel` is pulled in the background after the Ollama container starts (best effort). Other frequently needed switches: `global.storageClass`, `ingress.enabled`, `ingress.className`, `ai.ollama.gpu.enabled`, `ai.ollama.runtimeClassName` (set to `nvidia` where that RuntimeClass exists) and `space.enabled` (off by default because the `sovereign-space-node` image is not published by this repository's CI).

### 3. Verify Cluster Health
```bash
kubectl get pods -n sovereign -o wide
helm status sovereign-stack -n sovereign
```
