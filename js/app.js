/**
 * Sovereign Mini Datacenter — Main Application Coordinator & Render Loop
 *
 * Entry point ES module. Orchestrates all subsystem initialization
 * and owns the requestAnimationFrame render loop.
 *
 * @module app
 */

import TWEEN from 'tween';
import * as Materials from './materials.js';
import * as Scene from './scene.js';
import * as Models from './models.js';
import * as Space from './space.js';
import * as HUD from './hud.js';
import * as Calculator from './calculator.js';

let isRunning = false;

function init() {
  console.log('🚀 Initializing Sovereign Mini Datacenter Digital Twin...');

  // 1. Initialize Subsystems (order matters: materials first, then scene, then geometry)
  Materials.init();
  Scene.init('canvas-container');
  Models.buildAll(Scene.getScene());
  Space.init(Scene.getScene());
  HUD.init();
  Calculator.init();

  // 2. Wire the circular dependency: models needs hud's fan RPM
  Models.setFanRpmCallback(HUD.getFanRpm);

  // 3. Setup UI Event Listeners
  setupUIListeners();

  // 4. Start Animation Loop
  isRunning = true;
  animate();

  // 5. Hide Loading Overlay
  setTimeout(() => {
    const loader = document.getElementById('loading-screen');
    if (loader) {
      loader.style.opacity = '0';
      setTimeout(() => {
        loader.style.display = 'none';
      }, 500);
    }
  }, 400);
}

function animate() {
  if (!isRunning) return;
  requestAnimationFrame(animate);

  TWEEN.update();

  Scene.getControls().update();
  Models.updateAnimations();
  Space.update();

  Scene.getRenderer().render(Scene.getScene(), Scene.getCamera());
}

function setupUIListeners() {
  // Camera Presets
  document.querySelectorAll('.cam-pill').forEach((btn) => {
    btn.addEventListener('click', (e) => {
      document.querySelectorAll('.cam-pill').forEach((b) => b.classList.remove('active'));
      btn.classList.add('active');
      const preset = btn.dataset.preset;
      Scene.transitionToPreset(preset);
      HUD.playClickSound(600, 0.04);
    });
  });

  // Subsystem Focus Layers
  document.querySelectorAll('.layer-btn').forEach((btn) => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.layer-btn').forEach((b) => b.classList.remove('active'));
      btn.classList.add('active');
      const layer = btn.dataset.layer;
      Models.setSubsystemVisibility(layer);
      HUD.playClickSound(700, 0.04);
    });
  });

  // Exploded View Slider
  const explodeSlider = document.getElementById('slider-explode');
  if (explodeSlider) {
    explodeSlider.addEventListener('input', (e) => {
      const val = parseInt(e.target.value);
      document.getElementById('val-explode').textContent = `${val}%`;
      Models.setExplodedView(val);
    });
  }

  // Sun Elevation Slider
  const sunSlider = document.getElementById('slider-sun');
  if (sunSlider) {
    sunSlider.addEventListener('input', (e) => {
      const val = parseInt(e.target.value);
      const estWatts = Math.round(1640 * Math.sin((val * Math.PI) / 180));
      document.getElementById('val-sun').textContent = `${val}° (${estWatts}W)`;
      Scene.setSunElevation(val);
    });
  }

  // Quick Action Buttons
  document.getElementById('btn-door')?.addEventListener('click', () => {
    HUD.triggerRemoteDoorToggle();
  });

  document.getElementById('btn-night')?.addEventListener('click', () => {
    const night = Scene.toggleDayNight();
    HUD.showToast(night ? '🌙 Night Mode Active' : '☀️ Day Mode Active');
  });

  document.getElementById('btn-thermal')?.addEventListener('click', () => {
    HUD.toggleThermalHeatmap();
  });

  document.getElementById('btn-audio')?.addEventListener('click', () => {
    HUD.toggleAudio();
  });

  document.getElementById('btn-tx-space')?.addEventListener('click', () => {
    HUD.triggerRemoteDTNTransmit();
  });

  document.getElementById('btn-uav-opt')?.addEventListener('click', () => {
    HUD.triggerUAVOptimization();
  });

  // Modal Triggers
  setupModalTrigger('btn-calc', 'modal-sizing');
  setupModalTrigger('btn-investor', 'modal-investor');
  setupModalTrigger('btn-terminal', 'modal-terminal');

  // Modal Close Buttons
  document.querySelectorAll('.close-btn, .modal-close').forEach((btn) => {
    btn.addEventListener('click', (e) => {
      const modal = e.target.closest('.modal-overlay');
      if (modal) modal.classList.remove('active');
    });
  });

  // Close on clicking modal backdrop
  document.querySelectorAll('.modal-overlay').forEach((overlay) => {
    overlay.addEventListener('click', (e) => {
      if (e.target === overlay) {
        overlay.classList.remove('active');
      }
    });
  });

  // Export buttons (previously inline onclick handlers)
  document.querySelector('#modal-sizing .btn-primary')?.addEventListener('click', () => {
    Calculator.exportBOMCSV();
  });

  document.querySelector('#modal-investor .btn-primary')?.addEventListener('click', () => {
    Calculator.exportInvestorTCOCSV();
  });
}

function setupModalTrigger(btnId, modalId) {
  document.getElementById(btnId)?.addEventListener('click', () => {
    const modal = document.getElementById(modalId);
    if (modal) {
      modal.classList.add('active');
      if (modalId === 'modal-terminal') {
        document.getElementById('term-cmd-input')?.focus();
      }
    }
  });
}

// Bootstrap on DOM load
window.addEventListener('DOMContentLoaded', () => {
  init();
});
