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
5. **Post-Quantum Cryptography & Zero-Trust**: Quantum-safe key encapsulation (NIST FIPS 203 ML-KEM) and lattice signatures (NIST FIPS 204 ML-DSA) securing mesh peering and space bundles.

---

## 2. Repository Directory Structure

```
sovereign-mini-datacenter/
├── src/
│   └── sovereign_dc/            # Core Python library & CLI package (`smdc`)
│       ├── __init__.py          # Version definition & package exports
│       ├── __main__.py          # `python -m sovereign_dc` entry point
│       ├── cli.py               # Unified click/rich CLI interface
│       ├── config.py            # Layered configuration management (Defaults -> YAML -> Env)
│       ├── events.py            # Thread-safe in-process publish/subscribe event bus
│       ├── log.py               # Structured JSON & colored console formatters
│       ├── telemetry.py         # Hardware telemetry parsers (VE.Direct, RS485, DS18B20)
│       ├── agents/              # Autonomous local agent engines
│       │   ├── sentinel_copilot.py   # Energy & thermal watchdog + load shedder
│       │   ├── knowledge_indexer.py  # Semantic chunker & Qdrant RAG vectorizer
│       │   ├── gitlab_reviewer.py    # Automated git diff AI code reviewer
│       │   └── technician_notifier.py# Autonomous multi-channel technician dispatch
│       ├── economy/             # Autonomous Monetary & Compute Economy Layer
│       │   ├── wallet.py             # Ed25519 & NIST FIPS 204 ML-DSA-87 Node Wallets
│       │   ├── ledger.py             # Append-only hash-linked ledger & offline state channels
│       │   ├── market.py             # Solar-aware dynamic price oracle & service catalog
│       │   └── settlement.py         # Proof-of-Compute/Relay & RFC 9171 DTN bundle settlement
│       ├── enterprise/          # Enterprise Workload Onboarding & Coupling Framework
│       │   ├── schema.py             # smdc-app.yaml manifest dataclass & schema validation
│       │   ├── registry.py           # Multi-path app discovery, registration & scaffolding
│       │   ├── sdk.py                # Zero-dependency SMDCClient & AppLifecycleHandler SDK
│       │   └── manager.py            # Process supervision, load-shedding hooks & PQC packaging
│       ├── hal/                 # Hardware Abstraction Layer
│       │   ├── gpu.py                # NVIDIA Jetson / Tegra / Desktop GPU telemetry
│       │   ├── power.py              # Victron MPPT & SmartShunt BMS readers
│       │   ├── storage.py            # NVMe health, S.M.A.R.T. & IOPS telemetry
│       │   └── thermal.py            # 1-Wire DS18B20 liquid coolant & ambient probes
│       ├── mcp/                 # Native Model Context Protocol (MCP) Server
│       │   ├── __init__.py           # Package exports (MCPServer, MCPTool, MCPResource, MCPPrompt)
│       │   ├── server.py             # JSON-RPC 2.0 stdio server and request router
│       │   ├── tools.py              # 16 MCP operational tools (telemetry, pricing, DTN, PQC, apps, metaverse)
│       │   ├── resources.py          # 9 dynamic MCP resource URI endpoints
│       │   └── prompts.py            # 5 operational workflow prompts
│       ├── mesh/                # Multi-node peer-to-peer & LoRa networking
│       │   ├── mesh_sync.py          # WireGuard peer health and state synchronizer
│       │   ├── consensus.py          # Raft distributed consensus state machine
│       │   ├── chaos.py              # Split-brain, link loss & packet loss chaos simulator
│       │   └── lora/
│       │       └── meshtastic_gateway.py # Sub-GHz LoRa packet gateway (AES-256-GCM)
│       ├── metaverse/           # 5G RAN & UAV Metaverse Simulation Stack (IEEE IoT Mag 2026)
│       │   ├── ray_tracer.py         # 3GPP UMi & Sionna 3D multipath ray-tracer (3.5 GHz)
│       │   ├── agent.py              # Scene-Aware PPO (SA-PPO) & Model-Driven PPO (MD-PPO)
│       │   ├── slicing.py            # 5G Network Slicing traffic isolation (URLLC/eMBB/mMTC)
│       │   ├── depin_sla.py          # DePIN PoS/dBFT consensus & smart contract SLA verification
│       │   ├── engine.py             # Algorithm 1 MultiLayerOrchestrator (<6ms critical path)
│       │   └── benchmark.py          # Comparative RL benchmarking & capacity gain sweeps
│       ├── security/            # Post-Quantum Cryptography Engine
│       │   └── pqc.py                # NIST FIPS 203 ML-KEM & FIPS 204 ML-DSA
│       ├── space/               # Space communications & satellite tracking
│       │   ├── space_exporter.py     # Prometheus exporter for orbital/link metrics
│       │   ├── dtn/                  # RFC 9171 Delay-Tolerant Networking (BPv7)
│       │   │   ├── bundle.py         # Bundle creation, CBOR/JSON serialization, TTL
│       │   │   └── router.py         # Persistent NVMe store-and-forward spool
│       │   ├── orbital/              # Satellite orbital mechanics (SGP4)
│       │   │   ├── propagator.py     # AOS/LOS contact pass calculator
│       │   │   └── tle_updater.py    # Two-Line Element (TLE) ephemeris fetcher
│       │   └── transceiver/          # RF Link budget & ground station models
│       │       └── simulated_link.py # FSPL, Doppler shift, SNR, azimuth/elevation
│       └── web/                 # Operations Web Dashboard & REST API
│           └── dashboard.py          # Real-time HTTP dashboard & /api/status telemetry
├── examples/                    # Turnkey Enterprise Application Archetypes
│   └── enterprise_apps/         # Reference implementations for edge onboarding
│       ├── iot-edge-gateway/    # L0 Critical Sub-GHz sensor telemetry aggregator
│       ├── edge-vision-ai/      # L2 Background GPU-accelerated TensorRT computer vision
│       ├── spatial-digital-twin/# L1 Standard Three.js WebGL spatial simulation
│       ├── confidential-vault/  # L0 Critical NIST FIPS 203/204 zero-trust secrets vault
│       └── oran-ric-controller/ # L0 Critical O-RAN Near-RT RIC xApp 5G slicing & UAV controller
├── software/                    # Production deployment & service definitions
│   ├── docker-compose.yml       # Primary 11-service production stack
│   ├── Dockerfile.smdc          # Multi-architecture container blueprint (amd64, arm64)
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
├── tests/                       # Complete automated Pytest suite (368+ tests, 91.2%+ coverage)
├── ARCHITECTURE.md              # Multi-node autonomous network architecture specification
├── ENTERPRISE_ONBOARDING.md     # Enterprise manifest spec, power tiers & developer guide
├── COMMERCIALIZATION.md         # Investment thesis, TAM/SAM/SOM & 3-year TCO payback model
├── BENCHMARKS.md                # Quantified LLM, vector search, load shedding & space metrics
├── COMPLIANCE.md                # SOC 2, ISO 27001, NIST Zero Trust & PQC cryptographic attestation
├── pyproject.toml               # Python package metadata, dependencies & tool configs
└── uv.lock                      # Deterministic uv dependency lockfile
```

---

## 3. Core Subsystems & Technical Specifications

### A. Centralized Configuration & Event Bus (`src/sovereign_dc/config.py`, `events.py`)
- **Layered Configuration (`config.py`)**: Hierarchical configuration dataclass (`SovereignConfig`) supporting programmatic defaults $\to$ YAML configuration files $\to$ environment variables (`SMDC_*`).
- **In-Process Event Bus (`events.py`)**: Thread-safe publish/subscribe event dispatcher (`SovereignEventBus`) with wildcard event routing (`load_shedding.*`, `mesh.*`, `space.*`, `economy.*`, `metaverse.*`) and ring-buffered audit logs.

### B. Hardware Abstraction Layer (HAL) (`src/sovereign_dc/hal/`)
- **GPU (`gpu.py`)**: Automatic discovery of NVIDIA Tegra (Jetson Orin) via sysfs `/devices/platform/` or desktop GPUs via `pynvml`, parsing power draw, temperature, and utilization.
- **Power (`power.py`)**: Interfaces Victron SmartSolar MPPT controllers and SmartShunt battery monitors via VE.Direct text streams.
- **Storage (`storage.py`)**: Evaluates NVMe drive wear-out percentage, temperatures, and filesystem I/O metrics.
- **Thermal (`thermal.py`)**: Reads 1-Wire DS18B20 Dallas sensors across `/sys/bus/w1/devices/` for liquid cooling flow monitoring.

### C. Post-Quantum Cryptography & Security Engine (`src/sovereign_dc/security/pqc.py`)
- **NIST FIPS 204 (ML-DSA-65 & ML-DSA-87)**: Lattice-based digital signatures for cluster peer identity attestation, firmware verification, and RFC 9172 (BPSec) DTN bundle signing.
- **NIST FIPS 203 (ML-KEM-768 & ML-KEM-1024)**: Lattice-based Key Encapsulation Mechanism (KEM) establishing quantum-safe symmetric encryption keys across terrestrial and space links.

### D. Operations Dashboard, Live Digital Shadow & REST API (`src/sovereign_dc/web/dashboard.py`)
- Built-in zero-dependency HTTP server delivering a responsive, dark-mode glassmorphic single-page operations console (`smdc dashboard`).
- **Live Digital Shadow SSE Stream (`/api/telemetry/stream`)**: Continuous Server-Sent Events stream delivering live telemetry snapshots (fan RPM, coolant temperature, solar harvest, battery SoC) directly to 3D WebGL Digital Twins.
- **Hardware & Metaverse Control APIs**: Remote control endpoints (`/api/control/rack-door`, `/api/control/pdu-outlet`, `/api/control/dtn-transmit`, `/api/metaverse/optimize`, `/api/metaverse/emergency-stop`) enabling physical solenoid door unlock, RFC 9171 bundle spooling, and SA-PPO UAV teleoperation from the 3D twin.
- Provides real-time status REST APIs (`/api/status`, `/api/metaverse/status`, `/api/metaverse/slices`, `/health`) tracking battery State-of-Charge, solar harvest, thermal loops, Space DTN spools, economy wallets, 5G slices, and mesh peers.

### E. Mesh Chaos Engineering Simulator (`src/sovereign_dc/mesh/chaos.py`)
- Simulates network partition scenarios, split-brain conditions, terrestrial link dropouts with automatic Space DTN spooling fallback, and deterministic packet loss replication.

### F. Autonomous AI Agents (`src/sovereign_dc/agents/`)
- **Sentinel Copilot (`sentinel_copilot.py`)**: Scrapes physical telemetry (VE.Direct serial, RS485 Modbus, 1-Wire DS18B20) and enforces dynamic load shedding ($L_0 \to L_4$).
- **Knowledge Indexer (`knowledge_indexer.py`)**: Chunks documents into semantic windows, generating embeddings via local Ollama models (`nomic-embed-text` / `bge-m3`) stored in Qdrant collections.
- **GitLab Code Reviewer (`gitlab_reviewer.py`)**: Evaluates git diffs and patches locally using Ollama (`codellama` / `qwen2.5-coder` / `deepseek-coder`).
- **Technician Notifier (`technician_notifier.py`)**: Dispatches urgent hardware repair instructions over LoRa, Matrix, and SMTP.

### G. Space Delay-Tolerant Networking (DTN / BPv7) (`src/sovereign_dc/space/`)
- **RFC 9171 BPv7 Bundles (`dtn/bundle.py`)**: Creates and verifies BPv7 bundles with CRC-32 integrity and optional PQC signatures.
- **Store-and-Forward Router (`dtn/router.py`)**: Persistent NVMe spool for bundles awaiting satellite contact windows.
- **SGP4 Orbital Propagator (`orbital/propagator.py`)**: Computes AOS/LOS satellite contact passes, azimuth, elevation, and Doppler shifts.
- **RF Link Budget Simulator (`transceiver/simulated_link.py`)**: Free-Space Path Loss (FSPL) calculations across UHF, S-band, and X-band.

### H. Autonomous Monetary & Compute Economy Layer (`src/sovereign_dc/economy/`)
- **Node Wallet (`wallet.py`)**: Post-Quantum (ML-DSA-87) and Ed25519 keypair identity, deterministic address derivation (`sov_...` / `sov_pqc_...`), and payload signing.
- **Immutable Ledger & State Channels (`ledger.py`)**: Append-only transaction ledger with replay protection and bidirectional off-chain micropayment state channels.
- **Dynamic Solar-Aware Pricing Engine (`market.py`)**: Real-time pricing oracle adjusting service rates based on solar harvest ($>1000\text{ W} \to 50\%$ discount) and battery reserves ($<25\% \to 3.0\times$ surge pricing).
- **Delay-Tolerant Settlement (`settlement.py`)**: Cryptographic Proof-of-Compute and Proof-of-Relay verification reconciled across terrestrial mesh or RFC 9171 satellite contact passes.

### I. Native Model Context Protocol (MCP) Server (`src/sovereign_dc/mcp/`)
- **Standard Protocol Support (2024-11-05)**: Standardized JSON-RPC 2.0 stdio interface exposing datacenter hardware and services to AI assistants (Antigravity, Claude Desktop, Cursor, Cline).
- **Operational MCP Tools (`tools.py`)**: 16 callable tools including `get_telemetry`, `get_system_status`, `set_load_shedding`, `query_market_pricing`, `get_wallet_balances`, `spool_dtn_bundle`, `predict_satellite_passes`, `query_knowledge_indexer`, `run_security_audit`, `dispatch_technician_alert`, `list_enterprise_apps`, `manage_enterprise_app`, `scaffold_enterprise_app`, `run_metaverse_sim_cycle`, `get_5g_slices_status`, and `validate_depin_sla`.
- **Dynamic MCP Resources (`resources.py`)**: 9 live URI resources (`smdc://telemetry/current`, `smdc://system/manifest`, `smdc://economy/market`, `smdc://space/dtn/spool`, `smdc://security/pqc/status`, `smdc://enterprise/apps`, `smdc://enterprise/schema`, `smdc://metaverse/uav/status`, `smdc://metaverse/5g/slices`).
- **Standard MCP Prompts (`prompts.py`)**: 5 diagnostic and workflow prompts (`diagnose_power_incident`, `plan_compute_workload`, `prepare_space_transmission`, `onboard_enterprise_workload`, `optimize_uav_coverage`).

### J. Enterprise Workload Onboarding & Coupling Framework (`src/sovereign_dc/enterprise/`)
- **Declarative Manifest Specification (`schema.py`)**: Type-safe `smdc-app.yaml` manifest supporting multi-tier power shedding ($L_0 \to L_4$), memory/vRAM quotas, network policies, and health probes.
- **Enterprise Registry & Discovery (`registry.py`)**: Automated discovery across `/etc/smdc/apps`, `~/.smdc/apps`, and workspace paths with turnkey project scaffolding (`smdc app init`).
- **Zero-Dependency Enterprise SDK (`sdk.py`)**: Minimal footprint `SMDCClient` and `AppLifecycleHandler` providing direct event bus binding, hardware telemetry streams, dynamic pricing queries, and Space DTN spooling.
- **Supervision & Load Shedding Manager (`manager.py`)**: Dynamic process management auto-pausing deferrable background apps ($L_2 \to L_4$) on low battery / thermal events and resuming on solar recovery, plus NIST FIPS 204 ML-DSA-87 cryptographic bundle packaging (`.smdc-app` + `.sig`).

### K. 5G RAN & UAV Metaverse Simulation Stack (`src/sovereign_dc/metaverse/`)
- **Physics-Based Electromagnetic Ray-Tracer (`ray_tracer.py`)**: 3GPP UMi & Sionna 3D multipath ray-tracer at 3.5 GHz with up to 5 reflection orders, urban building LoS obstruction checking, Kriging spatial interpolation, and per-receiver SINR calculation.
- **Scene-Aware Deep Reinforcement Learning Agent (`agent.py`)**: 3-layer Actor-Critic neural networks with 20-dim state vector, discrete 6-directional action space ($\pm X, \pm Y, \pm Z$), sum-SINR reward formulation with disadvantaged receiver weighting, and Model-Driven PPO (MD-PPO) baseline comparison.
- **5G Network Slicing & SDN Controller (`slicing.py`)**: Mathematical bandwidth isolation ($T_{\mathrm{tx}} = (D \cdot 8)/B_{\mathrm{slice}} \cdot 1000\text{ ms}$) managing URLLC ($< 1\text{ ms}$), eMBB ($127\text{ Mbps}$), and mMTC ($12\text{k devs/km}^2$) slices.
- **DePIN Decentralized SLA & Blockchain Consensus (`depin_sla.py`)**: Multi-validator PoS/dBFT consensus requiring $\lceil 2N/3 \rceil + 1$ multi-signatures, enforcing minimum SINR rejection thresholds ($-15\text{ dB}$) and automated repositioning triggers.
- **Multi-Layer Orchestrator (`engine.py`)**: Implements Algorithm 1 (3-phase execution: parallel IoT/ray-tracing collection $\to$ critical path decision $<6\text{ ms} \to$ asynchronous DePIN block finalization).
- **Comparative RL Benchmark Suite (`benchmark.py`)**: Parametric sweeps evaluating $+79.6\%$ capacity gains for disadvantaged receiver Rx1, execution latencies, and Shannon spectral efficiencies.

---

## 4. Engineering Conventions & Coding Standards

When contributing code, modifying existing modules, or adding new features, agents **must adhere to the following standards**:

### Python Standards
- **Python Version**: Target Python $\ge 3.11$ (compatible with 3.11, 3.12, and 3.13).
- **Type Annotations**: All function arguments, return types, and class attributes must have explicit type annotations (`typing.List`, `typing.Dict`, `typing.Optional`, `typing.Tuple`, `typing.Any`).
- **Logging**: Use standard library `logging` or `sovereign_dc.log` with lazy `%s` interpolation. Never use bare `print()` in core libraries.
- **Package Layout**: All core Python code lives under `src/sovereign_dc/`. CLI subcommands must be registered in `src/sovereign_dc/cli.py`.

### Documentation, Mermaid Diagrams & Math Formatting Rules
- **Mermaid Diagrams on GitHub**:
  - **Quoted Node Labels**: Always quote node labels containing spaces, punctuation, special symbols, slashes, or hyphens: `NodeID["Label text"]`.
  - **No Bare Pipes**: **Never** use bare pipe characters (`|`) inside node labels. Use bullet points (`•`), commas, or slashes instead.
  - **Quoted Transition & Edge Labels**: Always quote transition labels containing special characters or ampersands: `-->|"Attestation & Keys"|`.
  - **No Raw Angle Brackets**: **Never** use raw `<` or `>` characters inside unquoted state transitions or labels (e.g., write `SoC under 50%` or `SoC < 50%` with quotes/text to avoid triggering GitHub's HTML sanitizer).
  - **Sequence Diagram Aliases**: Wrap participant aliases in quotes: `participant Sun as "☀️ Solar Array (MPPT)"`.
  - **XYChart Beta Syntax**: In `xychart-beta`, define `bar` and `line` series as numerical arrays directly (`bar [1.2, 2.4, 4.8]`), omitting bracketed duplicate series names.
- **LaTeX Math Rendering**:
  - **Display Math Blocks**: Place display math `$$` on its own separate line with a preceding and following blank line.
  - **No Underscores in `\text{...}`**: **Never** use underscores inside `\text{...}`. Use `$U_{\mathrm{gpu}}$`, `\mathrm{ef\_search}`, or `\text{ef-search}` instead.
  - **Table Math Spacing**: Ensure all mathematical expressions inside markdown tables have clean whitespace around comparison operators (`< 5 ms`, `> 1 kW`).

### Continuous Documentation Synchronization Rule
- **Mandatory AGENTS.md & README.md Updates**: Whenever new subsystems, modules, CLI commands, test suites, or architectural pillars are created or modified, `AGENTS.md` and `README.md` **must be updated immediately** to reflect the new state, file tree, test metrics, and CLI instructions.

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
.\.venv\Scripts\python -m sovereign_dc dashboard --port 8080
.\.venv\Scripts\python -m sovereign_dc app list
.\.venv\Scripts\python -m sovereign_dc app init --name "IoT Edge" --app-id iot-edge --category iot ./iot-edge
.\.venv\Scripts\python -m sovereign_dc app validate ./iot-edge
.\.venv\Scripts\python -m sovereign_dc app register ./iot-edge
.\.venv\Scripts\python -m sovereign_dc app start iot-edge
.\.venv\Scripts\python -m sovereign_dc app status iot-edge
.\.venv\Scripts\python -m sovereign_dc app package ./iot-edge --output iot-edge-1.0.0.smdc-app
.\.venv\Scripts\python -m sovereign_dc economy wallet
.\.venv\Scripts\python -m sovereign_dc economy market --soc 85 --solar 1200
.\.venv\Scripts\python -m sovereign_dc mesh chaos --help
.\.venv\Scripts\python -m sovereign_dc security pqc --help
.\.venv\Scripts\python -m sovereign_dc benchmark --all
.\.venv\Scripts\python -m sovereign_dc demo --steps 3
.\.venv\Scripts\python -m sovereign_dc mesh consensus --nodes 3
.\.venv\Scripts\python -m sovereign_dc space passes --hours 12
.\.venv\Scripts\python -m sovereign_dc sim run --cycles 5
.\.venv\Scripts\python -m sovereign_dc sim slices
.\.venv\Scripts\python -m sovereign_dc sim sla
.\.venv\Scripts\python -m sovereign_dc sim benchmark --episodes 5 --steps 10
.\.venv\Scripts\python -m sovereign_dc mcp test
.\.venv\Scripts\python -m sovereign_dc mcp tools
.\.venv\Scripts\python -m sovereign_dc mcp resources
.\.venv\Scripts\python -m sovereign_dc mcp prompts
.\.venv\Scripts\python -m sovereign_dc mcp serve
```

---

## 6. Checklist Before Completing Any Agent Task

1. [ ] **Pass All Quality Gates Locally**: Run `scripts/quality_gate.ps1` or `scripts/quality_gate.sh` (Ruff lint/format, Mypy typing, Pytest $\ge 85\%$ coverage).
2. [ ] **Pass All Unit Tests**: Verify all 302+ unit tests in `tests/` pass with zero failures.
3. [ ] **Preserve Existing Interfaces**: Ensure CLI arguments, Prometheus metric names, and DTN bundle schemas remain backward-compatible.
4. [ ] **Verify Markdown/Mermaid & Math**: Ensure any new or modified `.md` files strictly comply with GitHub Mermaid and LaTeX math rendering rules.
5. [ ] **Synchronize Documentation**: Update `AGENTS.md` and `README.md` with any newly added modules, subcommands, or architectural changes.
6. [ ] **Clean Conventional Commits**: Format commit messages cleanly using Conventional Commits (`feat:`, `fix:`, `docs:`, `test:`, `refactor:`).
7. [ ] **Commit & Push to Remote**: Always stage changes, commit, and push directly to `origin/main` (or working feature branch).
8. [ ] **Verify Remote CI Pipeline & Succeeded Before Stopping**: Always monitor and check that the remote GitHub Actions CI pipeline completes with **100% green (succeeded)** status after pushing. If any step fails, investigate, fix, commit, and re-verify until the entire CI pipeline succeeds before concluding the task.
