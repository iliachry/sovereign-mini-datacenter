/**
 * Sovereign Mini Datacenter — Live Telemetry Streaming, Digital Shadow & Hardware Control
 *
 * @module hud
 */

import * as THREE from 'three';
import { getCamera, getScene, setSunElevation } from './scene.js';
import { toggleDoor, setDoorOpen, getInteractiveObjects, getComponentData } from './models.js';
import { get as getMaterial } from './materials.js';
import { triggerSpaceTransmission } from './space.js';

let raycaster, mouse;
let activeComponentKey = null;
let isThermal = false;
let isLiveConnected = false;
let nodeBaseUrl = '';
let eventSource = null;
let currentFanRpm = 2400;

// Live-shadow connection management
const RETRY_BASE_MS = 2000; // First retry delay; doubles on every consecutive failure
const RETRY_MAX_MS = 60000; // Backoff cap
const RETRY_MAX_ATTEMPTS = 8; // Then stop and stay in simulation until reload
const POLL_INTERVAL_MS = 2000; // /api/status polling cadence (nodes without SSE)
const SIM_INTERVAL_MS = 2000; // Local simulated-telemetry cadence (no network)
const DOOR_SYNC_GRACE_MS = 3000; // Ignore live door state briefly after a local toggle
let retryAttempt = 0;
let retryTimer = null;
let pollTimer = null;
let simTimer = null;
let telemetryStopped = false;
let lastLocalDoorToggle = 0;

export function init() {
  raycaster = new THREE.Raycaster();
  mouse = new THREE.Vector2();

  window.addEventListener('pointerdown', onPointerDown, false);

  // Initial inspector default
  selectComponent('dgx1');

  // Initialize Live Telemetry Stream Connection
  initTelemetryStream();
}

/**
 * Resolve which node (if any) to shadow.
 * - `?node=<http(s) url>` always wins.
 * - Without it, only a page served from localhost auto-connects to the local dashboard.
 * - Otherwise (e.g. the public GitHub Pages site) no network connection is attempted.
 */
/**
 * Headers for control POSTs. A node exposed beyond loopback requires its operator token
 * (SMDC_DASHBOARD_TOKEN): pass it once as `?token=...`; it is kept in sessionStorage only.
 */
function controlHeaders() {
  const headers = { 'Content-Type': 'application/json' };
  let token = '';
  try {
    const fromUrl = new URLSearchParams(window.location.search).get('token');
    if (fromUrl) sessionStorage.setItem('smdc_token', fromUrl);
    token = sessionStorage.getItem('smdc_token') || '';
  } catch (e) {
    // sessionStorage unavailable: send no token
  }
  if (token) headers.Authorization = `Bearer ${token}`;
  return headers;
}

/** True (after telling the operator) when the node refused a control request. */
function controlDenied(res) {
  if (res.status !== 401 && res.status !== 403) return false;
  showToast('⛔ Node refused control: operator token required (?token=...)');
  return true;
}

function resolveNodeBaseUrl() {
  const nodeParam = new URLSearchParams(window.location.search).get('node');
  if (nodeParam) {
    try {
      const url = new URL(nodeParam);
      if (url.protocol === 'http:' || url.protocol === 'https:') {
        return url.origin + url.pathname.replace(/\/+$/, '');
      }
    } catch (e) {
      // fall through
    }
    console.warn('Ignoring invalid ?node= value (expected an http(s) URL)');
    return '';
  }

  const host = window.location.hostname;
  if (host === 'localhost' || host === '127.0.0.1' || host === '[::1]') {
    return window.location.port === '8080' ? window.location.origin : 'http://localhost:8080';
  }
  return '';
}

function initTelemetryStream() {
  nodeBaseUrl = resolveNodeBaseUrl();
  window.addEventListener('pagehide', stopTelemetry);
  window.addEventListener('pageshow', (e) => {
    if (e.persisted) startTelemetry(); // Restored from the back/forward cache
  });
  startTelemetry();
}

function startTelemetry() {
  telemetryStopped = false;
  retryAttempt = 0;

  if (!nodeBaseUrl) {
    // Public / air-gapped page: simulation only, no connection attempts.
    setConnected(false);
    return;
  }

  console.log(`🔌 Initializing Digital Shadow connection to node: ${nodeBaseUrl}...`);
  setConnected(false);
  connectSSE();
}

function setConnected(connected) {
  const changed = connected !== isLiveConnected;
  isLiveConnected = connected;
  updateConnectionBadge(connected);

  if (connected) {
    clearInterval(simTimer);
    simTimer = null;
    if (changed) showToast('🟢 Connected to Live Sovereign Node Digital Shadow');
  } else if (!simTimer && !telemetryStopped) {
    updateSimulatedTelemetry();
    simTimer = setInterval(updateSimulatedTelemetry, SIM_INTERVAL_MS);
  }
}

function closeEventSource() {
  if (eventSource) {
    eventSource.onopen = eventSource.onmessage = eventSource.onerror = null;
    eventSource.close();
    eventSource = null;
  }
}

/**
 * Works with both a continuous SSE stream (repeated `data: {...}` events) and a
 * stream that closes after each event (re-opened after RETRY_BASE_MS, without
 * flipping the badge, as long as it keeps delivering data).
 */
function connectSSE() {
  if (telemetryStopped) return;
  closeEventSource();

  let delivered = false;
  try {
    eventSource = new EventSource(`${nodeBaseUrl}/api/telemetry/stream`);
  } catch (e) {
    console.warn('SSE not supported or blocked, trying /api/status polling', e);
    pollStatusOnce();
    return;
  }

  eventSource.onopen = () => {
    if (!isLiveConnected) setConnected(true);
  };

  eventSource.onmessage = (event) => {
    let data;
    try {
      data = JSON.parse(event.data);
    } catch (e) {
      console.warn('Error parsing telemetry payload', e);
      return;
    }
    delivered = true;
    retryAttempt = 0;
    if (!isLiveConnected) setConnected(true);
    try {
      applyLiveTelemetry(data);
    } catch (e) {
      console.warn('Error applying telemetry payload', e);
    }
  };

  // The node is reachable but has no telemetry source: stop presenting stale values as live.
  eventSource.addEventListener('unavailable', () => {
    delivered = true;
    if (isLiveConnected) {
      console.warn('Node reports telemetry unavailable; showing simulation until it recovers');
      setConnected(false);
    }
  });

  eventSource.onerror = () => {
    // Take over from the browser's fixed-interval auto-reconnect.
    closeEventSource();
    if (telemetryStopped) return;

    if (delivered) {
      // Stream ended after delivering data: re-open promptly, keep the badge as is.
      retryTimer = setTimeout(connectSSE, RETRY_BASE_MS);
    } else {
      // Never produced data on this attempt: the node may lack SSE, or be down.
      pollStatusOnce();
    }
  };
}

async function fetchStatus() {
  const res = await fetch(`${nodeBaseUrl}/api/status`, { mode: 'cors' });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

/** Fallback for nodes without a usable SSE stream: poll while it works, back off when it doesn't. */
async function pollStatusOnce() {
  if (telemetryStopped) return;
  try {
    const data = await fetchStatus();
    if (telemetryStopped) return;
    retryAttempt = 0;
    if (!isLiveConnected) setConnected(true);
    applyLiveTelemetry(data);
    pollTimer = setTimeout(pollStatusOnce, POLL_INTERVAL_MS);
  } catch (e) {
    if (telemetryStopped) return;
    if (isLiveConnected) console.warn('Live node unreachable, falling back to simulation');
    setConnected(false);
    scheduleReconnect();
  }
}

function scheduleReconnect() {
  if (telemetryStopped) return;
  if (retryAttempt >= RETRY_MAX_ATTEMPTS) {
    console.warn(`Digital Shadow: node unreachable after ${RETRY_MAX_ATTEMPTS} retries, staying in simulation (reload to retry).`);
    return;
  }
  const delay = Math.min(RETRY_MAX_MS, RETRY_BASE_MS * 2 ** retryAttempt);
  retryAttempt += 1;
  clearTimeout(retryTimer);
  retryTimer = setTimeout(connectSSE, delay);
}

/** Tear down every connection and timer (page unload, or manual stop). */
export function stopTelemetry() {
  telemetryStopped = true;
  closeEventSource();
  clearTimeout(retryTimer);
  clearTimeout(pollTimer);
  clearInterval(simTimer);
  retryTimer = pollTimer = simTimer = null;
}

function updateConnectionBadge(connected) {
  const badge = document.getElementById('hud-conn-badge');
  if (badge) {
    if (connected) {
      badge.className = 'telem-pill';
      badge.innerHTML = `<div class="telem-dot"></div><span class="telem-label" style="color:var(--accent-emerald);">LIVE SHADOW:</span><span class="telem-val" style="color:#fff;">CONNECTED</span>`;
    } else {
      badge.className = 'telem-pill';
      badge.innerHTML = `<div class="telem-dot amber"></div><span class="telem-label" style="color:var(--accent-amber);">AIR-GAPPED:</span><span class="telem-val">SIMULATION</span>`;
    }
  }
}

function applyLiveTelemetry(data) {
  // 1. Update HUD Ribbon Values
  if (data.power) {
    const solarWatts = data.power.solar_watts;
    const soc = data.power.battery_soc;
    const volt = data.power.battery_voltage;

    const hudSolar = document.getElementById('hud-solar');
    if (hudSolar) hudSolar.textContent = `${Math.round(solarWatts).toLocaleString('en-US')} W`;

    const hudBat = document.getElementById('hud-battery');
    if (hudBat) hudBat.textContent = `${soc.toFixed(1)}% (${volt.toFixed(1)}V)`;

    // Map solar wattage to 3D sun elevation
    if (solarWatts > 0) {
      const estSunDeg = Math.min(90, Math.max(10, Math.round((solarWatts / 1640) * 80 + 10)));
      setSunElevation(estSunDeg);
      const slider = document.getElementById('slider-sun');
      if (slider) slider.value = estSunDeg;
      const valSun = document.getElementById('val-sun');
      if (valSun) valSun.textContent = `${estSunDeg}° (${Math.round(solarWatts).toLocaleString('en-US')}W)`;
    }
  }

  if (data.thermal) {
    const coolant = data.thermal.coolant_celsius;
    const hudCoolant = document.getElementById('hud-coolant');
    if (hudCoolant) hudCoolant.textContent = `${coolant.toFixed(1)}°C`;

    // Dynamic Coolant Tube Luminescence Color Shift (Cyan -> Amber -> Red Over-temp)
    const coolantMat = getMaterial('coolantTube');
    if (coolantMat) {
      if (coolant > 55.0) {
        coolantMat.emissive.setHex(0xf43f5e); // Red Over-temp
      } else if (coolant > 48.0) {
        coolantMat.emissive.setHex(0xf59e0b); // Amber Warning
      } else {
        coolantMat.emissive.setHex(0x0284c7); // Cool Cyan
      }
    }
  }

  if (data.hardware) {
    currentFanRpm = data.hardware.fan_rpm || 2400;

    // Synchronize the 3D door mesh and the button with live hardware state
    // (skipped briefly after a local toggle so a stale event cannot undo it).
    if (typeof data.hardware.door_open === 'boolean' && Date.now() - lastLocalDoorToggle > DOOR_SYNC_GRACE_MS) {
      setDoorOpen(data.hardware.door_open);
      const doorBtn = document.getElementById('btn-door');
      if (doorBtn) {
        doorBtn.classList.toggle('active', data.hardware.door_open);
      }
    }
  }
}

function updateSimulatedTelemetry() {
  const baseSolar = 1330;
  const solarVariation = Math.round(baseSolar + (Math.random() - 0.5) * 40);
  const hudSolar = document.getElementById('hud-solar');
  if (hudSolar) hudSolar.textContent = `${solarVariation.toLocaleString('en-US')} W`;

  const hudBat = document.getElementById('hud-battery');
  if (hudBat) hudBat.textContent = '88.5% (52.8V)';

  const hudCoolant = document.getElementById('hud-coolant');
  const coolantTemp = (31.2 + (Math.random() - 0.5) * 0.4).toFixed(1);
  if (hudCoolant) hudCoolant.textContent = `${coolantTemp}°C`;
}

// --- Hardware Remote Control REST Endpoints ---
export async function triggerRemoteDoorToggle() {
  // 1. Play local 3D animation immediately
  const openState = toggleDoor();
  lastLocalDoorToggle = Date.now();
  document.getElementById('btn-door')?.classList.toggle('active', openState);

  // 2. If connected to live node, dispatch POST /api/control/rack-door
  if (isLiveConnected) {
    try {
      const res = await fetch(`${nodeBaseUrl}/api/control/rack-door`, {
        method: 'POST',
        headers: controlHeaders(),
        body: JSON.stringify({ open: openState }),
      });
      if (res.ok) {
        const json = await res.json();
        showToast(json.door_open ? '🚪 Rack Door Solenoid Unlocked' : '🔒 Rack Door Solenoid Locked');
        return;
      }
      if (controlDenied(res)) return;
    } catch (e) {
      console.warn('Failed to dispatch rack door control', e);
    }
  }
  showToast(openState ? '🚪 Rack Door Opened (Simulated)' : '🔒 Rack Door Closed (Simulated)');
}

export async function triggerRemoteDTNTransmit() {
  // 1. Trigger space beam animation
  triggerSpaceTransmission();

  // 2. Dispatch to live node DTN router
  if (isLiveConnected) {
    try {
      const res = await fetch(`${nodeBaseUrl}/api/control/dtn-transmit`, {
        method: 'POST',
        headers: controlHeaders(),
        body: JSON.stringify({
          destination: 'dtn://ground-station-alpha.earth/telemetry',
          payload: `STATUS_SNAP_${Date.now()}`,
          priority: 2,
        }),
      });
      if (res.ok) {
        const json = await res.json();
        showToast(`🚀 DTN Bundle Queued: ${json.bundle_id.slice(0, 18)}...`);
        return;
      }
      if (controlDenied(res)) return;
    } catch (e) {
      console.warn('Failed to spool DTN bundle on live node', e);
    }
  }
  showToast('🚀 Spooling & Transmitting RFC 9171 Space Bundle (Simulated)...');
}

function onPointerDown(event) {
  if (event.target.closest('.interactive') || event.target.closest('.modal-card')) {
    return;
  }

  mouse.x = (event.clientX / window.innerWidth) * 2 - 1;
  mouse.y = -(event.clientY / window.innerHeight) * 2 + 1;

  const camera = getCamera();
  const interactiveList = getInteractiveObjects();
  raycaster.setFromCamera(mouse, camera);

  // The raycaster ignores .visible, so drop hits inside hidden (layer-isolated) groups.
  const intersects = raycaster.intersectObjects(interactiveList, true).filter((i) => isVisibleInScene(i.object));
  if (intersects.length > 0) {
    let hit = intersects[0].object;
    while (hit && !hit.userData.componentKey && hit.parent) {
      hit = hit.parent;
    }
    if (hit && hit.userData.componentKey) {
      selectComponent(hit.userData.componentKey);
    }
  }
}

function isVisibleInScene(obj) {
  for (let o = obj; o; o = o.parent) {
    if (!o.visible) return false;
  }
  return true;
}

export function selectComponent(compKey) {
  activeComponentKey = compKey;
  const data = getComponentData(compKey);
  if (!data) return;

  const titleEl = document.getElementById('comp-name');
  const badgeEl = document.getElementById('comp-badge');
  const descEl = document.getElementById('comp-desc');
  const specsEl = document.getElementById('comp-specs');

  if (titleEl) titleEl.textContent = data.name;
  if (badgeEl) badgeEl.textContent = data.badge;
  if (descEl) descEl.textContent = data.desc;

  if (specsEl) {
    specsEl.innerHTML = '';
    for (const [k, v] of Object.entries(data.specs)) {
      const row = document.createElement('div');
      row.className = 'spec-row';
      row.innerHTML = `<span class="spec-key">${k}</span><span class="spec-val">${v}</span>`;
      specsEl.appendChild(row);
    }
  }
}

export function toggleThermalHeatmap() {
  isThermal = !isThermal;
  const scene = getScene();
  const thermalMat = getMaterial('thermalShader');

  scene.traverse((child) => {
    // Beam/pulses (userData.noThermal) keep their own materials: they are animated, not thermal surfaces.
    if (child.isMesh && child.material && !child.userData.noThermal) {
      if (isThermal) {
        if (!child.userData.origMat) child.userData.origMat = child.material;
        child.material = thermalMat;
      } else if (child.userData.origMat) {
        child.material = child.userData.origMat;
      }
    }
  });

  const btn = document.getElementById('btn-thermal');
  if (btn) btn.classList.toggle('active', isThermal);
  showToast(isThermal ? '🌡️ Thermal Heatmap Active' : '🧊 Standard PBR Materials Active');
  return isThermal;
}

export function showToast(msg) {
  let container = document.getElementById('toast-container');
  if (!container) {
    container = document.createElement('div');
    container.id = 'toast-container';
    document.body.appendChild(container);
  }

  const toast = document.createElement('div');
  toast.className = 'toast';
  toast.textContent = msg;
  container.appendChild(toast);

  setTimeout(() => {
    toast.style.opacity = '0';
    toast.style.transition = 'opacity 0.3s ease';
    setTimeout(() => toast.remove(), 300);
  }, 2400);
}

export async function triggerUAVOptimization() {
  showToast('🎯 Triggering Scene-Aware PPO UAV Positioning Optimization...');
  if (isLiveConnected && nodeBaseUrl) {
    try {
      const res = await fetch(`${nodeBaseUrl}/api/metaverse/optimize`, {
        method: 'POST',
        headers: controlHeaders(),
        body: JSON.stringify({ cycles: 3 }),
      });
      if (res.ok) {
        const data = await res.json();
        const pos = data.uav_position;
        showToast(`✅ SA-PPO Optimized: UAV at (${pos[0].toFixed(1)}m, ${pos[1].toFixed(1)}m, ${pos[2].toFixed(1)}m)`);
      } else {
        controlDenied(res);
      }
    } catch (e) {
      console.warn('UAV optimization dispatch notice:', e);
    }
  } else {
    showToast('✨ SA-PPO simulated step (no live node connected)');
  }
}

export function getFanRpm() { return currentFanRpm; }
export function getIsLiveConnected() { return isLiveConnected; }
