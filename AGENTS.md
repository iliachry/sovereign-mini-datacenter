# 🤖 AI Agent Engineering Playbook & Repository Guide

> **Target Audience**: Autonomous AI coding agents (Antigravity, Claude, Cursor, Copilot, Cline, Aider, Windsurf) and human engineers working on the **Sovereign Mini Datacenter** codebase.  
> **Repository**: [sovereign-mini-datacenter](https://github.com/iliachry/sovereign-mini-datacenter)  
> **Author & Lead Architect**: [Ilias Chrysovergis](https://iliachry.gr) · [Metatopia Studio](https://metatopia.gr) · License: MIT · © 2026

---

## 1. Repository Purpose & Engineering Philosophy

**Sovereign Mini Datacenter** is an open-source, self-powered, solar-backed, liquid-cooled micro-datacenter stack designed for **complete data and computational sovereignty**.

### Key Architectural Pillars
1. **100% Off-Grid Independence**: Operates without relying on centralized cloud providers (AWS, GCP, Azure, Cloudflare) or public utility grids.
2. **Local AI & Multi-Agent Copilots**: On-premise LLM inference (Ollama) + semantic vector search (Qdrant) running on NVIDIA Jetson Orin AGX / DGX accelerators.
3. **Multi-Spectral Failover & Space DTN**: Resilient communication tiered from 10GbE fiber mesh $\to$ Starlink/5G $\to$ Sub-GHz LoRa Meshtastic $\to$ RFC 9171 Space Delay-Tolerant Networking (BPv7) with SGP4 orbital tracking.
4. **Hardware-Enforced Load Shedding**: Real-time solar and battery State-of-Charge (SoC) telemetry automatically throttles heavy GPU batch workloads during low power states.

---

## 2. Repository Directory Structure

```
sovereign-mini-datacenter/
├── src/
│   └── sovereign_dc/            # Core Python library & CLI package (`smdc`)
│       ├── __init__.py          # Version definition
│       ├── __main__.py          # `python -m sovereign_dc` entry point
│       ├── cli.py               # Unified click/rich CLI interface
│       ├── telemetry.py         # Hardware telemetry parsers (VE.Direct, RS485, DS18B20)
│       ├── agents/              # Autonomous local agent engines
│       │   ├── sentinel_copilot.py   # Energy & thermal watchdog + load shedder
│       │   ├── knowledge_indexer.py  # Semantic chunker & Qdrant RAG vectorizer
│       │   └── gitlab_reviewer.py    # Automated git diff AI code reviewer
│       ├── mesh/                # Multi-node peer-to-peer & LoRa networking
│       │   ├── mesh_sync.py          # WireGuard peer health and state synchronizer
│       │   └── lora/
│       │       └── meshtastic_gateway.py # Sub-GHz LoRa packet gateway (AES-256-GCM)
│       └── space/               # Space communications & satellite tracking
│           ├── space_exporter.py     # Prometheus exporter for orbital/link metrics
│           ├── dtn/                  # RFC 9171 Delay-Tolerant Networking (BPv7)
│           │   ├── bundle.py         # Bundle creation, CBOR/JSON serialization, TTL
│           │   └── router.py         # Persistent NVMe store-and-forward spool
│           ├── orbital/              # Satellite orbital mechanics (SGP4)
│           │   ├── propagator.py     # AOS/LOS contact pass calculator
│           │   └── tle_updater.py    # Two-Line Element (TLE) ephemeris fetcher
│           └── transceiver/          # RF Link budget & ground station models
│               └── simulated_link.py # FSPL, Doppler shift, SNR, azimuth/elevation
├── software/                    # Production deployment & service definitions
│   ├── docker-compose.yml       # Primary 11-service production stack
│   ├── setup.sh                 # Modular deployment CLI script
│   ├── env.example              # Environment variables template
│   ├── prometheus.yml           # Prometheus scrape configurations
│   ├── agents/                  # Standalone agent daemon scripts & compose files
│   ├── mesh/                    # Mesh sync daemons & LoRa gateway configs
│   ├── vpn/                     # Headscale / WireGuard zero-trust VPN configs
│   ├── backup/                  # Restic automated encrypted backup engine
│   ├── telemetry/               # Python/Prometheus power & BMS exporter
│   ├── space/                   # Space link telemetry exporter
│   ├── mailcow/                 # Sovereign mail server configurations
│   └── grafana/                 # Pre-provisioned dashboards (Power, Thermal, Space)
├── kubernetes/                  # Kubernetes cluster deployment configurations
│   ├── helm/sovereign-stack/    # Production Helm chart (AI, telemetry, ingress)
│   ├── k3s-sovereign.yaml       # K3s lightweight Kubernetes cluster manifest
│   └── talos-config.yaml        # Talos Linux immutable bare-metal OS config
├── firmware/                    # Embedded hardware micro-controller code
│   ├── esp32_telemetry_bridge.ino # Arduino C++ firmware (I2C OLED, VE.Direct serial)
│   └── esphome_smdc_bridge.yaml   # ESPHome firmware with MQTT/Prometheus metrics
├── hardware/                    # Physical engineering blueprints & schematics
│   ├── COMPONENTS.md            # Complete Bill of Materials (BOM) with pricing & specs
│   ├── ASSEMBLY_MANUAL.md       # Step-by-step physical rack assembly instructions
│   └── WIRING_DIAGRAM.md        # DC 48V/12V/5V, AC 230V, liquid cooling, & SFP+ wiring
├── cad/                         # 3D CAD models & manufacturing specifications
│   ├── rack_enclosure.scad      # Parametric OpenSCAD 9U 19" aluminum chassis model
│   ├── accessories.scad         # 3D printable brackets (DIN rail, Jetson mount, OLED bezel)
│   └── MANUFACTURING_GUIDE.md   # Laser cut DXF export, CNC sheet metal bending specs
├── docs/                        # Interactive Three.js WebGL Digital Twin & GitHub Pages
│   └── index.html               # 3D WebGL CAD viewer + interactive sizing & TCO calculator
├── tests/                       # Complete automated Pytest suite (92+ tests, 96.5% coverage)
├── ARCHITECTURE.md              # Multi-node autonomous network architecture specification
├── COMMERCIALIZATION.md         # Investment thesis, TAM/SAM/SOM & 3-year TCO payback model
├── BENCHMARKS.md                # Quantified LLM, vector search, load shedding & space metrics
├── COMPLIANCE.md                # SOC 2, ISO 27001, NIST Zero Trust & PQC cryptographic attestation
├── pyproject.toml               # Python package metadata, dependencies & tool configs
└── uv.lock                      # Deterministic uv dependency lockfile
```

---

## 3. Core Subsystems & Technical Specifications

### A. Autonomous AI Agents (`src/sovereign_dc/agents/`)
- **Sentinel Copilot (`sentinel_copilot.py`)**:
  - Scrapes physical telemetry (Victron VE.Direct serial, RS485 Modbus battery BMS, 1-Wire DS18B20 temperature).
  - Dynamically calculates load-shedding states:
    - `L0` (Nominal, SoC > 50%): Full AI batch pipelines and multi-node compute.
    - `L1` (Mild Throttling, SoC 30–50%): GPU power capped to 50W, non-critical metrics interval relaxed.
    - `L2` (Heavy Shedding, SoC 20–30%): Secondary compute nodes isolated via relay.
    - `L3` (Critical Preservation, SoC 10–20%): Primary compute suspended; LoRa emergency heartbeats only.
    - `L4` (Blackout Safe, SoC < 10%): Total compute shutdown; solar auto-wake armed.
- **Knowledge Indexer (`knowledge_indexer.py`)**:
  - Chunks documentation (`.md`, `.txt`, `.py`, `.json`) into semantic windows (default: 500 chars with 50-char overlap).
  - Generates dense vector embeddings using local Ollama models (`nomic-embed-text` / `bge-m3`) and stores them in Qdrant collections.
- **GitLab Code Reviewer (`gitlab_reviewer.py`)**:
  - Evaluates git diffs and patches locally using Ollama (`codellama` / `qwen2.5-coder` / `deepseek-coder`).
  - Outputs structured code review commentary, security risk ratings, and patch recommendations.

### B. Space Delay-Tolerant Networking (DTN / BPv7) (`src/sovereign_dc/space/`)
- **RFC 9171 BPv7 Bundles (`dtn/bundle.py`)**:
  - Implements Delay-Tolerant Networking bundle creation, source/destination Endpoint Identifiers (`dtn://node.sovereign.space`), creation timestamps, lifetime TTL, and CRC-32 integrity checks.
- **Store-and-Forward Router (`dtn/router.py`)**:
  - Persistent NVMe disk-spooling queue for bundles waiting for orbital passes or intermittent mesh contacts.
- **SGP4 Orbital Propagator (`orbital/propagator.py`)**:
  - Calculates satellite contact windows: Acquisition of Signal (AOS), Time of Closest Approach (TCA), and Loss of Signal (LOS).
  - Tracks elevation, azimuth, slant range, and Doppler frequency shifts.
- **RF Link Budget Simulator (`transceiver/simulated_link.py`)**:
  - Models Free-Space Path Loss (FSPL) across carrier frequencies (437 MHz UHF, 2.4 GHz S-band, 10.4 GHz X-band).

### C. Sovereign Mesh & LoRa Gateway (`src/sovereign_dc/mesh/`)
- **Mesh Sync (`mesh_sync.py`)**:
  - Multi-node health monitoring over WireGuard/Headscale private overlay IPs (`100.64.0.0/16`).
  - Triggers state synchronization between nodes and falls back to Space DTN queues if terrestrial links drop.
- **Meshtastic Gateway (`lora/meshtastic_gateway.py`)**:
  - Encodes telemetry and emergency commands into Sub-GHz (868/915 MHz) LoRa packets with hardware AES-256-GCM encryption.

---

## 4. Engineering Conventions & Coding Standards

When contributing code, modifying existing modules, or adding new features, agents **must adhere to the following standards**:

### Python Standards
- **Python Version**: Target Python $\ge 3.11$ (compatible with 3.11, 3.12, and 3.13).
- **Type Annotations**: All function arguments, return types, and class attributes must have explicit type annotations (`typing.List`, `typing.Dict`, `typing.Optional`, `typing.Tuple`, `typing.Any`).
- **Logging**: Use standard library `logging` instead of `print()` in core libraries. Format: `logging.basicConfig(format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")`.
- **Package Layout**: All core Python code lives under `src/sovereign_dc/`. CLI entry points must be declared in `pyproject.toml` under `[project.scripts]`.

### Documentation & Markdown Formatting Rules
- **Mermaid Diagrams on GitHub**:
  - Always quote node labels containing spaces, punctuation, special symbols, slashes, or hyphens: `NodeID["Label text"]`.
  - **Never** use bare pipe characters (`|`) inside node labels. Use bullet points (`•`), commas, or slashes instead.
  - **Never** use raw `<` or `>` characters inside unquoted state transitions or labels (e.g. use `SoC under 50%` instead of `SoC < 50%` to avoid triggering GitHub's HTML sanitizer).
  - Wrap sequence diagram participant aliases in quotes: `participant Sun as "☀️ Solar Array (MPPT)"`.
- **LaTeX Math Rendering**:
  - Place display math `$$` on its own separate line with a preceding and following blank line.
  - **Never** use underscores inside `\text{...}` (e.g. use `$U_{\mathrm{gpu}}$` or `\text{GPU-Util}` instead of `\text{GPU_Util}`).

---

## 5. Development, Quality Gates & Testing Commands

Agents and contributors must verify code against the repository quality gates before submitting changes:

```powershell
# Run all quality gates locally (PowerShell)
powershell -ExecutionPolicy Bypass -File scripts/quality_gate.ps1

# Run all quality gates locally (Bash / Linux / macOS)
./scripts/quality_gate.sh
```

### Individual Quality Gate Commands
```powershell
# 1. Code Formatting Check (Ruff)
uv tool run ruff format --check src/ tests/

# 2. Code Linting Check (Ruff)
uv tool run ruff check src/ tests/

# 3. Static Type Analysis (Mypy)
uv tool run mypy --ignore-missing-imports src/sovereign_dc

# 4. Pytest with Coverage Enforcement (>=85%)
uv run pytest tests/ --cov=src/sovereign_dc --cov-fail-under=85
```

```powershell
# Execute CLI locally
.\.venv\Scripts\python -m sovereign_dc --help
.\.venv\Scripts\python -m sovereign_dc status
.\.venv\Scripts\python -m sovereign_dc benchmark --all
.\.venv\Scripts\python -m sovereign_dc demo --steps 3
.\.venv\Scripts\python -m sovereign_dc mesh consensus --nodes 3
.\.venv\Scripts\python -m sovereign_dc space passes --hours 12
.\.venv\Scripts\python -m sovereign_dc agent ask "Summarize power status"
```

---

## 6. Checklist Before Completing Any Agent Task

1. [ ] **Pass All Quality Gates Locally**: Run `scripts/quality_gate.ps1` or `scripts/quality_gate.sh` (Ruff lint/format, Mypy typing, Pytest $\ge 85\%$ coverage).
2. [ ] **Pass All Unit Tests**: Verify all 274+ unit tests in `tests/` pass with zero failures.
3. [ ] **Preserve Existing Interfaces**: Ensure CLI arguments, Prometheus metric names, and DTN bundle schemas remain backward-compatible.
4. [ ] **Verify Markdown/Mermaid**: Ensure any new or modified `.md` files strictly comply with GitHub rendering guidelines.
5. [ ] **Clean Conventional Commits**: Format commit messages cleanly using Conventional Commits (`feat:`, `fix:`, `docs:`, `test:`, `refactor:`).
6. [ ] **Commit & Push to Remote**: Always stage changes, commit, and push directly to `origin/main` (or working feature branch).
7. [ ] **Verify Remote CI Pipeline & Succeeded Before Stopping**: Always monitor and check that the remote GitHub Actions CI pipeline completes with **100% green (succeeded)** status after pushing. If any step fails, investigate, fix, commit, and re-verify until the entire CI pipeline succeeds before concluding the task.
