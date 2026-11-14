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
export const DEFAULT_SIZING = Object.freeze({ nodes: 2, hours: 12, batPacks: 2, panels: 8 });
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
// that file's reference BOM: 4,045 + 2×6,950 + 2×1,200 + 8×180 + 2,178 + 1×600 = $24,563.
// Host, storage and switch (2,250) = switch 270 + NVMe 2×320 + host board 450 + host CPU 330 + RAM 2×160
// + boot drive 60 + host chassis and PSU 180.
// Host liquid loop and enclosure fans (718) = radiator 1×140 + CPU water block 90 + pump 90 + pump top and
// reservoir 60 + fans 6×32 + coolant 1 L×30 + compression fittings 6×8 + tubing 25 + fan bracket 25 + grilles 6×3.
// Protection, cabling, 12 V supply and telemetry bridge (858) = Class T fuses 2×55 + holders 2×45 + MEGA fuses 5×9
// + 35 mm² cable 6 m×12 + lugs 24×2 + PV combiner 70 + PV isolator 40 + 4 mm² PV cable 50 m×1
// + 6 mm² PV cable 10 m×1.50 + MC4 pairs 6×3 + earthing kit 80 + 48V-to-12V converter 95
// + 12 V distribution 30 + telemetry bridge 45 + AC output RCBO 50. These are held fixed here: the calculator does not
// rescale cable or fuses with the panel or battery count.
const COST_BASE_USD = 4045; // Host, storage, switch (2,250) + PDU (250) + host liquid loop and fans (718) + network cabling (170) + enclosure and mounting (657)
const COST_NODE_USD = 6950; // NVIDIA DGX Spark (128 GB / 4 TB), price listed on marketplace.nvidia.com, checked 2026-11-14
const COST_BATTERY_PACK_USD = 1200; // 5.12 kWh 48V 100Ah LiFePO4
const COST_PANEL_USD = 180; // 410W monocrystalline PERC
const COST_POWER_FIXED_USD = 2178; // MultiPlus-II (1,100) + SmartShunt (130) + Lynx Distributor (90) + protection, cabling, 12 V supply and telemetry bridge (858)
const COST_MPPT_USD = 600; // Victron SmartSolar MPPT 250/70-Tr
// One MPPT 250/70 takes two strings of four panels (4S2P): a third parallel string would exceed
// its 35 A PV short-circuit limit with 14 A panels, so every further 8 panels add a controller.
const PANELS_PER_MPPT = 8;

// ROI model
const ROI_YEARS = 3;
const MONTHS_PER_YEAR = 12;
const DAYS_PER_YEAR = 365;
// The ROI model is what a customer pays (COMMERCIALIZATION.md), not the build cost.
const SMDC_CAPEX_PER_UNIT_USD = 31500; // SMDC-Core-9U turnkey list price
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

  const mpptCount = Math.max(1, Math.ceil(panels / PANELS_PER_MPPT));
  const costs = {
    base: COST_BASE_USD,
    compute: nodes * COST_NODE_USD,
    battery: batPacks * COST_BATTERY_PACK_USD,
    solar: panels * COST_PANEL_USD,
    powerConversion: COST_POWER_FIXED_USD + mpptCount * COST_MPPT_USD,
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
    mpptCount,
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

  // Signed on purpose: when the comparison costs less than the SMDC, the saving and the
  // ROI are negative and are shown as such, never clamped to zero.
  const netSavings = comparisonTotal - smdcTotal;
  const monthlyNetSavings = cloudMonthly * nodeCount + gridEnergyMonthly - smdcOpexMonthly;
  // No payback exists when the monthly saving is zero or negative.
  const paybackMonths = monthlyNetSavings > 0 ? totalCapex / monthlyNetSavings : null;
  const paybackWithinHorizon = paybackMonths !== null && paybackMonths <= months;
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
    monthlyNetSavings,
    paybackMonths,
    paybackWithinHorizon,
    negative: netSavings < 0,
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
  // U+2212 minus sign, so a negative amount cannot be read as a hyphenated label.
  const signedUsd = (v) => `${v < 0 ? '\u2212' : ''}$${fmtInt(Math.abs(v))}`;
  const signedPct = (v) => `${v < 0 ? '\u2212' : ''}${Math.abs(v).toFixed(1)}%`;
  let payback;
  if (r.paybackMonths === null) payback = 'Not reached';
  else if (!r.paybackWithinHorizon) payback = `${r.paybackMonths.toFixed(1)} Months (beyond ${r.years} yr)`;
  else payback = `${r.paybackMonths.toFixed(1)} Months`;
  let verdict;
  if (r.negative) {
    verdict =
      r.paybackMonths === null
        ? `\u26A0 Negative return: the SMDC costs $${fmtInt(-r.netSavings)} more than cloud + grid energy over ${r.years} yr; payback is not reached within the model horizon (the monthly saving is zero or negative)`
        : `\u26A0 Negative return over ${r.years} yr: the SMDC costs $${fmtInt(-r.netSavings)} more than cloud + grid energy; payback falls after the ${r.years}-year model horizon`;
  } else {
    verdict = `\u2713 The SMDC costs $${fmtInt(r.netSavings)} less than cloud + grid energy over ${r.years} yr`;
  }
  return {
    negative: r.negative,
    payback,
    paybackReached: r.paybackWithinHorizon,
    savings: signedUsd(r.netSavings),
    roi: signedPct(r.roiPct),
    verdict,
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
      ['Power Conversion', `Victron MultiPlus-II 48/3000 + ${r.mpptCount}x SmartSolar MPPT 250/70 + SmartShunt + Lynx Distributor + Fuses, Cabling, PV Isolator, Earthing, 12V Supply & Telemetry Bridge`, 1, `$${fmtInt(r.costs.powerConversion)}`],
      ['Chassis, Host & Network', '9U 19" Rack & Shelf + Host Server (Board, CPU, RAM, Chassis & PSU) & NVMe Storage + 10GbE Switch + PDU + Host Liquid Loop & Fans', 1, `$${fmtInt(r.costs.base)}`],
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
  setText('roi-res-verdict', out.verdict);

  // A negative return is shown in the warning colour, not in the colours of a saving.
  const WARN = 'var(--accent-rose)';
  const setColor = (id, color) => {
    const el = document.getElementById(id);
    if (el) el.style.color = color;
  };
  setColor('roi-res-payback', out.paybackReached ? 'var(--accent-emerald)' : WARN);
  setColor('roi-res-savings', out.negative ? WARN : 'var(--accent-sky)');
  setColor('roi-res-roi', out.negative ? WARN : 'var(--accent-amber)');
  setColor('roi-res-verdict', out.negative ? WARN : 'var(--accent-emerald)');

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
      ['Result', out.verdict],
      ['Monthly Net Saving', `${r.monthlyNetSavings < 0 ? '\u2212' : ''}$${fmtInt(Math.abs(r.monthlyNetSavings))}`],
      [`SMDC ${r.years}-Year Cost (list price + control plane subscription)`, `$${fmtInt(r.smdcTotal)}`],
      [`Cloud ${r.years}-Year Spend`, `$${fmtInt(r.cloudTotal)}`],
      [`Grid Energy Displaced by Solar (${r.years} yr)`, `$${fmtInt(r.gridEnergyTotal)}`],
      ['Grid / Diesel Electricity Rate', `$${input.gridRate.toFixed(2)} / kWh`],
      ['Solar Energy Displacing Grid (per node)', `${r.displacedKwhPerDay.toFixed(1)} kWh/day`],
      ['CO2 Avoided per Year', `${r.co2TonnesPerYear.toFixed(1)} t (at ${GRID_CO2_KG_PER_KWH} kg CO2/kWh)`],
      ['Data Residency (model assumption)', 'On-premise; no cloud service in the modelled stack'],
      ['Solar Coverage of Reference Rack Load', `${Math.floor(r.solarCoveragePct)}%`],
    ],
    'smdc_investor_tco_model.csv'
  );
  showToast('📊 Investor TCO Model CSV Exported');
}

// --- 3. Simulated smdc Terminal (demo only) ---
// Nothing here talks to a node: the browser cannot run the smdc CLI. Every block below is
// canned, illustrative output and is labelled as such. No audit result, compliance score or
// "verified" status is ever printed, because nothing was checked.
const TERMINAL_BANNER =
  'Sovereign Mini Datacenter demo terminal (simulated; not connected to a node).\n';
const DEMO_TAG = '<span class="term-line-amber">[DEMO: illustrative values, not read from a node]</span>';

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
    print(`Simulated commands (canned sample output; run the real <span class="term-line-cyan">smdc</span> CLI on a node for actual data):
  • <span class="term-line-cyan">smdc status</span>             Sample of the status summary layout
  • <span class="term-line-cyan">smdc economy wallet</span>     Sample of the node wallet view
  • <span class="term-line-cyan">smdc economy market</span>     Sample of the solar-aware price quote
  • <span class="term-line-cyan">smdc space passes</span>       Sample of the simulated contact-pass table
  • <span class="term-line-cyan">smdc space send</span>         Plays the bundle-transmission animation
  • <span class="term-line-cyan">smdc audit</span>              Explains the real host hardening audit (prints no results here)
  • <span class="term-line-cyan">smdc mesh</span>               Reference three-site topology (design example)
  • <span class="term-line-cyan">clear</span>                   Clear terminal screen`);
  } else if (lower === 'clear') {
    screen.textContent = TERMINAL_BANNER;
  } else if (lower.includes('status')) {
    print(`${DEMO_TAG}
<span class="term-line-cyan">[SYSTEM STATUS: SAMPLE]</span>
  Solar PV:       2,660 W (3,280 W array)
  LiFePO4 Bank:   88.5% (52.8V, 10.24 kWh)
  Coolant Loop:   31.2°C (host loop, 1x 360mm radiator)
  Space Link:     simulated orbit model (no radio attached)
  Load Shedding:  L0`);
  } else if (lower.includes('economy') && lower.includes('wallet')) {
    print(`${DEMO_TAG}
<span class="term-line-purple">[NODE WALLET: SAMPLE]</span>
  Address (Ed25519):   sov_&lt;sample-address&gt;
  Address (ML-DSA-87): sov_pqc_&lt;sample-address&gt;
  Balance:             1,000.00 test credits (minted locally with --mint; no monetary value)`);
  } else if (lower.includes('economy') && lower.includes('market')) {
    print(`${DEMO_TAG}
<span class="term-line-amber">[PRICE QUOTE: SAMPLE]</span>
  Solar Harvest:  2,660 W (above the 1,000 W surplus threshold)
  Battery SoC:    88.5% (above the 75% surplus threshold)
  Solar Discount: 50.0% (surplus-solar rate)
  LLM Inference:  0.0025 credits / 1k tokens
  Space DTN Relay:0.0050 credits / MB`);
  } else if (lower.includes('space') && lower.includes('passes')) {
    print(`${DEMO_TAG}
<span class="term-line-cyan">[CONTACT PASSES: SAMPLE]</span>
  1. SOVEREIGN-LEO-1  AOS: +02m 14s  Max EL: 68.4°  Duration: 8m 42s
  2. STARLINK-RELAY   AOS: +48m 10s  Max EL: 42.1°  Duration: 6m 15s
  The real command predicts passes from a simulated orbit model; it tracks no real satellite.`);
  } else if (lower.includes('space') && lower.includes('send')) {
    triggerSpaceTransmission();
    print(`${DEMO_TAG}
<span class="term-line-accent">[DTN BUNDLE: ANIMATION ONLY]</span>
  Nothing was transmitted. On a node, <span class="term-line-cyan">smdc space send</span> stores a BPv7 bundle in the
  local store-and-forward spool; delivery needs a radio link, which this project only simulates.`);
  } else if (lower.includes('audit')) {
    print(`<span class="term-line-amber">[DEMO: NO AUDIT WAS RUN]</span>
  This browser terminal is a simulation. It cannot inspect a machine, so it prints
  no check results and no compliance score.
  The real audit is <span class="term-line-cyan">smdc audit</span>, run on a Linux node. It executes
  software/security/audit.sh (kernel hardening sysctls, host firewall, Docker socket
  exposure, .env secrets permissions and placeholders) and reports each check for
  that host. On other platforms it reports that nothing was checked.`);
  } else if (lower.includes('mesh')) {
    print(`${DEMO_TAG}
<span class="term-line-cyan">[REFERENCE TOPOLOGY: DESIGN EXAMPLE]</span>
  smdc-node-01  Athens core          100.64.0.1
  smdc-node-02  Alpine off-grid      100.64.0.2
  smdc-node-03  Aegean island        100.64.0.3
  These peers are an illustrative three-site design; none is discovered or probed.`);
  } else {
    printText(`Command not recognized: '${cmd}'. Type 'help' for options.`, null, '#ef4444');
  }

  screen.scrollTop = screen.scrollHeight;
}
