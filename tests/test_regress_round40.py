"""Regression tests for Tri-Council Round 40 findings."""

from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path
from typing import Any

from pytest import CaptureFixture

from sovereign_dc import cli
from sovereign_dc.enterprise.registry import EnterpriseRegistry

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_round40_registry_cache_rejects_corrupt_top_level_fields_and_preserves_unparsed(
    capsys: CaptureFixture[str],
) -> None:
    """Corrupt top-level or constraint-invalid cache entries are skipped/preserved without crashing CLI."""
    reg_file = EnterpriseRegistry().registry_file
    reg_file.parent.mkdir(parents=True, exist_ok=True)

    good_entry = EnterpriseRegistry.scaffold_manifest("Good App", "good-app").to_dict()
    corrupt_entries: list[dict[str, Any]] = [
        {"app_id": "bad-app", "name": None, "entrypoint": "python3 app.py"},
        {"app_id": "bad-ep", "name": "Ok", "entrypoint": None},
        {"app_id": "BAD_UPPER", "name": "Ok", "entrypoint": "python3 app.py"},
        {"app_id": "bad-port", "name": "Ok", "entrypoint": "python3 app.py", "network": {"ports": [70000]}},
    ]
    reg_file.write_text(
        json.dumps({"version": "1.0.0", "apps": [good_entry, *corrupt_entries]}),
        encoding="utf-8",
    )

    registry = EnterpriseRegistry(registry_file=reg_file)
    assert [a.app_id for a in registry.list_apps()] == ["good-app"]

    cli.cmd_app_list(Namespace())
    out = capsys.readouterr().out
    assert "good-app" in out
    for bad in corrupt_entries:
        assert str(bad["app_id"]) not in out

    good2 = EnterpriseRegistry.scaffold_manifest("Good App Two", "good-app-2")
    ok, errs = registry.register_app(good2, persist=True)
    assert ok and errs == []

    saved = json.loads(reg_file.read_text(encoding="utf-8"))
    saved_ids = [item.get("app_id") for item in saved["apps"] if isinstance(item, dict)]
    assert "good-app" in saved_ids
    assert "good-app-2" in saved_ids
    for bad in corrupt_entries:
        assert bad in saved["apps"]


def test_round40_wiring_host_sfp_and_headscale_cidr_consistency() -> None:
    """Host SFP+/site-router BOM matches wiring diagram and WireGuard CIDR matches headscale.yaml."""
    components = (REPO_ROOT / "hardware" / "COMPONENTS.md").read_text(encoding="utf-8")
    wiring = (REPO_ROOT / "hardware" / "WIRING_DIAGRAM.md").read_text(encoding="utf-8")

    assert "upstream site router/firewall" in components
    assert "10GbE SFP+" in components
    assert "Site-supplied" in wiring
    assert "site-supplied SFP/SFP+ module" in wiring

    arch = (REPO_ROOT / "ARCHITECTURE.md").read_text(encoding="utf-8")
    consensus = (REPO_ROOT / "src" / "sovereign_dc" / "mesh" / "consensus.py").read_text(encoding="utf-8")
    cli_py = (REPO_ROOT / "src" / "sovereign_dc" / "cli.py").read_text(encoding="utf-8")
    compliance = (REPO_ROOT / "COMPLIANCE.md").read_text(encoding="utf-8")
    headscale = (REPO_ROOT / "software" / "vpn" / "config" / "headscale.yaml").read_text(encoding="utf-8")

    for text in (arch, consensus, cli_py, compliance, headscale):
        assert "100.64.0.0/10" in text

    for text in (arch, consensus, cli_py):
        assert "100.64.0.0/16" not in text
