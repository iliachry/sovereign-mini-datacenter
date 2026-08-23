/**
 * Sovereign Mini Datacenter — Main Application Coordinator & Render Loop
 */

const App = (function () {
  let isRunning = false;

  function init() {
    console.log('🚀 Initializing Sovereign Mini Datacenter Digital Twin...');

    // 1. Initialize Subsystems
    MaterialsRegistry.init();
    SceneManager.init('canvas-container');
    ModelsBuilder.buildAll(SceneManager.getScene());
    SpaceManager.init(SceneManager.getScene());
    HUDManager.init();
    CalculatorManager.init();

    // 2. Setup UI Event Listeners
    setupUIListeners();

    // 3. Start Animation Loop
    isRunning = true;
    animate();

    // 4. Hide Loading Overlay
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

    if (window.TWEEN) {
      TWEEN.update();
    }

    SceneManager.getControls().update();
    ModelsBuilder.updateAnimations();
    SpaceManager.update();

    SceneManager.getRenderer().render(SceneManager.getScene(), SceneManager.getCamera());
  }

  function setupUIListeners() {
    // Camera Presets
    document.querySelectorAll('.cam-pill').forEach((btn) => {
      btn.addEventListener('click', (e) => {
        document.querySelectorAll('.cam-pill').forEach((b) => b.classList.remove('active'));
        btn.classList.add('active');
        const preset = btn.dataset.preset;
        SceneManager.transitionToPreset(preset);
        HUDManager.playClickSound(600, 0.04);
      });
    });

    // Subsystem Focus Layers
    document.querySelectorAll('.layer-btn').forEach((btn) => {
      btn.addEventListener('click', () => {
        document.querySelectorAll('.layer-btn').forEach((b) => b.classList.remove('active'));
        btn.classList.add('active');
        const layer = btn.dataset.layer;
        ModelsBuilder.setSubsystemVisibility(layer);
        HUDManager.playClickSound(700, 0.04);
      });
    });

    // Exploded View Slider
    const explodeSlider = document.getElementById('slider-explode');
    if (explodeSlider) {
      explodeSlider.addEventListener('input', (e) => {
        const val = parseInt(e.target.value);
        document.getElementById('val-explode').textContent = `${val}%`;
        ModelsBuilder.setExplodedView(val);
      });
    }

    // Sun Elevation Slider
    const sunSlider = document.getElementById('slider-sun');
    if (sunSlider) {
      sunSlider.addEventListener('input', (e) => {
        const val = parseInt(e.target.value);
        const estWatts = Math.round(1640 * Math.sin((val * Math.PI) / 180));
        document.getElementById('val-sun').textContent = `${val}° (${estWatts}W)`;
        SceneManager.setSunElevation(val);
      });
    }

    // Quick Action Buttons
    document.getElementById('btn-door')?.addEventListener('click', () => {
      HUDManager.triggerRemoteDoorToggle();
    });

    document.getElementById('btn-night')?.addEventListener('click', () => {
      const night = SceneManager.toggleDayNight();
      HUDManager.showToast(night ? '🌙 Night Mode Active' : '☀️ Day Mode Active');
    });

    document.getElementById('btn-thermal')?.addEventListener('click', () => {
      HUDManager.toggleThermalHeatmap();
    });

    document.getElementById('btn-audio')?.addEventListener('click', () => {
      HUDManager.toggleAudio();
    });

    document.getElementById('btn-tx-space')?.addEventListener('click', () => {
      HUDManager.triggerRemoteDTNTransmit();
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

  return {
    init,
  };
})();

// Bootstrap on DOM load
window.addEventListener('DOMContentLoaded', () => {
  App.init();
});
