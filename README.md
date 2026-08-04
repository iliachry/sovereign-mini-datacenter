# Sovereign Mini Datacenter

[![License: MIT](https://img.shields.io/badge/License-MIT-white.svg)](LICENSE)
[![Status: Prototype](https://img.shields.io/badge/Status-Prototype%20%26%20CAD-emerald.svg)](CAD/)
[![Compute: DGX Spark](https://img.shields.io/badge/Compute-Nvidia%20DGX%20Spark-76b900.svg)](docs/ELECTRICAL_THERMAL_SPECS.md)

**Sovereign Mini Datacenter** is an off-grid / hybrid, liquid-cooled micro-datacenter unit engineered for total physical and digital data autonomy. It integrates high-density AI compute nodes (Nvidia DGX Spark) with solar PV generation, high-density battery storage, and an open-source enterprise cloud orchestration stack.

Developed by **[Metatopia Studio](https://metatopia.gr)**.

---

## 🏗️ Hardware Architecture

The micro-datacenter is built as a self-contained, weather-sealed industrial enclosure:

1. **Compute Core:** Multi-node Nvidia DGX Spark GPU cluster (AI Inference & Training).
2. **Thermal Management:** Closed-loop dual-radiator liquid cooling system (Water/Glycol coolant blend, low-noise high-pressure pumps).
3. **Power Subsystem:** 
   * Photovoltaic (PV) Solar Array input (1.2 kWp - 3.6 kWp).
   * LiFePO4 Battery Bank (5.12 kWh - 15.36 kWh capacity).
   * MPPT Charge Controller & Pure Sine Wave Hybrid Inverter.
4. **Physical Chassis:** 19-inch 6U/12U ruggedized aluminum & carbon-composite enclosure.

---

## 💻 Sovereign Software Stack

Zero cloud dependency. The unit hosts a pre-configured open-source enterprise suite:

* **Local AI Inference:** vLLM / Ollama (DeepSeek, Llama 3, Mistral local execution).
* **Code & CI/CD:** GitLab Community Edition.
* **Project Management:** OpenProject.
* **Storage & Collaboration:** NextCloud Hub (End-to-End Encrypted).
* **Monitoring & Telemetry:** Prometheus + Grafana thermal/power dashboard.

---

## 📐 CAD & Engineering Files

* [`CAD/datacenter_enclosure.scad`](CAD/datacenter_enclosure.scad): OpenSCAD parametric 3D CAD model for enclosure & rack assembly.
* [`CAD/cooling_loop.scad`](CAD/cooling_loop.scad): OpenSCAD liquid cooling manifold & radiator layout.
* [`docs/ELECTRICAL_THERMAL_SPECS.md`](docs/ELECTRICAL_THERMAL_SPECS.md): Thermal dissipation & electrical sizing calculations.
* [`deploy/docker-compose.yml`](deploy/docker-compose.yml): Production Docker orchestration stack.
* [`viewer/index.html`](viewer/index.html): Interactive 3D WebGL CAD visualizer.

---

## 🚀 Interactive 3D Viewer

Open `viewer/index.html` in any browser to inspect the 3D model of the Sovereign Mini Datacenter with real-time exploded component views.

---

## 📄 License

Distributed under the MIT License. See [`LICENSE`](LICENSE) for details.  
© 2026 [Metatopia Studio](https://metatopia.gr).
