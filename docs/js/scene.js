/**
 * Sovereign Mini Datacenter — Three.js Scene Setup & Cinematic Camera Controls
 */

const SceneManager = (function () {
  let scene, camera, renderer, controls;
  let sunLight, ambientLight, cyanFill, purpleGlow, starField;
  let isNight = false;

  const CAMERA_PRESETS = {
    iso: { pos: [950, 750, 950], target: [0, 50, 0] },
    space: { pos: [400, 1150, 850], target: [0, 450, 0] },
    antenna: { pos: [0, 450, 320], target: [0, 260, 0] },
    front: { pos: [0, 50, 850], target: [0, 50, 0] },
    power: { pos: [550, 100, 450], target: [420, 50, 0] },
    solar: { pos: [-750, 150, 450], target: [-650, 0, 0] },
  };

  function init(containerId) {
    const container = document.getElementById(containerId);

    // 1. Scene
    scene = new THREE.Scene();
    scene.background = new THREE.Color(0x030712);
    scene.fog = new THREE.FogExp2(0x030712, 0.00025);

    // 2. Camera
    const aspect = window.innerWidth / window.innerHeight;
    camera = new THREE.PerspectiveCamera(42, aspect, 10, 15000);
    camera.position.set(950, 750, 950);

    // 3. WebGL Renderer with High-Fidelity Tone Mapping
    renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, powerPreference: 'high-performance' });
    renderer.setSize(window.innerWidth, window.innerHeight);
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.shadowMap.enabled = true;
    renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    renderer.toneMappingExposure = 1.15;
    container.appendChild(renderer.domElement);

    // 4. Orbit Controls
    controls = new THREE.OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.05;
    controls.minDistance = 200;
    controls.maxDistance = 4500;
    controls.maxPolarAngle = Math.PI / 2 - 0.01; // Prevent going underground
    controls.target.set(0, 50, 0);

    // 5. Lighting
    setupLighting();

    // 6. Environment (Grid, Basepad & Starfield)
    setupEnvironment();

    // 7. Event Listeners
    window.addEventListener('resize', onWindowResize, false);
  }

  function setupLighting() {
    ambientLight = new THREE.AmbientLight(0xffffff, 0.65);
    scene.add(ambientLight);

    sunLight = new THREE.DirectionalLight(0xfffbeb, 1.4);
    sunLight.position.set(900, 1400, 800);
    sunLight.castShadow = true;
    sunLight.shadow.mapSize.width = 2048;
    sunLight.shadow.mapSize.height = 2048;
    sunLight.shadow.camera.near = 100;
    sunLight.shadow.camera.far = 4000;
    const d = 1200;
    sunLight.shadow.camera.left = -d;
    sunLight.shadow.camera.right = d;
    sunLight.shadow.camera.top = d;
    sunLight.shadow.camera.bottom = -d;
    sunLight.shadow.bias = -0.0004;
    scene.add(sunLight);

    cyanFill = new THREE.PointLight(0x38bdf8, 0.8, 2500);
    cyanFill.position.set(-600, 600, -500);
    scene.add(cyanFill);

    purpleGlow = new THREE.PointLight(0xa855f7, 1.2, 3000);
    purpleGlow.position.set(150, 750, 250);
    scene.add(purpleGlow);
  }

  function setupEnvironment() {
    // Ground Grid Helper
    const grid = new THREE.GridHelper(3600, 60, 0x1e293b, 0x090f1f);
    grid.position.y = -200;
    scene.add(grid);

    // Structural Datacenter Concrete Pad
    const padGeom = new THREE.BoxGeometry(1500, 10, 1100);
    const padMat = new THREE.MeshStandardMaterial({ color: 0x0f172a, roughness: 0.85, metalness: 0.15 });
    const pad = new THREE.Mesh(padGeom, padMat);
    pad.position.set(0, -205, 0);
    pad.receiveShadow = true;
    scene.add(pad);

    // Starfield Particle System
    const starGeom = new THREE.BufferGeometry();
    const starCount = 1500;
    const starPos = new Float32Array(starCount * 3);
    for (let i = 0; i < starCount * 3; i += 3) {
      starPos[i] = (Math.random() - 0.5) * 8000;
      starPos[i + 1] = Math.random() * 4000 + 50;
      starPos[i + 2] = (Math.random() - 0.5) * 8000;
    }
    starGeom.setAttribute('position', new THREE.BufferAttribute(starPos, 3));
    const starMat = new THREE.PointsMaterial({ color: 0xffffff, size: 3.5, transparent: true, opacity: 0.45 });
    starField = new THREE.Points(starGeom, starMat);
    scene.add(starField);
  }

  function setSunElevation(deg) {
    const rad = (deg * Math.PI) / 180;
    const dist = 1600;
    const y = Math.max(10, Math.sin(rad) * dist);
    const x = Math.cos(rad) * dist;
    sunLight.position.set(x, y, 700);

    const factor = Math.max(0.1, deg / 90);
    sunLight.intensity = factor * 1.5;
    ambientLight.intensity = Math.max(0.2, factor * 0.7);
  }

  function toggleDayNight() {
    isNight = !isNight;
    if (isNight) {
      setSunElevation(5);
      scene.background.setHex(0x010309);
      scene.fog.color.setHex(0x010309);
      starField.material.opacity = 0.85;
    } else {
      setSunElevation(65);
      scene.background.setHex(0x030712);
      scene.fog.color.setHex(0x030712);
      starField.material.opacity = 0.35;
    }
    return isNight;
  }

  function transitionToPreset(presetKey, durationMs = 1000) {
    const preset = CAMERA_PRESETS[presetKey];
    if (!preset) return;

    if (window.TWEEN) {
      new TWEEN.Tween(camera.position)
        .to({ x: preset.pos[0], y: preset.pos[1], z: preset.pos[2] }, durationMs)
        .easing(TWEEN.Easing.Cubic.Out)
        .start();

      new TWEEN.Tween(controls.target)
        .to({ x: preset.target[0], y: preset.target[1], z: preset.target[2] }, durationMs)
        .easing(TWEEN.Easing.Cubic.Out)
        .start();
    } else {
      camera.position.set(...preset.pos);
      controls.target.set(...preset.target);
    }
  }

  function onWindowResize() {
    camera.aspect = window.innerWidth / window.innerHeight;
    camera.updateProjectionMatrix();
    renderer.setSize(window.innerWidth, window.innerHeight);
  }

  return {
    init,
    getScene: () => scene,
    getCamera: () => camera,
    getControls: () => controls,
    getRenderer: () => renderer,
    setSunElevation,
    toggleDayNight,
    transitionToPreset,
  };
})();
