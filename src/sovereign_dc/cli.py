"""
Sovereign Mini Datacenter CLI (smdc)
"""

import os
import sys
import argparse
import subprocess
import urllib.request
import urllib.error
try:
    from . import __version__
    from . import telemetry
except ImportError:
    try:
        from sovereign_dc import __version__, telemetry
    except ImportError:
        __version__ = "1.0.0"
        import telemetry

# Color codes
GREEN = "\033[1;32m"
BLUE = "\033[1;34m"
CYAN = "\033[1;36m"
YELLOW = "\033[1;33m"
RED = "\033[1;31m"
BOLD = "\033[1m"
RESET = "\033[0m"

def get_project_root():
    # If running from cloned git repo, locate software/
    cwd = os.getcwd()
    if os.path.exists(os.path.join(cwd, "software", "docker-compose.yml")):
        return cwd
    if os.path.exists(os.path.join(cwd, "docker-compose.yml")):
        return os.path.dirname(cwd)
    return cwd

def cmd_status(args):
    """Displays infrastructure status, running containers, and live telemetry."""
    print(f"\n{BOLD}{CYAN}=== Sovereign Mini Datacenter — System Status ==={RESET}")
    
    # 1. Container health check
    print(f"\n{BOLD}[1] Container Subsystems:{RESET}")
    try:
        res = subprocess.run(["docker", "ps", "--format", "table {{.Names}}\t{{.Status}}\t{{.Ports}}"], capture_output=True, text=True)
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

    # 2. Telemetry check
    print(f"\n{BOLD}[2] Power & Thermal Telemetry:{RESET}")
    try:
        req = urllib.request.Request("http://localhost:9101/metrics", headers={"User-Agent": "smdc-cli"})
        with urllib.request.urlopen(req, timeout=2) as response:
            content = response.read().decode("utf-8")
            metrics = {}
            for line in content.split("\n"):
                if line and not line.startswith("#"):
                    parts = line.split()
                    if len(parts) == 2:
                        metrics[parts[0]] = float(parts[1])
            
            soc = metrics.get("sovereign_battery_soc_percent", 0.0)
            volts = metrics.get("sovereign_battery_voltage_volts", 0.0)
            solar = metrics.get("sovereign_solar_pv_power_watts", 0.0)
            load = metrics.get("sovereign_system_power_draw_watts", 0.0)
            temp = metrics.get("sovereign_temp_coolant_celsius", 0.0)
            shedding = metrics.get("sovereign_load_shedding_active", 0.0)

            print(f"  • Battery Bank:    {GREEN}{soc:.1f}%{RESET} ({volts:.1f} V)")
            print(f"  • Solar PV Input:  {CYAN}{solar:.0f} W{RESET}")
            print(f"  • System AC Load:  {YELLOW}{load:.0f} W{RESET}")
            print(f"  • Coolant Temp:    {GREEN}{temp:.1f} °C{RESET}")
            print(f"  • Sentinel Status: {'⚡ ' + RED + 'LOAD SHEDDING' if shedding else GREEN + 'NORMAL'}{RESET}")
    except Exception:
        print(f"  {YELLOW}Telemetry exporter offline at :9101 (Run 'smdc telemetry' to start){RESET}")

    print(f"\n{BOLD}[3] Live 3D Digital Twin:{RESET} {CYAN}https://iliachry.gr/sovereign-mini-datacenter/{RESET}\n")

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

    if args.dry_run:
        print(f"{CYAN}DRY RUN: Validating compose stack configuration...{RESET}")
        cmd.extend(["config", "--quiet"])
        res = subprocess.run(cmd, cwd=soft_dir)
        if res.returncode == 0:
            print(f"{GREEN}✅ Configuration valid.{RESET}")
        return

    print(f"{BOLD}{GREEN}Launching Sovereign Datacenter Stack...{RESET}")
    cmd.extend(["up", "-d", "--remove-orphans"])
    subprocess.run(cmd, cwd=soft_dir)

def cmd_telemetry(args):
    """Starts the power & thermal Prometheus exporter."""
    print(f"{BOLD}{CYAN}Starting Sovereign Power & Thermal Exporter on port {args.port}...{RESET}")
    telemetry.run(port=args.port, simulation=not args.hardware)

def cmd_docs(args):
    """Prints documentation and 3D digital twin URL."""
    print(f"\n{BOLD}Sovereign Mini Datacenter v{__version__}{RESET}")
    print(f"• Repository:  {CYAN}https://github.com/iliachry/sovereign-mini-datacenter{RESET}")
    print(f"• 3D Viewer:   {GREEN}https://iliachry.gr/sovereign-mini-datacenter/{RESET}")
    print(f"• Docs & BOM:  {BOLD}hardware/COMPONENTS.md{RESET} & {BOLD}hardware/WIRING_DIAGRAM.md{RESET}\n")

def main():
    parser = argparse.ArgumentParser(
        prog="smdc",
        description="Sovereign Mini Datacenter - Infrastructure & AI Cluster Management CLI",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # Status
    p_status = subparsers.add_parser("status", help="Show datacenter status & live telemetry")
    p_status.set_defaults(func=cmd_status)

    # Deploy
    p_deploy = subparsers.add_parser("deploy", help="Deploy or update container stacks")
    p_deploy.add_argument("--all", action="store_true", help="Deploy all modules (Core, VPN, Backup, Telemetry)")
    p_deploy.add_argument("--with-vpn", action="store_true", help="Deploy Headscale mesh VPN")
    p_deploy.add_argument("--with-backup", action="store_true", help="Deploy Restic backup daemon")
    p_deploy.add_argument("--with-telemetry", action="store_true", help="Deploy solar & BMS telemetry exporter")
    p_deploy.add_argument("--dry-run", action="store_true", help="Validate compose configuration without starting")
    p_deploy.set_defaults(func=cmd_deploy)

    # Telemetry
    p_telem = subparsers.add_parser("telemetry", help="Run power, solar & thermal Prometheus exporter")
    p_telem.add_argument("--port", type=int, default=9101, help="Port to listen on (default: 9101)")
    p_telem.add_argument("--hardware", action="store_true", help="Read from physical serial/RS485 ports instead of simulation")
    p_telem.set_defaults(func=cmd_telemetry)

    # Docs
    p_docs = subparsers.add_parser("docs", help="Show 3D viewer and documentation URLs")
    p_docs.set_defaults(func=cmd_docs)

    args = parser.parse_args()
    if hasattr(args, "func"):
        args.func(args)
    else:
        parser.print_help()

if __name__ == "__main__":
    main()