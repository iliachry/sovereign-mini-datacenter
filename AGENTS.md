# 🤖 AI Agent Engineering Playbook & Repository Guide

> **Target Audience**: Autonomous AI coding agents (Antigravity, Claude, Cursor, Copilot, Cline, Aider, Windsurf) and human engineers working on the **Sovereign Mini Datacenter** codebase.  
> **Repository**: [sovereign-mini-datacenter](https://github.com/iliachry/sovereign-mini-datacenter)  
> **Author & Lead Architect**: [Ilias Chrysovergis](https://iliachry.gr) · [Metatopia Studio](https://metatopia.gr) · License: MIT · © 2026

---

## 1. Repository Purpose & Engineering Philosophy

**Sovereign Mini Datacenter** is an open-source, self-powered, solar-backed micro-datacenter stack with air-cooled AI nodes and a liquid-cooled host, designed for **complete data and computational sovereignty**.

### Key Architectural Pillars
1. **Cloud Independence & Off-Grid Capability**: Operates without relying on centralized cloud providers (AWS, GCP, Azure, Cloudflare). Solar and battery carry the load as far as the array is sized for it: the reference 8-panel build (3,280W) yields about 14.5 kWh/day on annual average against an 11.2 kWh/day draw at a 12 h/day GPU duty cycle, a surplus of about 3.3 kWh/day. Winter yield is lower, so low-sun periods draw on the battery and then on grid or generator.
2. **Local AI & Multi-Agent Copilots**: On-premise LLM inference (Ollama) + semantic vector search (Qdrant) running on 2× NVIDIA DGX Spark in the reference build (NVIDIA Jetson AGX Orin is supported as a lower-power alternative).
3. **Multi-Spectral Failover & Space DTN**: Resilient communication tiered from 10GbE fiber mesh $\to$ Starlink/5G $\to$ Sub-GHz LoRa Meshtastic $\to$ RFC 9171 Space Delay-Tolerant Networking (BPv7) with a simplified circular-orbit pass predictor.
4. **Tiered Load Shedding (software state, no hardware actuation)**: Battery State-of-Charge (SoC) telemetry maps to a shedding level 0–4 (below 50 / 30 / 20 / 10 %); the sentinel publishes it, the knowledge indexer defers batch work and the `EnterpriseManager` marks tracked apps paused by power tier. Nothing throttles a GPU or switches a relay.
5. **Post-Quantum Cryptography**: ML-DSA lattice signatures (the FIPS 204 algorithm) on node wallets and app packages, and ML-KEM key encapsulation (the FIPS 203 algorithm) as a library with a self-test. Neither secures mesh peering or space bundles today; that is planned (see `COMPLIANCE.md`).

---

## 2. Repository Directory Structure

```
sovereign-mini-datacenter/
├── src/
│   └── sovereign_dc/            # Core Python library & CLI package (`smdc`)
│       ├── __init__.py          # Version definition & package exports
│       ├── __main__.py          # `python -m sovereign_dc` entry point
│       ├── cli.py               # Unified argparse CLI interface
│       ├── config.py            # Layered configuration management (Defaults -> YAML -> Env)
│       ├── events.py            # Thread-safe in-process publish/subscribe event bus
│       ├── jsonutil.py          # Strict JSON serializer (sanitizes NaN/Infinity before encoding)
│       ├── log.py               # Structured JSON & colored console formatters
│       ├── telemetry.py         # Power/thermal Prometheus exporter (simulation, or ESP32 bridge via ESP32_BRIDGE_URL)
│       ├── shed_benchmark.py    # `smdc benchmark --shed`: load-shedding software-path latency (simulation)
│       ├── agents/              # Autonomous local agent engines
│       │   ├── bootstrap_provisioner.py # Read-only startup checker (hardware, peers, services, DTN spool)
│       │   ├── sentinel_copilot.py   # Polls power telemetry, publishes the operating mode
│       │   ├── knowledge_indexer.py  # Semantic chunker & Qdrant RAG vectorizer
│       │   ├── gitlab_reviewer.py    # Polls open GitLab MRs, posts one LLM review note each
│       │   └── technician_notifier.py# Technician alerts to a file log and the DTN spool
│       ├── economy/             # Autonomous Monetary & Compute Economy Layer
│       │   ├── wallet.py             # Ed25519 & NIST FIPS 204 ML-DSA-87 Node Wallets
│       │   ├── ledger.py             # Append-only hash-linked ledger & offline state channels
│       │   ├── market.py             # Solar-aware dynamic price oracle & service catalog
│       │   ├── node_state.py         # Shared node economic state (single wallet pair & ledger resolver)
│       │   └── settlement.py         # Proof-of-Compute/Relay & RFC 9171 DTN bundle settlement
│       ├── enterprise/          # Enterprise Workload Onboarding & Coupling Framework
│       │   ├── schema.py             # smdc-app.yaml manifest dataclass & schema validation
│       │   ├── registry.py           # Multi-path app discovery, registration & scaffolding
│       │   ├── sdk.py                # Zero-dependency SMDCClient & AppLifecycleHandler SDK
│       │   └── manager.py            # App state tracking (no process supervision), load-shedding hooks & PQC packaging
│       ├── hal/                 # Hardware Abstraction Layer
│       │   ├── gpu.py                # NVIDIA GPU discovery (name and memory via nvidia-smi)
│       │   ├── power.py              # Battery/solar readings from the power exporter's /metrics
│       │   ├── storage.py            # Filesystem capacity (total / used / free)
│       │   └── thermal.py            # Coolant/air temperatures from the exporter, host thermal zones
│       ├── mcp/                 # Native Model Context Protocol (MCP) Server
│       │   ├── __init__.py           # Package exports (MCPServer, MCPTool, MCPResource, MCPPrompt)
│       │   ├── server.py             # JSON-RPC 2.0 stdio server and request router
│       │   ├── tools.py              # 16 MCP operational tools (telemetry, pricing, DTN, PQC, apps, metaverse)
│       │   ├── resources.py          # 9 dynamic MCP resource URI endpoints
│       │   └── prompts.py            # 5 operational workflow prompts
│       ├── mesh/                # Multi-node peer-to-peer & LoRa networking
│       │   ├── mesh_sync.py          # Peer list (cluster YAML) and HTTP reachability probe; synchronises nothing
│       │   ├── consensus.py          # Raft distributed consensus state machine
│       │   ├── chaos.py              # Split-brain, link loss & packet loss chaos simulator
│       │   └── lora/
│       │       └── meshtastic_gateway.py # Sub-GHz LoRa to DTN gateway (no radio driver yet)
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
│       │   │   ├── bundle.py         # Bundle creation, JSON serialization, TTL
│       │   │   └── router.py         # Persistent NVMe store-and-forward spool
│       │   ├── orbital/              # Satellite orbital mechanics (simplified circular orbits)
│       │   │   ├── propagator.py     # AOS/LOS contact pass calculator
│       │   │   └── tle_updater.py    # Static satellite catalog (no network TLE fetch)
│       │   └── transceiver/          # RF Link budget & ground station models
│       │       └── simulated_link.py # FSPL, Doppler shift, SNR, azimuth/elevation
│       └── web/                 # Operations Web Dashboard & REST API
│           └── dashboard.py          # Real-time HTTP dashboard & /api/status telemetry
├── examples/                    # Turnkey Enterprise Application Archetypes
│   └── enterprise_apps/         # Simulated reference skeletons for edge onboarding
│       ├── iot-edge-gateway/    # L0 critical IoT gateway skeleton (simulated sensors)
│       ├── edge-vision-ai/      # L2 background vision skeleton (simulated inference, no GPU code)
│       ├── spatial-digital-twin/# L1 standard digital-twin skeleton (placeholder figures)
│       ├── confidential-vault/  # L0 critical secrets-vault skeleton (stores and encrypts nothing)
│       └── oran-ric-controller/ # L0 critical RIC-xApp-shaped skeleton driving the 5G slicing simulator (no radio)
├── software/                    # Production deployment & service definitions
│   ├── docker-compose.yml       # Primary 12-service production stack
│   ├── Dockerfile.smdc          # Multi-architecture container blueprint (amd64, arm64)
│   ├── setup.sh                 # Modular deployment CLI script
│   ├── env.example              # Environment variables template
│   ├── prometheus.yml           # Prometheus scrape configurations
│   ├── agents/                  # Standalone agent daemon scripts & compose files
│   ├── mesh/                    # Mesh peer reachability monitor & LoRa gateway configs
│   ├── vpn/                     # Headscale / WireGuard mesh VPN configs
│   ├── backup/                  # Restic automated encrypted backup engine
│   ├── telemetry/               # Python/Prometheus power & BMS exporter
│   ├── space/                   # Space link telemetry exporter
│   ├── mailcow/                 # Sovereign mail server configurations
│   └── grafana/                 # Pre-provisioned dashboards (Power, Thermal, Space)
├── kubernetes/                  # Kubernetes cluster deployment configurations
│   ├── helm/sovereign-stack/    # Production Helm chart (AI, telemetry, ingress)
│   ├── k3s-sovereign.yaml       # K3s lightweight Kubernetes cluster manifest
│   └── talos-config.yaml        # Talos Linux immutable bare-metal OS config
├── terraform/                   # Terraform & OpenTofu Infrastructure-as-Code (IaC)
│   ├── README.md                # IaC architectural guide, workflows & module index
│   ├── versions.tf              # Root OpenTofu/Terraform provider version constraints
│   ├── modules/                 # Reusable on-premise infrastructure modules
│   │   ├── talos-cluster/       # Bare-metal Talos Linux OS, LUKS2 & K8s bootstrapping
│   │   ├── proxmox-edge-node/   # Proxmox VE VM provisioning with PCIe GPU passthrough
│   │   ├── libvirt-edge-node/   # Local KVM/Libvirt VM edge node provisioning
│   │   ├── wireguard-mesh/      # P2P WireGuard mesh peering (10.42.0.0/16)
│   │   └── k8s-sovereign-stack/ # Helm automation for ./kubernetes/helm/sovereign-stack
│   └── environments/            # Turnkey deployment blueprints
│       ├── baremetal-talos/     # Production bare-metal 3-node Talos cluster
│       └── proxmox-dev/         # Virtualized Proxmox VE dev/testbed environment
├── firmware/                    # Embedded hardware micro-controller code
│   ├── esp32_telemetry_bridge.ino # Arduino C++ firmware (VE.Direct serial, DS18B20, HTTP /metrics)
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
├── tests/                       # Complete automated Pytest suite (1480 tests, 92.8% coverage)
├── ARCHITECTURE.md              # Multi-node autonomous network architecture specification
├── ENTERPRISE_ONBOARDING.md     # Enterprise manifest spec, power tiers & developer guide
├── COMMERCIALIZATION.md         # Investment thesis, TAM/SAM/SOM & 3-year TCO payback model
├── BENCHMARKS.md                # Results measured on a development machine (LLM tokens/s, DTN spool, load-shedding software path) plus labelled design targets; Qdrant and hardware not measured
├── COMPLIANCE.md                # Security control inventory (implemented / planned) mapped to SOC 2, ISO 27001, NIST; not a certification
├── pyproject.toml               # Python package metadata, dependencies & tool configs
└── uv.lock                      # Deterministic uv dependency lockfile (tracked)
```

---

## 3. Core Subsystems & Technical Specifications

### A. Centralized Configuration & Event Bus (`src/sovereign_dc/config.py`, `events.py`)
- **Layered Configuration (`config.py`)**: Hierarchical configuration dataclass (`SovereignConfig`) supporting programmatic defaults $\to$ YAML configuration files $\to$ environment variables (the upper-cased field name, e.g. `EXPORTER_PORT`, plus a few aliases). A value that cannot be converted to the field's numeric type (`EXPORTER_PORT=abc`) is ignored with a warning naming the variable, and the lower-precedence value is kept.
- **Load-Shedding Policy (`SHEDDING_L1_SOC`..`SHEDDING_L4_SOC`, `SHEDDING_HYSTERESIS_SOC`)**: The four SoC thresholds (defaults 50 / 30 / 20 / 10 %) are parsed and validated together, as one policy, by `checked_shedding_thresholds` (called from `SovereignConfig.__post_init__`; the loaders pass the raw values of these four fields through uncoerced): an unset or blank value stands for its default, and if any of the four is not a number, lies outside 0–100, or the order is not L1 > L2 > L3 > L4, one warning names the variables and all four defaults are used. An unparsable threshold is therefore never replaced on its own (unlike other numeric settings). `get_shedding_level(soc, previous_level=None)` is the plain mapping for stateless callers (dashboard label, MCP `recommended_level`); with `previous_level` a level is left only once SoC is `SHEDDING_HYSTERESIS_SOC` points (default 2, range 0–10) above its threshold (capped at 100 %). The policy functions (`shedding_policy_problem`, `checked_shedding_thresholds`, `checked_shedding_hysteresis`, `compute_shedding_level`) sit in a block marked `shared shedding policy` that is byte-identical in `config.py` and `agents/sentinel_copilot.py` (`tests/test_regress_round5.py` compares the blocks and sweeps both). Its sub-block `shedding thresholds (one rule)` (`shedding_policy_problem`, `checked_shedding_thresholds`) is also byte-identical in the power exporter (`telemetry.py`, `software/telemetry/power_exporter.py` and the Helm chart's copy), whose `sovereign_load_shedding_active` threshold is the L3 value of the same resolved policy (`tests/test_regress_round6.py` compares the copies and runs one matrix of settings through all of them). `software/telemetry/load_shedder.sh` reads the same four variables with the same test and, being a one-shot cron script, exits 2 instead of falling back when they are not a valid policy.
- **Node Identity (`NODE_ID`, default `config.DEFAULT_NODE_ID` = `smdc-dgx-01`)**: One id per node. `mesh_sync.py` and `space/dtn/router.py` carry the same literal because they also run standalone (tests compare them); the DTN router's default endpoint id is `DTN_LOCAL_EID` if set, else `dtn://<NODE_ID>.sovereign.space`, and the Helm chart passes `space.dtnNode.nodeId` as `NODE_ID`.
- **Backup Variables (`software/backup/`)**: `BACKUP_POLL_SECONDS` (default 300) is how often the backup container looks at `BACKUP_DUMP_DIR/current` for a dump generation that is not in the repository yet; it is not the backup interval (that is the schedule of the host job `backup.sh --dump-only`), and `BACKUP_INTERVAL_SECONDS` is no longer read. `BACKUP_NEXTCLOUD_MODE` (`maintenance`, the default, or `online`) decides whether Nextcloud is put into maintenance mode while its database is dumped and its volume copied. The full list is in `software/backup/README.md` and `software/env.example`. Both scripts read `.env` as data and decode values as Docker Compose does (`KEY=`, `KEY =`, `export KEY=` and `KEY:` spellings, last assignment wins; `$$` is one `$`; single quotes literal; a comment starts at a space followed by `#`); a form Compose would expand or unescape (`$NAME`, a backslash inside quotes) stops the script with exit 2 rather than yielding a different password. `restore.sh` runs under `pipefail` and therefore contains no `producer | grep -q` or `| head` pipeline (a reader that leaves early kills the writer with SIGPIPE); `tests/test_regress_round5.py` checks this for every script under `software/` and `scripts/` that sets `pipefail`. Snapshots are classified after the run: `backup.sh` adds `sovereign-complete` (with the `dumps-<epoch>-<digest>` generation tag) only after `restic backup` exited 0 and the `MANIFEST` was unchanged, and `sovereign-incomplete` when restic wrote a snapshot but failed; retention (`forget`) is applied to complete snapshots only, and `restore.sh restore-latest` / `--verify` use the newest complete snapshot (an incomplete one only by explicit id). With the default `RESTIC_REPOSITORY=/var/backups/sovereign`, `restore.sh` on the host resolves the container's `<project>_backup_storage` volume through `docker volume inspect` and stops when both the host directory and the volume hold a repository.
- **Node-Local State (`get_state_dir()`)**: `SMDC_STATE_DIR` if set, else `~/.smdc`, created with mode 0700. It holds the wallets, ledgers and node PQC keys, and by default the DTN spool (`dtn_spool.db`, overridden by `DTN_DB_PATH`), the technician alert log (`sovereign_logs/technician_notifications.jsonl`, created 0600; directory overridden by `SOVEREIGN_LOG_DIR`) and the sentinel mode file (`sovereign_mode`, overridden by `SOVEREIGN_MODE_FILE`). Shared `/tmp` is never a default. Legacy rule: while `<state dir>/dtn_spool.db` does not exist, an existing `/tmp/dtn_spool.db` keeps being used only if it and its SQLite side files are regular files owned by the current user and not group/world-writable; otherwise it is ignored.
- **Compose Overlays**: `software/setup.sh` merges the overlays with `software/docker-compose.yml`. To run one overlay on its own against a core stack that is already up, add the attach file, which declares the core network and volumes as external: `docker compose --project-directory software -f software/docker-compose.attach.yml -f software/agents/docker-compose.agents.yml up -d` (plus `-p <name>` when the core project is not named `software`). The only Compose files and Dockerfiles are those under `software/`; the `.py` files listed in `scripts/check_mirrors.sh` must stay byte-identical to their `src/sovereign_dc/` originals, and the Helm chart's `kubernetes/helm/sovereign-stack/files/power_exporter.py` to `software/telemetry/power_exporter.py` (`bash scripts/check_mirrors.sh` checks both lists), and `bash scripts/validate_compose.sh` checks every stack and the storage-identity invariant.
- **In-Process Event Bus (`events.py`)**: Thread-safe publish/subscribe event dispatcher (`SovereignEventBus`) with wildcard event routing (`load_shedding.*`, `mesh.*`, `space.*`, `economy.*`, `metaverse.*`) and ring-buffered audit logs.

### B. Hardware Abstraction Layer (HAL) (`src/sovereign_dc/hal/`)
- **GPU (`gpu.py`)**: Discovers NVIDIA GPUs via `nvidia-smi` and reports each one's name and total memory (unified memory is read from the host on Tegra/GB10 systems). It does not read power draw, temperature or utilization.
- **Power (`power.py`)**: Reads battery SoC, PV power, battery voltage and system load from the power exporter's `/metrics` (it does not open a VE.Direct or RS485 port itself); refuses an exporter that reports `sovereign_telemetry_source_up 0`.
- **Storage (`storage.py`)**: Reports total, used and free capacity of a filesystem path (`shutil.disk_usage`). It reads no S.M.A.R.T. data, drive wear, drive temperature or I/O figures.
- **Thermal (`thermal.py`)**: Reads coolant and rack air temperatures from the power exporter's `/metrics`, plus Linux `/sys/class/thermal` zones and `nvidia-smi` for the host; DS18B20 probes reach it through the ESP32 bridge and the exporter, not through `/sys/bus/w1`.

### C. Post-Quantum Cryptography & Security Engine (`src/sovereign_dc/security/pqc.py`)
- **ML-DSA-65 & ML-DSA-87 (FIPS 204 algorithm)**: Lattice-based digital signatures, used today for node wallets, `.smdc-app` package signatures (attached by `smdc app package`; checked only when someone runs `smdc app verify` with a trusted key, no installer enforces them) and the dashboard's key self-test. Peer attestation, firmware verification and RFC 9172 (BPSec) DTN bundle signing are planned, not implemented.
- **ML-KEM-768 & ML-KEM-1024 (FIPS 203 algorithm)**: Lattice-based Key Encapsulation Mechanism (KEM), available as a library and exercised by `smdc security pqc`. It is not wired into any terrestrial or space link. Both engines are pure-Python libraries and are not FIPS-validated.

### D. Operations Dashboard, Live Digital Shadow & REST API (`src/sovereign_dc/web/dashboard.py`)
- Built-in zero-dependency HTTP server delivering a responsive, dark-mode glassmorphic single-page operations console (`smdc dashboard`).
- **Live Digital Shadow SSE Stream (`/api/telemetry/stream`)**: Continuous Server-Sent Events stream (one snapshot every 2 s until the client disconnects) delivering telemetry snapshots (fan RPM, coolant temperature, solar harvest, battery SoC) directly to 3D WebGL Digital Twins.
- **Hardware & Metaverse Control APIs**: Remote control endpoints (`/api/control/rack-door`, `/api/control/pdu-outlet`, `/api/control/dtn-transmit`, `/api/metaverse/optimize`, `/api/metaverse/emergency-stop`, `/api/metaverse/resume`) for rack-door and PDU-outlet state, RFC 9171 bundle spooling, and SA-PPO UAV teleoperation from the 3D twin. The door and PDU controls change a **digital shadow** (in-process state) only: no solenoid or PDU driver ships with this package. Their responses and the `hardware` section of `/api/status` carry `actuated: false` / `simulated: true` unless an actuator backend is registered with `register_actuator(ACTUATOR_RACK_DOOR | ACTUATOR_PDU_OUTLET, callable)` (the callable returns whether hardware was driven); in hardware mode without a backend the POST returns HTTP 501 and changes nothing, and a backend that fails returns 502. `hardware.fan_rpm` is an estimate from coolant temperature and load (`fan_rpm_estimated: true`), not a measurement.
- **Control-Plane Access Rules**: The dashboard binds `127.0.0.1` by default (`--host` / `SMDC_DASHBOARD_HOST` to expose it). With `SMDC_DASHBOARD_TOKEN` set, every POST requires `Authorization: Bearer <token>`; without it, POSTs are accepted only from a local client by the same rule the GETs use (`_is_local_request`: loopback TCP peer and no `X-Forwarded-For` / `Forwarded` / `X-Real-IP` / `X-Forwarded-Host` / `X-Forwarded-Proto` / `Via` header) with a loopback `Origin`, on any bind address; a request relayed by a same-host reverse proxy is therefore refused (403), so the token is mandatory behind any reverse proxy. `_is_local_request` also validates the `Host` header (DNS-rebinding defence): local means `Host` is absent, a loopback host, or listed in `SMDC_DASHBOARD_ALLOWED_HOSTS` (comma-separated host names); a `Host` that is neither of those nor the bound address (any IP literal on a wildcard bind) is "untrusted" and gets the JSON/SSE endpoints only with a valid token (otherwise, with no token configured: reads 421, control POSTs 403; with a token configured: 401 for both). With the token set, the JSON GET endpoints and the SSE stream also require it from any client that is not local (a non-loopback peer, or a request carrying `X-Forwarded-For` / `Forwarded` / `X-Real-IP`): as the bearer header, or as `?token=` on `/api/telemetry/stream` only, since `EventSource` cannot send headers; `/health` and the page itself stay open. Without a token those GET endpoints are unauthenticated, so do not expose the dashboard beyond loopback without one. Cross-origin browser access is denied by default: only loopback origins (the locally served twin) are reflected in `Access-Control-Allow-Origin`; `SMDC_CORS_ORIGIN` lists further allowed origins (comma-separated, e.g. `https://iliachry.gr` for the hosted twin) or `*` to allow any. `/api/status` is cached for about 1 s and shared by all SSE clients. The 3D twin takes the token via `?token=` together with `?node=`, binds it to that node's origin in `sessionStorage`, never sends it to another origin and removes it from the address bar. The E-stop endpoint is idempotent and `/api/metaverse/resume` is the only way to resume. `/api/enterprise/telemetry` accepts out-of-process workload metrics.
- **Telemetry Provenance**: Payloads carry `power.is_simulated` / `thermal.is_simulated`; outside simulation mode a missing telemetry source yields HTTP 503 rather than invented readings. The exporter (`telemetry.py`, standalone copy `software/telemetry/power_exporter.py`) is the one owner of the canonical `sovereign_*` power/thermal series and publishes `sovereign_telemetry_simulated` and `sovereign_telemetry_source_up`. Its hardware source is the ESP32 bridge: with simulation off and `ESP32_BRIDGE_URL` set it scrapes the bridge's `/metrics` (Arduino `sovereign_hw_*` series or ESPHome `esphome_sensor_value{name=...}` rows; timeout `ESP32_BRIDGE_TIMEOUT_SECONDS`, default 2 s, cached 1 s) and re-exports the readings under the canonical names (table in `firmware/README.md`); it omits what the bridge did not supply (rack air temperatures and system power draw are never available from the bridge), derives `sovereign_load_shedding_active` from SoC < `SHEDDING_L3_SOC` (default 20 %, shedding level 3) only when SoC was read, and reports `source_up 0` when the bridge is unset, unreachable, empty, or its last valid VE.Direct frame is older than `ESP32_BRIDGE_MAX_FRAME_AGE_SECONDS` (default 10). The "ESP32 BRIDGE READER" block is byte-identical in both files and `tests/test_regress_round3_bridge.py` fails if the firmware exports a series the mapping does not list. Its `/fault` injection endpoints are disabled unless `ENABLE_FAULT_INJECTION=true`.
- Provides real-time status REST APIs (`/api/status`, `/api/metaverse/status`, `/api/metaverse/slices`, `/health`) tracking battery State-of-Charge, solar harvest, thermal loops, Space DTN spools, economy wallets, 5G slices, and the in-process simulated Raft cluster.

### E. Mesh Chaos Engineering Simulator (`src/sovereign_dc/mesh/chaos.py`)
- Simulates network partition scenarios, split-brain conditions, terrestrial link dropouts with automatic Space DTN spooling fallback, and deterministic packet loss replication.

### F. Autonomous AI Agents (`src/sovereign_dc/agents/`)
- **Sentinel Copilot (`sentinel_copilot.py`)**: Polls the power exporter's `/metrics` (`POWER_EXPORTER_URL`) every `SENTINEL_POLL_INTERVAL_SECONDS` (default 30, minimum 1) maps battery SoC to a shedding level 0–4 with the `SHEDDING_L1_SOC`..`SHEDDING_L4_SOC` thresholds (defaults 50 / 30 / 20 / 10 %, the same variables and defaults as `SovereignConfig.shedding_l1..l4_soc`; a test compares them), and writes the operating mode it derives from the level (`NORMAL` at levels 0–1, or `SOLAR_SURPLUS_COMPUTE` at level 0 with PV power above 1000 W and SoC above 75 %; `ECO_PRESERVATION` at levels 2–4; `CRITICAL_SHEDDING` is a valid mode-file value that this agent never selects) to the mode file that other agents read (a failed write is retried on every poll until it succeeds, logged once per failure streak), and publishes `load_shedding.changed` with `mode`, `level` and `battery_soc` on the in-process event bus when the mode or level changes or the SoC has moved by 1 point (or reaches 0 % or 100 %). The running loop tracks the level with a hysteresis: a level is entered as soon as SoC is below its threshold and left only once SoC is `SHEDDING_HYSTERESIS_SOC` points (default 2) above it (capped at 100 %), so a SoC hovering at a threshold does not toggle the level, the mode and the paused apps on every poll (a restarted sentinel starts from the plain mapping). Thresholds that are not a valid policy (L1 > L2 > L3 > L4, within 0–100) are replaced by all four defaults with one warning, exactly as `SovereignConfig` does. It does not read sensors, call an LLM, or schedule, throttle or stop any workload itself; its solar "forecast" is a clear-sky time-of-day curve that is only logged. `smdc benchmark --shed` measures this decision path in simulation. The separate host script `software/telemetry/load_shedder.sh` only logs its decision unless `LOAD_SHED_ACTION=pause` (then it `docker pause`s `LOAD_SHED_CONTAINER`); it gives up its pause marker only on a definite answer from a reachable Docker daemon (container running, exited or absent) and holds it while `docker inspect` fails, so a container it paused is still resumed after Docker recovers.
- **Knowledge Indexer (`knowledge_indexer.py`)**: Chunks documents under `DOCS_WATCH_DIR`, embeds them via local Ollama (`EMBEDDING_MODEL`, default `nomic-embed-text`) and stores them in the Qdrant collection `sovereign_knowledge`. Point ids are UUIDs derived from SHA-256 of (absolute path, chunk index), so a document given once by a relative and once by an absolute path is one entry; points written under the 32-bit integer ids of earlier releases are replaced when their document is re-indexed. Directories matching `DOCS_EXCLUDE_DIRS` (default `appdata_*,files_trashbin,files_versions,files_external,cache,uploads,.*`) are pruned, `DOCS_INCLUDE_GLOB` (default empty = everything; the agents overlay sets `*/files/**` for Nextcloud user files) restricts paths relative to the watch directory, files above `DOCS_MAX_FILE_BYTES` (default 5 MiB) are skipped, and documents that become ineligible are removed from the index. If an upsert batch fails after an earlier batch of the same document was written, the document's points are deleted again (rollback); if that deletion fails too, the path stays in a needs-cleanup set that every scan retries. If the final stale-chunk deletion fails after the chunks were written, the path is kept in a pending-reconciliation set so its points are still removed if the file disappears before a retry succeeds. The worker waits for a missing watch directory instead of creating it. No module in `sovereign_dc` configures logging when imported: the agents, `mesh_sync.py` and the exporters call `logging.basicConfig` only in their script entry point (`tests/test_regress_leftovers.py` checks this for every module). `smdc agent index` lists documents with the worker's own `list_documents()`, so the CLI and the worker index the same set.
- **GitLab Code Reviewer (`gitlab_reviewer.py`)**: Polls the GitLab API every 60 s for open merge requests (no webhook), sends the first 4,000 characters of each diff to a local model (`OLLAMA_CODE_MODEL`, default `qwen2.5-coder:7b`, via Ollama or an OpenAI-compatible endpoint) and posts one note per MR revision (the MR listing and each MR's notes are read page by page via GitLab's `X-Next-Page` header, up to 50 pages of 100); LLM output is escaped so it cannot trigger GitLab quick actions. Without `GITLAB_API_TOKEN` it reviews nothing. `smdc agent review <file>` runs the same model call on a local file.
- **Technician Notifier (`technician_notifier.py`)**: Dispatches urgent hardware repair instructions to a local file log and the Space DTN spool; the MQTT and LoRa channels are placeholders that report failure until a transport is implemented.

### G. Space Delay-Tolerant Networking (DTN / BPv7) (`src/sovereign_dc/space/`)
- **RFC 9171 BPv7 Bundles (`dtn/bundle.py`)**: Creates and parses bundles with unkeyed SHA-256 integrity digests (`chk`, `chk2`) and base64 payloads. Bundles are neither signed nor encrypted: `PQCSigner.sign_bundle` / `verify_bundle` in `security/pqc.py` can add and check an ML-DSA signature block on a bundle dict, but nothing in the DTN bundle/router path calls them (only tests do). Signed settlement envelopes (`economy/settlement.py`) are a separate mechanism that signs the settlement payload, not the bundle.
- **Store-and-Forward Router (`dtn/router.py`)**: Persistent NVMe spool for bundles awaiting satellite contact windows. Its default local endpoint id comes from `default_local_eid()`: `DTN_LOCAL_EID` if set, else `dtn://<NODE_ID>.sovereign.space` (default node id `smdc-dgx-01`).
- **DTN Submission Contract (`POST /api/control/dtn-transmit`, MCP `spool_dtn_bundle`, `SMDCClient.send_dtn_bundle`)**: JSON fields `destination` (endpoint ID), `payload` (text) or `payload_b64` (base64, for binary; wins when both are given), `ttl` (seconds, default 7 days, capped at 30 days; `ttl_seconds` in the MCP tool), `priority` (0 bulk, 1 normal = default, 2 expedited, 3 critical) and `source` (used if it is a valid endpoint ID, else `dtn://<node-id>/<service>`). The SDK falls back to the in-process spool only when the node is unreachable; an HTTP error answer is returned as `status: "rejected"` with `http_status` and `error`.
- **Orbital Pass Predictor (`orbital/propagator.py`)**: Simplified circular-orbit propagator (not SGP4) that computes AOS/LOS satellite contact passes, azimuth, elevation, and Doppler shifts.
- **RF Link Budget Simulator (`transceiver/simulated_link.py`)**: Free-Space Path Loss (FSPL) calculations across UHF, S-band, and X-band.

### H. Autonomous Monetary & Compute Economy Layer (`src/sovereign_dc/economy/`)
- **Node Wallet (`wallet.py`)**: Post-Quantum (ML-DSA-87) and Ed25519 keypair identity, deterministic address derivation (`sov_...` / `sov_pqc_...`), and payload signing.
- **Immutable Ledger & State Channels (`ledger.py`)**: Append-only transaction ledger with replay protection and unidirectional off-chain micropayment state channels (one payer, one payee per channel). Settlement keys are part of the signed transaction so a receipt cannot be paid twice across reconciled ledgers; a per-row chain digest plus an HMAC head anchor under the node identity key make deletion and reordering evident (`smdc economy audit [--anchor]`).
- **Node Identity (`node_state.py`)**: One wallet pair (`smdc_node_wallet.json`, `smdc_node_pqc_wallet.json`) and one ledger (`smdc_node_ledger.db`) in the state directory, shared by the CLI, the dashboard and the MCP server, so all three report the same address and balance. Nothing mints implicitly: a new ledger starts at zero and `smdc economy mint <amount> [--memo]` is the only source of credits, which are locally issued test credits with no external value. A wallet/ledger set left by an older release (CLI, dashboard or MCP) is adopted by renaming it; with several, the CLI set wins and the others are left on disk with one warning. Balances are integer micro-credits (`balances.balance_micro`), at most 2,000,000,000 credits.
- **Dynamic Solar-Aware Pricing Engine (`market.py`)**: Pricing oracle that multiplies every catalog rate by an energy-state factor (named constants in `market.py`; a test compares this line with them): a 50% discount while solar harvest is at least 500 W and battery SoC at least 75%, nominal pricing down to 40% SoC, a 1.5x surge below 40% SoC and a 3.0x surge below 25% SoC. An unknown solar harvest never earns the discount. The sentinel's `SOLAR_SURPLUS_COMPUTE` mode is a separate rule (more than 1000 W).
- **Delay-Tolerant Settlement (`settlement.py`)**: Cryptographic Proof-of-Compute and Proof-of-Relay verification reconciled across terrestrial mesh or RFC 9171 satellite contact passes.

### I. Native Model Context Protocol (MCP) Server (`src/sovereign_dc/mcp/`)
- **Standard Protocol Support (2024-11-05)**: Standardized JSON-RPC 2.0 stdio interface exposing datacenter hardware and services to AI assistants (Antigravity, Claude Desktop, Cursor, Cline).
- **Operational MCP Tools (`tools.py`)**: 16 callable tools including `get_telemetry`, `get_system_status`, `set_load_shedding`, `query_market_pricing`, `get_wallet_balances`, `spool_dtn_bundle`, `predict_satellite_passes`, `query_knowledge_indexer`, `run_security_audit`, `dispatch_technician_alert`, `list_enterprise_apps`, `manage_enterprise_app`, `scaffold_enterprise_app`, `run_metaverse_sim_cycle`, `get_5g_slices_status`, and `validate_depin_sla`. `get_5g_slices_status` (and the `smdc://metaverse/5g/slices` resource) reads the process-wide metaverse orchestrator, the same one the dashboard serves at `/api/metaverse/slices`; in a process where no cycle has run, the result states `source: "fresh_simulation"` (otherwise `"process_orchestrator"`). `run_metaverse_sim_cycle` runs its cycles on that same shared orchestrator, so the status tools reflect them; when a `seed` is passed it runs a separate, reproducible orchestrator instead and says so (`orchestrator: "separate_seeded_run"`). `query_market_pricing` reads telemetry only for an input the caller did not supply (the battery SoC is required, a missing solar reading is `unknown` and earns no discount) and reports `input_sources` per input (`caller`, `measured_telemetry`, `simulated_telemetry`, `unknown`). `set_load_shedding` only publishes the event: its `description` and `paused_tiers` state the implemented effect (which power tiers the `EnterpriseManager` marks paused at that level), and the hardware actions of the design are listed under `design_targets_not_implemented`.
- **Dynamic MCP Resources (`resources.py`)**: 9 live URI resources (`smdc://telemetry/current`, `smdc://system/manifest`, `smdc://economy/market`, `smdc://space/dtn/spool`, `smdc://security/pqc/status`, `smdc://enterprise/apps`, `smdc://enterprise/schema`, `smdc://metaverse/uav/status`, `smdc://metaverse/5g/slices`).
- **Standard MCP Prompts (`prompts.py`)**: 5 diagnostic and workflow prompts (`diagnose_power_incident`, `plan_compute_workload`, `prepare_space_transmission`, `onboard_enterprise_workload`, `optimize_uav_coverage`).

### J. Enterprise Workload Onboarding & Coupling Framework (`src/sovereign_dc/enterprise/`)
- **Declarative Manifest Specification (`schema.py`)**: Type-safe `smdc-app.yaml` manifest supporting multi-tier power shedding ($L_0 \to L_4$), memory/vRAM quotas, network policies, and health probes.
- **Enterprise Registry & Discovery (`registry.py`)**: Automated discovery across `/etc/smdc/apps`, `~/.smdc/apps`, and workspace paths with turnkey project scaffolding (`smdc app init`; it refuses with a non-zero exit and writes nothing when any scaffold file already exists, and `--force` first moves each existing file to `<name>.bak-<timestamp>`; files are written atomically). The registry file is shared by every `smdc` process: a save locks it, reloads it and applies only this instance's pending changes, so concurrent `smdc app register` runs are merged. Lookups (`get_app`, `list_apps`) re-read the file when its modification time or size changed, so a long-running process (MCP server, dashboard) sees apps registered after it started; the `EnterpriseManager` reconciles its tracked runtimes with the registry on every lookup and shedding event (current manifest and power tier, runtimes of unregistered apps dropped). `register_app` returns `(False, [reason])` when the file could not be saved (lock timeout, unwritable directory, unreadable file) and takes the registration back; `smdc app register` then exits non-zero. `unregister_app` behaves the same way (`last_save_error` holds the reason).
- **Zero-Dependency Enterprise SDK (`sdk.py`)**: Minimal footprint `SMDCClient` and `AppLifecycleHandler` providing direct event bus binding, hardware telemetry streams, dynamic pricing queries (`get_dynamic_pricing()` prices the node's own telemetry when called without arguments, returns `status: "unavailable"` and no multiplier when the battery SoC cannot be read, and always reports its `inputs` and their source), and Space DTN spooling.
- **Load Shedding State Manager (`manager.py`)**: In-memory app state tracking (it does not launch or supervise OS processes; resource figures are estimates from the manifest) marking apps paused by power tier on `load_shedding.*` events (level 1 pauses `L4_IDLE`, level 2 adds `L3_DEFERRABLE`, level 3 adds `L2_BACKGROUND`, level 4 adds `L1_STANDARD`; `L0_CRITICAL` never) and while a reported `battery_soc` is below the manifest's `power.min_battery_soc`, resuming only what shedding paused once the level and SoC allow it; `start_app` applies the level and SoC of the last event at once, so an app the policy pauses right now is started paused by shedding (`started` then `paused` events, `paused_by: load_shedding`) instead of running until the next event (`max_ambient_temp_c`, `allow_solar_burst` and `min_solar_watts` are reserved, not enforced), plus NIST FIPS 204 ML-DSA-87 cryptographic bundle packaging (`.smdc-app` + `.sig`).

### K. 5G RAN & UAV Metaverse Simulation Stack (`src/sovereign_dc/metaverse/`)
- **Physics-Based Electromagnetic Ray-Tracer (`ray_tracer.py`)**: 3GPP UMi & Sionna 3D multipath ray-tracer at 3.5 GHz with up to 5 reflection orders, urban building LoS obstruction checking, Kriging spatial interpolation, and per-receiver SINR calculation.
- **Scene-Aware Deep Reinforcement Learning Agent (`agent.py`)**: 3-layer Actor-Critic neural networks with 20-dim state vector, discrete 6-directional action space ($\pm X, \pm Y, \pm Z$), sum-SINR reward formulation with disadvantaged receiver weighting, and Model-Driven PPO (MD-PPO) baseline comparison.
- **5G Network Slicing & SDN Controller (`slicing.py`)**: Mathematical bandwidth isolation ($T_{\mathrm{tx}} = (D \cdot 8)/B_{\mathrm{slice}} \cdot 1000\text{ ms}$) managing URLLC ($< 1\text{ ms}$), eMBB ($127\text{ Mbps}$), and mMTC ($12\text{k devs/km}^2$) slices.
- **DePIN Decentralized SLA & Blockchain Consensus (`depin_sla.py`)**: Multi-validator PoS/dBFT consensus requiring $\lfloor 2N/3 \rfloor + 1$ multi-signatures, enforcing minimum SINR rejection thresholds ($-15\text{ dB}$) and automated repositioning triggers.
- **Multi-Layer Orchestrator (`engine.py`)**: Implements Algorithm 1 (3-phase execution: parallel IoT/ray-tracing collection $\to$ critical path decision $<6\text{ ms} \to$ asynchronous DePIN block finalization).
- **Comparative RL Benchmark Suite (`benchmark.py`)**: Parametric SA-PPO vs MD-PPO sweeps over per-receiver capacity, measured decision/ray-tracing latencies, and Shannon spectral efficiencies. The paper's $+79.6\%$ Rx1 gain is not reproduced by this simplified simulator; results vary with the seed.

### L. Terraform & OpenTofu Infrastructure-as-Code (`terraform/`)
- **Zero-Cloud Declarative IaC**: Compatible with OpenTofu ($\ge 1.7$) and Terraform ($\ge 1.6$), managing bare-metal and virtualized on-premise infrastructure without public cloud lock-in.
- **Talos Linux Automation (`modules/talos-cluster/`)**: Automates machine secret creation, LUKS2 disk encryption, 10GbE network interfaces, NVIDIA GPU container extensions, and kubeconfig generation.
- **Hypervisor Edge Nodes (`modules/proxmox-edge-node/`, `modules/libvirt-edge-node/`)**: Provisions VMs with PCI GPU passthrough and high-performance NVMe storage.
- **WireGuard Mesh (`modules/wireguard-mesh/`)**: Generates node keypairs, per-pair preshared keys and full-mesh `wg0.conf` text (`10.42.0.0/16`). Standalone: no environment calls it and it installs nothing on the nodes.
- **Kubernetes Workload Bootstrap (`modules/k8s-sovereign-stack/`)**: Automated Helm deployment of the sovereign datacenter stack directly onto provisioned clusters.

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

```bash
# Execute the CLI from a checkout (Linux / macOS / Windows): `uv run` uses the project environment
uv run smdc --help
uv run smdc status
uv run smdc doctor
uv run smdc dashboard --port 8080
uv run smdc audit                                  # host hardening audit (Linux only)
uv run smdc app list
uv run smdc app init "IoT Edge" --app-id iot-edge --category iot --target-dir ./iot-edge
uv run smdc app validate ./iot-edge
uv run smdc app register ./iot-edge
uv run smdc app start iot-edge
uv run smdc app status iot-edge
uv run smdc app package ./iot-edge --output iot-edge-1.0.0.smdc-app
uv run smdc app verify iot-edge-1.0.0.smdc-app --public-key <hex-or-file>   # exit 1 unless digest, size and ML-DSA-87 signature match the given key
uv run smdc economy wallet
uv run smdc economy mint 100 --memo "local test"
uv run smdc economy market --soc 85 --solar 1200
uv run smdc economy history
uv run smdc economy audit --anchor
uv run smdc benchmark --ai --live
uv run smdc benchmark --all
uv run smdc benchmark --shed --shed-iterations 200
uv run smdc mesh chaos --help
uv run smdc mesh consensus --nodes 3
uv run smdc security pqc --help
uv run smdc demo --steps 3
uv run smdc space passes --hours 12
uv run smdc sim run --cycles 5
uv run smdc sim slices
uv run smdc sim sla
uv run smdc sim benchmark --episodes 5 --steps 10
uv run smdc mcp test
uv run smdc mcp tools
uv run smdc mcp resources
uv run smdc mcp prompts
uv run smdc mcp serve
```

Windows equivalent without `uv run`: `.\.venv\Scripts\python -m sovereign_dc <command>` (on Linux / macOS: `.venv/bin/python -m sovereign_dc <command>`).

---

## 6. Checklist Before Completing Any Agent Task

1. [ ] **Pass All Quality Gates Locally**: Run `scripts/quality_gate.ps1` or `scripts/quality_gate.sh` (Ruff lint/format, Mypy typing, Pytest $\ge 85\%$ coverage).
2. [ ] **Pass All Unit Tests**: Verify all 1480 tests in `tests/` pass with zero failures.
3. [ ] **Preserve Existing Interfaces**: Ensure CLI arguments, Prometheus metric names, and DTN bundle schemas remain backward-compatible.
4. [ ] **Verify Markdown/Mermaid & Math**: Ensure any new or modified `.md` files strictly comply with GitHub Mermaid and LaTeX math rendering rules.
5. [ ] **Synchronize Documentation**: Update `AGENTS.md` and `README.md` with any newly added modules, subcommands, or architectural changes.
6. [ ] **Clean Conventional Commits**: Format commit messages cleanly using Conventional Commits (`feat:`, `fix:`, `docs:`, `test:`, `refactor:`).
7. [ ] **Commit & Push to Remote**: Always stage changes, commit, and push directly to `origin/main` (or working feature branch).
8. [ ] **Verify Remote CI Pipeline & Succeeded Before Stopping**: Always monitor and check that the remote GitHub Actions CI pipeline completes with **100% green (succeeded)** status after pushing. If any step fails, investigate, fix, commit, and re-verify until the entire CI pipeline succeeds before concluding the task.
