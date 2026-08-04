#!/usr/bin/env bash
# ====================================================================
# Sovereign Mini Datacenter - Node Initialization & Setup Script
# Target OS: Ubuntu Server 24.04 LTS (x86_64 / arm64)
# ====================================================================

set -e

echo "🚀 [1/4] Updating system packages..."
sudo apt-get update && sudo apt-get upgrade -y
sudo apt-get install -y curl wget git build-essential ca-certificates gnupg lsb-release

echo "🐳 [2/4] Installing Docker Engine & Docker Compose..."
if ! command -v docker &> /dev/null; then
    sudo mkdir -p /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    echo \
      "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
      $(lsb_release -cs) stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
    sudo apt-get update
    sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
    sudo usermod -aG docker $USER
fi

echo "🟢 [3/4] Installing NVIDIA Container Toolkit for GPU Acceleration..."
if ! command -v nvidia-smi &> /dev/null; then
    echo "⚠️ NVIDIA Drivers not detected. Please install nvidia-driver-535 or higher."
else
    curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
    curl -s -L https://nvidia.github.io/libnvidia-container/experimental/deb/libnvidia-container.list | \
      sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' | \
      sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list
    sudo apt-get update
    sudo apt-get install -y nvidia-container-toolkit
    sudo nvidia-ctk runtime configure --runtime=docker
    sudo systemctl restart docker
fi

echo "📦 [4/4] Starting Sovereign Stack..."
if [ ! -f .env ]; then
    cp env.example .env
fi

docker compose up -d

echo ""
echo "===================================================================="
echo "✅ Sovereign Mini Datacenter Stack Deployed Successfully!"
echo "--------------------------------------------------------------------"
echo "  • Open-WebUI (AI Chat):  http://localhost:3000"
echo "  • Ollama LLM API:       http://localhost:11434"
echo "  • GitLab CE:            http://localhost:8080"
echo "  • OpenProject:          http://localhost:8081"
echo "  • NextCloud:            http://localhost:8082"
echo "===================================================================="
