# ====================================================================
# Sovereign Mini Datacenter — Cross-Platform Developer Tasks
# ====================================================================
.PHONY: help dev-setup test test-fast test-subsystem lint format typecheck check quality-gate dashboard serve-twin sim benchmark doctor docker-validate scad-export clean

PYTHON ?= uv run python
PYTEST ?= uv run pytest
RUFF ?= uv tool run ruff
MYPY ?= uv tool run mypy

help:
	@echo "===================================================================="
	@echo "Sovereign Mini Datacenter — Developer Automation"
	@echo "===================================================================="
	@echo "Setup:"
	@echo "  make dev-setup        - Bootstraps virtualenv, installs deps & pre-commit hooks"
	@echo "  make doctor           - Validates local system dependencies & service ports"
	@echo ""
	@echo "Testing & Code Quality:"
	@echo "  make test-fast        - Runs test suite without coverage (ultra-fast inner loop)"
	@echo "  make test             - Runs full test suite with coverage enforcement (>=85%)"
	@echo "  make test-subsystem SUB=cli - Runs tests for specific subsystem"
	@echo "  make lint             - Runs Ruff linter"
	@echo "  make format           - Formats code with Ruff and auto-fixes lint issues"
	@echo "  make typecheck        - Runs Mypy static type analysis"
	@echo "  make check            - Fast pre-push sanity check (format + lint + types + test-fast)"
	@echo "  make quality-gate     - Full multi-stage quality gates"
	@echo ""
	@echo "Runtime & Services:"
	@echo "  make dashboard        - Launches Web Operations Dashboard & REST API (port 8080)"
	@echo "  make serve-twin       - Serves 3D WebGL Digital Twin & launches browser (port 8088)"
	@echo "  make sim              - Executes 5 cycles of 6-layer metaverse wireless simulation"
	@echo "  make benchmark        - Runs comparative RL benchmark (SA-PPO vs MD-PPO)"
	@echo ""
	@echo "Validation & Build:"
	@echo "  make docker-validate  - Validates all Docker Compose stacks"
	@echo "  make scad-export      - Compiles OpenSCAD chassis to docs/cad/rack_enclosure.stl"
	@echo "  make clean            - Removes cache and coverage artifacts"
	@echo "===================================================================="

dev-setup:
	@echo "--> Bootstrapping dependencies with uv..."
	uv sync --all-extras
	@if [ ! -f software/.env ]; then cp software/env.example software/.env && echo "Created software/.env"; fi
	uv run pre-commit install
	@echo "--> Development environment ready!"

doctor:
	$(PYTHON) -m sovereign_dc.cli doctor

test-fast:
	$(PYTEST) --no-cov tests/

test:
	$(PYTEST) tests/ --cov=sovereign_dc --cov-fail-under=85

test-subsystem:
	@if [ -z "$(SUB)" ]; then echo "Error: Specify SUB=<name>, e.g. make test-subsystem SUB=cli"; exit 1; fi
	$(PYTEST) --no-cov tests/test_*$(SUB)*.py

lint:
	$(RUFF) check src/ tests/

format:
	$(RUFF) format src/ tests/
	$(RUFF) check --fix src/ tests/

typecheck:
	$(MYPY) --ignore-missing-imports src/sovereign_dc

check: format lint typecheck test-fast
	@echo "All fast checks passed!"

quality-gate:
	@bash scripts/quality_gate.sh || powershell -ExecutionPolicy Bypass -File scripts/quality_gate.ps1

dashboard:
	$(PYTHON) -m sovereign_dc.cli dashboard --port 8080

serve-twin:
	$(PYTHON) -m sovereign_dc.cli docs --serve --port 8088

sim:
	$(PYTHON) -m sovereign_dc.cli sim run --cycles 5

benchmark:
	$(PYTHON) -m sovereign_dc.cli sim benchmark --episodes 15 --steps 20

docker-validate:
	@if [ ! -f software/.env ]; then cp software/env.example software/.env; fi
	docker compose -f software/docker-compose.yml config --quiet
	@echo "Docker Compose configurations valid."

scad-export:
	@mkdir -p docs/cad
	openscad --hardwarnings --export-format binstl -o docs/cad/rack_enclosure.stl cad/rack_enclosure.scad
	@echo "OpenSCAD export complete."

clean:
	rm -rf .pytest_cache .ruff_cache .mypy_cache .coverage htmlcov dist build
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	@echo "Cleaned build and cache artifacts."
