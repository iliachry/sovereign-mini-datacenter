/**
 * Sovereign Mini Datacenter — 3D Infrastructure Geometries & Assembly Builder
 *
 * @module models
 */

import * as THREE from 'three';
import TWEEN from 'tween';
import { get as getMaterial } from './materials.js';

let mainGroup, rackGroup, computeGroup, powerGroup, coolingGroup, solarGroup, spaceGroup;
let doorPivot, doorOpen = false;
let interactiveObjects = [];
let fanBlades = [];

/**
 * Late-bound callback to get fan RPM from HUD.
 * Set by app.js to break the circular models ↔ hud dependency.
 * @type {function(): number}
 */
let _getFanRpmCb = () => 2400;

export function setFanRpmCallback(cb) {
  _getFanRpmCb = cb;
}

const COMPONENT_DATA = {
  space_antenna: {
    name: 'Motorized Phased Array Space Terminal',
    badge: 'SPACE / DTN',
    desc: 'Dual-axis electronically steerable beamforming phased array terminal executing RFC 9171 Delay-Tolerant Networking (BPv7) bundle reconciliations with passing LEO/MEO satellite constellations in real time.',
    specs: { 'Protocol': 'RFC 9171 BPv7', 'Frequency': 'S-Band / Ku-Band', 'Throughput': '25 Mbps Downlink', 'Tracking': 'SGP4 Auto-Track', 'EIRP': '42.5 dBW' },
  },
  dgx1: {
    name: 'NVIDIA Jetson Orin AGX AI Core 1',
    badge: 'AI COMPUTE',
    desc: 'Primary edge accelerator delivering 275 TOPS INT8 AI compute with 64GB unified memory, hosting local Ollama LLMs and Qdrant semantic vector embeddings.',
    specs: { 'Compute': '275 TOPS INT8', 'Memory': '64GB 256-bit LPDDR5', 'Power': '60W TDP', 'Cooling': 'EK Copper Block', 'Thermal': '42.4°C' },
  },
  dgx2: {
    name: 'NVIDIA Jetson Orin AGX AI Core 2',
    badge: 'AI COMPUTE',
    desc: 'Secondary parallel AI accelerator coupled over high-speed NVLink/10GbE fabric for distributed multi-agent LLM inference and GitLab code reviews.',
    specs: { 'Compute': '275 TOPS INT8', 'Memory': '64GB 256-bit LPDDR5', 'Power': '60W TDP', 'Cooling': 'EK Copper Block', 'Thermal': '43.1°C' },
  },
  battery1: {
    name: 'LiFePO4 Battery Pack 1 (48V 100Ah)',
    badge: 'ENERGY / 48V',
    desc: '48V 100Ah (5.12 kWh) rackmount lithium iron phosphate battery module with integrated smart RS485 Modbus BMS and automated cell balancing.',
    specs: { 'Capacity': '5.12 kWh', 'Voltage': '51.2V Nom.', 'Max Discharge': '100A (5 kW)', 'Cycle Life': '6,000+ @ 80% DoD', 'SoC': '88.5%' },
  },
  battery2: {
    name: 'LiFePO4 Battery Pack 2 (48V 100Ah)',
    badge: 'ENERGY / 48V',
    desc: 'Secondary 5.12 kWh battery pack connected in parallel on the common 48V DC busbar, giving 10.24 kWh total autonomous energy storage.',
    specs: { 'Capacity': '5.12 kWh', 'Voltage': '51.2V Nom.', 'Max Discharge': '100A (5 kW)', 'Cycle Life': '6,000+ @ 80% DoD', 'SoC': '88.5%' },
  },
  switch: {
    name: 'MikroTik CRS309-1G-8S+IN',
    badge: 'NETWORKING',
    desc: 'Enterprise 10-Gigabit core switch with 8x SFP+ cages, hardware wire-speed L2/L3 routing offload, and redundant power inputs.',
    specs: { 'Ports': '8x 10GbE SFP+', 'Switching Capacity': '162 Gbps', 'OS': 'RouterOS / SwOS', 'Power': '18W', 'Latency': '< 1.2 µs' },
  },
  server: {
    name: 'AMD EPYC 4004 System Host',
    badge: 'CORE HOST',
    desc: 'Host coordinator with 64GB ECC DDR5, dual Samsung 990 PRO 4TB NVMe SSDs in RAID-1, and local ZFS backup spool.',
    specs: { 'CPU': 'AMD EPYC 4004', 'RAM': '64GB ECC DDR5', 'NVMe': '8TB PCIe 4.0', 'OS': 'Talos Linux / Ubuntu 24.04', 'Temp': '37.8°C' },
  },
  pdu: {
    name: 'APC Metered Rack PDU (1U)',
    badge: 'POWER DISTRIB',
    desc: '1U intelligent power distribution unit with per-port telemetry, remote power cycling, and digital current monitoring.',
    specs: { 'Outlets': '8x IEC C13', 'Voltage': '230V AC', 'Max Current': '16A', 'Telemetry': 'Prometheus Exporter', 'Draw': '340W' },
  },
  inverter: {
    name: 'Victron MultiPlus-II 48/3000',
    badge: 'HYBRID INVERTER',
    desc: '3,000VA pure sine wave hybrid inverter/charger with seamless 20ms UPS grid transfer and DC-AC power management.',
    specs: { 'Output': '3,000VA (2.4kW)', 'DC Input': '48V (38-66V)', 'Efficiency': '95%', 'Transfer': '< 20ms', 'Mode': 'Off-Grid Inverting' },
  },
  mppt: {
    name: 'Victron SmartSolar MPPT 150/35',
    badge: 'SOLAR MPPT',
    desc: 'Ultra-fast Maximum Power Point Tracking (MPPT) solar charge controller converting high-voltage PV input to 48V battery bank.',
    specs: { 'Max PV Voltage': '150V', 'Max Charge Current': '35A', 'Peak Efficiency': '98%', 'Comms': 'VE.Direct Serial', 'Power': '1,330W Harvest' },
  },
  solar_panels: {
    name: 'Bifacial Solar Photovoltaic Array (1.64 kWp)',
    badge: 'SOLAR PV',
    desc: '4x 410W high-efficiency monocrystalline bifacial solar panels producing up to 8.6 kWh daily energy yield in Southern European latitudes.',
    specs: { 'Rating': '1,640W Peak', 'Cells': 'Monocrystalline N-Type', 'Bifacial Gain': '+15%', 'Daily Yield': '8.6 kWh / day', 'Area': '7.8 m²' },
  },
  uav_relay: {
    name: 'Autonomous 5G UAV Mesh Relay (SA-PPO)',
    badge: '5G / SA-PPO',
    desc: 'Autonomous aerial base-station optimizing 3D positioning via Scene-Aware PPO (SA-PPO) and Sionna ray-tracing to provide dynamic URLLC & eMBB coverage for disadvantaged ground receivers.',
    specs: { 'AI Policy': 'Scene-Aware PPO', 'Carrier': '3.5 GHz (100 MHz)', 'Latency': '< 1ms URLLC', 'SLA Consensus': 'PoS/dBFT 6/7 Sigs', 'Rx1 Gain': '+79.6% Capacity' },
  },
  rf_heatmap: {
    name: 'Volumetric 3D SINR Heatmap Grid',
    badge: 'DIGITAL TWIN',
    desc: 'Real-time ray-traced electromagnetic signal propagation field calibrated by 180 heterogeneous IoT ground sensors and Kriging spatial interpolation.',
    specs: { 'Resolution': '10x10 Spatial Grid', 'Reflection Depth': '5th-Order Multipath', 'Threshold': '-15 dB Min SINR', 'Update Rate': '12 ms' },
  },
  rx1: {
    name: 'Ground Receiver 1 (Disadvantaged Urban Canyon)',
    badge: 'IoT / 5G UE',
    desc: 'Severely shadowed user equipment located in an urban street canyon, achieving +79.6% capacity gain under SA-PPO positioning optimization.',
    specs: { 'Location': 'Urban Canyon', 'Initial SINR': '-12.0 dB', 'Optimized SINR': '-9.7 dB', 'Capacity': '0.187 bps/Hz', 'Gain': '+79.6%' },
  },
  rx2: {
    name: 'Ground Receiver 2 (Suburban Edge)',
    badge: 'IoT / 5G UE',
    desc: 'Intermediate user equipment receiving secondary multipath reflections, achieving +49.9% capacity boost under SA-PPO.',
    specs: { 'Location': 'Suburban Edge', 'Initial SINR': '-9.4 dB', 'Optimized SINR': '-8.1 dB', 'Capacity': '0.256 bps/Hz', 'Gain': '+49.9%' },
  },
  rx3: {
    name: 'Ground Receiver 3 (Line-of-Sight)',
    badge: 'IoT / 5G UE',
    desc: 'Direct line-of-sight user equipment with minimal obstruction, achieving +27.0% capacity gain.',
    specs: { 'Location': 'Open Line-of-Sight', 'Initial SINR': '-5.5 dB', 'Optimized SINR': '-5.1 dB', 'Capacity': '0.489 bps/Hz', 'Gain': '+27.0%' },
  },
};

function registerInteractive(mesh, compKey) {
  mesh.userData.componentKey = compKey;
  interactiveObjects.push(mesh);
}

export function buildAll(scene) {
  mainGroup = new THREE.Group();
  scene.add(mainGroup);

  rackGroup = new THREE.Group();
  computeGroup = new THREE.Group();
  powerGroup = new THREE.Group();
  coolingGroup = new THREE.Group();
  solarGroup = new THREE.Group();
  spaceGroup = new THREE.Group();
  metaverseGroup = new THREE.Group();

  mainGroup.add(rackGroup);
  mainGroup.add(computeGroup);
  mainGroup.add(powerGroup);
  mainGroup.add(coolingGroup);
  mainGroup.add(solarGroup);
  mainGroup.add(spaceGroup);
  mainGroup.add(metaverseGroup);

  buildRackEnclosure();
  buildRackMountComponents();
  buildCoolingLoop();
  buildPowerWall();
  buildSolarArray();
  buildSpaceTerminal();
  buildMetaverseSystem();
}

function buildRackEnclosure() {
  const W = 540, D = 550, H = 400.05, t = 3.0;
  const alumMat = getMaterial('aluminum');
  const railMat = getMaterial('steelRail');

  // 4 Corner Extruded Aluminum Pillars
  const colGeom = new THREE.BoxGeometry(26, H, 26);
  const colPositions = [
    [-W / 2 + 13, 0, -D / 2 + 13],
    [W / 2 - 13, 0, -D / 2 + 13],
    [-W / 2 + 13, 0, D / 2 - 13],
    [W / 2 - 13, 0, D / 2 - 13],
  ];
  colPositions.forEach((p) => {
    const col = new THREE.Mesh(colGeom, alumMat);
    col.position.set(...p);
    col.castShadow = true;
    rackGroup.add(col);
  });

  // Top & Bottom Solid Plates
  const plateGeom = new THREE.BoxGeometry(W, t, D);
  const topPlate = new THREE.Mesh(plateGeom, alumMat);
  topPlate.position.set(0, H / 2, 0);
  rackGroup.add(topPlate);

  const botPlate = new THREE.Mesh(plateGeom, alumMat);
  botPlate.position.set(0, -H / 2, 0);
  botPlate.receiveShadow = true;
  rackGroup.add(botPlate);

  // 4 Vertical EIA-310-D Standard 19" Steel Rails
  const railGeom = new THREE.BoxGeometry(18, H - 12, 18);
  const railPositions = [
    [-241.3 + 9, 0, -D / 2 + 35],
    [241.3 - 9, 0, -D / 2 + 35],
    [-241.3 + 9, 0, D / 2 - 45],
    [241.3 - 9, 0, D / 2 - 45],
  ];
  railPositions.forEach((p) => {
    const rail = new THREE.Mesh(railGeom, railMat);
    rail.position.set(...p);
    rail.castShadow = true;
    rackGroup.add(rail);
  });

  // Side Panels
  const sideGeom = new THREE.BoxGeometry(t, H - 16, D - 40);
  const leftSide = new THREE.Mesh(sideGeom, alumMat);
  leftSide.position.set(-W / 2, 0, 0);
  rackGroup.add(leftSide);

  const rightSide = new THREE.Mesh(sideGeom, alumMat);
  rightSide.position.set(W / 2, 0, 0);
  rackGroup.add(rightSide);

  // Tinted Hinged Front Door with Handle
  doorPivot = new THREE.Group();
  doorPivot.position.set(-W / 2 + 10, 0, D / 2);

  const doorMesh = new THREE.Mesh(
    new THREE.BoxGeometry(W - 20, H - 16, 6),
    getMaterial('glassDoor')
  );
  doorMesh.position.set(W / 2 - 10, 0, 0);
  doorPivot.add(doorMesh);

  const handle = new THREE.Mesh(
    new THREE.CylinderGeometry(4, 4, 80),
    getMaterial('aluminum')
  );
  handle.position.set(W - 35, 0, 8);
  doorPivot.add(handle);

  rackGroup.add(doorPivot);
}

function buildRackMountComponents() {
  const chassisMat = getMaterial('chassisBody');
  const uHeight = 44.45;
  const baseY = 200 - uHeight / 2 - 10;
  const compW = 482.6;
  const compD = 440;

  // 1U MikroTik 10GbE Switch (Slot 1U)
  const swGroup = new THREE.Group();
  swGroup.position.set(0, baseY - 0 * uHeight, 0);
  const swBody = new THREE.Mesh(new THREE.BoxGeometry(compW, uHeight - 4, compD), chassisMat);
  swBody.castShadow = true;
  swGroup.add(swBody);

  // SFP+ Cages & LEDs
  for (let i = 0; i < 8; i++) {
    const cage = new THREE.Mesh(
      new THREE.BoxGeometry(14, 12, 4),
      getMaterial('aluminum')
    );
    cage.position.set(-160 + i * 22, 0, compD / 2 + 1);
    swGroup.add(cage);

    const led = new THREE.Mesh(
      new THREE.SphereGeometry(1.5, 8, 8),
      i % 2 === 0 ? getMaterial('ledGreen') : getMaterial('ledBlue')
    );
    led.position.set(-160 + i * 22, 8, compD / 2 + 2);
    swGroup.add(led);
  }
  computeGroup.add(swGroup);
  registerInteractive(swBody, 'switch');

  // 1U APC Metered PDU (Slot 2U)
  const pduGroup = new THREE.Group();
  pduGroup.position.set(0, baseY - 1 * uHeight, 0);
  const pduBody = new THREE.Mesh(new THREE.BoxGeometry(compW, uHeight - 4, compD), chassisMat);
  pduGroup.add(pduBody);

  const pduDisplay = new THREE.Mesh(
    new THREE.BoxGeometry(40, 14, 2),
    getMaterial('ledBlue')
  );
  pduDisplay.position.set(160, 0, compD / 2 + 1);
  pduGroup.add(pduDisplay);
  computeGroup.add(pduGroup);
  registerInteractive(pduBody, 'pdu');

  // 2U NVIDIA Jetson AGX / DGX Spark AI Compute Blade 1 (Slot 3U-4U)
  const dgx1Group = new THREE.Group();
  dgx1Group.position.set(0, baseY - 2.5 * uHeight, 0);
  const dgx1Body = new THREE.Mesh(new THREE.BoxGeometry(compW, uHeight * 2 - 4, compD), chassisMat);
  dgx1Group.add(dgx1Body);

  // Dual Gold Heatsinks & Coolant Blocks
  [-80, 80].forEach((x) => {
    const heatsink = new THREE.Mesh(
      new THREE.BoxGeometry(90, 20, 120),
      getMaterial('goldHeatsink')
    );
    heatsink.position.set(x, 10, 0);
    dgx1Group.add(heatsink);

    const block = new THREE.Mesh(
      new THREE.BoxGeometry(70, 8, 100),
      getMaterial('copperBlock')
    );
    block.position.set(x, 22, 0);
    dgx1Group.add(block);
  });

  const statusLed1 = new THREE.Mesh(
    new THREE.BoxGeometry(60, 8, 2),
    getMaterial('ledGreen')
  );
  statusLed1.position.set(0, 0, compD / 2 + 1);
  dgx1Group.add(statusLed1);

  computeGroup.add(dgx1Group);
  registerInteractive(dgx1Body, 'dgx1');

  // 1U AMD EPYC Host Server (Slot 5U)
  const srvGroup = new THREE.Group();
  srvGroup.position.set(0, baseY - 4 * uHeight, 0);
  const srvBody = new THREE.Mesh(new THREE.BoxGeometry(compW, uHeight - 4, compD), chassisMat);
  srvGroup.add(srvBody);

  // 4x NVMe Drive Trays
  for (let i = 0; i < 4; i++) {
    const drive = new THREE.Mesh(
      new THREE.BoxGeometry(32, 28, 4),
      getMaterial('aluminum')
    );
    drive.position.set(-140 + i * 40, 0, compD / 2 + 1);
    srvGroup.add(drive);
  }
  computeGroup.add(srvGroup);
  registerInteractive(srvBody, 'server');

  // 2U LiFePO4 Battery Pack 1 (Slot 6U-7U)
  const bat1Group = new THREE.Group();
  bat1Group.position.set(0, baseY - 5.5 * uHeight, 0);
  const bat1Body = new THREE.Mesh(
    new THREE.BoxGeometry(compW, uHeight * 2 - 4, compD),
    new THREE.MeshStandardMaterial({ color: 0x0f172a, metalness: 0.85, roughness: 0.25 })
  );
  bat1Group.add(bat1Body);

  const socBar1 = new THREE.Mesh(
    new THREE.BoxGeometry(50, 8, 2),
    getMaterial('ledGreen')
  );
  socBar1.position.set(-140, 0, compD / 2 + 1);
  bat1Group.add(socBar1);
  computeGroup.add(bat1Group);
  registerInteractive(bat1Body, 'battery1');

  // 2U LiFePO4 Battery Pack 2 (Slot 8U-9U)
  const bat2Group = new THREE.Group();
  bat2Group.position.set(0, baseY - 7.5 * uHeight, 0);
  const bat2Body = new THREE.Mesh(
    new THREE.BoxGeometry(compW, uHeight * 2 - 4, compD),
    new THREE.MeshStandardMaterial({ color: 0x0f172a, metalness: 0.85, roughness: 0.25 })
  );
  bat2Group.add(bat2Body);

  const socBar2 = new THREE.Mesh(
    new THREE.BoxGeometry(50, 8, 2),
    getMaterial('ledGreen')
  );
  socBar2.position.set(-140, 0, compD / 2 + 1);
  bat2Group.add(socBar2);
  computeGroup.add(bat2Group);
  registerInteractive(bat2Body, 'battery2');
}

function buildCoolingLoop() {
  const tubeMat = getMaterial('coolantTube');

  // Dual Glowing Coolant Feed Tubes
  const tube1 = new THREE.Mesh(new THREE.CylinderGeometry(5, 5, 280, 16), tubeMat);
  tube1.position.set(-180, 40, -120);
  coolingGroup.add(tube1);

  const tube2 = new THREE.Mesh(new THREE.CylinderGeometry(5, 5, 280, 16), tubeMat);
  tube2.position.set(180, 40, -120);
  coolingGroup.add(tube2);

  // Top Exhaust Fan Grilles
  [-120, 120].forEach((x) => {
    const fanRing = new THREE.Mesh(
      new THREE.TorusGeometry(45, 4, 16, 32),
      getMaterial('aluminum')
    );
    fanRing.position.set(x, 202, 0);
    fanRing.rotation.x = Math.PI / 2;
    coolingGroup.add(fanRing);

    const blades = new THREE.Mesh(
      new THREE.BoxGeometry(70, 2, 70),
      new THREE.MeshStandardMaterial({ color: 0x0f172a, metalness: 0.7 })
    );
    blades.position.set(x, 200, 0);
    fanBlades.push(blades);
    coolingGroup.add(blades);
  });
}

function buildPowerWall() {
  const wallGroup = new THREE.Group();
  wallGroup.position.set(430, 0, 0);

  // Structural Aluminum Backplate & Frame Support
  const plate = new THREE.Mesh(
    new THREE.BoxGeometry(16, 380, 360),
    new THREE.MeshStandardMaterial({ color: 0x1e293b, metalness: 0.8, roughness: 0.3 })
  );
  plate.position.set(0, 0, 0);
  plate.receiveShadow = true;
  wallGroup.add(plate);

  // Ground mount support legs
  [-140, 140].forEach((z) => {
    const leg = new THREE.Mesh(
      new THREE.BoxGeometry(40, 18, 14),
      getMaterial('aluminum')
    );
    leg.position.set(-10, -190, z);
    wallGroup.add(leg);
  });

  // Victron MultiPlus-II Hybrid Inverter
  const invBody = new THREE.Mesh(
    new THREE.BoxGeometry(70, 220, 150),
    getMaterial('victronBlue')
  );
  invBody.position.set(40, 50, -70);
  invBody.castShadow = true;
  wallGroup.add(invBody);
  registerInteractive(invBody, 'inverter');

  // Victron SmartSolar MPPT 150/35
  const mpptBody = new THREE.Mesh(
    new THREE.BoxGeometry(50, 150, 100),
    getMaterial('victronBlue')
  );
  mpptBody.position.set(30, 70, 85);
  mpptBody.castShadow = true;
  wallGroup.add(mpptBody);
  registerInteractive(mpptBody, 'mppt');

  // Lynx Power In DC Busbar
  const lynx = new THREE.Mesh(
    new THREE.BoxGeometry(40, 45, 220),
    new THREE.MeshStandardMaterial({ color: 0x0f172a, metalness: 0.85 })
  );
  lynx.position.set(25, -130, 20);
  wallGroup.add(lynx);

  // High-Voltage DC Interconnect Conduits
  const conduit = new THREE.Mesh(
    new THREE.CylinderGeometry(7, 7, 240, 16),
    new THREE.MeshStandardMaterial({ color: 0x334155, metalness: 0.9 })
  );
  conduit.rotation.z = Math.PI / 2;
  conduit.position.set(-110, -130, 0);
  wallGroup.add(conduit);

  powerGroup.add(wallGroup);
}

function buildSolarArray() {
  const arrayGroup = new THREE.Group();
  arrayGroup.position.set(-640, -30, 0);
  arrayGroup.rotation.z = 0.28; // Optimal PV tilt angle ~16°

  const frameMat = getMaterial('solarFrame');
  const cellMat = getMaterial('solarCells');

  for (let row = 0; row < 2; row++) {
    for (let col = 0; col < 2; col++) {
      const pGroup = new THREE.Group();
      pGroup.position.set(col * 210 - 105, 0, row * 330 - 165);

      const frame = new THREE.Mesh(new THREE.BoxGeometry(200, 8, 320), frameMat);
      frame.castShadow = true;
      pGroup.add(frame);

      const cells = new THREE.Mesh(new THREE.BoxGeometry(192, 2, 312), cellMat);
      cells.position.y = 4;
      pGroup.add(cells);

      arrayGroup.add(pGroup);
      registerInteractive(cells, 'solar');
    }
  }

  // Industrial Galvanized Steel Mounting Struts
  const strutGeom = new THREE.CylinderGeometry(10, 10, 280, 16);
  const strutMat = new THREE.MeshStandardMaterial({ color: 0x64748b, metalness: 0.9 });
  [-150, 150].forEach((z) => {
    const leg = new THREE.Mesh(strutGeom, strutMat);
    leg.position.set(-120, -140, z);
    leg.castShadow = true;
    arrayGroup.add(leg);
  });

  solarGroup.add(arrayGroup);
}

function buildSpaceTerminal() {
  const termMount = new THREE.Group();
  termMount.position.set(0, 203, 0);

  const bodyMat = getMaterial('spaceTerminalBody');

  // Heavy-duty Machined Base Plate
  const basePlate = new THREE.Mesh(new THREE.CylinderGeometry(45, 55, 12, 32), bodyMat);
  basePlate.position.y = 6;
  termMount.add(basePlate);

  // Motorized Dual-Axis Gimbal Base
  const gimbal = new THREE.Mesh(new THREE.CylinderGeometry(20, 24, 65, 24), bodyMat);
  gimbal.position.y = 42;
  gimbal.castShadow = true;
  termMount.add(gimbal);

  const pivotBall = new THREE.Mesh(
    new THREE.SphereGeometry(24, 24, 24),
    new THREE.MeshStandardMaterial({ color: 0x334155, metalness: 0.9 })
  );
  pivotBall.position.y = 80;
  termMount.add(pivotBall);

  // Low-Profile Electronically Steerable Phased Array Face
  const apertureGroup = new THREE.Group();
  apertureGroup.position.set(0, 95, 0);
  apertureGroup.rotation.x = -Math.PI / 6; // Angled skywards towards LEO orbit

  const housing = new THREE.Mesh(
    new THREE.BoxGeometry(220, 12, 160),
    new THREE.MeshStandardMaterial({ color: 0x090d16, metalness: 0.95, roughness: 0.15 })
  );
  housing.castShadow = true;
  apertureGroup.add(housing);

  const apertureFace = new THREE.Mesh(
    new THREE.BoxGeometry(208, 3, 148),
    getMaterial('phasedArrayAperture')
  );
  apertureFace.position.y = 6;
  housing.add(apertureFace);

  // Pulsing Status Halo Ring
  const halo = new THREE.Mesh(
    new THREE.TorusGeometry(22, 2, 16, 32),
    getMaterial('ledPurple')
  );
  halo.position.set(0, 7, 0);
  halo.rotation.x = Math.PI / 2;
  housing.add(halo);

  termMount.add(apertureGroup);
  registerInteractive(apertureFace, 'space_antenna');

  spaceGroup.add(termMount);
}

let uavGroup;
let uavRotors = [];

function buildMetaverseSystem() {
  // 1. Autonomous 5G UAV Drone
  uavGroup = new THREE.Group();
  uavGroup.position.set(0, 480, 180);

  // Drone Central Hub Chassis
  const chassisGeom = new THREE.BoxGeometry(70, 22, 70);
  const chassisMat = new THREE.MeshStandardMaterial({ color: 0x0f172a, metalness: 0.85, roughness: 0.2 });
  const chassis = new THREE.Mesh(chassisGeom, chassisMat);
  chassis.castShadow = true;
  uavGroup.add(chassis);

  // Drone 5G Micro-Patch Antenna Stalk
  const mastGeom = new THREE.CylinderGeometry(3, 3, 40, 16);
  const mastMat = new THREE.MeshStandardMaterial({ color: 0x38bdf8, metalness: 0.9, roughness: 0.1 });
  const mast = new THREE.Mesh(mastGeom, mastMat);
  mast.position.set(0, -25, 0);
  uavGroup.add(mast);

  const radomeGeom = new THREE.SphereGeometry(8, 16, 16);
  const radomeMat = getMaterial('ledSky');
  const radome = new THREE.Mesh(radomeGeom, radomeMat);
  radome.position.set(0, -45, 0);
  uavGroup.add(radome);

  // 4 Quad-Rotor Carbon Arms and Rotors
  const armGeom = new THREE.BoxGeometry(110, 6, 8);
  const armMat = new THREE.MeshStandardMaterial({ color: 0x1e293b, metalness: 0.7 });
  const arm1 = new THREE.Mesh(armGeom, armMat);
  arm1.rotation.y = Math.PI / 4;
  uavGroup.add(arm1);
  const arm2 = new THREE.Mesh(armGeom, armMat);
  arm2.rotation.y = -Math.PI / 4;
  uavGroup.add(arm2);

  // 4 Rotors
  const rotorPositions = [
    [38, 10, 38],
    [-38, 10, 38],
    [38, 10, -38],
    [-38, 10, -38],
  ];
  rotorPositions.forEach((p) => {
    const motor = new THREE.Mesh(
      new THREE.CylinderGeometry(6, 6, 8, 12),
      new THREE.MeshStandardMaterial({ color: 0x64748b })
    );
    motor.position.set(p[0], p[1], p[2]);
    uavGroup.add(motor);

    const bladeGeom = new THREE.BoxGeometry(40, 1, 5);
    const blade = new THREE.Mesh(
      bladeGeom,
      new THREE.MeshStandardMaterial({ color: 0x10b981, transparent: true, opacity: 0.85 })
    );
    blade.position.set(p[0], p[1] + 5, p[2]);
    uavGroup.add(blade);
    uavRotors.push(blade);
  });

  registerInteractive(chassis, 'uav_relay');
  metaverseGroup.add(uavGroup);

  // 2. Volumetric SINR Heatmap Floor Grid (380 x 380 mm visual plane)
  const heatmapGeom = new THREE.PlaneGeometry(380, 380, 8, 8);
  const heatmapMat = new THREE.MeshBasicMaterial({
    color: 0x10b981,
    wireframe: true,
    transparent: true,
    opacity: 0.35,
  });
  const heatmap = new THREE.Mesh(heatmapGeom, heatmapMat);
  heatmap.rotation.x = -Math.PI / 2;
  heatmap.position.set(0, -190, 0);
  registerInteractive(heatmap, 'rf_heatmap');
  metaverseGroup.add(heatmap);

  // 3. Three Ground Receivers (Rx1 Disadvantaged, Rx2, Rx3)
  const rxPositions = [
    { id: 'rx1', name: 'Rx1 (Urban Canyon Disadvantaged)', pos: [-160, -185, 140], color: 0xef4444 },
    { id: 'rx2', name: 'Rx2 (Suburban Edge)', pos: [150, -185, 120], color: 0x38bdf8 },
    { id: 'rx3', name: 'Rx3 (Open LoS)', pos: [40, -185, -160], color: 0x10b981 },
  ];
  rxPositions.forEach((r) => {
    const nodeGeom = new THREE.CylinderGeometry(8, 8, 12, 16);
    const nodeMat = new THREE.MeshStandardMaterial({ color: r.color, metalness: 0.8, roughness: 0.2 });
    const node = new THREE.Mesh(nodeGeom, nodeMat);
    node.position.set(r.pos[0], r.pos[1], r.pos[2]);
    registerInteractive(node, r.id);
    metaverseGroup.add(node);

    // Halo pulse ring
    const ring = new THREE.Mesh(
      new THREE.RingGeometry(12, 16, 24),
      new THREE.MeshBasicMaterial({ color: r.color, side: THREE.DoubleSide, transparent: true, opacity: 0.6 })
    );
    ring.rotation.x = -Math.PI / 2;
    ring.position.set(r.pos[0], r.pos[1] - 5, r.pos[2]);
    metaverseGroup.add(ring);
  });
}

export function toggleDoor() {
  if (!doorPivot) return false;
  doorOpen = !doorOpen;
  const targetRot = doorOpen ? -Math.PI / 1.5 : 0;

  new TWEEN.Tween(doorPivot.rotation)
    .to({ y: targetRot }, 600)
    .easing(TWEEN.Easing.Cubic.Out)
    .start();

  return doorOpen;
}

export function setExplodedView(progress) {
  const p = progress / 100.0;
  if (powerGroup) powerGroup.position.x = 430 + p * 200;
  if (solarGroup) solarGroup.position.x = -640 - p * 200;
  if (spaceGroup) spaceGroup.position.y = p * 150;
  if (computeGroup) computeGroup.position.z = p * 180;
  if (metaverseGroup) metaverseGroup.position.y = p * 120;
}

export function setSubsystemVisibility(layerKey) {
  const all = layerKey === 'all';
  if (computeGroup) computeGroup.visible = all || layerKey === 'compute';
  if (powerGroup) powerGroup.visible = all || layerKey === 'power';
  if (coolingGroup) coolingGroup.visible = all || layerKey === 'cooling';
  if (solarGroup) solarGroup.visible = all || layerKey === 'solar';
  if (spaceGroup) spaceGroup.visible = all || layerKey === 'space';
  if (metaverseGroup) metaverseGroup.visible = all || layerKey === 'metaverse';
}

export function updateAnimations() {
  const rpm = _getFanRpmCb();
  const speed = (rpm / 2400) * 0.12;
  fanBlades.forEach((f) => {
    f.rotation.y += speed;
  });

  // Animate UAV Drone hovering & rotors
  if (uavGroup) {
    uavGroup.position.y = 480 + Math.sin(Date.now() * 0.003) * 6;
    uavRotors.forEach((r) => {
      r.rotation.y += 0.35;
    });
  }
}

export function getInteractiveObjects() { return interactiveObjects; }
export function getComponentData(key) { return COMPONENT_DATA[key]; }

