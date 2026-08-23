/**
 * Sovereign Mini Datacenter — Sizing Configurator, Investor ROI & Cyber Terminal
 */

const CalculatorManager = (function () {
  function init() {
    updateSizingCalculator();
    updateInvestorCalculator();
  }

  // --- 1. Sizing Configurator ---
  function updateSizingCalculator() {
    const nodes = parseInt(document.getElementById('calc-nodes')?.value || 2);
    const hours = parseInt(document.getElementById('calc-hours')?.value || 12);
    const batPacks = parseInt(document.getElementById('calc-battery')?.value || 2);
    const panels = parseInt(document.getElementById('calc-solar')?.value || 4);

    const valNodes = document.getElementById('calc-val-nodes');
    const valHours = document.getElementById('calc-val-hours');
    const valBat = document.getElementById('calc-val-battery');
    const valSolar = document.getElementById('calc-val-solar');

    if (valNodes) valNodes.textContent = `${nodes} Node${nodes > 1 ? 's' : ''} (${nodes * 275} TOPS)`;
    if (valHours) valHours.textContent = `${hours} hrs/day`;
    if (valBat) valBat.textContent = `${(batPacks * 5.12).toFixed(2)} kWh (${batPacks}x 48V)`;
    if (valSolar) valSolar.textContent = `${panels * 410}W (${panels}x 410W)`;

    // Energy Math
    const idlePower = 180 + nodes * 45; // W
    const activePower = 180 + nodes * 160; // W
    const dailyDrawKwh = ((idlePower * (24 - hours) + activePower * hours) / 1000).toFixed(1);
    const dailyYieldKwh = ((panels * 410 * 5.2 * 0.85) / 1000).toFixed(1); // 5.2 Peak Sun Hours
    const totalCapacityKwh = batPacks * 5.12 * 0.85; // 85% usable DoD
    const avgPowerKw = parseFloat(dailyDrawKwh) / 24;
    const autonomyHours = (totalCapacityKwh / avgPowerKw).toFixed(1);

    // BOM Cost Estimator
    const baseCost = 2800; // Rack, PDU, switch, liquid cooling, wiring
    const computeCost = nodes * 1650; // Jetson Orin AGX 64GB
    const batteryCost = batPacks * 1350; // 5.12 kWh LiFePO4
    const solarCost = panels * 210; // 410W monocrystalline + racking
    const inverterCost = 1450; // Victron MultiPlus + MPPT
    const totalBOM = baseCost + computeCost + batteryCost + solarCost + inverterCost;

    const resDraw = document.getElementById('calc-res-draw');
    const resYield = document.getElementById('calc-res-yield');
    const resAutonomy = document.getElementById('calc-res-autonomy');
    const resCost = document.getElementById('calc-res-cost');

    if (resDraw) resDraw.textContent = `${dailyDrawKwh} kWh`;
    if (resYield) resYield.textContent = `${dailyYieldKwh} kWh`;
    if (resAutonomy) resAutonomy.textContent = `${autonomyHours} hrs`;
    if (resCost) resCost.textContent = `$${totalBOM.toLocaleString()}`;
  }

  function exportBOMCSV() {
    const nodes = document.getElementById('calc-nodes')?.value || 2;
    const batPacks = document.getElementById('calc-battery')?.value || 2;
    const panels = document.getElementById('calc-solar')?.value || 4;
    const cost = document.getElementById('calc-res-cost')?.textContent || '$10,600';

    const csvContent =
      'data:text/csv;charset=utf-8,' +
      'Category,Component,Qty,Est. Cost\n' +
      `AI Compute,NVIDIA Jetson Orin AGX 64GB,${nodes},"$${nodes * 1650}"\n` +
      `Energy Storage,LiFePO4 48V 100Ah 5.12kWh Module,${batPacks},"$${batPacks * 1350}"\n` +
      `Solar PV,410W Monocrystalline Bifacial Panel,${panels},"$${panels * 210}"\n` +
      'Power Conversion,Victron MultiPlus-II 48/3000 + MPPT 150/35,1,"$1450"\n' +
      'Chassis & Network,9U 19" Rack + 10GbE Switch + APC PDU + Cooling,1,"$2800"\n' +
      `Total,Sovereign Mini Datacenter Complete BOM,,${cost}\n`;

    const encodedUri = encodeURI(csvContent);
    const link = document.createElement('a');
    link.setAttribute('href', encodedUri);
    link.setAttribute('download', 'smdc_bill_of_materials.csv');
    document.body.appendChild(link);
    link.click();
    link.remove();
    HUDManager.showToast('📥 Bill of Materials CSV Exported');
  }

  // --- 2. Investor ROI Simulator ---
  function updateInvestorCalculator() {
    const cloudMonthly = parseFloat(document.getElementById('roi-cloud-spend')?.value || 2500);
    const gridRate = parseFloat(document.getElementById('roi-grid-rate')?.value || 0.35);
    const nodeCount = parseInt(document.getElementById('roi-nodes')?.value || 1);

    const valCloud = document.getElementById('roi-val-cloud');
    const valGrid = document.getElementById('roi-val-grid');
    const valNodes = document.getElementById('roi-val-nodes');

    if (valCloud) valCloud.textContent = `$${cloudMonthly.toLocaleString()} / mo`;
    if (valGrid) valGrid.textContent = `$${gridRate.toFixed(2)} / kWh`;
    if (valNodes) valNodes.textContent = `${nodeCount} Node${nodeCount > 1 ? 's' : ''} (Turnkey)`;

    // Financial Analysis
    const smdcCapexPerNode = 10600;
    const totalCapex = smdcCapexPerNode * nodeCount;
    const smdcOpexMonthly = nodeCount * 50; // Maintenance reserve
    const smdc3YrCost = totalCapex + smdcOpexMonthly * 36;

    const cloud3YrCost = cloudMonthly * 36 * nodeCount;
    const netSavings3Yr = Math.max(0, cloud3YrCost - smdc3YrCost);
    const monthlyNetSavings = (cloudMonthly - smdcOpexMonthly) * nodeCount;
    const paybackMonths = monthlyNetSavings > 0 ? (totalCapex / monthlyNetSavings).toFixed(1) : 'N/A';
    const roiPercentage = totalCapex > 0 ? ((netSavings3Yr / totalCapex) * 100).toFixed(1) : '0.0';
    const co2TonsPerYear = (nodeCount * 365 * 7.5 * 0.42 * 0.001 * 2.8).toFixed(1);

    const resPayback = document.getElementById('roi-res-payback');
    const resSavings = document.getElementById('roi-res-savings');
    const resRoi = document.getElementById('roi-res-roi');
    const resCo2 = document.getElementById('roi-res-co2');

    if (resPayback) resPayback.textContent = `${paybackMonths} Months`;
    if (resSavings) resSavings.textContent = `$${netSavings3Yr.toLocaleString()}`;
    if (resRoi) resRoi.textContent = `${roiPercentage}%`;
    if (resCo2) resCo2.textContent = `${co2TonsPerYear} Tons`;

    // Visual Bar
    const totalCompare = smdc3YrCost + cloud3YrCost;
    const smdcPct = Math.round((smdc3YrCost / totalCompare) * 100);
    const cloudPct = 100 - smdcPct;

    const barSmdc = document.getElementById('roi-bar-smdc');
    const barCloud = document.getElementById('roi-bar-cloud');
    const barValSmdc = document.getElementById('roi-bar-val-smdc');
    const barValCloud = document.getElementById('roi-bar-val-cloud');

    if (barSmdc) barSmdc.style.width = `${smdcPct}%`;
    if (barCloud) barCloud.style.width = `${cloudPct}%`;
    if (barValSmdc) barValSmdc.textContent = `$${(smdc3YrCost / 1000).toFixed(1)}k`;
    if (barValCloud) barValCloud.textContent = `$${(cloud3YrCost / 1000).toFixed(1)}k`;
  }

  function exportInvestorTCOCSV() {
    const savings = document.getElementById('roi-res-savings')?.textContent || '$88,336';
    const payback = document.getElementById('roi-res-payback')?.textContent || '5.1 Months';
    const roi = document.getElementById('roi-res-roi')?.textContent || '278.9%';

    const csvContent =
      'data:text/csv;charset=utf-8,' +
      'Metric,Value\n' +
      `Payback Period,${payback}\n` +
      `3-Year Net Savings,${savings}\n` +
      `3-Year ROI,${roi}\n` +
      'Data Sovereignty Compliance,100% On-Premise\n' +
      'Power Grid Dependency,0% (Off-Grid Solar Backed)\n';

    const encodedUri = encodeURI(csvContent);
    const link = document.createElement('a');
    link.setAttribute('href', encodedUri);
    link.setAttribute('download', 'smdc_investor_tco_model.csv');
    document.body.appendChild(link);
    link.click();
    link.remove();
    HUDManager.showToast('📊 Investor TCO Model CSV Exported');
  }

  // --- 3. Interactive Cyber Terminal (smdc) ---
  function handleTerminalInput(e) {
    if (e.key !== 'Enter') return;
    const input = document.getElementById('term-cmd-input');
    if (!input) return;
    const cmd = input.value.trim();
    input.value = '';

    const screen = document.getElementById('term-output');
    if (!screen) return;

    screen.innerHTML += `\n<span class="term-line-accent">smdc$ ${cmd}</span>\n`;

    const lower = cmd.toLowerCase();
    if (lower === 'help') {
      screen.innerHTML += `Available SMDC CLI Commands:
  • <span class="term-line-cyan">smdc status</span>             Inspect container health, solar harvest, and space link
  • <span class="term-line-cyan">smdc economy wallet</span>     Display node Post-Quantum & Ed25519 wallet balances
  • <span class="term-line-cyan">smdc economy market</span>     Query solar-aware compute & relay price quotes
  • <span class="term-line-cyan">smdc space passes</span>       Predict upcoming LEO satellite contact passes
  • <span class="term-line-cyan">smdc space send</span>         Queue DTN BPv7 bundle for space transmission
  • <span class="term-line-cyan">smdc audit</span>              Run NIST Zero Trust & PQC compliance audit
  • <span class="term-line-cyan">smdc mesh</span>               Inspect multi-node WireGuard mesh peers
  • <span class="term-line-cyan">clear</span>                   Clear terminal screen`;
    } else if (lower === 'clear') {
      screen.innerHTML = 'Sovereign Mini Datacenter Interactive Terminal (smdc v1.3.0).\n';
    } else if (lower.includes('status')) {
      screen.innerHTML += `<span class="term-line-cyan">[SYSTEM STATUS]</span>
  Solar PV:       1,330 W (150V MPPT Peak Nominal)
  LiFePO4 Bank:   88.5% (52.8V, 10.24 kWh Reserve)
  Coolant Loop:   31.2°C (Dual 360mm Rads, Flow: 4.2 L/min)
  AI Inference:   Ollama nomic-embed-text (0.42ms/token)
  Space Link:     IN CONTACT (57.1° EL, SNR: 14.2 dB)
  Load Shedding:  L0 (Full Compute Operations Authorized)`;
    } else if (lower.includes('economy') && lower.includes('wallet')) {
      screen.innerHTML += `<span class="term-line-purple">[NODE WALLET]</span>
  Address (Ed25519):   sov_89f7a24c8b910e12d
  Address (ML-DSA-87): sov_pqc_99e14a72d3f901bc
  Balance:             1,000.00 SOV Credits
  Active Channels:     2 Open Channels (Streaming Compute)`;
    } else if (lower.includes('economy') && lower.includes('market')) {
      screen.innerHTML += `<span class="term-line-amber">[MARKET PRICING ORACLE]</span>
  Solar Harvest:  1,330 W (>1,000 W Threshold)
  Battery SoC:    88.5% (>75% Threshold)
  Solar Discount: 50.0% OFF (Surplus Solar Compute Rate)
  LLM Inference:  0.0025 SOV / 1k tokens
  Space DTN Relay:0.0050 SOV / MB`;
    } else if (lower.includes('space') && lower.includes('passes')) {
      screen.innerHTML += `<span class="term-line-cyan">[UPCOMING CONTACT PASSES]</span>
  1. SOVEREIGN-LEO-1  AOS: +02m 14s  Max EL: 68.4°  Duration: 8m 42s
  2. STARLINK-RELAY   AOS: +48m 10s  Max EL: 42.1°  Duration: 6m 15s`;
    } else if (lower.includes('space') && lower.includes('send')) {
      SpaceManager.triggerSpaceTransmission();
      screen.innerHTML += `<span class="term-line-accent">[DTN BPv7 BUNDLE TRANSMITTED]</span>
  Bundle ID:      dtn://sovereign-node-alpha/telemetry/0x4a92
  Payload:        Telemetry snapshot (4.8 KB)
  Destination:    dtn://ground-station-alpha.earth/relay
  Status:         SPOOLED & TRANSMITTING VIA PHASED ARRAY 🚀`;
    } else if (lower.includes('audit')) {
      screen.innerHTML += `<span class="term-line-accent">[COMPLIANCE & SECURITY AUDIT]</span>
  NIST SP 800-207 Zero Trust:  PASSED (Mutual WireGuard + PQC)
  NIST FIPS 203 ML-KEM-768:    ACTIVE (Key Encapsulation)
  NIST FIPS 204 ML-DSA-87:     ACTIVE (Digital Signatures)
  Overall Compliance Score:    100% GREEN (CIS Hardened)`;
    } else {
      screen.innerHTML += `<span style="color:#ef4444;">Command not recognized: '${cmd}'. Type 'help' for options.</span>`;
    }

    screen.scrollTop = screen.scrollHeight;
  }

  return {
    init,
    updateSizingCalculator,
    exportBOMCSV,
    updateInvestorCalculator,
    exportInvestorTCOCSV,
    handleTerminalInput,
  };
})();
