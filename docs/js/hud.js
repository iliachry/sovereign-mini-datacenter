/**
 * Sovereign Mini Datacenter — HUD Telemetry, Raycasting Inspector & Audio
 */

const HUDManager = (function () {
  let raycaster, mouse;
  let activeComponentKey = null;
  let isThermal = false;
  let audioCtx = null;
  let audioMuted = true;

  function init() {
    raycaster = new THREE.Raycaster();
    mouse = new THREE.Vector2();

    window.addEventListener('pointerdown', onPointerDown, false);
    setInterval(updateSimulatedTelemetry, 2500);

    // Initial inspector default
    selectComponent('dgx1');
  }

  function updateSimulatedTelemetry() {
    // Dynamic solar & battery fluctuation
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

  function onPointerDown(event) {
    // Prevent raycasting when clicking on overlay elements
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
  };
})();
