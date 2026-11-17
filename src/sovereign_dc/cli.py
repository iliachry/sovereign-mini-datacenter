"""
Sovereign Mini Datacenter CLI (smdc)
Includes Space Communications, Delay-Tolerant Networking, Host Hardening Audit & Multi-Node Mesh.
"""

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from typing import Any

# Ensure clean UTF-8 output on Windows consoles
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from sovereign_dc import __version__, telemetry
from sovereign_dc.config import DEFAULT_NODE_ID
from sovereign_dc.space.dtn.bundle import Bundle, BundlePriority
from sovereign_dc.space.dtn.router import DTNRouter
from sovereign_dc.space.orbital.propagator import GroundStation
from sovereign_dc.space.orbital.tle_updater import get_active_satellites
from sovereign_dc.space.transceiver.simulated_link import SpaceChannelSimulator

# Color codes
GREEN = "\033[1;32m"
BLUE = "\033[1;34m"
CYAN = "\033[1;36m"
YELLOW = "\033[1;33m"
MAGENTA = "\033[1;35m"
RED = "\033[1;31m"
BOLD = "\033[1m"
RESET = "\033[0m"


def get_project_root():
    cwd = os.getcwd()
    if os.path.exists(os.path.join(cwd, "software", "docker-compose.yml")):
        return cwd
    if os.path.exists(os.path.join(cwd, "docker-compose.yml")):
        return os.path.dirname(cwd)
    return cwd


def _arg(args: Any, name: str, default: Any) -> Any:
    """Returns a parsed CLI value, using the default only when it is absent (0 is a real value)."""
    value = getattr(args, name, None)
    return default if value is None else value


def cmd_status(args):
    """Displays infrastructure status, containers, power telemetry, and space link."""
    print(f"\n{BOLD}{CYAN}=== Sovereign Mini Datacenter — System Status ==={RESET}")

    # 1. Container health check
    print(f"\n{BOLD}[1] Container Subsystems:{RESET}")
    try:
        res = subprocess.run(
            ["docker", "ps", "--format", "table {{.Names}}\t{{.Status}}\t{{.Ports}}"], capture_output=True, text=True
        )
        if res.returncode == 0 and res.stdout.strip():
            lines = res.stdout.strip().split("\n")
            for line in lines:
                if "sovereign_" in line or "NAMES" in line:
                    if "Up" in line:
                        print(f"  {GREEN}●{RESET} {line}")
                    elif "NAMES" in line:
                        print(f"    {BOLD}{line}{RESET}")
                    else:
                        print(f"  {YELLOW}○{RESET} {line}")
        else:
            print(f"  {YELLOW}No active sovereign containers found. (Run 'smdc deploy' to launch){RESET}")
    except Exception as e:
        print(f"  {RED}Docker daemon unavailable: {e}{RESET}")

    # 2. Power & Thermal Telemetry
    from sovereign_dc.config import get_config

    cfg = get_config()
    power_url = cfg.power_exporter_url
    space_url = f"http://localhost:{cfg.space_exporter_port}/metrics"
    print(f"\n{BOLD}[2] Power & Thermal Telemetry:{RESET}")
    try:
        from sovereign_dc.hal.power import TelemetryUnavailable, fetch_exporter_metrics, optional_metric

        try:
            power_metrics: dict[str, float] | None = fetch_exporter_metrics(power_url)
        except TelemetryUnavailable as unavailable:
            power_metrics = None
            print(f"  {YELLOW}Power telemetry exporter is up but has no measurements: {unavailable}{RESET}")
        if power_metrics is not None:
            readings = power_metrics

            def shown(name: str, digits: int, unit: str) -> str:
                # A missing or non-finite sample is "n/a", never a made-up number.
                value = optional_metric(readings, name)
                return "n/a" if value is None else f"{value:.{digits}f}{unit}"

            shedding = optional_metric(readings, "sovereign_load_shedding_active")
            simulated = (optional_metric(readings, "sovereign_telemetry_simulated") or 0.0) > 0
            soc_text = shown("sovereign_battery_soc_percent", 1, "%")
            volts_text = shown("sovereign_battery_voltage_volts", 1, " V")
            print(f"  • Battery Bank:    {GREEN}{soc_text}{RESET} ({volts_text})")
            print(f"  • Solar PV Input:  {CYAN}{shown('sovereign_solar_pv_power_watts', 0, ' W')}{RESET}")
            print(f"  • System AC Load:  {YELLOW}{shown('sovereign_system_power_draw_watts', 0, ' W')}{RESET}")
            print(f"  • Coolant Temp:    {GREEN}{shown('sovereign_temp_coolant_celsius', 1, ' °C')}{RESET}")
            if shedding is None:
                print(f"  • Sentinel Status: {YELLOW}n/a{RESET}")
            else:
                print(f"  • Sentinel Status: {'⚡ ' + RED + 'LOAD SHEDDING' if shedding else GREEN + 'NORMAL'}{RESET}")
            if simulated:
                print(f"  {YELLOW}(simulated telemetry: the exporter is not reading hardware){RESET}")
    except Exception:
        print(f"  {YELLOW}Power telemetry exporter offline at {power_url} (Run 'smdc telemetry' to start){RESET}")

    # 3. Space & Satellite Telemetry
    print(f"\n{BOLD}[3] Space & Satellite Communications (DTN / BPv7):{RESET}")
    try:
        req = urllib.request.Request(space_url, headers={"User-Agent": "smdc-cli"})
        with urllib.request.urlopen(req, timeout=2) as response:
            content = response.read().decode("utf-8")
            metrics = {}
            for line in content.split("\n"):
                if line and not line.startswith("#"):
                    parts = line.split()
                    if len(parts) == 2:
                        metrics[parts[0]] = float(parts[1])

            active = metrics.get("sovereign_space_link_active", 0) > 0
            el = metrics.get("sovereign_space_elevation_degrees", 0.0)
            az = metrics.get("sovereign_space_azimuth_degrees", 0.0)
            snr = metrics.get("sovereign_space_link_snr_db", 0.0)
            doppler = metrics.get("sovereign_space_doppler_shift_hz", 0.0)
            next_pass = int(metrics.get("sovereign_space_next_pass_seconds", 0))
            spool_count = int(metrics.get("sovereign_space_bundle_spool_count", 0))

            status_str = f"{GREEN}ONLINE (IN CONTACT){RESET}" if active else f"{YELLOW}STANDBY (TRACKING){RESET}"
            print(f"  • Space Link:      {status_str}")
            print(f"  • Antenna Pointing:Azimuth {CYAN}{az:.1f}°{RESET}, Elevation {CYAN}{el:.1f}°{RESET}")
            print(f"  • Link Quality:    SNR {GREEN}{snr:.1f} dB{RESET} | Doppler {doppler:.0f} Hz")
            print(f"  • Next Pass (AOS): {CYAN}{next_pass // 60}m {next_pass % 60}s{RESET}")
            print(f"  • DTN Spool Queue: {MAGENTA}{spool_count} bundles queued{RESET}")
    except Exception:
        gs = GroundStation("Sovereign-Ground-01", 37.9838, 23.7275)
        sats = get_active_satellites()
        print(f"  • Status:          {YELLOW}STANDBY (Offline Exporter){RESET}")
        if not sats:
            print(f"  • Primary Relay:   {YELLOW}No satellites in catalog{RESET}")
        else:
            passes = gs.predict_passes(sats[0], duration_hours=6.0)
            print(f"  • Primary Relay:   {CYAN}{sats[0].name}{RESET}")
            if passes:
                next_sec = max(0, int(passes[0]["aos_time"] - time.time()))
                print(
                    f"  • Next Pass (AOS): {CYAN}{next_sec // 60}m {next_sec % 60}s{RESET} (Max El: {passes[0]['max_elevation']}°)"
                )
            else:
                print(f"  • Next Pass (AOS): {YELLOW}No pass in the next 6 hours{RESET}")

    print(f"\n{BOLD}[4] Live 3D Digital Twin:{RESET} {CYAN}https://iliachry.gr/sovereign-mini-datacenter/{RESET}\n")


def cmd_audit(args):
    """Executes the host security hardening audit (software/security/audit.sh).

    Returns the audit script's exit code. The audit inspects Linux kernel, Docker and firewall
    state, so on other platforms it reports that nothing was checked instead of printing results.
    """
    print(f"\n{BOLD}{CYAN}=== Sovereign Mini Datacenter — Host Hardening Audit ==={RESET}\n")
    candidates = [
        os.path.join(get_project_root(), "software", "security", "audit.sh"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "security", "audit.sh"),
    ]
    audit_script = next((c for c in candidates if os.path.exists(c)), None)

    if audit_script is None:
        print(f"  {RED}✖ Audit script not found; no checks were run.{RESET}\n")
        return 2
    if not sys.platform.startswith("linux"):
        print(
            f"  {YELLOW}⚠ The hardening audit only runs on a Linux datacenter node "
            f"(this is {sys.platform}); no checks were run.{RESET}\n"
        )
        return 2
    return subprocess.run(["bash", audit_script]).returncode


def cmd_mesh(args):
    """Displays the reference multi-node mesh topology (a design example; no peer is probed)."""
    print(f"\n{BOLD}{CYAN}🌐 Sovereign Global Mesh — Reference Multi-Node Topology{RESET}")
    print(f"{YELLOW}Illustrative three-site design: these peers are not discovered or probed by this command.{RESET}\n")
    peers = [
        (
            "smdc-node-01",
            "Ground Station Alpha (Athens)",
            "100.64.0.1",
            "Primary Gateway",
            "2x DGX Spark",
            "10.24 kWh",
            f"{YELLOW}NOT PROBED{RESET}",
        ),
        (
            "smdc-node-02",
            "Alpine Off-Grid (Switzerland)",
            "100.64.0.2",
            "Edge Node",
            "275 TOPS",
            "15.36 kWh",
            f"{YELLOW}NOT PROBED{RESET}",
        ),
        (
            "smdc-node-03",
            "Aegean Island Autonomous",
            "100.64.0.3",
            "Edge Satellite Node",
            "275 TOPS",
            "20.48 kWh",
            f"{YELLOW}NOT PROBED{RESET}",
        ),
    ]
    print(
        f"{'Node ID':<14} {'Location / Name':<32} {'Mesh IP':<13} {'Role':<18} {'Compute':<10} {'Battery':<10} {'Status'}"
    )
    print("-" * 115)
    for nid, loc, ip, role, tops, bat, status in peers:
        print(f"{BOLD}{nid:<14}{RESET} {loc:<32} {ip:<13} {role:<18} {tops:<10} {bat:<10} {status}")
    print(
        f"\n{BOLD}Sync Transports:{RESET} WireGuard Overlay (Terrestrial) ➔ Delay-Tolerant Space Relays (Orbital Fallback)\n"
    )


def cmd_space_passes(args):
    """Predicts and lists upcoming satellite contact passes."""
    lat = float(os.getenv("GROUND_STATION_LAT", "37.9838"))
    lon = float(os.getenv("GROUND_STATION_LON", "23.7275"))
    gs = GroundStation("Sovereign-Ground-01", lat, lon)
    satellites = get_active_satellites()

    print(f"\n{BOLD}{CYAN}🛰️  Upcoming Space Contact Passes (Next {args.hours} Hours){RESET}")
    print(f"Ground Station: {BOLD}{gs.name}{RESET} (Lat: {lat:.4f}°, Lon: {lon:.4f}°)\n")
    print(f"{'Satellite':<24} {'AOS Time':<20} {'Duration':<10} {'Max El':<10} {'AOS Az':<10}")
    print("-" * 76)

    total_passes = 0
    for sat in satellites:
        passes = gs.predict_passes(sat, duration_hours=args.hours, min_elevation_deg=args.min_el)
        for p in passes:
            total_passes += 1
            aos_dt = datetime.fromtimestamp(p["aos_time"]).strftime("%Y-%m-%d %H:%M:%S")
            dur = f"{p['duration_seconds'] // 60}m {p['duration_seconds'] % 60}s"
            max_el = f"{p['max_elevation']}°"
            az = f"{p['aos_azimuth']}°"
            print(f"{sat.name:<24} {aos_dt:<20} {dur:<10} {max_el:<10} {az:<10}")
    print("")


def cmd_space_status(args):
    """Displays detailed space link budget and live orbital coordinates."""
    lat = float(os.getenv("GROUND_STATION_LAT", "37.9838"))
    lon = float(os.getenv("GROUND_STATION_LON", "23.7275"))
    gs = GroundStation("Sovereign-Ground-01", lat, lon)
    sim = SpaceChannelSimulator(gs)
    satellites = get_active_satellites()

    print(f"\n{BOLD}{CYAN}🛰️  Space Communication & Link Budget Status{RESET}\n")
    for sat in satellites:
        m = sim.get_active_link_metrics(sat)
        contact_str = f"{GREEN}● IN CONTACT{RESET}" if m["is_in_contact"] else f"{YELLOW}○ OUT OF RANGE{RESET}"
        print(f"{BOLD}Satellite:{RESET} {CYAN}{sat.name:<22}{RESET} [{contact_str}]")
        print(f"  • Sub-Satellite Coordinates: NORAD ID {sat.norad_id}, Altitude {sat.altitude_km:.0f} km")
        print(
            f"  • Look Angles: Azimuth {m['azimuth_deg']}°, Elevation {m['elevation_deg']}°, Slant Range {m['range_km']} km"
        )
        print(
            f"  • RF Budget:   Path Loss {m['path_loss_db']} dB, SNR {m['snr_db']} dB, Doppler {m['doppler_shift_hz']} Hz"
        )
        print("")


def _dtn_spool_path() -> str:
    """The node's DTN spool: DTN_DB_PATH if set, otherwise the configured ``dtn_db_path``.

    Going through the node configuration keeps the CLI on the same spool as the dashboard,
    the MCP server and the SDK when the path comes from a YAML config file.
    """
    from sovereign_dc.config import get_config

    return os.getenv("DTN_DB_PATH") or get_config().dtn_db_path


def cmd_space_send(args):
    """Queues a bundle into the DTN store-and-forward spool for the next pass."""
    from sovereign_dc.config import get_config
    from sovereign_dc.web.dashboard import node_eid

    db_path = _dtn_spool_path()
    router = DTNRouter(db_path=db_path)

    if os.path.isfile(args.message_or_file):
        with open(args.message_or_file, "rb") as f:
            payload = f.read()
    else:
        payload = args.message_or_file.encode("utf-8")

    src_eid = node_eid(get_config().node_id, "cli")
    prio_map = {
        0: BundlePriority.BULK,
        1: BundlePriority.NORMAL,
        2: BundlePriority.EXPEDITED,
        3: BundlePriority.CRITICAL,
    }
    prio = prio_map.get(args.priority, BundlePriority.NORMAL)

    bundle = Bundle(
        source_eid=src_eid,
        destination_eid=args.destination_eid,
        payload=payload,
        priority=prio,
        lifetime_seconds=args.ttl,
    )

    if router.queue_bundle(bundle):
        print(f"\n{GREEN}✅ Bundle queued successfully!{RESET}")
        print(f"  • Bundle ID:     {bundle.bundle_id}")
        print(f"  • Source:        {bundle.source_eid}")
        print(f"  • Destination:   {CYAN}{bundle.destination_eid}{RESET}")
        print(f"  • Size:          {len(payload)} bytes")
        print(f"  • Priority:      Tier {prio}")
        print("  • Status:        Waiting in spool for next orbital contact pass.\n")
        return 0
    print(f"\n{RED}✖ Bundle was not queued (expired TTL or spool error).{RESET}\n")
    return 1


def cmd_space_queue(args):
    """Lists bundles currently queued in the DTN store-and-forward spool."""
    db_path = _dtn_spool_path()
    router = DTNRouter(db_path=db_path)
    stats = router.get_queue_stats()

    print(f"\n{BOLD}{MAGENTA}📦 DTN Store-and-Forward Spool Queue{RESET}")
    print(f"Spool Location: {BOLD}{db_path}{RESET}\n")
    print(f"  • Total Queued Bundles: {stats['queued_bundle_count']}")
    print(f"  • Total Spool Size:     {stats['total_spool_bytes']:,} bytes")
    print("  • Priority Breakdown:")
    for prio, count in stats["priority_counts"].items():
        if count > 0:
            color = RED if prio == "critical" else (YELLOW if prio == "expedited" else CYAN)
            print(f"      - {color}{prio.capitalize():<12}{RESET}: {count}")

    print(f"\n{BOLD}Next Transmission Window (Top 5 Bundles):{RESET}")
    queue = router.get_outbound_queue()
    if not queue:
        print(f"  {YELLOW}Queue is empty.{RESET}\n")
    else:
        for i, b in enumerate(queue[:5]):
            prio_color = (
                RED
                if b.priority == BundlePriority.CRITICAL
                else (YELLOW if b.priority == BundlePriority.EXPEDITED else GREEN)
            )
            prio_name = {3: "CRITICAL", 2: "EXPEDITED", 1: "NORMAL", 0: "BULK"}.get(b.priority, "UNKNOWN")
            print(f"  {i + 1}. [{prio_color}{prio_name}{RESET}] ID: {b.bundle_id}")
            print(f"     Dst: {CYAN}{b.destination_eid}{RESET} | Size: {b.total_application_data_length} bytes")
        if len(queue) > 5:
            print(f"  ... and {len(queue) - 5} more.")
    print("")


# Keep in step with PLACEHOLDER_RE in software/setup.sh.
_ENV_PLACEHOLDER_MARKERS = ("change_me", "changeme", "replace_this", "your_", "your@", "yourdomain", "yourmailprovider")


def _env_placeholder_keys(env_file: str) -> list[str]:
    """Returns the keys in a .env file whose values still look like template placeholders."""
    keys: list[str] = []
    with open(env_file, encoding="utf-8", errors="replace") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            if any(marker in value.lower() for marker in _ENV_PLACEHOLDER_MARKERS):
                keys.append(key.strip())
    return keys


# Keep in step with OPENPROJECT_* and check_openproject_pgdata_volume() in software/setup.sh:
# the shell script exposes no pre-flight-only entry point, so the guard is implemented twice.
_DEPLOY_MODULES = ("vpn", "backup", "telemetry", "space", "agents", "security")
_OPENPROJECT_CONTAINER = "sovereign_openproject"
_OPENPROJECT_PGDATA_PATH = "/var/openproject/pgdata"
_OPENPROJECT_PGDATA_VOLUME_KEY = "openproject_pgdata"


def _docker_stdout(cmd: list[str], cwd: str | None = None) -> tuple[int, str]:
    """Runs a docker query; returns (exit code, stdout). A missing docker binary is exit 127."""
    try:
        res = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    except OSError:
        return 127, ""
    return res.returncode, res.stdout if isinstance(res.stdout, str) else ""


def _openproject_pgdata_guard(compose_cmd: list[str], soft_dir: str, rerun_command: str) -> str | None:
    """Pre-flight guard for the OpenProject database volume; returns a refusal message or None.

    docker-compose.yml mounts the named volume ``<project>_openproject_pgdata`` at
    /var/openproject/pgdata. The openproject/community image declares that path as a VOLUME,
    so a deployment created before the named volume existed keeps its PostgreSQL data in an
    anonymous volume. Recreating the container would start OpenProject on the empty named
    volume: the data would not be deleted, but orphaned. Deployment is refused until the
    data has been copied across.

    Returns None when the container does not exist or already uses the named volume.
    """
    rc, _ = _docker_stdout(["docker", "container", "inspect", _OPENPROJECT_CONTAINER])
    if rc != 0:
        return None

    # Project name exactly as Compose resolves it (COMPOSE_PROJECT_NAME, `name:`, directory).
    project = ""
    rc, config_out = _docker_stdout([*compose_cmd, "config"], cwd=soft_dir)
    if rc == 0:
        for line in config_out.splitlines():
            if line.startswith("name:"):
                project = line[len("name:") :].strip().strip("\"'")
                break
    if not project:
        _, label_out = _docker_stdout(
            [
                "docker",
                "container",
                "inspect",
                "--format",
                '{{ index .Config.Labels "com.docker.compose.project" }}',
                _OPENPROJECT_CONTAINER,
            ]
        )
        project = label_out.strip()
    if not project or project == "<no value>":
        return (
            "Cannot determine the Compose project name; refusing to touch the existing "
            f"{_OPENPROJECT_CONTAINER} container."
        )
    expected = f"{project}_{_OPENPROJECT_PGDATA_VOLUME_KEY}"

    _, mounts_out = _docker_stdout(
        ["docker", "container", "inspect", "--format", "{{ json .Mounts }}", _OPENPROJECT_CONTAINER]
    )
    try:
        mounts = json.loads(mounts_out) if mounts_out.strip() else []
    except ValueError:
        mounts = []
    mount = next(
        (m for m in mounts or [] if isinstance(m, dict) and m.get("Destination") == _OPENPROJECT_PGDATA_PATH),
        None,
    )
    # Only a Docker volume other than the named one is at risk (no mount / bind mount: nothing to orphan here).
    if mount is None or mount.get("Type") != "volume":
        return None
    mount_name = str(mount.get("Name") or "")
    if not mount_name or mount_name == expected:
        return None

    return f"""The existing {_OPENPROJECT_CONTAINER} container keeps its PostgreSQL data in the volume
  {mount_name}
but docker-compose.yml now mounts the named volume {expected} at {_OPENPROJECT_PGDATA_PATH}.
Deploying now would start OpenProject with an EMPTY database (the old data would be orphaned, not deleted).
Nothing was deployed. Migrate the data once, then re-run this command:

  # 1. Stop OpenProject so PostgreSQL shuts down cleanly
  docker stop {_OPENPROJECT_CONTAINER}

  # 2. Create the named volume (labelled so Compose adopts it) and copy the data into it.
  #    If {expected} already exists, make sure it is empty first.
  docker volume create \\
      --label com.docker.compose.project={project} \\
      --label com.docker.compose.volume={_OPENPROJECT_PGDATA_VOLUME_KEY} \\
      {expected}
  docker run --rm -v {mount_name}:/from -v {expected}:/to alpine cp -a /from/. /to/

  # 3. Remove the old container. Do NOT pass -v: that would delete the old volume,
  #    which is your only copy until OpenProject is confirmed working again.
  docker rm {_OPENPROJECT_CONTAINER}

  # 4. Re-run
  {rerun_command}

  Once OpenProject is verified, the old volume can be removed with:
  docker volume rm {mount_name}
"""


def cmd_deploy(args):
    """Deploys specified datacenter stacks using docker compose."""
    root = get_project_root()
    soft_dir = os.path.join(root, "software") if os.path.exists(os.path.join(root, "software")) else root

    compose_main = os.path.join(soft_dir, "docker-compose.yml")
    if not os.path.exists(compose_main):
        print(f"{RED}Error: Could not find docker-compose.yml in {soft_dir}{RESET}")
        sys.exit(1)

    cmd = ["docker", "compose", "-f", compose_main]

    if args.all or args.with_vpn:
        cmd.extend(["-f", os.path.join(soft_dir, "vpn", "docker-compose.vpn.yml")])
    if args.all or args.with_backup:
        cmd.extend(["-f", os.path.join(soft_dir, "backup", "docker-compose.backup.yml")])
    if args.all or args.with_telemetry:
        cmd.extend(["-f", os.path.join(soft_dir, "telemetry", "docker-compose.telemetry.yml")])
    if args.all or args.with_space:
        cmd.extend(["-f", os.path.join(soft_dir, "space", "docker-compose.space.yml")])
    if args.all or args.with_agents:
        cmd.extend(["-f", os.path.join(soft_dir, "agents", "docker-compose.agents.yml")])
    if args.all or args.with_security:
        cmd.extend(["-f", os.path.join(soft_dir, "security", "docker-compose.crowdsec.yml")])

    if args.dry_run:
        print(f"{CYAN}DRY RUN: Validating compose stack configuration...{RESET}")
        # Validation must work on a fresh checkout: fall back to the template when .env is absent.
        env_example = os.path.join(soft_dir, "env.example")
        if not os.path.isfile(os.path.join(soft_dir, ".env")) and os.path.isfile(env_example):
            cmd[2:2] = ["--env-file", env_example]
        cmd.extend(["config", "--quiet"])
        res = subprocess.run(cmd, cwd=soft_dir)
        if res.returncode == 0:
            print(f"{GREEN}✅ Configuration valid.{RESET}")
        else:
            print(f"{RED}✖ Compose configuration is invalid (exit {res.returncode}).{RESET}")
        return res.returncode

    # Never bring up internet-facing services with the template's public placeholder secrets.
    env_file = os.path.join(soft_dir, ".env")
    if not os.path.isfile(env_file):
        print(f"{RED}✖ {env_file} not found. Copy env.example to .env and set real values first.{RESET}")
        return 1
    placeholders = _env_placeholder_keys(env_file)
    if placeholders:
        print(f"{RED}✖ Refusing to deploy: placeholder values remain in .env for: {', '.join(placeholders)}{RESET}")
        return 1

    # Never recreate OpenProject on an empty database volume (same guard as software/setup.sh).
    flags = ["--all"] if args.all else [f"--with-{m}" for m in _DEPLOY_MODULES if getattr(args, f"with_{m}", False)]
    refusal = _openproject_pgdata_guard(cmd, soft_dir, " ".join(["smdc", "deploy", *flags]))
    if refusal:
        print(f"{RED}✖ Refusing to deploy.{RESET}\n{refusal}", file=sys.stderr)
        return 1

    print(f"{BOLD}{GREEN}Launching Sovereign Datacenter Stack...{RESET}")
    cmd.extend(["up", "-d", "--remove-orphans"])
    res = subprocess.run(cmd, cwd=soft_dir)
    if res.returncode != 0:
        print(f"{RED}✖ Stack deployment failed (exit {res.returncode}).{RESET}")
    return res.returncode


def cmd_telemetry(args):
    """Starts the power & thermal Prometheus exporter."""
    print(f"{BOLD}{CYAN}Starting Sovereign Power & Thermal Exporter on port {args.port}...{RESET}")
    telemetry.run(port=args.port, simulation=not args.hardware)


def cmd_agent_status(args):
    """Checks local Ollama LLM and Qdrant vector database."""
    print(f"\n{BOLD}{CYAN}=== Sovereign Autonomous AI Subsystems ==={RESET}\n")
    ollama_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    try:
        req = urllib.request.Request(f"{ollama_url}/api/tags", headers={"User-Agent": "smdc-cli"})
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            models = [m.get("name") for m in data.get("models", [])]
            print(f"  {GREEN}●{RESET} {BOLD}Ollama LLM Engine:{RESET} ONLINE at {ollama_url}")
            print(f"    Available Models ({len(models)}): {', '.join(models) if models else 'None loaded'}")
    except Exception as e:
        print(f"  {YELLOW}○{RESET} {BOLD}Ollama LLM Engine:{RESET} OFFLINE ({e})")

    qdrant_url = os.getenv("QDRANT_BASE_URL", "http://localhost:6333")
    try:
        req = urllib.request.Request(f"{qdrant_url}/collections", headers={"User-Agent": "smdc-cli"})
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            collections = [c.get("name") for c in data.get("result", {}).get("collections", [])]
            print(f"  {GREEN}●{RESET} {BOLD}Qdrant Vector DB:{RESET}  ONLINE at {qdrant_url}")
            print(
                f"    Collections ({len(collections)}): {', '.join(collections) if collections else 'sovereign_knowledge (pending)'}"
            )
    except Exception as e:
        print(f"  {YELLOW}○{RESET} {BOLD}Qdrant Vector DB:{RESET}  OFFLINE ({e})")
    print("")


def cmd_agent_ask(args):
    """Runs a direct query against the sovereign local LLM."""
    query = args.query
    model = args.model or os.getenv("OLLAMA_DEFAULT_MODEL", "qwen2.5-coder:7b")
    ollama_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    print(f"{BOLD}{CYAN}Querying Sovereign AI ({model})...{RESET}\n")
    try:
        req_data = json.dumps({"model": model, "prompt": query, "stream": False}).encode("utf-8")
        req = urllib.request.Request(
            f"{ollama_url}/api/generate", data=req_data, headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=45) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            answer = data.get("response", "No output.")
            print(answer)
    except Exception as e:
        print(f"{RED}Error communicating with Ollama: {e}{RESET}")
        print(
            f"{YELLOW}Ensure Ollama container is running (run 'smdc deploy' or 'docker compose up -d ollama').{RESET}"
        )
        return 1
    return 0


def cmd_agent_review(args):
    """Reviews code diff or file using local AI reviewer agent."""
    path = args.target
    if not os.path.exists(path):
        print(f"{RED}Error: Target file or diff '{path}' not found.{RESET}")
        return 1
    with open(path, encoding="utf-8", errors="replace") as f:
        content = f.read()

    try:
        from sovereign_dc.agents import gitlab_reviewer
    except ImportError:
        from software.agents import gitlab_reviewer  # type: ignore[no-redef]

    prompt = f"""
You are an expert security auditor and software architect running on the Sovereign Mini Datacenter.
Review the following code/diff and provide actionable feedback, security findings, and optimization suggestions:

```
{content[:4000]}
```
"""
    print(f"{BOLD}{CYAN}Running AI Code Review on {path}...{RESET}\n")
    review = gitlab_reviewer.query_ollama(prompt)
    print(review)
    # query_ollama reports an unreachable or silent model in-band; that is not a review.
    failure_markers = [getattr(gitlab_reviewer, name, None) for name in ("LLM_UNAVAILABLE_PREFIX", "LLM_EMPTY_OUTPUT")]
    if any(isinstance(m, str) and m and str(review).startswith(m) for m in failure_markers):
        return 1
    return 0


def cmd_agent_index(args):
    """Indexes documents from a target directory into Qdrant for semantic RAG."""
    path = getattr(args, "directory", None)
    if not path:
        from sovereign_dc.config import get_config

        path = get_config().docs_watch_dir
    if not os.path.isdir(path):
        print(f"{RED}Error: Target directory '{path}' does not exist.{RESET}")
        return 1

    try:
        from sovereign_dc.agents.knowledge_indexer import (
            ensure_qdrant_collection,
            is_oversized,
            list_documents,
            process_file,
        )
    except ImportError:
        from software.agents.knowledge_indexer import (
            ensure_qdrant_collection,
            is_oversized,
            list_documents,
            process_file,
        )

    print(f"{BOLD}{CYAN}Indexing documents from {path} into Qdrant vector database...{RESET}")
    if ensure_qdrant_collection() is False:
        print(f"{RED}✖ Qdrant collection is unavailable; nothing was indexed.{RESET}\n")
        return 1
    indexed_count = 0
    failed: list[str] = []
    skipped: list[str] = []
    # The indexer's own listing: same suffixes, DOCS_EXCLUDE_DIRS pruning and DOCS_INCLUDE_GLOB
    # filter as the background worker, so both index exactly the same set of documents.
    walk_errors: list[OSError] = []
    for full_path in list_documents(path, walk_errors):
        if is_oversized(full_path):
            skipped.append(full_path)
        elif process_file(full_path) is True:
            indexed_count += 1
        else:
            failed.append(full_path)
    # A directory that could not be listed may hide documents: that is a failure, not "0 documents".
    failed.extend(
        f"{walk_error.filename or path} (could not be listed: {walk_error.strerror})" for walk_error in walk_errors
    )
    for full_path in skipped:
        print(f"  {YELLOW}⚠ Skipped (larger than DOCS_MAX_FILE_BYTES): {full_path}{RESET}")
    for full_path in failed:
        print(f"  {RED}✖ Failed: {full_path}{RESET}")
    if failed:
        print(
            f"{RED}✖ Indexed {indexed_count} document(s); {len(failed)} failed"
            f"{f', {len(skipped)} skipped' if skipped else ''}. See the log lines above for the cause.{RESET}\n"
        )
        return 1
    print(
        f"{GREEN}✅ Successfully indexed {indexed_count} document(s) into the Sovereign RAG collection"
        f"{f' ({len(skipped)} skipped as too large)' if skipped else ''}.{RESET}\n"
    )
    return 0


def cmd_docs(args):
    """Prints documentation URLs or serves local 3D digital twin."""
    print(f"\n{BOLD}Sovereign Mini Datacenter v{__version__}{RESET}")
    print(f"• Repository:  {CYAN}https://github.com/iliachry/sovereign-mini-datacenter{RESET}")
    print(f"• 3D Viewer:   {GREEN}https://iliachry.gr/sovereign-mini-datacenter/{RESET}")
    print(f"• Docs & BOM:  {BOLD}hardware/COMPONENTS.md{RESET} & {BOLD}hardware/WIRING_DIAGRAM.md{RESET}\n")

    if getattr(args, "serve", False):
        import functools
        import http.server
        import socketserver
        import webbrowser
        from pathlib import Path

        port = getattr(args, "port", None) or 8088
        docs_dir = Path(get_project_root()) / "docs"

        if not docs_dir.exists():
            print(f"  {RED}✖ Docs directory not found at {docs_dir}{RESET}\n")
            return 1

        handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(docs_dir))
        url = f"http://localhost:{port}/index.html"
        print(f"  {GREEN}●{RESET} Serving 3D Digital Twin at: {CYAN}{url}{RESET}")
        print(f"  Press {BOLD}Ctrl+C{RESET} to stop server.\n")

        if not getattr(args, "no_browser", False):
            try:
                webbrowser.open(url)
            except Exception:
                pass

        try:
            with socketserver.TCPServer(("127.0.0.1", port), handler) as httpd:
                httpd.serve_forever()
        except KeyboardInterrupt:
            print(f"\n  {YELLOW}3D Digital Twin server stopped.{RESET}\n")
        except OSError as e:
            print(f"  {RED}✖ Could not bind to port {port}: {e}{RESET}\n")
            return 1


def cmd_doctor(args):
    """Executes pre-flight system diagnostics and development environment readiness checks."""
    import shutil
    import socket

    print(f"\n{BOLD}{CYAN}=== Sovereign Mini Datacenter — Pre-Flight System Diagnostics ==={RESET}\n")

    checks_passed = 0
    total_checks = 0

    def print_check(name: str, passed: bool, detail: str = "", fix: str = "") -> None:
        nonlocal checks_passed, total_checks
        total_checks += 1
        if passed:
            checks_passed += 1
            print(f"  [{GREEN}PASS{RESET}] {BOLD}{name:<32}{RESET} {GREEN}✔{RESET} {detail}")
        else:
            print(f"  [{YELLOW}WARN{RESET}] {BOLD}{name:<32}{RESET} {YELLOW}○{RESET} {detail}")
            if fix:
                print(f"         {CYAN}↳ Remediation:{RESET} {fix}")

    # 1. Python Runtime
    py_ver = sys.version_info
    py_ok = py_ver >= (3, 11)
    print_check(
        "Python Runtime (>=3.11)",
        py_ok,
        f"Python {py_ver.major}.{py_ver.minor}.{py_ver.micro}",
        "Install Python 3.11 or newer",
    )

    # 2. Virtual Environment
    in_venv = sys.prefix != getattr(sys, "base_prefix", sys.prefix)
    print_check(
        "Virtual Environment Active",
        in_venv,
        f"{sys.prefix}",
        "Run 'uv sync' and activate virtual environment (.venv)",
    )

    # 3. uv Package Manager
    uv_bin = shutil.which("uv")
    print_check(
        "uv Package Manager",
        bool(uv_bin),
        f"{uv_bin}" if uv_bin else "Not installed",
        "Install uv: https://github.com/astral-sh/uv",
    )

    # 4. Git Repository & Pre-Commit
    is_git = os.path.exists(os.path.join(get_project_root(), ".git"))
    has_precommit = os.path.exists(os.path.join(get_project_root(), ".git", "hooks", "pre-commit"))
    print_check(
        "Git Repository & Hooks",
        is_git and has_precommit,
        "Pre-commit hook installed"
        if has_precommit
        else ("Git repo detected, no hooks" if is_git else "Not a git repo"),
        "Run 'uv run pre-commit install'",
    )

    # 5. Environment File
    env_file = os.path.join(get_project_root(), "software", ".env")
    has_env = os.path.exists(env_file)
    print_check(
        "Environment Config (.env)",
        has_env,
        "software/.env found" if has_env else "software/.env missing",
        "Copy software/env.example to software/.env",
    )

    # 6. Docker Engine
    docker_bin = shutil.which("docker")
    docker_running = False
    if docker_bin:
        try:
            docker_res = subprocess.run(["docker", "info"], capture_output=True, timeout=3)
            docker_running = docker_res.returncode == 0
        except Exception:
            docker_running = False
    print_check(
        "Docker Engine & Daemon",
        docker_running,
        "Docker daemon active"
        if docker_running
        else ("Docker CLI present but daemon not running" if docker_bin else "Docker not installed"),
        "Start Docker Desktop or systemd docker.service",
    )

    # 7. Hardware Acceleration (GPU / Jetson)
    gpu_detected = False
    gpu_name = "CPU Only"
    if shutil.which("nvidia-smi"):
        try:
            gpu_res = subprocess.run(
                ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
                capture_output=True,
                text=True,
                timeout=2,
            )
            if gpu_res.returncode == 0 and gpu_res.stdout.strip():
                gpu_detected = True
                gpu_name = gpu_res.stdout.strip().split("\n")[0]
        except Exception:
            pass
    elif os.path.exists("/sys/devices/platform/host1x"):
        gpu_detected = True
        gpu_name = "NVIDIA Tegra Jetson Orin"

    print_check(
        "Hardware Acceleration",
        gpu_detected,
        gpu_name,
        "NVIDIA drivers or Jetson Orin required for hardware GPU inference",
    )

    # 8. OpenSCAD 3D Modeling
    scad_bin = shutil.which("openscad")
    print_check(
        "OpenSCAD (CAD Verification)",
        bool(scad_bin),
        f"{scad_bin}" if scad_bin else "Not installed (optional for CAD compilation)",
        "Install OpenSCAD to compile 3D models locally",
    )

    # 9. Local Service Ports & Endpoints
    def check_port_open(port: int) -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.3)
            return s.connect_ex(("127.0.0.1", port)) == 0

    dashboard_active = check_port_open(8080)
    twin_active = check_port_open(8088)
    ollama_active = check_port_open(11434)
    qdrant_active = check_port_open(6333)
    telem_active = check_port_open(9101)

    print(f"\n  {BOLD}Local Service Endpoints & Ports:{RESET}")
    print(
        f"    • Operations Dashboard (Port 8080):  {GREEN if dashboard_active else YELLOW}{'ACTIVE (http://localhost:8080)' if dashboard_active else 'INACTIVE (Launch with smdc dashboard)'}{RESET}"
    )
    print(
        f"    • 3D Digital Twin (Port 8088):       {GREEN if twin_active else YELLOW}{'ACTIVE (http://localhost:8088)' if twin_active else 'INACTIVE (Launch with smdc docs --serve)'}{RESET}"
    )
    print(
        f"    • Prometheus Exporter (Port 9101):   {GREEN if telem_active else YELLOW}{'ACTIVE (http://localhost:9101/metrics)' if telem_active else 'INACTIVE (Launch with smdc telemetry)'}{RESET}"
    )
    print(
        f"    • Ollama Local LLM (Port 11434):     {GREEN if ollama_active else YELLOW}{'ONLINE' if ollama_active else 'OFFLINE (Start with ollama serve)'}{RESET}"
    )
    print(
        f"    • Qdrant Vector DB (Port 6333):      {GREEN if qdrant_active else YELLOW}{'ONLINE' if qdrant_active else 'OFFLINE (Start via docker compose)'}{RESET}"
    )

    print(
        f"\n{BOLD}Diagnostic Summary:{RESET} {GREEN}{checks_passed}/{total_checks}{RESET} core environment checks passed.\n"
    )


def _measure_ollama_generation(base_url: str, model: str, num_predict: int = 128) -> dict[str, Any]:
    """Measures real token throughput from a running Ollama server using its own timing counters.

    Returns ``{"error": ...}`` when the server or model is unavailable; never estimates.
    """
    body = json.dumps(
        {
            "model": model,
            "prompt": "Explain in three sentences why battery state of charge matters for an off-grid server.",
            "stream": False,
            "options": {"num_predict": num_predict, "temperature": 0},
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        f"{base_url.rstrip('/')}/api/generate", data=body, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        return {"model": model, "error": f"Ollama request failed: {e}"}
    eval_count = data.get("eval_count")
    eval_ns = data.get("eval_duration")
    if not isinstance(eval_count, int) or not isinstance(eval_ns, int) or eval_ns <= 0:
        return {"model": model, "error": "Ollama response carried no timing counters"}
    return {
        "model": model,
        "source": "ollama eval_count / eval_duration",
        "eval_tokens": eval_count,
        "eval_seconds": round(eval_ns / 1e9, 3),
        "tokens_per_second": round(eval_count / (eval_ns / 1e9), 2),
        "load_seconds": round(int(data.get("load_duration", 0)) / 1e9, 3),
        "prompt_tokens": data.get("prompt_eval_count"),
    }


def cmd_benchmark(args):
    """Executes empirical performance benchmarks across AI, Space DTN, and System."""
    print(f"\n{BOLD}{CYAN}=== Sovereign Mini Datacenter Benchmark Suite ==={RESET}\n")
    benchmarks_data: dict[str, Any] = {}
    results: dict[str, Any] = {
        "timestamp": datetime.now(UTC).isoformat(),
        "version": __version__,
        "benchmarks": benchmarks_data,
    }

    shed_requested = bool(getattr(args, "shed", False))
    if _arg(args, "shed_iterations", 1) < 1:
        print(f"{RED}✖ --shed-iterations must be at least 1.{RESET}")
        return 2
    run_all = args.all or not (args.ai or args.dtn or args.system or shed_requested)

    if run_all or args.ai:
        print(f"{BOLD}[1/4] Running synthetic CPU vector micro-benchmark (no model inference)...{RESET}")
        t0 = time.time()
        chunks_processed = 500
        # Synthetic dense 768-dim vector embedding calculation
        _ = [[((i * j) % 100) / 100.0 for j in range(768)] for i in range(chunks_processed)]
        t_embed = time.time() - t0
        rate = chunks_processed / max(0.0001, t_embed)
        print(
            f"  • Synthetic 768-dim Vector Build: {GREEN}{rate:.1f} vectors/sec{RESET} ({chunks_processed} vectors in {t_embed * 1000:.1f} ms)"
        )
        benchmarks_data["ai_embedding"] = {
            "chunks_per_second": round(rate, 1),
            "latency_ms": round(t_embed * 1000, 2),
            "model": None,
            "synthetic": True,
        }
        if getattr(args, "live", False):
            from sovereign_dc.config import get_config

            model = _arg(args, "model", "qwen2.5-coder:7b")
            measured = _measure_ollama_generation(get_config().ollama_url, model)
            if "error" in measured:
                print(f"  • Live LLM Inference:          {RED}not measured: {measured['error']}{RESET}")
            else:
                print(
                    f"  • Live LLM Inference ({model}): {GREEN}{measured['tokens_per_second']:.1f} tok/s{RESET} "
                    f"({measured['eval_tokens']} tokens in {measured['eval_seconds']:.2f} s, "
                    f"first-token load {measured['load_seconds']:.2f} s)"
                )
            benchmarks_data["llm_generation"] = measured
        else:
            print(
                f"  • Model Inference:             {YELLOW}not exercised (pass --live to measure a running Ollama){RESET}"
            )

    if run_all or args.dtn:
        print(f"\n{BOLD}[2/4] Running RFC 9171 Space DTN Spool Benchmark...{RESET}")
        import tempfile

        num_bundles = 200
        # The benchmark spool lives in a temporary directory that is removed afterwards
        # (database and SQLite side files included), also when ingestion fails.
        with tempfile.TemporaryDirectory(prefix="smdc-bench-") as bench_dir:
            t0 = time.time()
            router = DTNRouter(db_path=os.path.join(bench_dir, "smdc_bench_spool.db"))
            for i in range(num_bundles):
                b = Bundle(
                    source_eid=router.local_node_eid,
                    destination_eid=f"dtn://ground-station-{i}.earth/telemetry",
                    payload=f'{{"metric": "solar_yield", "seq": {i}, "val": 1240.5}}'.encode(),
                )
                router.queue_bundle(b)
            t_dtn = time.time() - t0
        dtn_rate = num_bundles / max(0.0001, t_dtn)
        print(
            f"  • DTN Bundle Ingestion:       {GREEN}{dtn_rate:.1f} bundles/sec{RESET} ({num_bundles} bundles in {t_dtn * 1000:.1f} ms)"
        )
        benchmarks_data["dtn_spool"] = {
            "bundles_per_second": round(dtn_rate, 1),
            "duration_ms": round(t_dtn * 1000, 2),
            "bundles_count": num_bundles,
        }

    if run_all or args.system:
        print(f"\n{BOLD}[3/4] Running Unified Memory & Compute Benchmark...{RESET}")
        t0 = time.time()
        size_mb = 32
        data = bytearray(size_mb * 1024 * 1024)
        for i in range(0, len(data), 4096):
            data[i] = i % 255
        t_mem = time.time() - t0
        mem_bw = (size_mb / max(0.0001, t_mem)) / 1024.0
        print(
            f"  • Sequential Memory Bandwidth: {GREEN}{mem_bw:.2f} GB/s{RESET} ({size_mb} MB in {t_mem * 1000:.1f} ms)"
        )
        benchmarks_data["system_memory"] = {
            "bandwidth_gbs": round(mem_bw, 2),
            "duration_ms": round(t_mem * 1000, 2),
        }

    if run_all or shed_requested:
        from sovereign_dc.shed_benchmark import DEFAULT_ITERATIONS, measure_shed_latency

        iterations = _arg(args, "shed_iterations", DEFAULT_ITERATIONS)
        print(f"\n{BOLD}[4/4] Running load-shedding software-path latency benchmark (simulation)...{RESET}")
        shed = measure_shed_latency(iterations=iterations)
        print(
            f"  {YELLOW}Simulated on this machine: {shed['machine']}. No polling interval, no HTTP scrape, "
            f"no GPU/cgroup/relay actuation is included.{RESET}"
        )
        labels = {
            "sample_to_mode_decision": "Sample -> sentinel mode decision",
            "sample_to_apps_paused": f"Sample -> {shed['deferrable_apps']} deferrable apps marked PAUSED",
            "sample_to_mode_published": "Sample -> event dispatched + mode file written",
        }
        for key, label in labels.items():
            stage = shed["stages"][key]
            print(
                f"  • {label + ':':<48} {GREEN}median {stage['median_ms']:.4f} ms{RESET}, "
                f"p95 {stage['p95_ms']:.4f} ms (n={shed['iterations']})"
            )
        benchmarks_data["load_shedding"] = shed

    print(f"\n{GREEN}✅ Benchmark suite execution complete.{RESET}\n")

    if getattr(args, "export", None):
        with open(args.export, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2)
        print(f"📄 Exported benchmark results to: {CYAN}{args.export}{RESET}\n")

    # --live asked for a measurement: not getting one is a failure, not a quiet success.
    if "error" in benchmarks_data.get("llm_generation", {}):
        return 1
    return 0


def cmd_demo(args):
    """Runs a 1-click live demo sandbox displaying simulated power, space passes, and local agent prompts."""
    print(f"\n{BOLD}{CYAN}=== Sovereign Mini Datacenter — Live Demonstration Sandbox ==={RESET}")
    print(f"{YELLOW}Simulating full 9U datacenter stack (100% off-grid, 10.24 kWh battery, dual DGX)...{RESET}\n")

    steps = max(0, _arg(args, "steps", 3))
    no_delay = getattr(args, "no_delay", False)
    for step in range(1, steps + 1):
        solar_w = 1180 + (step * 25)
        battery_soc = 88.4 + (step * 0.2)
        print(f"{BOLD}─── Demo Step {step}/{steps} [{datetime.now().strftime('%H:%M:%S')}] ───{RESET}")
        print(f"☀️  Solar Generation:   {GREEN}{solar_w} W{RESET} (MPPT Tracking: 99.8%)")
        print(f"🔋 Battery State:      {CYAN}{battery_soc:.1f}% (52.8V LiFePO4){RESET} | Status: NOMINAL (L0)")
        print(f"🤖 AI Copilot (Ollama): {BOLD}qwen2.5-coder:7b{RESET} running local code review")
        print(f"🛰️ Space DTN Spool:     {MAGENTA}Active TX to Starlink-LEO-Alpha (El: 48.5°){RESET}\n")
        if step < steps and not no_delay:
            time.sleep(0.5)

    print(f"{GREEN}🎉 Demo completed! Run '{BOLD}smdc docs{RESET}{GREEN}' to explore the 3D Digital Twin.{RESET}\n")


def cmd_mesh_consensus(args):
    """Simulates decentralized Raft leader election and task replication across swarm nodes."""
    from sovereign_dc.mesh.consensus import RaftCluster

    node_count = _arg(args, "nodes", 3)
    if node_count < 1:
        print(f"{RED}✖ --nodes must be at least 1.{RESET}")
        return 2
    node_ids = [f"smdc-node-{i:02d}" for i in range(1, node_count + 1)]

    print(f"\n{BOLD}{CYAN}=== Decentralized Multi-Node Raft Consensus Simulation ==={RESET}")
    print(f"Cluster: {node_count} nodes over WireGuard overlay (100.64.0.0/16)\n")

    cluster = RaftCluster(node_ids)
    candidate_id = node_ids[0]
    print(f"🗳️ Node {BOLD}{candidate_id}{RESET} triggering leader election term 1...")
    leader_id = cluster.step_election(candidate_id)
    if leader_id:
        print(
            f"👑 Leader elected: {GREEN}{BOLD}{leader_id}{RESET} with quorum ({len(node_ids) // 2 + 1}/{len(node_ids)} votes)"
        )
    else:
        print(f"{RED}✖ No leader elected: quorum was not reached.{RESET}")

    if leader_id:
        print("📦 Leader submitting GPU batch scheduling command to cluster log...")
        cmd_idx = cluster.nodes[leader_id].submit_command({"task": "ollama_batch_inference", "priority": "L0"})
        print(f"   Log entry #{cmd_idx} appended.")

        print("🔄 Broadcasting heartbeats and replicating log to all swarm followers...")
        cluster.replicate_heartbeats(leader_id)

    print("\nCluster Node Status:")
    for nid, node in cluster.nodes.items():
        st = node.status()
        role_color = GREEN if st["role"] == "leader" else CYAN
        print(
            f"  • {BOLD}{nid}{RESET}: Role={role_color}{st['role'].upper()}{RESET} | Term={st['term']} | LogEntries={st['log_length']} | Committed={st['commit_index']}"
        )

    if not leader_id:
        print(f"\n{RED}✖ Raft consensus simulation failed.{RESET}\n")
        return 1
    leader_commit = cluster.nodes[leader_id].status()["commit_index"]
    if leader_commit < cmd_idx:
        print(f"\n{RED}✖ Log entry #{cmd_idx} was not committed by a majority.{RESET}\n")
        return 1
    print(f"\n{GREEN}✅ Leader elected and log entry #{cmd_idx} committed by a majority.{RESET}\n")


def cmd_mesh_chaos(args):
    """Executes chaos engineering scenarios against simulated multi-node cluster."""
    from sovereign_dc.mesh.chaos import MeshChaosSimulator

    print(f"\n{BOLD}{CYAN}=== Sovereign Multi-Node Mesh — Chaos Engineering Suite ==={RESET}\n")
    sim = MeshChaosSimulator()
    scenario = getattr(args, "scenario", "all") or "all"

    if scenario == "split-brain":
        results = {"split_brain": sim.simulate_split_brain_partition()}
    elif scenario == "leader-crash":
        results = {"leader_crash": sim.simulate_leader_crash_and_failover()}
    elif scenario == "dtn-fallback":
        results = {"dtn_fallback": sim.simulate_terrestrial_sever_dtn_fallback()}
    elif scenario == "packet-loss":
        results = {"packet_loss": sim.simulate_packet_loss_replication()}
    else:
        results = sim.run_all_scenarios()

    for r in results.values():
        st = f"{GREEN}PASSED{RESET}" if r.success else f"{RED}FAILED{RESET}"
        print(
            f"  [ {st} ] Scenario: {BOLD}{r.scenario}{RESET} ({r.elapsed_ms:.1f}ms) | Score: {GREEN}{r.resilience_score:.1f}%{RESET}"
        )
        for k, v in r.details.items():
            print(f"         • {k}: {v}")

    failed = [r.scenario for r in results.values() if not r.success]
    if failed:
        print(f"\n{RED}✖ Chaos scenario(s) failed: {', '.join(failed)}.{RESET}\n")
        return 1
    print(f"\n{GREEN}✅ Chaos engineering stress-tests completed.{RESET}\n")
    return 0


def cmd_security_pqc(args):
    """Benchmarks and verifies NIST FIPS 204 ML-DSA and FIPS 203 ML-KEM cryptographic engines."""
    from sovereign_dc.security.pqc import PQCKEM, PQCAlgorithm, PQCSigner

    print(f"\n{BOLD}{CYAN}=== Post-Quantum Cryptography (PQC) Verification Suite ==={RESET}\n")
    print(f"Standards: {BOLD}NIST FIPS 204 (ML-DSA / Dilithium) & FIPS 203 (ML-KEM / Kyber){RESET}\n")

    # 1. ML-DSA Digital Signature test
    signer = PQCSigner(PQCAlgorithm.ML_DSA_65)
    t0 = time.perf_counter()
    kp = signer.generate_keypair()
    t_kg = (time.perf_counter() - t0) * 1000.0

    test_msg = b"SOVEREIGN_NODE_ATTESTATION_PAYLOAD_SHA3_512"
    t0 = time.perf_counter()
    sig = signer.sign(test_msg, kp.private_key)
    t_sign = (time.perf_counter() - t0) * 1000.0

    t0 = time.perf_counter()
    verified = signer.verify(test_msg, sig, kp.public_key)
    t_ver = (time.perf_counter() - t0) * 1000.0

    print(f"{BOLD}[1] ML-DSA-65 (Lattice Digital Signature):{RESET}")
    print(f"  • Key ID:          {CYAN}{kp.key_id}{RESET}")
    print(f"  • Public Key Size: {len(kp.public_key)} bytes | Signature Size: {len(sig)} bytes")
    print(f"  • Keygen Latency:  {t_kg:.2f}ms | Sign: {t_sign:.2f}ms | Verify: {t_ver:.2f}ms")
    print(f"  • Status:          {GREEN if verified else RED}{'AUTHENTICATED' if verified else 'FAILED'}{RESET}")

    # 2. ML-KEM Key Encapsulation test
    kem = PQCKEM(PQCAlgorithm.ML_KEM_768)
    t0 = time.perf_counter()
    kem_kp = kem.generate_keypair()
    t_kem_kg = (time.perf_counter() - t0) * 1000.0

    t0 = time.perf_counter()
    ct, ss_sender = kem.encapsulate(kem_kp.public_key)
    t_enc = (time.perf_counter() - t0) * 1000.0

    t0 = time.perf_counter()
    ss_recip = kem.decapsulate(ct, kem_kp.private_key)
    t_dec = (time.perf_counter() - t0) * 1000.0

    kem_ok = ss_sender == ss_recip
    print(f"\n{BOLD}[2] ML-KEM-768 (Lattice Key Encapsulation):{RESET}")
    print(f"  • Ciphertext Size: {len(ct)} bytes | Shared Secret: {len(ss_sender) * 8}-bit AES Key")
    print(f"  • Keygen Latency:  {t_kem_kg:.2f}ms | Encapsulate: {t_enc:.2f}ms | Decapsulate: {t_dec:.2f}ms")
    print(f"  • Shared Secret:   {GREEN if kem_ok else RED}{'MATCHED' if kem_ok else 'MISMATCH'}{RESET}")

    if not (verified and kem_ok):
        print(f"\n{RED}✖ Post-Quantum Cryptography self-test failed.{RESET}\n")
        return 1
    print(f"\n{GREEN}✅ Post-Quantum Cryptography engines operating nominally.{RESET}\n")
    return 0


def _economy_paths(args: Any) -> tuple[str, str]:
    """``(wallet_path, ledger_path)`` for an economy command.

    The node's one wallet and ledger (``sovereign_dc.economy.node_state``, shared with the
    dashboard and the MCP server) unless ``--wallet-file`` / ``--db-path`` name other files.
    A wallet/ledger set left by an older release is adopted first.
    """
    from sovereign_dc.economy.node_state import migrate_legacy_node_state, node_state_paths

    migrate_legacy_node_state()
    paths = node_state_paths()
    return (
        getattr(args, "wallet_file", None) or paths.wallet,
        getattr(args, "db_path", None) or paths.ledger,
    )


def _economy_wallet(args: Any, wallet_path: str, *, pqc: bool = False) -> Any:
    """Loads the wallet an economy command acts on, creating it (with a zero balance) if absent."""
    from sovereign_dc.config import get_config
    from sovereign_dc.economy.node_state import load_node_pqc_wallet, load_node_wallet
    from sovereign_dc.economy.wallet import AddressType, NodeWallet

    node_id = getattr(args, "node_id", None)
    if not getattr(args, "wallet_file", None):
        return load_node_pqc_wallet(node_id) if pqc else load_node_wallet(node_id)
    if os.path.exists(wallet_path):
        return NodeWallet.load_from_file(wallet_path)
    wallet = NodeWallet.create(
        node_id=node_id or get_config().node_id,
        algorithm=AddressType.ML_DSA_87 if pqc else AddressType.ED25519,
    )
    wallet.save_to_file(wallet_path)
    return wallet


def _mint_local_test_credits(wallet: Any, ledger: Any, raw_amount: Any, memo: str) -> int:
    """Mints local test credits and says what they are; returns the process exit code."""
    from sovereign_dc.economy.node_state import LOCAL_TEST_CREDIT_NOTICE, mint_local_test_credits

    try:
        tx = mint_local_test_credits(raw_amount, memo, wallet=wallet, ledger=ledger)
    except ValueError as e:
        print(f"\n{RED}❌ Invalid mint amount {raw_amount!r}: {e}.{RESET}\n")
        return 1
    print(
        f"\n💰 Minted {GREEN}{_format_credits(tx.amount)} LOCAL TEST credits{RESET} "
        f"to wallet {CYAN}{wallet.address}{RESET} (memo: {tx.memo})"
    )
    print(f"   {YELLOW}{LOCAL_TEST_CREDIT_NOTICE}{RESET}")
    print(f"   New balance: {GREEN}{_format_credits(ledger.get_balance(wallet.address))} SMDC-Credits{RESET}\n")
    return 0


def cmd_economy_wallet(args):
    """Shows the node wallet and its balance (the same identity the dashboard and MCP report).

    Nothing is minted implicitly: a new wallet has a zero balance. ``--mint`` (like
    ``smdc economy mint``) issues local test credits explicitly.
    """
    from sovereign_dc.economy.ledger import Ledger

    wallet_path, db_path = _economy_paths(args)
    pqc = bool(getattr(args, "pqc", False))
    custom_wallet = bool(getattr(args, "wallet_file", None))
    if not custom_wallet and pqc:
        from sovereign_dc.economy.node_state import node_state_paths

        wallet_path = node_state_paths().pqc_wallet
    existed = os.path.exists(wallet_path)
    if getattr(args, "create", False) and existed:
        print(
            f"\n{RED}✖ A wallet already exists at {wallet_path}; it was not overwritten "
            f"(replacing it would orphan its balance). Move the file away to create a new one.{RESET}\n"
        )
        return 1

    wallet = _economy_wallet(args, wallet_path, pqc=pqc)
    ledger = Ledger(db_path=db_path)
    if not existed:
        print(f"\n{GREEN}✅ Created new sovereign node wallet (balance 0: nothing is minted implicitly).{RESET}\n")

    mint_amt = getattr(args, "mint", None)
    if mint_amt is not None:
        rc = _mint_local_test_credits(wallet, ledger, mint_amt, "")
        if rc != 0:
            return rc

    balance = ledger.get_balance(wallet.address)
    nonce = ledger.get_nonce(wallet.address)

    print(f"\n{BOLD}{CYAN}=== Sovereign Node Cryptographic Wallet ==={RESET}\n")
    print(f"  • Node ID:         {BOLD}{wallet.node_id}{RESET}")
    print(f"  • Wallet Address:  {CYAN}{wallet.address}{RESET}")
    print(f"  • Algorithm:       {MAGENTA}{wallet.keypair.algorithm.value}{RESET}")
    print(f"  • Public Key:      {wallet.keypair.public_key_hex[:32]}... ({len(wallet.keypair.public_key_hex)} chars)")
    print(f"  • Balance:         {GREEN}{_format_credits(balance)} SMDC-Credits{RESET} (local test credits)")
    print(f"  • Sequence Nonce:  {nonce}")
    print(f"  • Storage Path:    {wallet_path}")
    print(f"  • Ledger:          {db_path}\n")
    return 0


def cmd_economy_mint(args):
    """Mints local test credits to the node wallet: the only way a balance comes into being."""
    from sovereign_dc.economy.ledger import Ledger

    wallet_path, db_path = _economy_paths(args)
    wallet = _economy_wallet(args, wallet_path)
    return _mint_local_test_credits(wallet, Ledger(db_path=db_path), args.amount, getattr(args, "memo", "") or "")


def _format_credits(amount: float) -> str:
    """Formats a credit amount with 2 to 6 decimals, never hiding micro-credit digits."""
    text = f"{amount:.6f}".rstrip("0")
    whole, _, fraction = text.partition(".")
    return f"{whole}.{fraction.ljust(2, '0')}"


def cmd_economy_send(args):
    """Transfers compute credits to a peer node with cryptographic signature."""

    from sovereign_dc.economy.ledger import Ledger, credits_to_micro

    wallet_path, db_path = _economy_paths(args)
    # A wallet created here starts at zero: fund it with 'smdc economy mint' (local test credits).
    wallet = _economy_wallet(args, wallet_path)
    ledger = Ledger(db_path=db_path)

    recipient = args.recipient
    memo = getattr(args, "memo", "") or "M2M_TRANSFER"
    try:
        amount = float(args.amount)
        # Validate before touching the ledger so an unusable amount gets a message of its
        # own: credits have 6 decimal places (1 micro-credit = 0.000001).
        credits_to_micro(amount)
    except (TypeError, ValueError) as e:
        print(
            f"\n{RED}❌ Invalid amount {args.amount!r}: {e}. "
            f"Amounts must be finite numbers with at most 6 decimal places (e.g. 1.5 or 0.000001).{RESET}\n"
        )
        return 1

    try:
        tx = ledger.transfer(sender_wallet=wallet, recipient=recipient, amount=amount, memo=memo)
        print(f"\n{GREEN}✅ Transfer of {_format_credits(amount)} credits completed!{RESET}\n")
        print(f"  • Transaction ID: {CYAN}{tx.tx_id}{RESET}")
        print(f"  • Sender:         {tx.sender}")
        print(f"  • Recipient:      {tx.recipient}")
        print(f"  • Amount:         {GREEN}{_format_credits(tx.amount)} credits{RESET}")
        print(f"  • Signature Algo: {tx.signature_algorithm}")
        print(f"  • Timestamp:      {tx.timestamp}\n")
    except Exception as e:
        print(f"\n{RED}❌ Transfer failed: {e}{RESET}\n")
        return 1
    return 0


def cmd_economy_market(args):
    """Displays dynamic solar-aware compute & relay marketplace prices."""
    from sovereign_dc.economy.market import ComputeMarket, ServiceType

    market = ComputeMarket()
    soc = _arg(args, "soc", 85.0)
    solar = _arg(args, "solar", 850.0)

    print(f"\n{BOLD}{CYAN}=== Sovereign Compute & Energy Marketplace ==={RESET}\n")
    print(f"Simulated Physical Conditions: Battery SoC = {BOLD}{soc:.1f}%{RESET} | Solar = {BOLD}{solar:.0f} W{RESET}")
    mult, status_desc = market.get_dynamic_multiplier(battery_soc=soc, solar_power_w=solar)
    print(f"Current Energy Multiplier:    {GREEN if mult <= 1.0 else YELLOW}{mult:.2f}x{RESET} ({status_desc})\n")

    print(f"{BOLD}Service Catalog & Dynamic Rates:{RESET}")
    for st in ServiceType:
        quote = market.calculate_quote(st, quantity=1.0, battery_soc=soc, solar_power_w=solar)
        print(
            f"  • {BOLD}{st.value:<18}{RESET} Base: {quote.base_unit_price:>6.4f} -> {GREEN}{quote.final_unit_price:>6.4f} credits{RESET} / {quote.unit_name}"
        )
    print(f"\n💡 Nodes with excess solar energy automatically offer up to {GREEN}50% discount{RESET} on GPU inference.")
    print(f"   Low battery nodes apply {YELLOW}up to 3.0x surge pricing{RESET} to protect local power reserves.\n")


def cmd_economy_history(args):
    """Displays recent transactions from the compute ledger."""

    from sovereign_dc.economy.ledger import Ledger

    db_path = _economy_paths(args)[1]
    ledger = Ledger(db_path=db_path)
    limit = max(0, _arg(args, "limit", 15))
    txs = ledger.get_history(limit=limit)

    print(f"\n{BOLD}{CYAN}=== Compute Ledger Transaction History ==={RESET}\n")
    if not txs:
        print("  No transactions recorded yet in ledger.\n")
        return

    for tx in txs:
        arrow = f"{CYAN}{tx.sender[:12]}...{RESET} -> {GREEN}{tx.recipient[:12]}...{RESET}"
        print(
            f"  • [{tx.timestamp[:19]}] {arrow} | {BOLD}{_format_credits(tx.amount)} credits{RESET} | Memo: {tx.memo}"
        )
    print()


def cmd_economy_verify(args):
    """Verifies the ledger chain and its head anchor; --anchor (re)anchors a verified chain."""
    from sovereign_dc.economy.ledger import Ledger

    db_path = _economy_paths(args)[1]
    if not os.path.isfile(db_path):
        print(f"\n{RED}✖ No ledger found at {db_path}.{RESET}\n")
        return 1
    ledger = Ledger(db_path=db_path)
    print(f"\n{BOLD}{CYAN}=== Compute Ledger Integrity ==={RESET}\n")
    print(f"  • Ledger:        {db_path}")

    if getattr(args, "anchor", False):
        # Explicit operator step: it vouches for whatever the file holds right now.
        anchored, reason = ledger.anchor_chain_with_reason()
        if anchored:
            print(f"  • Anchor:        {GREEN}chain verified and anchored with the node identity key{RESET}")
        elif reason == "empty":
            print(f"  • Anchor:        {YELLOW}nothing to anchor: the ledger has no transactions{RESET}\n")
            return 1
        elif reason == "no_key":
            print(f"  • Anchor:        {RED}refused: node identity key unavailable{RESET}\n")
            return 1
        else:
            print(f"  • Anchor:        {RED}refused: the chain does not verify{RESET}\n")
            return 1

    links_ok, status, tx_count = ledger.chain_report()
    anchor_color = GREEN if status == "valid" else (RED if status == "invalid" else YELLOW)
    print(f"  • Chain links:   {GREEN + 'VALID' if links_ok else RED + 'INVALID'}{RESET}")
    print(f"  • Head anchor:   {anchor_color}{status.upper()}{RESET}")
    if links_ok and status == "absent" and tx_count > 0:
        print(f"  {YELLOW}Unanchored: tail truncation would go undetected. Run with --anchor to anchor it.{RESET}")
    elif links_ok and status == "absent" and tx_count == 0:
        print("  Empty ledger: no transactions to anchor yet (anchored automatically from the first transaction).")
    print()
    return 0 if links_ok and status in ("valid", "absent") else 1


def cmd_economy(args):
    """Main entry point for economy commands."""
    return cmd_economy_wallet(args)


def cmd_dashboard(args):
    """Launches real-time Web Operations Dashboard and REST API server."""
    from sovereign_dc.web.dashboard import run_dashboard_server

    port = _arg(args, "port", 8080)
    open_browser = not getattr(args, "no_browser", False)
    run_dashboard_server(port=port, open_browser=open_browser, host=getattr(args, "host", None))


def cmd_bootstrap(args):
    """Executes autonomous node bootstrap provisioner on hardware power-up."""
    from sovereign_dc.agents.bootstrap_provisioner import BootstrapProvisioner
    from sovereign_dc.agents.technician_notifier import MessageSeverity, TechnicianNotifierChain

    print(f"\n{BOLD}{CYAN}=== Sovereign Mini Datacenter — Autonomous Node Bootstrap ==={RESET}\n")

    node_id = getattr(args, "node_id", None) or os.getenv("NODE_ID") or DEFAULT_NODE_ID
    role = getattr(args, "role", None) or os.getenv("NODE_ROLE", "Primary Compute Core")
    dry_run = getattr(args, "dry_run", False)

    notifier = TechnicianNotifierChain(node_id=node_id)
    provisioner = BootstrapProvisioner(node_id=node_id, role=role, notifier_chain=notifier, dry_run=dry_run)

    if getattr(args, "notify_test", False):
        print(f"📡 {BOLD}Dispatching test alert across multi-channel technician notifier...{RESET}")
        res = notifier.notify(
            event_type="DIAGNOSTIC_TEST",
            severity=MessageSeverity.INFO,
            message=f"Technician notification test from sovereign node {node_id}.",
            details={"channels_tested": ["File", "MQTT", "LoRa", "DTN"]},
        )
        for ch, ok in res.items():
            st = f"{GREEN}OK{RESET}" if ok else f"{RED}FAILED{RESET}"
            print(f"  • {ch:<16}: {st}")
        failed = [ch for ch, ok in res.items() if not ok]
        if failed and len(failed) == len(res):
            print(f"\n{RED}✖ Notification test failed: no channel delivered the alert.{RESET}\n")
            return 1
        if failed:
            print(f"\n{YELLOW}⚠ Notification test finished; unavailable channels: {', '.join(failed)}.{RESET}\n")
        else:
            print(f"\n{GREEN}✅ Multi-channel notification test complete.{RESET}\n")
        return 0

    phase_target = getattr(args, "phase", None)
    if phase_target:
        print(f"Executing isolated Bootstrap Phase {phase_target}...")
        phase_map = {
            1: ("Discovery", provisioner.phase_1_discovery),
            2: ("Network", provisioner.phase_2_network),
            3: ("Services", provisioner.phase_3_services),
            4: ("Sync", provisioner.phase_4_sync),
            5: ("Ready", provisioner.phase_5_ready),
        }
        if phase_target not in phase_map:
            print(f"{RED}Invalid phase {phase_target}. Choose from 1 to 5.{RESET}")
            return 2
        name, fn = phase_map[phase_target]
        try:
            output = fn()
        except Exception as e:
            print(f"\n{RED}✖ Phase {phase_target} ({name}) failed: {e}{RESET}")
            return 1
        if provisioner.state.errors:
            print(f"\n{RED}✖ Phase {phase_target} ({name}) reported errors:{RESET}")
            for err in provisioner.state.errors:
                print(f"  • {err}")
            print(json.dumps(output, indent=2))
            return 1
        print(f"\n{GREEN}Phase {phase_target} ({name}) completed:{RESET}")
        print(json.dumps(output, indent=2))
        return 0

    # Full sequence execution
    print(f"🚀 Initializing 5-Phase Autonomous Bootstrap for {BOLD}{node_id}{RESET} ({role})...\n")
    state = provisioner.run_all_phases()

    print(f"\n{BOLD}[1] Phase 1 — Hardware Discovery:{RESET}")
    hw = state.hardware_info
    print(f"  • Host OS:        {hw.get('os')} {hw.get('release')} ({hw.get('arch')})")
    print(f"  • CPU Cores:      {hw.get('cpu_count')} Cores")
    print(f"  • Accelerators:   {hw.get('gpus', [{}])[0].get('name', 'N/A')}")
    power = hw.get("power", {})
    if "battery_soc" in power:
        sim_tag = " (simulated)" if power.get("simulated") else ""
        solar_text = f"{power['solar_w']:.0f}W Solar" if "solar_w" in power else "solar n/a"
        print(f"  • Battery / PV:   {power['battery_soc']:.1f}% SoC | {solar_text}{sim_tag}")
    else:
        print(f"  • Battery / PV:   {YELLOW}telemetry unavailable{RESET}")

    print(f"\n{BOLD}[2] Phase 2 — Multi-Tier Network Fabric:{RESET}")
    net = state.network_info
    print(f"  • WireGuard Mesh: {GREEN + 'CONNECTED' if net.get('tier1_wireguard') else YELLOW + 'STANDBY'}{RESET}")
    print(f"  • Active Peers:   {', '.join(net.get('active_peers', [])) or 'None'}")
    lora = f"{GREEN}ARMED{RESET}" if net.get("tier3_lora") else f"{YELLOW}not available{RESET}"
    dtn = f"{GREEN}ARMED{RESET}" if net.get("tier4_space_dtn") else f"{YELLOW}not available{RESET}"
    print(f"  • Out-of-Band:    LoRa {lora} | Space DTN spool {dtn}")

    print(f"\n{BOLD}[3] Phase 3 — Service Stacks:{RESET}")
    srv = state.services_info
    print(f"  • Core Containers:{len(srv.get('services_started', []))} running")
    print(f"  • Local AI Engine:Ollama ({GREEN + 'READY' if srv.get('ollama_ready') else YELLOW + 'STANDBY'}{RESET})")
    print(f"  • Vector Engine:  Qdrant ({GREEN + 'READY' if srv.get('qdrant_ready') else YELLOW + 'STANDBY'}{RESET})")

    print(f"\n{BOLD}[4] Phase 4 — Data & State Sync:{RESET}")
    sync = state.sync_info
    crdt = sync.get("crdt_sync", "UNKNOWN")
    print(f"  • CRDT Status:    {GREEN if crdt == 'SUCCESS' else YELLOW}{crdt}{RESET}")
    print(f"  • DTN Spool:      {sync.get('dtn_spool_bundles', 0)} bundles queued")

    print(f"\n{BOLD}[5] Phase 5 — Node Operational Attestation:{RESET}")
    st_color = GREEN if state.is_nominal else YELLOW
    print(
        f"  • Node Status:    {st_color}{BOLD}{'NODE_ONLINE_READY' if state.is_nominal else 'NODE_ONLINE_DEGRADED'}{RESET}"
    )
    print(f"  • Elapsed Time:   {state.elapsed_seconds():.2f}s")
    if dry_run:
        print("  • Technician Log: not dispatched (dry run)\n")
    else:
        print("  • Technician Log: dispatched to the available notifier channels (see log for per-channel results)\n")
    if state.errors:
        print(f"{RED}✖ Bootstrap finished with errors:{RESET}")
        for err in state.errors:
            print(f"  • {err}")
        print()

    if getattr(args, "daemon", False):
        print(f"{CYAN}Entering continuous background health watchdog (interval: 120s)...{RESET}")
        try:
            while True:
                time.sleep(120)
                provisioner.phase_1_discovery()
                provisioner.phase_2_network()
        except KeyboardInterrupt:
            print("\nWatchdog terminated by operator.")
    # A degraded-but-running node (optional tiers unverified) is not a failure; errors are.
    return 1 if state.errors else 0


# --- Model Context Protocol (MCP) Handlers ---


def cmd_mcp_serve(args: Any) -> None:
    """Runs standard Model Context Protocol (MCP) JSON-RPC 2.0 stdio server."""
    from sovereign_dc.mcp.server import MCPServer

    server = MCPServer()
    server.run_stdio()


def cmd_mcp_tools(args: Any) -> None:
    """Lists all registered Model Context Protocol (MCP) tools."""
    from sovereign_dc.mcp.tools import get_mcp_tools

    tools = get_mcp_tools()
    print(f"\n{BOLD}{CYAN}=== Sovereign Mini Datacenter — Registered MCP Tools ({len(tools)}) ==={RESET}\n")
    for i, t in enumerate(tools, 1):
        print(f"  {GREEN}{i:2d}. {BOLD}{t.name:<26}{RESET} {t.description}")
        props = t.input_schema.get("properties", {})
        req = t.input_schema.get("required", [])
        if props:
            for p_name, p_info in props.items():
                req_tag = f"{RED}*{RESET}" if p_name in req else ""
                print(
                    f"      • {CYAN}{p_name}{req_tag}{RESET} ({p_info.get('type', 'any')}): {p_info.get('description', '')}"
                )
    print(f"\n{MAGENTA}💡 Run 'smdc mcp serve' to launch JSON-RPC stdio server for Claude Desktop / Cursor.{RESET}\n")


def cmd_mcp_resources(args: Any) -> None:
    """Lists all registered Model Context Protocol (MCP) resource URIs."""
    from sovereign_dc.mcp.resources import get_mcp_resources

    resources = get_mcp_resources()
    print(f"\n{BOLD}{CYAN}=== Sovereign Mini Datacenter — Registered MCP Resources ({len(resources)}) ==={RESET}\n")
    for i, r in enumerate(resources, 1):
        print(f"  {GREEN}{i:2d}. {BOLD}{r.uri:<32}{RESET} [{r.mime_type}]")
        print(f"      {r.name} — {r.description}")
    print(f"\n{MAGENTA}💡 Read dynamically via JSON-RPC 'resources/read' with uri parameter.{RESET}\n")


def cmd_mcp_prompts(args: Any) -> None:
    """Lists all registered Model Context Protocol (MCP) operational prompts."""
    from sovereign_dc.mcp.prompts import get_mcp_prompts

    prompts = get_mcp_prompts()
    print(f"\n{BOLD}{CYAN}=== Sovereign Mini Datacenter — Registered MCP Prompts ({len(prompts)}) ==={RESET}\n")
    for i, p in enumerate(prompts, 1):
        print(f"  {GREEN}{i:2d}. {BOLD}{p.name:<28}{RESET} {p.description}")
        if p.arguments:
            for arg in p.arguments:
                req_tag = f"{RED}*{RESET}" if arg.required else ""
                print(f"      • {YELLOW}{arg.name}{req_tag}{RESET}: {arg.description}")
    print()


def cmd_mcp_test(args: Any) -> int:
    """Performs end-to-end self-test of the MCP server handshake, tools, resources and prompts."""
    from sovereign_dc.mcp.server import MCPServer

    print(f"\n{BOLD}{CYAN}=== Sovereign Mini Datacenter — MCP Self-Test & Diagnostic ==={RESET}\n")
    server = MCPServer()
    failures = 0

    # 1. Test initialize handshake
    init_req = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05"}}
    res = server.handle_request(init_req)
    if res and "result" in res and res["result"].get("protocolVersion") == "2024-11-05":
        print(
            f"  [{GREEN}PASS{RESET}] Handshake: MCP Protocol 2024-11-05 initialized ({server.server_name} v{server.version})"
        )
    else:
        failures += 1
        print(f"  [{RED}FAIL{RESET}] Handshake: Invalid response: {res}")

    # 2. Test ping
    ping_req = {"jsonrpc": "2.0", "id": 2, "method": "ping"}
    res = server.handle_request(ping_req)
    if res and res.get("result") == {}:
        print(f"  [{GREEN}PASS{RESET}] Ping: JSON-RPC 2.0 keepalive nominal")
    else:
        failures += 1
        print(f"  [{RED}FAIL{RESET}] Ping: Failed")

    # 3. Test tools/list
    tlist_req = {"jsonrpc": "2.0", "id": 3, "method": "tools/list"}
    res = server.handle_request(tlist_req)
    t_count = len(res.get("result", {}).get("tools", [])) if res else 0
    if t_count > 0:
        print(f"  [{GREEN}PASS{RESET}] Tools List: Discovered {BOLD}{t_count}{RESET} operational tools")
    else:
        failures += 1
        print(f"  [{RED}FAIL{RESET}] Tools List: No tools discovered: {res}")

    # 4. Test tool call: get_telemetry
    tcall_req = {
        "jsonrpc": "2.0",
        "id": 4,
        "method": "tools/call",
        "params": {"name": "get_telemetry", "arguments": {}},
    }
    res = server.handle_request(tcall_req)
    if res and not res.get("result", {}).get("isError"):
        tele_text = json.dumps(res.get("result", {}))
        provenance = (
            "simulated"
            if '\\"is_simulated\\": true' in tele_text or '"is_simulated": true' in tele_text
            else "measured"
        )
        print(f"  [{GREEN}PASS{RESET}] Tool Call: `get_telemetry` returned a telemetry payload ({provenance})")
    else:
        failures += 1
        print(f"  [{RED}FAIL{RESET}] Tool Call: `get_telemetry` failed: {res}")

    # 5. Test resources/read: smdc://telemetry/current
    rread_req = {
        "jsonrpc": "2.0",
        "id": 5,
        "method": "resources/read",
        "params": {"uri": "smdc://telemetry/current"},
    }
    res = server.handle_request(rread_req)
    if res and "contents" in res.get("result", {}):
        print(f"  [{GREEN}PASS{RESET}] Resource Read: `smdc://telemetry/current` successfully resolved")
    else:
        failures += 1
        print(f"  [{RED}FAIL{RESET}] Resource Read: Failed: {res}")

    # 6. Test prompts/get: diagnose_power_incident
    pget_req = {
        "jsonrpc": "2.0",
        "id": 6,
        "method": "prompts/get",
        "params": {"name": "diagnose_power_incident", "arguments": {"symptom": "Low SoC test"}},
    }
    res = server.handle_request(pget_req)
    if res and "messages" in res.get("result", {}):
        print(f"  [{GREEN}PASS{RESET}] Prompt Get: `diagnose_power_incident` synthesized context")
    else:
        failures += 1
        print(f"  [{RED}FAIL{RESET}] Prompt Get: Failed: {res}")

    if failures:
        print(f"\n{RED}{BOLD}✖ {failures} MCP diagnostic check(s) failed.{RESET}\n")
        return 1
    print(f"\n{GREEN}{BOLD}✅ All Model Context Protocol (MCP) diagnostic checks passed.{RESET}\n")
    return 0


def cmd_app_init(args):
    """Scaffold a new turnkey enterprise application."""
    from pathlib import Path

    from sovereign_dc.enterprise.registry import EnterpriseRegistry, ScaffoldConflictError
    from sovereign_dc.enterprise.schema import AppCategory, PowerPriority, RuntimeType, slugify_app_id

    print(f"\n{BOLD}{CYAN}=== Sovereign Mini Datacenter — Enterprise App Scaffolding ==={RESET}\n")
    app_name = args.name or "My Enterprise Workload"
    app_id = args.app_id or slugify_app_id(app_name)

    try:
        category = AppCategory(args.category)
    except ValueError:
        category = AppCategory.CUSTOM

    try:
        runtime = RuntimeType(args.runtime)
    except ValueError:
        runtime = RuntimeType.PROCESS

    try:
        power_tier = PowerPriority(args.power_tier)
    except ValueError:
        power_tier = PowerPriority.L1_STANDARD

    target_dir = Path(args.target_dir or f"./apps/{app_id}")
    manifest = EnterpriseRegistry.scaffold_manifest(
        name=app_name,
        app_id=app_id,
        category=category,
        runtime=runtime,
        entrypoint=args.entrypoint or "python3 app.py",
        gpu_required=args.gpu,
        power_tier=power_tier,
    )

    backups: list[tuple[Path, Path]] = []
    try:
        created_path = EnterpriseRegistry.create_project_scaffold(
            target_dir, manifest, create_sample_code=True, force=bool(getattr(args, "force", False)), backups=backups
        )
    except ScaffoldConflictError as e:
        print(f"  {RED}✖ Refusing to scaffold into {BOLD}{target_dir}{RESET}{RED}: these files already exist:{RESET}")
        for existing in e.existing:
            print(f"      - {existing}")
        print(
            f"\n  Nothing was written. Choose another {CYAN}--target-dir{RESET}, or pass {CYAN}--force{RESET} to "
            "replace them (each existing file is first moved to <name>.bak-<timestamp>).\n"
        )
        return 1
    except OSError as e:
        print(f"  {RED}✖ Could not create the scaffold in {target_dir}: {e}{RESET}\n")
        return 1
    for original, backup in backups:
        print(f"  {YELLOW}! Existing file kept as backup:{RESET} {original.name} → {backup}")
    print(f"  {GREEN}✔ Created enterprise app scaffold:{RESET} {BOLD}{created_path}{RESET}")
    print(f"  • App Name:      {CYAN}{manifest.name}{RESET} (`{manifest.app_id}`)")
    print(f"  • Category:      {MAGENTA}{manifest.category.value}{RESET}")
    print(f"  • Power Tier:    {YELLOW}{manifest.power.tier.value}{RESET} (Min SoC: {manifest.power.min_battery_soc}%)")
    print(f"  • Runtime:       {BLUE}{manifest.runtime.value}{RESET} (`{manifest.entrypoint}`)")
    print(
        f"  • Quotas:        {manifest.resources.cpu_cores} Cores | {manifest.resources.ram_mb} MB RAM | {manifest.resources.max_power_w} W Max"
    )

    print(f"\n{BOLD}Next Steps to Onboard:{RESET}")
    print(f"  1. Validate manifest:   {CYAN}smdc app validate {created_path}{RESET}")
    print(f"  2. Register with node:  {CYAN}smdc app register {created_path}{RESET}")
    print(f"  3. Start execution:     {CYAN}smdc app start {manifest.app_id}{RESET}")
    print(f"  4. Monitor telemetry:   {CYAN}smdc app status {manifest.app_id}{RESET}\n")
    return 0


def _print_backup_declaration_notice(manifest: Any) -> None:
    """Tells the operator that ``storage.backup_enabled: true`` does not back anything up."""
    from sovereign_dc.enterprise.schema import BACKUP_DECLARATION_NOTICE

    if not manifest.storage.backup_enabled:
        return
    volume = manifest.storage.persistent_volume or manifest.storage.mount_point or "(no volume declared)"
    print(f"  {YELLOW}! Backup notice:{RESET} volume '{volume}' is NOT backed up automatically.")
    print(f"    {BACKUP_DECLARATION_NOTICE}\n")


def cmd_app_validate(args):
    """Validate an application manifest file or directory against schema and node budgets."""
    from pathlib import Path

    from sovereign_dc.enterprise.registry import EnterpriseRegistry

    print(f"\n{BOLD}{CYAN}=== Sovereign Mini Datacenter — Enterprise Manifest Validation ==={RESET}\n")
    target = Path(args.path or ".")
    if target.is_dir():
        manifest_file = target / "smdc-app.yaml"
        if not manifest_file.exists():
            manifest_file = target / "smdc-app.json"
    else:
        manifest_file = target

    if not manifest_file.exists():
        print(f"  {RED}✖ Manifest file not found:{RESET} {manifest_file}")
        return 1

    registry = EnterpriseRegistry()
    manifest = registry.load_manifest_file(manifest_file)
    if not manifest:
        print(f"  {RED}✖ Validation failed:{RESET} Unable to parse manifest at {manifest_file}")
        return 1

    errors = manifest.validate()
    if errors:
        print(f"  {RED}✖ Validation errors found ({len(errors)}):{RESET}")
        for err in errors:
            print(f"    • {RED}{err}{RESET}")
        return 1
    else:
        print(f"  {GREEN}✔ Manifest is valid:{RESET} {BOLD}{manifest.name}{RESET} ({manifest.app_id})")
        print(f"  • Version:       {manifest.version}")
        print(f"  • Category:      {MAGENTA}{manifest.category.value}{RESET}")
        print(
            f"  • Power Tier:    {YELLOW}{manifest.power.tier.value}{RESET} (Min SoC: {manifest.power.min_battery_soc}%)"
        )
        for policy_line in manifest.power.enforcement_summary():
            print(f"      - {policy_line}")
        print(f"  • Max Power:     {manifest.resources.max_power_w} W")
        print(f"  • Compute:       {manifest.resources.cpu_cores} Cores | {manifest.resources.ram_mb} MB RAM")
        if manifest.resources.gpu_required:
            print(f"  • GPU VRAM:      {manifest.resources.gpu_vram_mb} MB (Required)")
        print(f"  • Ports:         {manifest.network.ports or 'None'}")
        print(f"  • Storage Mount: {manifest.storage.mount_point or 'Ephemeral'}\n")
        _print_backup_declaration_notice(manifest)
    return 0


def cmd_app_register(args):
    """Register an application with the local SMDC registry."""
    from pathlib import Path

    from sovereign_dc.enterprise.registry import EnterpriseRegistry

    print(f"\n{BOLD}{CYAN}=== Sovereign Mini Datacenter — Register Enterprise Application ==={RESET}\n")
    target = Path(args.path or ".")
    if target.is_dir():
        manifest_file = target / "smdc-app.yaml"
        if not manifest_file.exists():
            manifest_file = target / "smdc-app.json"
    else:
        manifest_file = target

    registry = EnterpriseRegistry()
    manifest = registry.load_manifest_file(manifest_file)
    if not manifest:
        print(f"  {RED}✖ Failed to load manifest from:{RESET} {manifest_file}")
        return 1

    success, errors = registry.register_app(manifest, manifest_path=manifest_file, persist=True)
    if success:
        print(
            f"  {GREEN}✔ Successfully registered application:{RESET} {BOLD}{manifest.name}{RESET} (`{manifest.app_id}`)"
        )
        print(f"  • Run with: {CYAN}smdc app start {manifest.app_id}{RESET}\n")
        _print_backup_declaration_notice(manifest)
    else:
        print(f"  {RED}✖ Registration rejected:{RESET} {'; '.join(errors)}\n")
        return 1
    return 0


def cmd_app_unregister(args):
    """Unregister an application from the local SMDC registry."""
    from sovereign_dc.enterprise.registry import EnterpriseRegistry

    registry = EnterpriseRegistry()
    if registry.unregister_app(args.app_id, persist=True):
        print(f"\n  {GREEN}✔ Successfully unregistered application:{RESET} {BOLD}{args.app_id}{RESET}\n")
    elif registry.last_save_error:
        print(f"\n  {RED}✖ Not unregistered:{RESET} {registry.last_save_error}\n")
        return 1
    else:
        print(f"\n  {YELLOW}○ Application not found in registry:{RESET} {args.app_id}\n")
        return 1
    return 0


def cmd_app_list(args):
    """List all registered enterprise applications and their states."""
    from sovereign_dc.enterprise.manager import EnterpriseManager

    print(f"\n{BOLD}{CYAN}=== Sovereign Mini Datacenter — Onboarded Enterprise Applications ==={RESET}\n")
    manager = EnterpriseManager()
    states = manager.list_runtime_states()

    if not states:
        print(f"  {YELLOW}No enterprise applications registered.{RESET}")
        print(f"  Scaffold a new application using: {CYAN}smdc app init <app-name>{RESET}\n")
        return

    print(f"  {BOLD}{'APP ID':<20} {'NAME':<24} {'CATEGORY':<16} {'POWER TIER':<16} {'STATUS':<12}{RESET}")
    print(f"  {'-' * 20} {'-' * 24} {'-' * 16} {'-' * 16} {'-' * 12}")

    for s in states:
        m = s.manifest
        status_color = (
            GREEN if s.status.value == "running" else (YELLOW if s.status.value in ["paused", "starting"] else RESET)
        )
        print(
            f"  {CYAN}{m.app_id:<20}{RESET} {m.name:<24} {MAGENTA}{m.category.value:<16}{RESET} {YELLOW}{m.power.tier.value:<16}{RESET} {status_color}{s.status.value.upper():<12}{RESET}"
        )
    print()


def cmd_app_start(args):
    """Start an enterprise application."""
    from sovereign_dc.enterprise.manager import EnterpriseManager

    print(f"\n{BOLD}{CYAN}=== Sovereign Mini Datacenter — Launch Enterprise Workload ==={RESET}\n")
    manager = EnterpriseManager()
    success, msg = manager.start_app(args.app_id)
    if success:
        state = manager.get_runtime_state(args.app_id)
        held = state is not None and state.status.value == "paused"
        print(f"  {YELLOW}⏸ {msg}{RESET}" if held else f"  {GREEN}✔ {msg}{RESET}")
        if state:
            print(f"  • Power Budget (estimate): {YELLOW}{state.power_draw_w:.1f} W{RESET}")
            print(f"  • Memory Budget (estimate): {state.ram_usage_mb:.1f} MB")
            print(f"  • CPU Budget (estimate):    {state.cpu_usage_percent:.1f}%\n")
        return 0
    print(f"  {RED}✖ {msg}{RESET}\n")
    return 1


def cmd_app_stop(args):
    """Stop an enterprise application."""
    from sovereign_dc.enterprise.manager import EnterpriseManager

    manager = EnterpriseManager()
    success, msg = manager.stop_app(args.app_id)
    if success:
        print(f"\n  {GREEN}✔ {msg}{RESET}\n")
        return 0
    print(f"\n  {RED}✖ {msg}{RESET}\n")
    return 1


def cmd_app_restart(args):
    """Restart an enterprise application."""
    from sovereign_dc.enterprise.manager import EnterpriseManager

    manager = EnterpriseManager()
    success, msg = manager.restart_app(args.app_id)
    if success:
        print(f"\n  {GREEN}✔ {msg}{RESET}\n")
        return 0
    print(f"\n  {RED}✖ {msg}{RESET}\n")
    return 1


def cmd_app_status(args):
    """Show detailed status and telemetry for enterprise applications."""
    from sovereign_dc.enterprise.manager import EnterpriseManager

    print(f"\n{BOLD}{CYAN}=== Sovereign Mini Datacenter — Enterprise Application Telemetry ==={RESET}\n")
    manager = EnterpriseManager()

    if args.app_id:
        state = manager.get_runtime_state(args.app_id)
        if not state:
            print(f"  {RED}✖ Application '{args.app_id}' not found.{RESET}\n")
            return 1
        states = [state]
    else:
        states = manager.list_runtime_states()

    if not states:
        print(f"  {YELLOW}No enterprise applications registered.{RESET}\n")
        return

    for s in states:
        m = s.manifest
        status_color = (
            GREEN if s.status.value == "running" else (YELLOW if s.status.value in ["paused", "starting"] else RESET)
        )
        print(f"  {BOLD}• Application:{RESET}      {CYAN}{m.name}{RESET} (`{m.app_id}` v{m.version})")
        print(f"    - Category:         {MAGENTA}{m.category.value}{RESET}")
        print(f"    - Status:           {status_color}{s.status.value.upper()}{RESET} (Health: {s.health_status})")
        print(
            f"    - Power Policy:     {YELLOW}{m.power.tier.value}{RESET} (Min SoC: {m.power.min_battery_soc}%, Estimated Draw: {s.power_draw_w:.1f} W)"
        )
        print(
            f"    - Resource Estimate: CPU: {s.cpu_usage_percent:.1f}% | RAM: {s.ram_usage_mb:.1f} MB (Max: {m.resources.ram_mb} MB)"
        )
        print(
            f"    - Network Ports:    {m.network.ports or 'None'} (WireGuard: {m.network.expose_wireguard}, Space DTN: {m.network.space_dtn_enabled})"
        )
        print(f"    - Uptime:           {s.to_dict()['uptime_seconds']}s (Restarts: {s.restart_count})")
        if s.custom_metrics:
            print(f"    - Custom Metrics:   {json.dumps(s.custom_metrics)}")
        print()


def cmd_app_package(args):
    """Package an application into a .smdc-app bundle signed with this node's ML-DSA-87 key.

    Signing is not verification: nothing on a receiving node checks the signature unless
    someone runs ``smdc app verify`` (or calls ``verify_package``) with a key they trust.
    """
    from pathlib import Path

    from sovereign_dc.enterprise.manager import EnterpriseManager

    print(f"\n{BOLD}{CYAN}=== Sovereign Mini Datacenter — Package Enterprise Application ==={RESET}\n")
    target = Path(args.path or ".")
    manager = EnterpriseManager()
    success, msg, meta = manager.package_app(target, output_path=args.output, sign_pqc=not args.no_pqc)

    if success:
        print(f"  {GREEN}✔ {msg}{RESET}")
        print(f"  • App ID:            {CYAN}{meta.get('app_id')}{RESET} (v{meta.get('version')})")
        print(f"  • SHA-256 Digest:    {meta.get('sha256')}")
        print(f"  • Package Size:      {meta.get('package_size_bytes')} bytes")
        pqc_sig = meta.get("pqc_signature")
        if pqc_sig:
            print(f"  • PQC Signature:     {GREEN}NIST FIPS 204 ML-DSA-87 signature attached{RESET}")
            print(f"  • Signer Key ID:     {pqc_sig.get('key_id')}")
        elif args.no_pqc:
            print(f"  • PQC Signature:     {YELLOW}Omitted (--no-pqc){RESET}")
        else:  # package_app fails when a requested signature cannot be made; never report success here
            print(f"  {RED}✖ Signing was requested but the package carries no signature.{RESET}\n")
            return 1
        if pqc_sig:
            print(
                "\n  Nothing verifies this signature automatically. On the receiving side run:\n"
                "    smdc app verify <package> --public-key <this node's public key, obtained over a trusted channel>\n"
                "  (the key is `pqc_signature.public_key_hex` in the sidecar written on THIS node).\n"
            )
        else:
            print("\n  The package is unsigned: a receiver cannot check where it came from.\n")
        return 0
    print(f"  {RED}✖ {msg}{RESET}\n")
    return 1


def _public_key_argument(value: str) -> str:
    """``--public-key``: hex text, or the path of a file that holds hex text (whitespace ignored)."""
    from pathlib import Path

    try:
        candidate = Path(value)
        if candidate.is_file():
            return "".join(candidate.read_text(encoding="utf-8").split())
    except (OSError, ValueError):  # a 5,184-character hex key is not a usable path
        pass
    return "".join(value.split())


def cmd_app_verify(args: argparse.Namespace) -> int:
    """Verify a ``.smdc-app`` package against a public key the caller already trusts.

    Exits 0 only when the archive matches its sidecar (SHA-256, size) and the sidecar's
    ML-DSA-87 signature was made by exactly the given key. The key inside the sidecar is
    never trusted by itself. This is the only verification entry point: no installer calls
    it, so a package is verified only when someone runs this command (or
    ``sovereign_dc.enterprise.verify_package``).
    """
    from sovereign_dc.enterprise.manager import verify_package

    print(f"\n{BOLD}{CYAN}=== Sovereign Mini Datacenter — Verify Enterprise Application Package ==={RESET}\n")
    ok, reason = verify_package(args.package, _public_key_argument(args.public_key), sidecar_path=args.sidecar)
    if ok:
        print(f"  {GREEN}✔ {args.package}: archive matches its sidecar and the ML-DSA-87 signature is valid{RESET}")
        print("    for the public key you supplied. Whether that key belongs to the publisher is yours to establish.\n")
        return 0
    print(f"  {RED}✖ {args.package}: NOT verified: {reason}{RESET}\n")
    return 1


def cmd_app_test(args):
    """Run automated self-test of the enterprise onboarding framework."""
    import tempfile
    from pathlib import Path

    from sovereign_dc.enterprise.manager import EnterpriseManager
    from sovereign_dc.enterprise.registry import EnterpriseRegistry
    from sovereign_dc.enterprise.schema import AppCategory, PowerPriority

    print(f"\n{BOLD}{CYAN}=== Sovereign Mini Datacenter — Enterprise Framework Self-Test ==={RESET}\n")

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        app_dir = tmp_path / "test-iot-sensor"
        reg_file = tmp_path / "registry.json"

        # 1. Test Scaffolding
        print(f"  [{CYAN}RUN{RESET}] Step 1: Scaffolding test application...")
        manifest = EnterpriseRegistry.scaffold_manifest(
            name="Test IoT Ingest",
            app_id="test-iot-ingest",
            category=AppCategory.IOT,
            power_tier=PowerPriority.L0_CRITICAL,
        )
        EnterpriseRegistry.create_project_scaffold(app_dir, manifest, create_sample_code=True)
        assert (app_dir / "smdc-app.yaml").exists()
        assert (app_dir / "app.py").exists()
        print(f"  [{GREEN}PASS{RESET}] Step 1: Scaffolding created valid directory structure")

        # 2. Test Validation
        print(f"  [{CYAN}RUN{RESET}] Step 2: Validating manifest schema...")
        registry = EnterpriseRegistry(registry_file=reg_file)
        loaded = registry.load_manifest_file(app_dir / "smdc-app.yaml")
        assert loaded is not None
        assert len(loaded.validate()) == 0
        print(f"  [{GREEN}PASS{RESET}] Step 2: Manifest validated with zero schema violations")

        # 3. Test Registration
        print(f"  [{CYAN}RUN{RESET}] Step 3: Registering with local registry...")
        ok, errs = registry.register_app(loaded, manifest_path=app_dir / "smdc-app.yaml")
        assert ok
        assert registry.get_app("test-iot-ingest") is not None
        print(f"  [{GREEN}PASS{RESET}] Step 3: Application successfully registered")

        # 4. Test Lifecycle & Supervision
        print(f"  [{CYAN}RUN{RESET}] Step 4: Testing lifecycle supervision...")
        manager = EnterpriseManager(registry=registry)
        start_ok, _ = manager.start_app("test-iot-ingest")
        assert start_ok
        state = manager.get_runtime_state("test-iot-ingest")
        assert state is not None and state.status.value == "running"
        print(
            f"  [{GREEN}PASS{RESET}] Step 4: Application state set to running (no process is launched by the manager)"
        )

        # 5. Test Packaging
        print(f"  [{CYAN}RUN{RESET}] Step 5: Packaging into PQC-signed .smdc-app bundle...")
        pkg_ok, _, meta = manager.package_app(app_dir, output_path=tmp_path / "test.smdc-app", sign_pqc=True)
        assert pkg_ok
        assert "sha256" in meta
        from sovereign_dc.enterprise.manager import verify_package
        from sovereign_dc.security.pqc import PQCAlgorithm, get_node_pqc_keypair

        node_key = get_node_pqc_keypair(PQCAlgorithm.ML_DSA_87).public_key
        verified, verify_msg = verify_package(tmp_path / "test.smdc-app", node_key)
        assert verified, verify_msg
        print(
            f"  [{GREEN}PASS{RESET}] Step 5: Package built with SHA-256 digest and ML-DSA-87 signature (self-verified)"
        )

    print(f"\n{GREEN}{BOLD}✅ All Enterprise Framework diagnostic checks passed.{RESET}\n")


def cmd_sim_run(args):
    """Executes multi-layer metaverse simulation cycles (Algorithm 1)."""
    from sovereign_dc.metaverse.engine import MetaverseOrchestrator

    print(f"\n{BOLD}{CYAN}=== Sovereign Mini Datacenter — Metaverse 6-Layer Simulation (Algorithm 1) ==={RESET}\n")
    cycles = getattr(args, "cycles", 5)
    deterministic = getattr(args, "deterministic", False)

    orchestrator = MetaverseOrchestrator(seed=getattr(args, "seed", 111))
    print(
        f"  {BOLD}Initial UAV Position:{RESET} ({orchestrator.uav_position[0]:.1f}m, {orchestrator.uav_position[1]:.1f}m, {orchestrator.uav_position[2]:.1f}m)"
    )
    print(f"  {BOLD}Executing {cycles} Multi-Layer Simulation Cycles...{RESET}\n")

    print(
        f"  {'Cycle':<7} | {'UAV Position (x,y,z)':<22} | {'Rx1 (dB)':<9} | {'Rx2 (dB)':<9} | {'Rx3 (dB)':<9} | {'Cap (bps/Hz)':<13} | {'Crit Path':<10} | {'DePIN Consensus':<16}"
    )
    print(f"  {'-' * 108}")

    traces = orchestrator.run_cycles(count=cycles, deterministic=deterministic)
    for t in traces:
        pos_str = f"({t.uav_position[0]:.1f}, {t.uav_position[1]:.1f}, {t.uav_position[2]:.1f})"
        rx1 = f"{t.per_receiver_sinr.get('Rx1', -15.0):.1f}"
        rx2 = f"{t.per_receiver_sinr.get('Rx2', -15.0):.1f}"
        rx3 = f"{t.per_receiver_sinr.get('Rx3', -15.0):.1f}"
        cap = f"{t.sum_capacity_bps_hz:.3f}"
        lat = f"{t.critical_path_latency_ms:.2f} ms"
        sla = t.sla_result
        depin = (
            f"{GREEN}VALID ({sla.validator_signatures_count} sigs, quorum {sla.required_threshold}){RESET}"
            if sla.valid
            else f"{RED}REJECTED{RESET}"
        )
        print(
            f"  #{t.cycle_index:<6} | {pos_str:<22} | {rx1:<9} | {rx2:<9} | {rx3:<9} | {cap:<13} | {lat:<10} | {depin}"
        )

    if not traces:
        print(f"\n{YELLOW}No simulation cycles were executed.{RESET}\n")
        return 0
    last = traces[-1]

    def _verdict(value: float, limit: float) -> str:
        ok = value < limit
        return f"{GREEN if ok else RED}{'within' if ok else 'EXCEEDS'} {limit:g} ms target{RESET}"

    print(f"\n{BOLD}{GREEN}✓ Simulation Complete:{RESET}")
    print(
        f"  • Decision-to-Action Latency: {last.decision_latency_ms:.2f} ms ({_verdict(last.decision_latency_ms, 10.0)})"
    )
    print(
        f"  • Critical Path Execution: {last.critical_path_latency_ms:.2f} ms ({_verdict(last.critical_path_latency_ms, 6.0)})"
    )
    print(f"  • URLLC Slicing Latency: {last.urllc_latency_ms:.2f} ms ({_verdict(last.urllc_latency_ms, 1.0)})")
    print(f"  • Total Sum Capacity: {last.sum_capacity_bps_hz:.4f} bps/Hz")
    print(f"  • DePIN Block Finality: {last.sla_result.finality_time_sec:.1f}s (PoS/dBFT consensus)\n")


def cmd_sim_benchmark(args):
    """Runs comparative parametric benchmark between SA-PPO and MD-PPO."""
    from sovereign_dc.metaverse.benchmark import MetaverseBenchmark

    print(f"\n{BOLD}{CYAN}=== Metaverse Wireless Management — Parametric RL Benchmark ==={RESET}\n")
    episodes = _arg(args, "episodes", 15)
    steps = _arg(args, "steps", 20)
    if episodes < 1 or steps < 1:
        print(f"{RED}✖ --episodes and --steps must be at least 1.{RESET}")
        return 2

    print(
        f"  {BOLD}Benchmarking SA-PPO (Scene-Aware) vs. MD-PPO (Model-Driven baseline){RESET} over {episodes} episodes..."
    )
    bench = MetaverseBenchmark(seed=getattr(args, "seed", 111))
    report = bench.run_comparison(episodes=episodes, steps_per_episode=steps)

    print(
        f"\n  {'Receiver':<10} | {'Disadvantaged?':<15} | {'SA-PPO SINR (dB)':<18} | {'MD-PPO SINR (dB)':<18} | {'Capacity Gain (%)':<18}"
    )
    print(f"  {'-' * 88}")

    for rx_id, m in report.receiver_metrics.items():
        dis_str = f"{YELLOW}YES (Canyon){RESET}" if m.is_disadvantaged else "No (Edge/LoS)"
        sa_sinr = f"{m.sa_ppo_mean_sinr_db:+.2f} ± {m.sa_ppo_std_sinr_db:.2f}"
        md_sinr = f"{m.md_ppo_mean_sinr_db:+.2f} ± {m.md_ppo_std_sinr_db:.2f}"
        gain_color = GREEN if m.capacity_gain_pct > 0 else RED
        gain_str = f"{gain_color}{m.capacity_gain_pct:+.1f}%{RESET}"
        print(f"  {rx_id:<10} | {dis_str:<24} | {sa_sinr:<18} | {md_sinr:<18} | {gain_str:<18}")

    print(f"\n{BOLD}{GREEN}✓ Benchmark Results Summary:{RESET}")
    print(f"  • Total Sum Capacity (SA-PPO): {report.sa_ppo_total_sum_capacity:.4f} bps/Hz")
    print(f"  • Total Sum Capacity (MD-PPO): {report.md_ppo_total_sum_capacity:.4f} bps/Hz")
    overall = report.overall_capacity_gain_pct
    rx1_gain = report.receiver_metrics["Rx1"].capacity_gain_pct
    print(f"  • Overall Capacity Gain: {GREEN if overall > 0 else RED}{overall:+.1f}%{RESET}")
    print(
        f"  • Rx1 (Disadvantaged) Gain: {GREEN if rx1_gain > 0 else RED}{rx1_gain:+.1f}%{RESET} "
        f"({'SA-PPO ahead' if rx1_gain > 0 else 'SA-PPO behind'} for this seed; results vary by seed)"
    )
    print(
        f"  • Ray-Tracing Step Time: {report.avg_ray_tracing_latency_ms:.1f} ms | URLLC: {report.avg_urllc_latency_ms:.1f} ms\n"
    )


def cmd_sim_slices(args):
    """Displays the configuration of the simulated 5G slices (a fresh simulator: no traffic has run)."""
    from sovereign_dc.metaverse.slicing import NetworkSlicingManager

    print(f"\n{BOLD}{CYAN}=== 5G Network Slicing & Traffic Isolation Subsystem (simulated) ==={RESET}\n")
    print(
        f"  {YELLOW}Fresh simulator in this CLI process: slice configuration and targets only, no traffic has run."
        f"\n  A running dashboard keeps its own state (GET /api/metaverse/slices).{RESET}\n"
    )
    mgr = NetworkSlicingManager()
    summary = mgr.get_summary()

    print(
        f"  {'Slice':<8} | {'Bandwidth':<12} | {'Target Latency':<16} | {'Avg Latency':<14} | {'Reliability':<14} | {'Active Conns':<14}"
    )
    print(f"  {'-' * 88}")
    for s_name, data in summary.items():
        bw = f"{data['allocated_bandwidth_mbps']} Mbps"
        tgt_lat = f"<{data['target_latency_ms']} ms"
        avg_lat = f"{data['avg_latency_ms']:.2f} ms"
        rel = f"{data['target_reliability_pct']}%"
        conns = f"{data['active_connections']}"
        print(f"  {s_name:<8} | {bw:<12} | {tgt_lat:<16} | {avg_lat:<14} | {rel:<14} | {conns:<14}")

    print(f"\n  {BOLD}Bandwidth Isolation Formula:{RESET} T_tx = (D * 8) / B_slice * 1000 ms")
    print("  • Modelled: URLLC priority mini-slot scheduling, eMBB 256-QAM 4x4 MIMO, mMTC NOMA (12,000 IoT/km² target)")
    print("  • Nothing here is measured on a radio.\n")


def cmd_sim_sla(args):
    """Validates DePIN SLA and smart contract consensus rules."""
    from sovereign_dc.metaverse.depin_sla import DePINSLAValidator

    print(f"\n{BOLD}{CYAN}=== DePIN Blockchain SLA & Byzantine Multi-Sig Validator ==={RESET}\n")
    val = DePINSLAValidator()

    print(f"  • Total DePIN Validator Nodes: {len(val.validators)}")
    print(f"  • Byzantine Consensus Threshold: floor(2N/3) + 1 = {val.consensus_threshold} signatures")
    print("  • Min SINR Rejection Rule: < -15.0 dB")
    print("  • Optimization Alert Rule: < -10.0 dB\n")

    # Test Nominal Position
    pos_nominal = (10.0, 15.0, 40.0)
    sinrs_nominal = {"Rx1": -9.5, "Rx2": -7.8, "Rx3": -4.2}
    res_nom = val.evaluate_uav_position_sla(pos_nominal, sinrs_nominal)
    print(f"  [Test 1] Nominal Position {pos_nominal} (Min SINR: {res_nom.min_sinr_db:.1f} dB):")
    print(
        f"    ↳ Approved: {GREEN}{res_nom.position_approved}{RESET} | Multi-Sig: {res_nom.validator_signatures_count}/{res_nom.required_threshold} | Block Hash: {res_nom.block_hash[:16]}..."
    )

    # Test Degraded Position (< -15 dB)
    pos_bad = (-80.0, -80.0, 15.0)
    sinrs_bad = {"Rx1": -18.2, "Rx2": -11.0, "Rx3": -9.0}
    res_bad = val.evaluate_uav_position_sla(pos_bad, sinrs_bad)
    print(f"\n  [Test 2] Degraded Position {pos_bad} (Min SINR: {res_bad.min_sinr_db:.1f} dB):")
    print(
        f"    ↳ Approved: {RED}{res_bad.position_approved}{RESET} | Reason: {YELLOW}{res_bad.rejection_reason}{RESET}"
    )
    print(f"    ↳ Optimization Event Triggered: {GREEN}{res_bad.optimization_event_triggered}{RESET}\n")


def main():
    parser = argparse.ArgumentParser(
        prog="smdc",
        description="Sovereign Mini Datacenter - Infrastructure, AI Cluster & Space Comms Management CLI",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # Status
    p_status = subparsers.add_parser("status", help="Show datacenter status & live telemetry")
    p_status.set_defaults(func=cmd_status)

    # Doctor (Pre-flight System & Dev Diagnostics)
    p_doctor = subparsers.add_parser(
        "doctor", help="Inspect local environment readiness, dependencies, and service ports"
    )
    p_doctor.set_defaults(func=cmd_doctor)

    # Bootstrap Provisioner
    p_bootstrap = subparsers.add_parser(
        "bootstrap", help="Autonomous DGX/Jetson power-up hardware, mesh & service provisioner"
    )
    p_bootstrap.add_argument("--node-id", type=str, help=f"Node ID override (default: NODE_ID, else {DEFAULT_NODE_ID})")
    p_bootstrap.add_argument("--role", type=str, help="Node role override (e.g. 'Primary Compute Core')")
    p_bootstrap.add_argument("--phase", type=int, choices=[1, 2, 3, 4, 5], help="Execute only specific phase (1-5)")
    p_bootstrap.add_argument(
        "--dry-run", action="store_true", help="Execute in simulation mode without making system modifications"
    )
    p_bootstrap.add_argument(
        "--notify-test", action="store_true", help="Test multi-channel technician notification dispatch"
    )
    p_bootstrap.add_argument(
        "--daemon", action="store_true", help="Run continuous background health watchdog after bootstrap"
    )
    p_bootstrap.set_defaults(func=cmd_bootstrap)

    # Audit
    p_audit = subparsers.add_parser("audit", help="Run the host hardening audit (kernel, Docker and firewall checks)")
    p_audit.set_defaults(func=cmd_audit)

    # Benchmark
    p_bench = subparsers.add_parser("benchmark", help="Run empirical AI, Space DTN & unified memory benchmarks")
    p_bench.add_argument("--all", action="store_true", help="Run complete benchmark suite")
    p_bench.add_argument("--ai", action="store_true", help="Benchmark AI token throughput and embedding rate")
    p_bench.add_argument("--dtn", action="store_true", help="Benchmark Space DTN bundle spool ingestion")
    p_bench.add_argument("--system", action="store_true", help="Benchmark unified memory bandwidth")
    p_bench.add_argument(
        "--shed",
        action="store_true",
        help="Measure, in simulation on this machine, the load-shedding software path "
        "(telemetry sample -> sentinel mode decision -> deferrable apps paused); no hardware actuation",
    )
    p_bench.add_argument(
        "--shed-iterations", type=int, default=200, help="Iterations for --shed (default: 200; median/p95 are reported)"
    )
    p_bench.add_argument(
        "--live",
        action="store_true",
        help="Also measure real token throughput against the configured Ollama server (OLLAMA_URL)",
    )
    p_bench.add_argument("--model", type=str, default="qwen2.5-coder:7b", help="Model to measure with --live")
    p_bench.add_argument("--export", type=str, help="Export benchmark results to JSON file path")
    p_bench.set_defaults(func=cmd_benchmark)

    # Demo Sandbox
    p_demo = subparsers.add_parser("demo", help="Run 1-click live demonstration sandbox")
    p_demo.add_argument("--steps", type=int, default=3, help="Number of telemetry cycles to simulate (default: 3)")
    p_demo.add_argument(
        "--no-delay", action="store_true", help="Execute without sleep delays for fast automated testing"
    )
    p_demo.set_defaults(func=cmd_demo)

    # Mesh Subcommand Suite
    p_mesh = subparsers.add_parser("mesh", help="Show multi-node cluster topology & sync status")
    mesh_subs = p_mesh.add_subparsers(dest="mesh_command", help="Mesh actions")
    p_mesh_consensus = mesh_subs.add_parser("consensus", help="Simulate decentralized Raft leader election")
    p_mesh_consensus.add_argument("--nodes", type=int, default=3, help="Number of cluster nodes (default: 3)")
    p_mesh_consensus.set_defaults(func=cmd_mesh_consensus)
    p_mesh_chaos = mesh_subs.add_parser("chaos", help="Simulate network partitions, leader crashes & DTN fallback")
    p_mesh_chaos.add_argument(
        "--scenario",
        choices=["all", "split-brain", "leader-crash", "dtn-fallback", "packet-loss"],
        default="all",
        help="Specific chaos scenario to execute (default: all)",
    )
    p_mesh_chaos.set_defaults(func=cmd_mesh_chaos)
    p_mesh.set_defaults(func=cmd_mesh)

    # Security Subcommand Suite
    p_sec = subparsers.add_parser("security", help="Host hardening audit & Post-Quantum Cryptography")
    sec_subs = p_sec.add_subparsers(dest="sec_command", help="Security actions")
    p_sec_audit = sec_subs.add_parser("audit", help="Run the host hardening audit (kernel, Docker and firewall checks)")
    p_sec_audit.set_defaults(func=cmd_audit)
    p_sec_pqc = sec_subs.add_parser("pqc", help="Benchmark NIST FIPS 204 ML-DSA and FIPS 203 ML-KEM engines")
    p_sec_pqc.set_defaults(func=cmd_security_pqc)
    p_sec.set_defaults(func=cmd_audit)

    # Deploy
    p_deploy = subparsers.add_parser("deploy", help="Deploy or update container stacks")
    p_deploy.add_argument(
        "--all", action="store_true", help="Deploy all modules (Core, VPN, Backup, Telemetry, Space, Agents, Security)"
    )
    p_deploy.add_argument("--with-vpn", action="store_true", help="Deploy Headscale mesh VPN")
    p_deploy.add_argument("--with-backup", action="store_true", help="Deploy Restic backup daemon")
    p_deploy.add_argument("--with-telemetry", action="store_true", help="Deploy solar & BMS telemetry exporter")
    p_deploy.add_argument("--with-space", action="store_true", help="Deploy Space Communications DTN node")
    p_deploy.add_argument("--with-agents", action="store_true", help="Deploy Autonomous AI Agents")
    p_deploy.add_argument(
        "--with-security", action="store_true", help="Deploy CrowdSec intrusion detection (no bouncer)"
    )
    p_deploy.add_argument("--dry-run", action="store_true", help="Validate compose configuration without starting")
    p_deploy.set_defaults(func=cmd_deploy)

    # Telemetry
    p_telem = subparsers.add_parser("telemetry", help="Run power, solar & thermal Prometheus exporter")
    p_telem.add_argument("--port", type=int, default=9101, help="Port to listen on (default: 9101)")
    p_telem.add_argument(
        "--hardware",
        action="store_true",
        help="Disable simulation and re-export the readings of the ESP32 telemetry bridge named by "
        "ESP32_BRIDGE_URL (e.g. http://192.168.1.50/metrics) under the canonical sovereign_* names. "
        "Without a reachable, fresh bridge the exporter reports sovereign_telemetry_source_up 0 "
        "and no invented measurements (VE.Direct / RS485 are not read directly)",
    )
    p_telem.set_defaults(func=cmd_telemetry)

    # Agent Subcommand Suite
    p_agent = subparsers.add_parser("agent", help="Autonomous AI Agents (Code Review, Semantic RAG & Status)")
    agent_subs = p_agent.add_subparsers(dest="agent_command", help="Agent actions")

    p_ag_status = agent_subs.add_parser("status", help="Check status of Ollama LLM and Qdrant Vector DB")
    p_ag_status.set_defaults(func=cmd_agent_status)

    p_ag_ask = agent_subs.add_parser("ask", help="Query the local Ollama LLM engine")
    p_ag_ask.add_argument("query", help="Question or prompt to send to the local LLM")
    p_ag_ask.add_argument("--model", help="Specific model name (default: qwen2.5-coder:7b)")
    p_ag_ask.set_defaults(func=cmd_agent_ask)

    p_ag_review = agent_subs.add_parser("review", help="Perform automated AI code review on a file or diff")
    p_ag_review.add_argument("target", help="Path to code file or diff patch")
    p_ag_review.set_defaults(func=cmd_agent_review)

    p_ag_index = agent_subs.add_parser("index", help="Index document directory into Qdrant vector database")
    p_ag_index.add_argument(
        "directory",
        nargs="?",
        help="Document directory to chunk and vectorize (default: DOCS_WATCH_DIR, else /data/documents). "
        "Honours DOCS_EXCLUDE_DIRS, DOCS_INCLUDE_GLOB and DOCS_MAX_FILE_BYTES like the indexer worker",
    )
    p_ag_index.set_defaults(func=cmd_agent_index)

    # Space Subcommand Suite
    p_space = subparsers.add_parser("space", help="Space & Satellite Communications (DTN / BPv7 / Orbit Tracking)")
    space_subs = p_space.add_subparsers(dest="space_command", help="Space actions")

    p_sp_passes = space_subs.add_parser("passes", help="Predict upcoming satellite contact passes")
    p_sp_passes.add_argument("--hours", type=float, default=12.0, help="Prediction window in hours (default: 12)")
    p_sp_passes.add_argument("--min-el", type=float, default=10.0, help="Minimum elevation in degrees (default: 10.0)")
    p_sp_passes.set_defaults(func=cmd_space_passes)

    p_sp_status = space_subs.add_parser("status", help="Show real-time space link budget & orbital look angles")
    p_sp_status.set_defaults(func=cmd_space_status)

    p_sp_send = space_subs.add_parser("send", help="Enqueue a bundle for space transmission on next pass")
    p_sp_send.add_argument(
        "destination_eid", help="Destination Endpoint Identifier (e.g., dtn://ground-station.earth/telemetry)"
    )
    p_sp_send.add_argument("message_or_file", help="Text message or path to binary file to transmit")
    p_sp_send.add_argument(
        "--priority", type=int, choices=[0, 1, 2, 3], default=1, help="0=Bulk, 1=Normal, 2=Expedited, 3=Critical"
    )
    p_sp_send.add_argument(
        "--ttl", type=int, default=86400 * 7, help="Bundle TTL lifetime in seconds (default: 7 days)"
    )
    p_sp_send.set_defaults(func=cmd_space_send)

    p_sp_queue = space_subs.add_parser(
        "queue", aliases=["dtn-spool"], help="List bundles in DTN store-and-forward spool"
    )
    p_sp_queue.set_defaults(func=cmd_space_queue)

    # Economy & Monetary Subcommand Suite
    p_econ = subparsers.add_parser(
        "economy",
        aliases=["wallet"],
        help="Sovereign Compute & Energy Monetary Layer (M2M micro-payments & credits)",
    )
    econ_subs = p_econ.add_subparsers(dest="economy_command", help="Economy actions")

    p_ec_wallet = econ_subs.add_parser("wallet", help="Inspect or generate node cryptographic wallet and balance")
    p_ec_wallet.add_argument(
        "--create", action="store_true", help="Create the wallet; fails if it already exists (never overwrites)"
    )
    p_ec_wallet.add_argument(
        "--pqc", action="store_true", help="Show the node's NIST FIPS 204 ML-DSA-87 post-quantum wallet"
    )
    p_ec_wallet.add_argument(
        "--mint", type=str, help="Mint local test credits (no external value) to the wallet; see 'economy mint'"
    )
    p_ec_wallet.add_argument("--node-id", type=str, help="Node identifier for a newly created wallet")
    p_ec_wallet.add_argument("--wallet-file", type=str, help="Custom wallet JSON file path")
    p_ec_wallet.add_argument("--db-path", type=str, help="Custom ledger SQLite database path")
    p_ec_wallet.set_defaults(func=cmd_economy_wallet)

    p_ec_mint = econ_subs.add_parser(
        "mint",
        help="Mint LOCAL TEST credits to the node wallet (locally issued, no external value)",
        description="Mints local test credits to the node wallet in the node ledger. A new ledger "
        "starts at zero and nothing else mints: this explicit command is the only source of a "
        "balance. The credits are issued by this node for itself and have no external value.",
    )
    p_ec_mint.add_argument("amount", type=str, help="Credits to mint (> 0, at most 6 decimal places)")
    p_ec_mint.add_argument("--memo", type=str, default="", help="Transaction memo (default: LOCAL_TEST_CREDIT)")
    p_ec_mint.add_argument("--wallet-file", type=str, help="Custom wallet JSON file path")
    p_ec_mint.add_argument("--db-path", type=str, help="Custom ledger SQLite database path")
    p_ec_mint.set_defaults(func=cmd_economy_mint)

    # Top-level shortcut for `smdc economy mint`: same handler, same arguments.
    p_mint = subparsers.add_parser(
        "mint",
        help="Shortcut for `economy mint`: mint LOCAL TEST credits to the node wallet (no external value)",
    )
    p_mint.add_argument("amount", type=str, help="Credits to mint (> 0, at most 6 decimal places)")
    p_mint.add_argument("--memo", type=str, default="", help="Transaction memo (default: LOCAL_TEST_CREDIT)")
    p_mint.add_argument("--wallet-file", type=str, help="Custom wallet JSON file path")
    p_mint.add_argument("--db-path", type=str, help="Custom ledger SQLite database path")
    p_mint.set_defaults(func=cmd_economy_mint)

    p_ec_send = econ_subs.add_parser("send", help="Send signed compute credits to peer node")
    p_ec_send.add_argument("recipient", help="Recipient wallet address (e.g. sov_...)")
    p_ec_send.add_argument("amount", type=float, help="Amount of compute credits to transfer")
    p_ec_send.add_argument("--memo", type=str, default="", help="Transaction memo / compute tag")
    p_ec_send.add_argument("--wallet-file", type=str, help="Custom wallet JSON file path")
    p_ec_send.add_argument("--db-path", type=str, help="Custom ledger SQLite database path")
    p_ec_send.set_defaults(func=cmd_economy_send)

    p_ec_market = econ_subs.add_parser("market", help="Inspect dynamic compute pricing and solar discount rates")
    p_ec_market.add_argument("--soc", type=float, default=85.0, help="Simulated battery SoC percentage (default: 85%%)")
    p_ec_market.add_argument(
        "--solar", type=float, default=850.0, help="Simulated solar power in Watts (default: 850W)"
    )
    p_ec_market.set_defaults(func=cmd_economy_market)

    p_ec_history = econ_subs.add_parser("history", help="List recent transactions in ledger")
    p_ec_history.add_argument("--limit", type=int, default=15, help="Maximum transactions to show (default: 15)")
    p_ec_history.add_argument("--db-path", type=str, help="Custom ledger SQLite database path")
    p_ec_history.set_defaults(func=cmd_economy_history)

    p_ec_audit = econ_subs.add_parser("audit", help="Audit ledger chain integrity and its head anchor")
    p_ec_audit.add_argument("--db-path", type=str, help="Custom ledger SQLite database path")
    p_ec_audit.add_argument(
        "--anchor", action="store_true", help="Anchor the verified chain head with this node's identity key"
    )
    p_ec_audit.set_defaults(func=cmd_economy_verify)

    p_econ.set_defaults(func=cmd_economy_wallet)

    # Docs
    p_docs = subparsers.add_parser("docs", help="Show 3D viewer and documentation URLs, or serve locally")
    p_docs.add_argument("--serve", "-s", action="store_true", help="Launch local HTTP server for 3D Digital Twin")
    p_docs.add_argument("--port", type=int, default=8088, help="Port to serve 3D Digital Twin on (default: 8088)")
    p_docs.add_argument("--no-browser", action="store_true", help="Do not automatically open default web browser")
    p_docs.set_defaults(func=cmd_docs)

    # Dashboard
    p_dash = subparsers.add_parser("dashboard", help="Launch real-time Web Operations Dashboard & REST API")
    p_dash.add_argument("--port", type=int, default=8080, help="Port to listen on (default: 8080)")
    p_dash.add_argument(
        "--host",
        default=None,
        help="Bind address (default: 127.0.0.1, or SMDC_DASHBOARD_HOST). Exposing beyond loopback "
        "requires SMDC_DASHBOARD_TOKEN for control endpoints",
    )
    p_dash.add_argument("--no-browser", action="store_true", help="Do not automatically open default web browser")
    p_dash.set_defaults(func=cmd_dashboard)

    # Model Context Protocol (MCP) Server Suite
    p_mcp = subparsers.add_parser(
        "mcp", help="Native Model Context Protocol (MCP) Server for Claude Desktop, Cursor & AI agents"
    )
    mcp_subs = p_mcp.add_subparsers(dest="mcp_command", help="MCP actions")

    p_mcp_serve = mcp_subs.add_parser("serve", help="Run standard MCP JSON-RPC 2.0 stdio server loop")
    p_mcp_serve.set_defaults(func=cmd_mcp_serve)

    p_mcp_tools = mcp_subs.add_parser("tools", help="List all registered MCP tools and JSON schemas")
    p_mcp_tools.set_defaults(func=cmd_mcp_tools)

    p_mcp_resources = mcp_subs.add_parser("resources", help="List all registered MCP resources and URIs")
    p_mcp_resources.set_defaults(func=cmd_mcp_resources)

    p_mcp_prompts = mcp_subs.add_parser("prompts", help="List all registered MCP operational prompt templates")
    p_mcp_prompts.set_defaults(func=cmd_mcp_prompts)

    p_mcp_test = mcp_subs.add_parser("test", help="Run end-to-end self-test of MCP handshake, tools and resources")
    p_mcp_test.set_defaults(func=cmd_mcp_test)

    p_mcp.set_defaults(func=cmd_mcp_tools)

    # Enterprise Application & Onboarding Framework Suite
    p_app = subparsers.add_parser(
        "app",
        aliases=["enterprise"],
        help="Enterprise Application & Workload Onboarding Framework (IoT, AI, Spatial, Vaults)",
    )
    app_subs = p_app.add_subparsers(dest="app_command", help="Application actions")

    p_app_init = app_subs.add_parser("init", help="Scaffold a new enterprise application project directory")
    p_app_init.add_argument("name", nargs="?", help="Application display name")
    p_app_init.add_argument("--app-id", type=str, help="Application unique identifier (slug)")
    p_app_init.add_argument(
        "--category",
        choices=["iot", "ai_inference", "spatial_media", "database", "distributed", "web_service", "custom"],
        default="custom",
        help="Application operational vertical category (default: custom)",
    )
    p_app_init.add_argument(
        "--runtime",
        choices=["process", "docker", "systemd", "wasm"],
        default="process",
        help="Execution runtime engine (default: process)",
    )
    p_app_init.add_argument("--entrypoint", type=str, help="Entrypoint execution command (e.g. 'python3 app.py')")
    p_app_init.add_argument("--gpu", action="store_true", help="Declare GPU vRAM requirements")
    p_app_init.add_argument(
        "--power-tier",
        choices=["L0_CRITICAL", "L1_STANDARD", "L2_BACKGROUND", "L3_DEFERRABLE", "L4_IDLE"],
        default="L1_STANDARD",
        help="Power load shedding priority tier (default: L1_STANDARD)",
    )
    p_app_init.add_argument("--target-dir", type=str, help="Target destination directory for scaffolding")
    p_app_init.add_argument(
        "--force",
        action="store_true",
        help="Replace scaffold files that already exist in the target directory "
        "(each is first moved to <name>.bak-<timestamp>); without it, init refuses and writes nothing",
    )
    p_app_init.set_defaults(func=cmd_app_init)

    p_app_val = app_subs.add_parser("validate", help="Validate manifest against schema and node hardware budgets")
    p_app_val.add_argument("path", nargs="?", default=".", help="Path to manifest file or application directory")
    p_app_val.set_defaults(func=cmd_app_validate)

    p_app_reg = app_subs.add_parser("register", help="Register an enterprise application with local node")
    p_app_reg.add_argument("path", nargs="?", default=".", help="Path to manifest file or application directory")
    p_app_reg.set_defaults(func=cmd_app_register)

    p_app_unreg = app_subs.add_parser("unregister", help="Unregister an application from local node")
    p_app_unreg.add_argument("app_id", help="Application unique identifier to remove")
    p_app_unreg.set_defaults(func=cmd_app_unregister)

    p_app_list = app_subs.add_parser("list", aliases=["ps"], help="List all registered enterprise applications")
    p_app_list.set_defaults(func=cmd_app_list)

    p_app_start = app_subs.add_parser("start", help="Start an enterprise application")
    p_app_start.add_argument("app_id", help="Application unique identifier to start")
    p_app_start.set_defaults(func=cmd_app_start)

    p_app_stop = app_subs.add_parser("stop", help="Stop a running enterprise application")
    p_app_stop.add_argument("app_id", help="Application unique identifier to stop")
    p_app_stop.set_defaults(func=cmd_app_stop)

    p_app_restart = app_subs.add_parser("restart", help="Restart an enterprise application")
    p_app_restart.add_argument("app_id", help="Application unique identifier to restart")
    p_app_restart.set_defaults(func=cmd_app_restart)

    p_app_status = app_subs.add_parser("status", help="Show real-time telemetry and power metrics for applications")
    p_app_status.add_argument("app_id", nargs="?", help="Specific application identifier to query")
    p_app_status.set_defaults(func=cmd_app_status)

    p_app_pkg = app_subs.add_parser(
        "package", help="Package application into a .smdc-app bundle with an ML-DSA-87 signature"
    )
    p_app_pkg.add_argument("path", nargs="?", default=".", help="Path to application directory")
    p_app_pkg.add_argument("--output", type=str, help="Custom output package file path")
    p_app_pkg.add_argument("--no-pqc", action="store_true", help="Skip NIST FIPS 204 ML-DSA post-quantum signature")
    p_app_pkg.set_defaults(func=cmd_app_package)

    p_app_verify = app_subs.add_parser(
        "verify",
        help="Verify a .smdc-app package (SHA-256, size, ML-DSA-87 signature) against a public key you trust",
    )
    p_app_verify.add_argument("package", help="Path to the .smdc-app archive (its sidecar is <package>.sig)")
    p_app_verify.add_argument(
        "--public-key",
        required=True,
        help="Trusted ML-DSA-87 public key of the signer: hex text, or a file containing it. "
        "Obtain it from the signer over a trusted channel, not from the package being verified.",
    )
    p_app_verify.add_argument("--sidecar", default=None, help="Signature sidecar path (default: <package>.sig)")
    p_app_verify.set_defaults(func=cmd_app_verify)

    # Top-level shortcut for `smdc app verify`: same handler, same arguments.
    p_verify = subparsers.add_parser(
        "verify",
        help="Shortcut for `app verify`: verify a .smdc-app package against a public key you trust",
    )
    p_verify.add_argument("package", help="Path to the .smdc-app archive (its sidecar is <package>.sig)")
    p_verify.add_argument(
        "--public-key",
        required=True,
        help="Trusted ML-DSA-87 public key of the signer: hex text, or a file containing it",
    )
    p_verify.add_argument("--sidecar", default=None, help="Signature sidecar path (default: <package>.sig)")
    p_verify.set_defaults(func=cmd_app_verify)

    p_app_test = app_subs.add_parser("test", help="Run end-to-end automated self-test of enterprise framework")
    p_app_test.set_defaults(func=cmd_app_test)

    p_app.set_defaults(func=cmd_app_list)

    # Metaverse / 5G Wireless Management Simulation Suite
    p_sim = subparsers.add_parser(
        "sim",
        aliases=["metaverse"],
        help="Metaverse Framework for Wireless Systems Management (Algorithm 1, SA-PPO, 5G Slicing, DePIN SLA)",
    )
    sim_subs = p_sim.add_subparsers(dest="sim_command", help="Simulation actions")

    p_sim_run = sim_subs.add_parser("run", help="Execute multi-layer simulation cycles with real-time telemetry")
    p_sim_run.add_argument("--cycles", type=int, default=5, help="Number of simulation cycles to execute (default: 5)")
    p_sim_run.add_argument("--seed", type=int, default=111, help="Random seed (default: 111)")
    p_sim_run.add_argument("--deterministic", action="store_true", help="Use deterministic policy inference")
    p_sim_run.set_defaults(func=cmd_sim_run)

    p_sim_bench = sim_subs.add_parser(
        "benchmark", aliases=["bench"], help="Run comparative RL benchmark (SA-PPO vs MD-PPO baseline)"
    )
    p_sim_bench.add_argument(
        "--episodes", type=int, default=15, help="Number of benchmark training episodes (default: 15)"
    )
    p_sim_bench.add_argument("--steps", type=int, default=20, help="Steps per episode (default: 20)")
    p_sim_bench.add_argument("--seed", type=int, default=111, help="Random seed (default: 111)")
    p_sim_bench.set_defaults(func=cmd_sim_benchmark)

    p_sim_slices = sim_subs.add_parser("slices", help="Inspect 5G network slicing status & QoS profiles")
    p_sim_slices.set_defaults(func=cmd_sim_slices)

    p_sim_sla = sim_subs.add_parser(
        "sla", help="Test DePIN smart contract SLA validation & Byzantine multi-sig consensus"
    )
    p_sim_sla.set_defaults(func=cmd_sim_sla)

    p_sim.set_defaults(func=cmd_sim_run)

    args = parser.parse_args()
    if hasattr(args, "func"):
        rc = args.func(args)
        # Commands return None on success; an int is an explicit process exit status.
        if isinstance(rc, int) and not isinstance(rc, bool) and rc != 0:
            sys.exit(rc)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
