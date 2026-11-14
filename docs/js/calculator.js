/**
 * Sovereign Mini Datacenter — Sizing Configurator, Investor ROI & Cyber Terminal
 *
 * @module calculator
 */

import { showToast } from './hud.js';
import { triggerSpaceTransmission } from './space.js';

// ---------------------------------------------------------------------------
// Pure model (no DOM access) — every constant is named so the maths is auditable.
// ---------------------------------------------------------------------------

/** Slider defaults in index.html; also the reference rack used by the ROI model. */
export const DEFAULT_SIZING = Object.freeze({ nodes: 2, hours: 12, batPacks: 2, panels: 4 });
export const DEFAULT_ROI = Object.freeze({ cloudMonthly: 2500, gridRate: 0.35, nodeCount: 1 });

// Electrical model
const BASE_LOAD_W = 180; // Host server, switch, PDU, pump & fans (always on)
// Compute node = NVIDIA DGX Spark. NVIDIA publishes a 240 W external power supply and a
// 140 W GB10 SoC TDP (docs.nvidia.com/dgx/dgx-spark/hardware.html); it publishes no idle figure.
const NODE_IDLE_W = 45; // Per compute node, idle — planning estimate, not an NVIDIA-published figure
const NODE_ACTIVE_W = 240; // Per compute node under load — DGX Spark rated supply power (upper bound)
const NODE_MEMORY_GB = 128; // Unified LPDDR5x memory per DGX Spark
const PANEL_WATTS = 410; // W peak per PV panel
const PEAK_SUN_HOURS = 5.2; // h/day (Southern European annual average)
const PV_SYSTEM_EFFICIENCY = 0.85; // MPPT, wiring, temperature & soiling derate
const BATTERY_PACK_KWH = 5.12; // 48V 100Ah LiFePO4 module
const BATTERY_USABLE_DOD = 0.85; // Usable depth of discharge

// BOM cost estimator (USD)
// Unit prices are the priced rows of hardware/COMPONENTS.md; at DEFAULT_SIZING the total equals
// that file's reference BOM: 4,097 + 2×4,699 + 2×1,200 + 4×180 + 1,860 = $18,475.
const COST_BASE_USD = 4097; // Host, storage, switch (1,940) + PDU (250) + cooling (1,115) + network cabling (170) + enclosure (622)
const COST_NODE_USD = 4699; // NVIDIA DGX Spark Founders Edition (128 GB / 4 TB), NVIDIA MSRP since Feb 2026
const COST_BATTERY_PACK_USD = 1200; // 5.12 kWh 48V 100Ah LiFePO4
const COST_PANEL_USD = 180; // 410W monocrystalline PERC
const COST_POWER_CONVERSION_USD = 1860; // MultiPlus-II (1,100) + MPPT 150/70 (540) + SmartShunt (130) + Lynx Distributor (90)

// ROI model
const ROI_YEARS = 3;
const MONTHS_PER_YEAR = 12;
const DAYS_PER_YEAR = 365;
// The ROI model is what a customer pays (COMMERCIALIZATION.md), not the build cost.
const SMDC_CAPEX_PER_UNIT_USD = 24500; // SMDC-Core-9U turnkey list price
const SMDC_OPEX_PER_UNIT_MONTHLY_USD = 199; // Sovereign Control Plane subscription per node
const GRID_CO2_KG_PER_KWH = 0.42; // Grid emission factor (kg CO2 per kWh displaced)
const KG_PER_TONNE = 1000;

const fmtInt = (n) => Math.round(n).toLocaleString('en-US');

export function computeSizing({ nodes, hours, batPacks, panels }) {
  const idlePowerW = BASE_LOAD_W + nodes * NODE_IDLE_W;
  const activePowerW = BASE_LOAD_W + nodes * NODE_ACTIVE_W;
  const dailyDrawKwh = (idlePowerW * (24 - hours) + activePowerW * hours) / 1000;
  const arrayWatts = panels * PANEL_WATTS;
  const dailyYieldKwh = (arrayWatts * PEAK_SUN_HOURS * PV_SYSTEM_EFFICIENCY) / 1000;
  const batteryKwh = batPacks * BATTERY_PACK_KWH;
  const usableKwh = batteryKwh * BATTERY_USABLE_DOD;
  const autonomyHours = usableKwh / (dailyDrawKwh / 24);
  const balanceKwh = dailyYieldKwh - dailyDrawKwh; // < 0 means the array cannot carry the load
  const solarCoveragePct = Math.min(100, (dailyYieldKwh / dailyDrawKwh) * 100);

  const costs = {
    base: COST_BASE_USD,
    compute: nodes * COST_NODE_USD,
    battery: batPacks * COST_BATTERY_PACK_USD,
    solar: panels * COST_PANEL_USD,
    powerConversion: COST_POWER_CONVERSION_USD,
  };
  const totalBom = costs.base + costs.compute + costs.battery + costs.solar + costs.powerConversion;

  return {
    unifiedMemoryGb: nodes * NODE_MEMORY_GB,
    arrayWatts,
    batteryKwh,
    dailyDrawKwh,
    dailyYieldKwh,
    autonomyHours,
    balanceKwh,
    solarCoveragePct,
    costs,
    totalBom,
  };
}

export function computeRoi({ cloudMonthly, gridRate, nodeCount }) {
  const months = ROI_YEARS * MONTHS_PER_YEAR;

  // Grid/diesel energy the on-site solar displaces, per unit, for the reference rack
  // (DEFAULT_SIZING). Only energy the array actually produces is counted.
  const ref = computeSizing(DEFAULT_SIZING);
  const displacedKwhPerDay = Math.min(ref.dailyDrawKwh, ref.dailyYieldKwh);
  const gridEnergyMonthly = (gridRate * displacedKwhPerDay * DAYS_PER_YEAR * nodeCount) / MONTHS_PER_YEAR;
  const gridEnergyTotal = gridRate * displacedKwhPerDay * DAYS_PER_YEAR * ROI_YEARS * nodeCount;

  // SMDC side
  const totalCapex = SMDC_CAPEX_PER_UNIT_USD * nodeCount;
  const smdcOpexMonthly = SMDC_OPEX_PER_UNIT_MONTHLY_USD * nodeCount;
  const smdcTotal = totalCapex + smdcOpexMonthly * months;

  // Comparison side: cloud spend + the grid/diesel energy bill the solar array avoids
  const cloudTotal = cloudMonthly * months * nodeCount;
  const comparisonTotal = cloudTotal + gridEnergyTotal;

  const netSavings = Math.max(0, comparisonTotal - smdcTotal);
  const monthlyNetSavings = cloudMonthly * nodeCount + gridEnergyMonthly - smdcOpexMonthly;
  const paybackMonths = monthlyNetSavings > 0 ? totalCapex / monthlyNetSavings : null;
  const roiPct = totalCapex > 0 ? (netSavings / totalCapex) * 100 : 0;
  const co2TonnesPerYear = (nodeCount * displacedKwhPerDay * DAYS_PER_YEAR * GRID_CO2_KG_PER_KWH) / KG_PER_TONNE;

  const smdcPct = Math.round((smdcTotal / (smdcTotal + comparisonTotal)) * 100);

  return {
    years: ROI_YEARS,
    displacedKwhPerDay,
    solarCoveragePct: ref.solarCoveragePct,
    gridEnergyTotal,
    totalCapex,
    smdcTotal,
    cloudTotal,
    comparisonTotal,
    netSavings,
    paybackMonths,
    roiPct,
    co2TonnesPerYear,
    smdcPct,
    comparisonPct: 100 - smdcPct,
  };
}

/** Display strings for the sizing results (also used to verify the static placeholders). */
export function formatSizing(r) {
  const deficit = r.balanceKwh < 0;
  return {
    draw: `${r.dailyDrawKwh.toFixed(1)} kWh`,
    yield: `${r.dailyYieldKwh.toFixed(1)} kWh`,
    autonomy: `${r.autonomyHours.toFixed(1)} hrs`,
    cost: `$${fmtInt(r.totalBom)}`,
    deficit,
    balance: deficit
      ? `⚠ Energy deficit: ${Math.abs(r.balanceKwh).toFixed(1)} kWh/day — solar covers ${Math.floor(r.solarCoveragePct)}% of the draw (not fully off-grid at this configuration)`
      : `✓ Solar surplus: +${r.balanceKwh.toFixed(1)} kWh/day — solar covers the full daily draw`,
  };
}

/** Display strings for the ROI results (also used to verify the static placeholders). */
export function formatRoi(r, gridRate) {
  return {
    payback: r.paybackMonths === null ? 'N/A' : `${r.paybackMonths.toFixed(1)} Months`,
    savings: `$${fmtInt(r.netSavings)}`,
    roi: `${r.roiPct.toFixed(1)}%`,
    co2: `${r.co2TonnesPerYear.toFixed(1)} Tons`,
    barSmdc: `$${(r.smdcTotal / 1000).toFixed(1)}k`,
    barComparison: `$${(r.comparisonTotal / 1000).toFixed(1)}k`,
    energy: `Includes $${fmtInt(r.gridEnergyTotal)} of grid energy displaced by solar over ${r.years} yr (${r.displacedKwhPerDay.toFixed(1)} kWh/day per node at $${gridRate.toFixed(2)}/kWh)`,
  };
}

/** RFC 4180 row: every field quoted, inner quotes doubled. */
export function csvRow(fields) {
  return fields.map((f) => `"${String(f ?? '').replace(/"/g, '""')}"`).join(',');
}

function downloadCsv(rows, filename) {
  const csv = rows.map(csvRow).join('\r\n') + '\r\n';
  const link = document.createElement('a');
  link.setAttribute('href', 'data:text/csv;charset=utf-8,' + encodeURIComponent(csv));
  link.setAttribute('download', filename);
  document.body.appendChild(link);
  link.click();
  link.remove();
}

// ---------------------------------------------------------------------------
// DOM bindings
// ---------------------------------------------------------------------------

export function init() {
  updateSizingCalculator();
  updateInvestorCalculator();
  bindInlineHandlers();
}

/**
 * Bind event listeners that were previously inline HTML attributes
 * (oninput, onclick) which don't work with ES modules since globals
 * are no longer available.
 */
function bindInlineHandlers() {
  // Sizing Calculator sliders
  const sizingSliders = ['calc-nodes', 'calc-hours', 'calc-battery', 'calc-solar'];
  sizingSliders.forEach((id) => {
    const el = document.getElementById(id);
    if (el) el.addEventListener('input', updateSizingCalculator);
  });

  // Investor ROI sliders
  const roiSliders = ['roi-cloud-spend', 'roi-grid-rate', 'roi-nodes'];
  roiSliders.forEach((id) => {
    const el = document.getElementById(id);
    if (el) el.addEventListener('input', updateInvestorCalculator);
  });

  // Terminal input
  const termInput = document.getElementById('term-cmd-input');
  if (termInput) termInput.addEventListener('keydown', handleTerminalInput);
}

function readNumber(id, fallback, parse = parseFloat) {
  const v = parse(document.getElementById(id)?.value);
  return Number.isFinite(v) ? v : fallback;
}

function setText(id, text) {
  const el = document.getElementById(id);
  if (el) el.textContent = text;
  return el;
}

function readSizingInputs() {
  return {
    nodes: readNumber('calc-nodes', DEFAULT_SIZING.nodes, (v) => parseInt(v, 10)),
    hours: readNumber('calc-hours', DEFAULT_SIZING.hours, (v) => parseInt(v, 10)),
    batPacks: readNumber('calc-battery', DEFAULT_SIZING.batPacks, (v) => parseInt(v, 10)),
    panels: readNumber('calc-solar', DEFAULT_SIZING.panels, (v) => parseInt(v, 10)),
  };
}

function readRoiInputs() {
  return {
    cloudMonthly: readNumber('roi-cloud-spend', DEFAULT_ROI.cloudMonthly),
    gridRate: readNumber('roi-grid-rate', DEFAULT_ROI.gridRate),
    nodeCount: readNumber('roi-nodes', DEFAULT_ROI.nodeCount, (v) => parseInt(v, 10)),
  };
}

// --- 1. Sizing Configurator ---
export function updateSizingCalculator() {
  const input = readSizingInputs();
  const r = computeSizing(input);
  const out = formatSizing(r);

  setText('calc-val-nodes', `${input.nodes} Node${input.nodes > 1 ? 's' : ''} (${fmtInt(r.unifiedMemoryGb)} GB unified)`);
  setText('calc-val-hours', `${input.hours} hrs/day`);
  setText('calc-val-battery', `${r.batteryKwh.toFixed(2)} kWh (${input.batPacks}x 48V)`);
  setText('calc-val-solar', `${fmtInt(r.arrayWatts)}W (${input.panels}x ${PANEL_WATTS}W)`);

  setText('calc-res-draw', out.draw);
  setText('calc-res-yield', out.yield);
  setText('calc-res-autonomy', out.autonomy);
  setText('calc-res-cost', out.cost);

  // Be explicit when the array cannot carry the load: never imply off-grid operation in deficit.
  const balanceEl = setText('calc-res-balance', out.balance);
  if (balanceEl) balanceEl.style.color = out.deficit ? 'var(--accent-rose)' : 'var(--accent-emerald)';
  const yieldEl = document.getElementById('calc-res-yield');
  if (yieldEl) yieldEl.style.color = out.deficit ? 'var(--accent-rose)' : 'var(--accent-emerald)';
}

export function exportBOMCSV() {
  const input = readSizingInputs();
  const r = computeSizing(input);
  const out = formatSizing(r);

  downloadCsv(
    [
      ['Category', 'Component', 'Qty', 'Est. Cost'],
      ['AI Compute', 'NVIDIA DGX Spark (128GB unified memory, 4TB NVMe)', input.nodes, `$${fmtInt(r.costs.compute)}`],
      ['Energy Storage', 'LiFePO4 48V 100Ah 5.12kWh Module', input.batPacks, `$${fmtInt(r.costs.battery)}`],
      ['Solar PV', '410W Monocrystalline PERC Panel', input.panels, `$${fmtInt(r.costs.solar)}`],
      ['Power Conversion', 'Victron MultiPlus-II 48/3000 + SmartSolar MPPT 150/70 + SmartShunt + Lynx Distributor', 1, `$${fmtInt(r.costs.powerConversion)}`],
      ['Chassis, Host & Network', '9U 19" Rack + Host Server & NVMe Storage + 10GbE Switch + PDU + Liquid Cooling', 1, `$${fmtInt(r.costs.base)}`],
      ['Total', 'Sovereign Mini Datacenter Complete BOM', '', out.cost],
      ['Energy Balance', `Daily draw ${out.draw} vs solar yield ${out.yield}`, '', `${r.balanceKwh >= 0 ? '+' : '-'}${Math.abs(r.balanceKwh).toFixed(1)} kWh/day`],
    ],
    'smdc_bill_of_materials.csv'
  );
  showToast('📥 Bill of Materials CSV Exported');
}

// --- 2. Investor ROI Simulator ---
export function updateInvestorCalculator() {
  const input = readRoiInputs();
  const r = computeRoi(input);
  const out = formatRoi(r, input.gridRate);

  setText('roi-val-cloud', `$${fmtInt(input.cloudMonthly)} / mo`);
  setText('roi-val-grid', `$${input.gridRate.toFixed(2)} / kWh`);
  setText('roi-val-nodes', `${input.nodeCount} Node${input.nodeCount > 1 ? 's' : ''} (Turnkey)`);

  setText('roi-res-payback', out.payback);
  setText('roi-res-savings', out.savings);
  setText('roi-res-roi', out.roi);
  setText('roi-res-co2', out.co2);

  // Visual Bar
  const barSmdc = document.getElementById('roi-bar-smdc');
  const barCloud = document.getElementById('roi-bar-cloud');
  if (barSmdc) barSmdc.style.width = `${r.smdcPct}%`;
  if (barCloud) barCloud.style.width = `${r.comparisonPct}%`;
  setText('roi-bar-val-smdc', out.barSmdc);
  setText('roi-bar-val-cloud', out.barComparison);
  setText('roi-res-energy', out.energy);
}

export function exportInvestorTCOCSV() {
  const input = readRoiInputs();
  const r = computeRoi(input);
  const out = formatRoi(r, input.gridRate);

  downloadCsv(
    [
      ['Metric', 'Value'],
      ['Payback Period', out.payback],
      [`${r.years}-Year Net Savings`, out.savings],
      [`${r.years}-Year ROI`, out.roi],
      [`SMDC ${r.years}-Year Cost (list price + control plane subscription)`, `$${fmtInt(r.smdcTotal)}`],
      [`Cloud ${r.years}-Year Spend`, `$${fmtInt(r.cloudTotal)}`],
      [`Grid Energy Displaced by Solar (${r.years} yr)`, `$${fmtInt(r.gridEnergyTotal)}`],
      ['Grid / Diesel Electricity Rate', `$${input.gridRate.toFixed(2)} / kWh`],
      ['Solar Energy Displacing Grid (per node)', `${r.displacedKwhPerDay.toFixed(1)} kWh/day`],
      ['CO2 Avoided per Year', `${r.co2TonnesPerYear.toFixed(1)} t (at ${GRID_CO2_KG_PER_KWH} kg CO2/kWh)`],
      ['Data Sovereignty Compliance', '100% On-Premise'],
      ['Solar Coverage of Reference Rack Load', `${Math.floor(r.solarCoveragePct)}%`],
    ],
    'smdc_investor_tco_model.csv'
  );
  showToast('📊 Investor TCO Model CSV Exported');
}

// --- 3. Interactive Cyber Terminal (smdc) ---
export function handleTerminalInput(e) {
  if (e.key !== 'Enter') return;
  const input = document.getElementById('term-cmd-input');
  if (!input) return;
  const cmd = input.value.trim();
  input.value = '';

  const screen = document.getElementById('term-output');
  if (!screen) return;

  // User input is always inserted as text, never parsed as HTML.
  const print = (html) => screen.insertAdjacentHTML('beforeend', html);
  const printText = (text, className, color) => {
    const span = document.createElement('span');
    if (className) span.className = className;
    if (color) span.style.color = color;
    span.textContent = text;
    screen.appendChild(span);
  };

  print('\n');
  printText(`smdc$ ${cmd}`, 'term-line-accent');
  print('\n');

  const lower = cmd.toLowerCase();
  if (lower === 'help') {
    print(`Available SMDC CLI Commands:
  • <span class="term-line-cyan">smdc status</span>             Inspect container health, solar harvest, and space link
  • <span class="term-line-cyan">smdc economy wallet</span>     Display node Post-Quantum & Ed25519 wallet balances
  • <span class="term-line-cyan">smdc economy market</span>     Query solar-aware compute & relay price quotes
  • <span class="term-line-cyan">smdc space passes</span>       Predict upcoming LEO satellite contact passes
  • <span class="term-line-cyan">smdc space send</span>         Queue DTN BPv7 bundle for space transmission
  • <span class="term-line-cyan">smdc audit</span>              Run NIST Zero Trust & PQC compliance audit
  • <span class="term-line-cyan">smdc mesh</span>               Inspect multi-node WireGuard mesh peers
  • <span class="term-line-cyan">clear</span>                   Clear terminal screen`);
  } else if (lower === 'clear') {
    screen.textContent = 'Sovereign Mini Datacenter Interactive Terminal (smdc v1.4.0).\n';
  } else if (lower.includes('status')) {
    print(`<span class="term-line-cyan">[SYSTEM STATUS]</span>
  Solar PV:       1,330 W (150V MPPT Peak Nominal)
  LiFePO4 Bank:   88.5% (52.8V, 10.24 kWh Reserve)
  Coolant Loop:   31.2°C (Dual 360mm Rads, Flow: 4.2 L/min)
  AI Inference:   Ollama nomic-embed-text (0.42ms/token)
  Space Link:     IN CONTACT (57.1° EL, SNR: 14.2 dB)
  Load Shedding:  L0 (Full Compute Operations Authorized)`);
  } else if (lower.includes('economy') && lower.includes('wallet')) {
    print(`<span class="term-line-purple">[NODE WALLET]</span>
  Address (Ed25519):   sov_89f7a24c8b910e12d
  Address (ML-DSA-87): sov_pqc_99e14a72d3f901bc
  Balance:             1,000.00 SOV Credits
  Active Channels:     2 Open Channels (Streaming Compute)`);
  } else if (lower.includes('economy') && lower.includes('market')) {
    print(`<span class="term-line-amber">[MARKET PRICING ORACLE]</span>
  Solar Harvest:  1,330 W (>1,000 W Threshold)
  Battery SoC:    88.5% (>75% Threshold)
  Solar Discount: 50.0% OFF (Surplus Solar Compute Rate)
  LLM Inference:  0.0025 SOV / 1k tokens
  Space DTN Relay:0.0050 SOV / MB`);
  } else if (lower.includes('space') && lower.includes('passes')) {
    print(`<span class="term-line-cyan">[UPCOMING CONTACT PASSES]</span>
  1. SOVEREIGN-LEO-1  AOS: +02m 14s  Max EL: 68.4°  Duration: 8m 42s
  2. STARLINK-RELAY   AOS: +48m 10s  Max EL: 42.1°  Duration: 6m 15s`);
  } else if (lower.includes('space') && lower.includes('send')) {
    triggerSpaceTransmission();
    print(`<span class="term-line-accent">[DTN BPv7 BUNDLE TRANSMITTED]</span>
  Bundle ID:      dtn://sovereign-node-alpha/telemetry/0x4a92
  Payload:        Telemetry snapshot (4.8 KB)
  Destination:    dtn://ground-station-alpha.earth/relay
  Status:         SPOOLED & TRANSMITTING VIA PHASED ARRAY 🚀`);
  } else if (lower.includes('audit')) {
    print(`<span class="term-line-accent">[COMPLIANCE & SECURITY AUDIT]</span>
  NIST SP 800-207 Zero Trust:  PASSED (Mutual WireGuard + PQC)
  NIST FIPS 203 ML-KEM-1024:   ACTIVE (Key Encapsulation)
  NIST FIPS 204 ML-DSA-87:     ACTIVE (Digital Signatures)
  Overall Compliance Score:    100% GREEN (CIS Hardened)`);
  } else {
    printText(`Command not recognized: '${cmd}'. Type 'help' for options.`, null, '#ef4444');
  }

  screen.scrollTop = screen.scrollHeight;
}
