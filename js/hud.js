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
let nodeToken = ''; // Operator token for nodeBaseUrl's origin only (see loadNodeToken)
let telemetryProvenance = 'unknown'; // 'measured' | 'simulated' | 'unknown' (connected node's own flags)
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

const TOKEN_KEY_PREFIX = 'smdc_token::'; // + node origin
const LEGACY_TOKEN_KEY = 'smdc_token'; // origin-wide key used by earlier versions: never read

/** Origin (scheme://host:port) of a node base URL, or '' if it is not an http(s) URL. */
function nodeOrigin(baseUrl) {
  try {
    const url = new URL(baseUrl);
    return url.protocol === 'http:' || url.protocol === 'https:' ? url.origin : '';
  } catch (e) {
    return '';
  }
}

/**
 * Load the operator token (the node's SMDC_DASHBOARD_TOKEN) for the node this page shadows.
 *
 * The token is bound to the node origin it was supplied for:
 * - `?token=` in the page URL applies only to the node selected by that same URL, and is
 *   stored in sessionStorage under a key that contains that node's origin;
 * - a stored token is only ever returned for the exact origin it was stored for, so a link
 *   with a different `?node=` never receives a token given for another node;
 * - the token is removed from the address bar once read (it would otherwise stay in the
 *   history and in any copied link).
 */
function loadNodeToken(baseUrl) {
  const origin = nodeOrigin(baseUrl);
  let fromUrl = '';
  try {
    const pageUrl = new URL(window.location.href);
    fromUrl = pageUrl.searchParams.get('token') || '';
    if (pageUrl.searchParams.has('token')) {
      pageUrl.searchParams.delete('token');
      window.history.replaceState(null, '', pageUrl.pathname + pageUrl.search + pageUrl.hash);
    }
  } catch (e) {
    // No usable location/history: fall through with whatever was read
  }
  try {
    sessionStorage.removeItem(LEGACY_TOKEN_KEY);
    if (!origin) return '';
    if (fromUrl) sessionStorage.setItem(TOKEN_KEY_PREFIX + origin, fromUrl);
    return sessionStorage.getItem(TOKEN_KEY_PREFIX + origin) || '';
  } catch (e) {
    // sessionStorage unavailable: keep the token for this page load only
    return origin ? fromUrl : '';
  }
}

/** The operator token, but only for requests to the node origin it belongs to. */
function tokenFor(requestUrl) {
  if (!nodeToken) return '';
  const origin = nodeOrigin(nodeBaseUrl);
  return origin && nodeOrigin(requestUrl) === origin ? nodeToken : '';
}

/** Request headers for the node at `requestUrl` (bearer token only for its own origin). */
function nodeHeaders(requestUrl, extra = {}) {
  const headers = { ...extra };
  const token = tokenFor(requestUrl);
  if (token) headers.Authorization = `Bearer ${token}`;
  return headers;
}

/** POST a JSON control request to the connected node. */
function postControl(path, body) {
  const url = `${nodeBaseUrl}${path}`;
  return fetch(url, {
    method: 'POST',
    headers: nodeHeaders(url, { 'Content-Type': 'application/json' }),
    body: JSON.stringify(body),
  });
}

/** True (after telling the operator) when the node refused a control request. */
function controlDenied(res) {
  if (res.status !== 401 && res.status !== 403) return false;
  showToast('⛔ Node refused control: operator token required (?token=...)');
  return true;
}

/**
 * Resolve which node (if any) to shadow.
 * - `?node=<http(s) url>` always wins.
 * - Without it, only a page served from localhost auto-connects to the local dashboard.
 * - Otherwise (e.g. the public GitHub Pages site) no network connection is attempted.
 */
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
  nodeToken = loadNodeToken(nodeBaseUrl);
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
  if (changed) telemetryProvenance = 'unknown'; // decided by the next payload
  updateConnectionBadge(connected);
  if (changed) updateSpaceLinkPill(null); // neutral until the next payload says otherwise

  if (connected) {
    clearInterval(simTimer);
    simTimer = null;
    if (changed) showToast('🟢 Connected to Sovereign Node (telemetry provenance shown in the badge)');
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
    // EventSource cannot send headers: the node accepts the token for this stream as ?token=.
    const streamUrl = `${nodeBaseUrl}/api/telemetry/stream`;
    const token = tokenFor(streamUrl);
    eventSource = new EventSource(token ? `${streamUrl}?token=${encodeURIComponent(token)}` : streamUrl);
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
  const url = `${nodeBaseUrl}/api/status`;
  const res = await fetch(url, { mode: 'cors', headers: nodeHeaders(url) });
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

/**
 * The badge separates two facts: whether a node is connected, and whether the values it
 * sends are measured or come from its simulator (`is_simulated` on its power/thermal data).
 */
function updateConnectionBadge(connected) {
  const badge = document.getElementById('hud-conn-badge');
  if (!badge) return;
  badge.className = 'telem-pill';
  if (!connected) {
    badge.innerHTML = `<div class="telem-dot amber"></div><span class="telem-label" style="color:var(--accent-amber);">AIR-GAPPED:</span><span class="telem-val">SIMULATION</span>`;
  } else if (telemetryProvenance === 'measured') {
    badge.innerHTML = `<div class="telem-dot"></div><span class="telem-label" style="color:var(--accent-emerald);">LIVE SHADOW:</span><span class="telem-val" style="color:#fff;">MEASURED TELEMETRY</span>`;
  } else if (telemetryProvenance === 'simulated') {
    badge.innerHTML = `<div class="telem-dot amber"></div><span class="telem-label" style="color:var(--accent-amber);">NODE CONNECTED:</span><span class="telem-val">SIMULATED TELEMETRY</span>`;
  } else {
    badge.innerHTML = `<div class="telem-dot amber"></div><span class="telem-label" style="color:var(--accent-amber);">NODE CONNECTED:</span><span class="telem-val">TELEMETRY UNVERIFIED</span>`;
  }
}

/** A finite number, or null for anything else (the node sends null for "not measured"). */
function finiteOrNull(value) {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

/** 'simulated' if any section says so, 'measured' only if every present section says it is not. */
function payloadProvenance(data) {
  const flags = [data.power, data.thermal].filter(Boolean).map((section) => section.is_simulated);
  if (flags.some((flag) => flag === true)) return 'simulated';
  if (flags.length > 0 && flags.every((flag) => flag === false)) return 'measured';
  return 'unknown';
}

function applyLiveTelemetry(data) {
  // 0. Never present a node's simulated values as live measurements
  const provenance = payloadProvenance(data);
  if (provenance !== telemetryProvenance) {
    telemetryProvenance = provenance;
    updateConnectionBadge(isLiveConnected);
  }

  // 1. Update HUD Ribbon Values
  if (data.power) {
    // The node sends null for a quantity it has no measurement of: show "--", never a number.
    const solarWatts = finiteOrNull(data.power.solar_watts);
    const soc = finiteOrNull(data.power.battery_soc);
    const volt = finiteOrNull(data.power.battery_voltage);

    const hudSolar = document.getElementById('hud-solar');
    if (hudSolar) hudSolar.textContent = solarWatts === null ? '-- W' : `${Math.round(solarWatts).toLocaleString('en-US')} W`;

    const hudBat = document.getElementById('hud-battery');
    if (hudBat) hudBat.textContent = `${soc === null ? '--' : soc.toFixed(1)}% (${volt === null ? '--' : volt.toFixed(1)}V)`;

    // Map solar wattage to 3D sun elevation
    if (solarWatts !== null && solarWatts > 0) {
      const estSunDeg = Math.min(90, Math.max(10, Math.round((solarWatts / 3280) * 80 + 10)));
      setSunElevation(estSunDeg);
      const slider = document.getElementById('slider-sun');
      if (slider) slider.value = estSunDeg;
      const valSun = document.getElementById('val-sun');
      if (valSun) valSun.textContent = `${estSunDeg}° (${Math.round(solarWatts).toLocaleString('en-US')}W)`;
    }
  }

  if (data.thermal) {
    const coolant = finiteOrNull(data.thermal.coolant_celsius);
    const hudCoolant = document.getElementById('hud-coolant');
    if (hudCoolant) hudCoolant.textContent = coolant === null ? '--°C' : `${coolant.toFixed(1)}°C`;

    // Dynamic Coolant Tube Luminescence Color Shift (Cyan -> Amber -> Red Over-temp)
    const coolantMat = getMaterial('coolantTube');
    if (coolantMat) {
      if (coolant === null) {
        coolantMat.emissive.setHex(0x0284c7); // No reading: neutral colour, no over-temp claim
      } else if (coolant > 55.0) {
        coolantMat.emissive.setHex(0xf43f5e); // Red Over-temp
      } else if (coolant > 48.0) {
        coolantMat.emissive.setHex(0xf59e0b); // Amber Warning
      } else {
        coolantMat.emissive.setHex(0x0284c7); // Cool Cyan
      }
    }
  }

  updateSpaceLinkPill(data.space);

  if (data.hardware) {
    // fan_rpm is the node's estimate from coolant temperature and load (fan_rpm_estimated),
    // not a tachometer reading; it only drives the fan animation speed.
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

/** Labels of the node's predicted contact states (`space.contact_state` in /api/status). */
const SPACE_LINK_LABELS = {
  in_contact: 'IN CONTACT (PREDICTED)',
  tracking: 'AWAITING PASS',
  no_pass: 'NO PASS PREDICTED',
  no_satellites: 'NO SATELLITES',
};

/**
 * The space-link pill shows the connected node's own pass prediction (an orbit-model
 * estimate, not a measured radio link). Without a node, or without that field, it says
 * SIMULATION rather than claiming a contact.
 */
function updateSpaceLinkPill(space) {
  const el = document.getElementById('hud-space-link');
  if (!el) return;
  const label = isLiveConnected && space ? SPACE_LINK_LABELS[space.contact_state] : undefined;
  el.textContent = label || 'SIMULATION';
}

function updateSimulatedTelemetry() {
  updateSpaceLinkPill(null);
  const baseSolar = 2660;
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
/**
 * Toast for an accepted rack-door control. Only `actuated: true` (a registered actuator
 * backend drove the hardware) is reported as a solenoid action; otherwise the node changed
 * its digital shadow only and nothing physical moved.
 */
function doorToast(json) {
  if (json.actuated === true) {
    return json.door_open ? '🚪 Rack Door Solenoid Unlocked (actuated)' : '🔒 Rack Door Solenoid Locked (actuated)';
  }
  return json.door_open
    ? '🚪 Rack door shadow state: OPEN (digital shadow only — no actuator, nothing moved)'
    : '🔒 Rack door shadow state: CLOSED (digital shadow only — no actuator, nothing moved)';
}

export async function triggerRemoteDoorToggle() {
  // 1. Play local 3D animation immediately
  const openState = toggleDoor();
  lastLocalDoorToggle = Date.now();
  document.getElementById('btn-door')?.classList.toggle('active', openState);

  // 2. If connected to live node, dispatch POST /api/control/rack-door
  if (isLiveConnected) {
    try {
      const res = await postControl('/api/control/rack-door', { open: openState });
      if (res.ok) {
        const json = await res.json();
        showToast(doorToast(json));
        return;
      }
      // The node did not change anything: undo the local animation so the twin shows the truth.
      const reverted = toggleDoor();
      document.getElementById('btn-door')?.classList.toggle('active', reverted);
      if (controlDenied(res)) return;
      if (res.status === 501 || res.status === 502) {
        showToast(res.status === 501
          ? '⛔ Node refused: no rack-door actuator is installed — nothing moved'
          : '⛔ Rack-door actuator failed — nothing moved');
        return;
      }
      showToast(`⛔ Rack door control failed (HTTP ${res.status}) — nothing changed`);
      return;
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
      const res = await postControl('/api/control/dtn-transmit', {
        destination: 'dtn://ground-station-alpha.earth/telemetry',
        payload: `STATUS_SNAP_${Date.now()}`,
        priority: 2,
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
      const res = await postControl('/api/metaverse/optimize', { cycles: 3 });
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
