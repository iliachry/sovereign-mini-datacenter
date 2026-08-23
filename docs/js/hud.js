/**
 * Sovereign Mini Datacenter — Live Telemetry Streaming, Digital Shadow & Hardware Control
 */

const HUDManager = (function () {
  let raycaster, mouse;
  let activeComponentKey = null;
  let isThermal = false;
  let audioCtx = null;
  let audioMuted = true;
  let isLiveConnected = false;
  let nodeBaseUrl = '';
  let eventSource = null;
  let currentFanRpm = 2400;

  function init() {
    raycaster = new THREE.Raycaster();
    mouse = new THREE.Vector2();

    window.addEventListener('pointerdown', onPointerDown, false);

    // Initial inspector default
    selectComponent('dgx1');

    // Initialize Live Telemetry Stream Connection
    initTelemetryStream();
  }

  function initTelemetryStream() {
    // 1. Determine Node URL (from query parameter or current origin)
    const urlParams = new URLSearchParams(window.location.search);
    nodeBaseUrl = urlParams.get('node') || (window.location.port === '8080' ? window.location.origin : 'http://localhost:8080');

    console.log(`🔌 Initializing Digital Shadow connection to node: ${nodeBaseUrl}...`);

    // Try EventSource SSE connection
    tryConnectSSE();
  }

  function tryConnectSSE() {
    if (eventSource) {
      eventSource.close();
    }

    try {
      const streamUrl = `${nodeBaseUrl}/api/telemetry/stream`;
      eventSource = new EventSource(streamUrl);

      eventSource.onopen = () => {
        isLiveConnected = true;
        updateConnectionBadge(true);
        showToast('🟢 Connected to Live Sovereign Node Digital Shadow');
      };

      eventSource.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          applyLiveTelemetry(data);
        } catch (e) {
          console.warn('Error parsing telemetry payload', e);
        }
      };

      eventSource.onerror = () => {
        if (isLiveConnected) {
          console.warn('SSE stream disconnected, falling back to polling...');
          isLiveConnected = false;
          updateConnectionBadge(false);
        }
        eventSource.close();
        // Fallback to polling /api/status
        startPollingFallback();
      };
    } catch (e) {
      console.warn('SSE not supported or blocked, using polling fallback', e);
      startPollingFallback();
    }
  }

  let pollInterval = null;
  function startPollingFallback() {
    if (pollInterval) clearInterval(pollInterval);

    pollInterval = setInterval(async () => {
      try {
        const res = await fetch(`${nodeBaseUrl}/api/status`, { mode: 'cors' });
        if (res.ok) {
          const data = await res.json();
          if (!isLiveConnected) {
            isLiveConnected = true;
            updateConnectionBadge(true);
          }
          applyLiveTelemetry(data);
        } else {
          fallbackToSimulation();
        }
      } catch (e) {
        fallbackToSimulation();
      }
    }, 2000);
  }

  function fallbackToSimulation() {
    if (isLiveConnected) {
      isLiveConnected = false;
      updateConnectionBadge(false);
    }
    updateSimulatedTelemetry();
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
      if (hudSolar) hudSolar.textContent = `${Math.round(solarWatts).toLocaleString()} W`;

      const hudBat = document.getElementById('hud-battery');
      if (hudBat) hudBat.textContent = `${soc.toFixed(1)}% (${volt.toFixed(1)}V)`;

      // Map solar wattage to 3D sun elevation
      if (solarWatts > 0) {
        const estSunDeg = Math.min(90, Math.max(10, Math.round((solarWatts / 1640) * 80 + 10)));
        SceneManager.setSunElevation(estSunDeg);
        const slider = document.getElementById('slider-sun');
        if (slider) slider.value = estSunDeg;
        const valSun = document.getElementById('val-sun');
        if (valSun) valSun.textContent = `${estSunDeg}° (${Math.round(solarWatts)}W)`;
      }
    }

    if (data.thermal) {
      const coolant = data.thermal.coolant_celsius;
      const hudCoolant = document.getElementById('hud-coolant');
      if (hudCoolant) hudCoolant.textContent = `${coolant.toFixed(1)}°C`;

      // Dynamic Coolant Tube Luminescence Color Shift (Cyan -> Amber if hot)
      const coolantMat = MaterialsRegistry.get('coolantTube');
      if (coolantMat) {
        if (coolant > 48.0) {
          coolantMat.emissive.setHex(0xf59e0b); // Amber Warning
        } else if (coolant > 55.0) {
          coolantMat.emissive.setHex(0xf43f5e); // Red Over-temp
        } else {
          coolantMat.emissive.setHex(0x0284c7); // Cool Cyan
        }
      }
    }

    if (data.hardware) {
      currentFanRpm = data.hardware.fan_rpm || 2400;

      // Synchronize 3D door state with live hardware state
      if (typeof data.hardware.door_open === 'boolean') {
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
    if (hudSolar) hudSolar.textContent = `${solarVariation.toLocaleString()} W`;

    const hudBat = document.getElementById('hud-battery');
    if (hudBat) hudBat.textContent = '88.5% (52.8V)';

    const hudCoolant = document.getElementById('hud-coolant');
    const coolantTemp = (31.2 + (Math.random() - 0.5) * 0.4).toFixed(1);
    if (hudCoolant) hudCoolant.textContent = `${coolantTemp}°C`;
  }

  // --- Hardware Remote Control REST Endpoints ---
  async function triggerRemoteDoorToggle() {
    // 1. Play local 3D animation immediately
    const openState = ModelsBuilder.toggleDoor();
    playClickSound(500, 0.06);

    // 2. If connected to live node, dispatch POST /api/control/rack-door
    if (isLiveConnected) {
      try {
        const res = await fetch(`${nodeBaseUrl}/api/control/rack-door`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ open: openState }),
        });
        if (res.ok) {
          const json = await res.json();
          showToast(json.door_open ? '🚪 Rack Door Solenoid Unlocked' : '🔒 Rack Door Solenoid Locked');
          return;
        }
      } catch (e) {
        console.warn('Failed to dispatch rack door control', e);
      }
    }
    showToast(openState ? '🚪 Rack Door Opened (Simulated)' : '🔒 Rack Door Closed (Simulated)');
  }

  async function triggerRemoteDTNTransmit() {
    // 1. Trigger space beam animation
    SpaceManager.triggerSpaceTransmission();
    playClickSound(1000, 0.1);

    // 2. Dispatch to live node DTN router
    if (isLiveConnected) {
      try {
        const res = await fetch(`${nodeBaseUrl}/api/control/dtn-transmit`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
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

    const camera = SceneManager.getCamera();
    const interactiveList = ModelsBuilder.getInteractiveObjects();
    raycaster.setFromCamera(mouse, camera);

    const intersects = raycaster.intersectObjects(interactiveList, true);
    if (intersects.length > 0) {
      let hit = intersects[0].object;
      while (hit && !hit.userData.componentKey && hit.parent) {
        hit = hit.parent;
      }
      if (hit && hit.userData.componentKey) {
        selectComponent(hit.userData.componentKey);
        playClickSound(800, 0.05);
      }
    }
  }

  function selectComponent(compKey) {
    activeComponentKey = compKey;
    const data = ModelsBuilder.getComponentData(compKey);
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

  function toggleThermalHeatmap() {
    isThermal = !isThermal;
    const scene = SceneManager.getScene();
    const thermalMat = MaterialsRegistry.get('thermalShader');

    scene.traverse((child) => {
      if (child.isMesh && child.material) {
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

  function playClickSound(freq = 600, duration = 0.04) {
    if (audioMuted) return;
    try {
      if (!audioCtx) audioCtx = new (window.AudioContext || window.webkitAudioContext)();
      if (audioCtx.state === 'suspended') audioCtx.resume();

      const osc = audioCtx.createOscillator();
      const gain = audioCtx.createGain();
      osc.type = 'sine';
      osc.frequency.setValueAtTime(freq, audioCtx.currentTime);
      gain.gain.setValueAtTime(0.08, audioCtx.currentTime);
      gain.gain.exponentialRampToValueAtTime(0.001, audioCtx.currentTime + duration);

      osc.connect(gain);
      gain.connect(audioCtx.destination);
      osc.start();
      osc.stop(audioCtx.currentTime + duration);
    } catch (e) {
      console.warn('Audio synthesis disabled', e);
    }
  }

  function toggleAudio() {
    audioMuted = !audioMuted;
    if (!audioMuted && !audioCtx) {
      audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    }
    const btn = document.getElementById('btn-audio');
    if (btn) btn.innerHTML = audioMuted ? '🔇 Audio: OFF' : '🔊 Audio: ON';
    showToast(audioMuted ? '🔇 Audio Muted' : '🔊 Cyber SFX Active');
    return !audioMuted;
  }

  function showToast(msg) {
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

  return {
    init,
    selectComponent,
    toggleThermalHeatmap,
    toggleAudio,
    showToast,
    playClickSound,
    triggerRemoteDoorToggle,
    triggerRemoteDTNTransmit,
    getFanRpm: () => currentFanRpm,
    isLiveConnected: () => isLiveConnected,
  };
})();
