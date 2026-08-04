# Sovereign Mini Datacenter

![Sovereign Mini Datacenter 3D Render](cad/render.jpg)

**Sovereign Mini Datacenter** is a self-powered, liquid-cooled micro-datacenter stack designed for complete data autonomy.

Developed by **[Metatopia Studio](https://metatopia.gr)**.

---

## 🏗️ Repository Modules

### 1. 💻 Software & Orchestration ([`software/`](software/))
* **[`docker-compose.yml`](software/docker-compose.yml):** Production stack with Ollama (GPU-accelerated local LLMs), Open-WebUI, GitLab CE, OpenProject, and NextCloud Hub.
* **[`setup.sh`](software/setup.sh):** Executable bash installer for Ubuntu Server 24.04 LTS (NVIDIA drivers, CUDA toolkit, Docker runtime).
* **[`env.example`](software/env.example):** Environment configuration template.

### 2. ⚡ Hardware Specifications ([`hardware/`](hardware/))
* **[`COMPONENTS.md`](hardware/COMPONENTS.md):** Complete Bill of Materials (NVIDIA DGX Spark, Victron Energy MPPT & Inverter, LiFePO4 48V 10.24kWh Battery Bank, Solar PV Array, EKWB/Alphacool Liquid Cooling).
* **[`WIRING_DIAGRAM.md`](hardware/WIRING_DIAGRAM.md):** Electrical schematics (DC solar/battery routing, AC power distribution, and dual 360mm liquid cooling loop).

### 3. 📐 CAD & Manufacturing ([`cad/`](cad/))
* **[`rack_enclosure.scad`](cad/rack_enclosure.scad):** 19-inch 9U ruggedized enclosure 3D model in OpenSCAD.
* **[`MANUFACTURING_GUIDE.md`](cad/MANUFACTURING_GUIDE.md):** Aluminum sheet metal laser cutting, CNC bending & assembly guide.

---

## 📄 License

Distributed under the MIT License. See [`LICENSE`](LICENSE) for details.  
© 2026 Metatopia Studio.
