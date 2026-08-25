# Edge Vision AI Pipeline

This template demonstrates an **$L_2$ Background** GPU-accelerated computer vision workload on SMDC.

## Architecture
- **Power Priority**: `L2_BACKGROUND` (Automatically pauses when battery SoC falls below 40% or thermal throttling triggers; automatically resumes when solar harvest exceeds 300 W).
- **GPU Acceleration**: NVIDIA Jetson Orin AGX TensorRT / CUDA runtime.
- **Space DTN Integration**: Spools RFC 9171 BPv7 telemetry bundles to orbital satellites when severe visual anomalies are identified.

## Quickstart
```bash
# Validate manifest
smdc app validate examples/enterprise_apps/edge-vision-ai/

# Register application
smdc app register examples/enterprise_apps/edge-vision-ai/

# Start pipeline
smdc app start edge-vision-ai

# Package application with Post-Quantum cryptographic signature
smdc app package examples/enterprise_apps/edge-vision-ai/ --output vision-ai-1.0.0.smdc-app
```
