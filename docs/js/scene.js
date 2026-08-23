/**
 * Sovereign Mini Datacenter — Three.js Scene Setup & Cinematic Camera Controls
 *
 * @module scene
 */

import * as THREE from 'three';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';
import TWEEN from 'tween';
import { get as getMaterial } from './materials.js';

let scene, camera, renderer, controls;
let sunLight, ambientLight, cyanFill, purpleGlow, starField;
let isNight = false;

const CAMERA_PRESETS = {
  iso: { pos: [1150, 800, 1150], target: [0, 160, 0] },
  space: { pos: [650, 850, 850], target: [0, 220, 0] },
  antenna: { pos: [0, 380, 280], target: [0, 230, 0] },
  front: { pos: [0, 60, 880], target: [0, 60, 0] },
  power: { pos: [580, 100, 450], target: [400, 40, 0] },
  solar: { pos: [-780, 140, 450], target: [-600, 0, 0] },
};

export function init(containerId) {
  const container = document.getElementById(containerId);

  // 1. Scene with Rich Cosmic Indigo Background
  scene = new THREE.Scene();
  scene.background = new THREE.Color(0x060b1e);
  scene.fog = new THREE.FogExp2(0x060b1e, 0.00014);

  // 2. Camera with Wide Cinematic Perspective
  const aspect = window.innerWidth / window.innerHeight;
  camera = new THREE.PerspectiveCamera(42, aspect, 10, 20000);
  camera.position.set(1150, 800, 1150);

  // 3. WebGL Renderer with High-Fidelity Tone Mapping
  renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, powerPreference: 'high-performance' });
  renderer.setSize(window.innerWidth, window.innerHeight);
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.25;
  container.appendChild(renderer.domElement);

  // 4. Orbit Controls
  controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;
  controls.dampingFactor = 0.05;
  controls.minDistance = 150;
  controls.maxDistance = 6000;
  controls.maxPolarAngle = Math.PI / 2 - 0.01;
  controls.target.set(0, 160, 0);

  // 5. Lighting
  setupLighting();

  // 6. Environment (Grid, Basepad & Starfield)
  setupEnvironment();

  // 7. Event Listeners
  window.addEventListener('resize', onWindowResize, false);
}

function setupLighting() {
  ambientLight = new THREE.AmbientLight(0xdbeafe, 0.75);
  scene.add(ambientLight);

  // Main Warm Sunlight
  sunLight = new THREE.DirectionalLight(0xffedd5, 1.6);
  sunLight.position.set(900, 1400, 800);
  sunLight.castShadow = true;
  sunLight.shadow.mapSize.width = 2048;
  sunLight.shadow.mapSize.height = 2048;
  sunLight.shadow.camera.near = 100;
  sunLight.shadow.camera.far = 4500;
  const d = 1400;
  sunLight.shadow.camera.left = -d;
  sunLight.shadow.camera.right = d;
  sunLight.shadow.camera.top = d;
  sunLight.shadow.camera.bottom = -d;
  sunLight.shadow.bias = -0.0004;
  scene.add(sunLight);

  // Cyber Rim Light (Backlight for metallic chassis silhouette)
  const rimLight = new THREE.DirectionalLight(0x38bdf8, 1.1);
  rimLight.position.set(-800, 900, -800);
  scene.add(rimLight);

  // Subtle Cyan Floor Glow
  cyanFill = new THREE.PointLight(0x06b6d4, 1.0, 2800);
  cyanFill.position.set(-600, 500, -500);
  scene.add(cyanFill);

  // Space Orbital Purple Glow
  purpleGlow = new THREE.PointLight(0xa855f7, 1.4, 3500);
  purpleGlow.position.set(200, 800, 300);
  scene.add(purpleGlow);
}

function setupEnvironment() {
  // Ground Grid Helper with Subtle Glowing Grid Lines
  const grid = new THREE.GridHelper(3600, 60, 0x334155, 0x0f172a);
  grid.position.y = -200;
  scene.add(grid);

  // Structural Datacenter Foundation Pad
  const padGeom = new THREE.BoxGeometry(1520, 10, 1120);
  const padMat = new THREE.MeshStandardMaterial({ color: 0x0f172a, roughness: 0.8, metalness: 0.2 });
  const pad = new THREE.Mesh(padGeom, padMat);
  pad.position.set(0, -205, 0);
  pad.receiveShadow = true;
  scene.add(pad);

  // Glowing Neon Perimeter Strip around Foundation Pad
  const edgeGeom = new THREE.BoxGeometry(1530, 2, 1130);
  const edgeMat = getMaterial('padGlow');
  const edge = new THREE.Mesh(edgeGeom, edgeMat);
  edge.position.set(0, -199, 0);
  scene.add(edge);

  // Starfield Particle System with Rich Twinkling Stars
  const starGeom = new THREE.BufferGeometry();
  const starCount = 2000;
  const starPos = new Float32Array(starCount * 3);
  for (let i = 0; i < starCount * 3; i += 3) {
    starPos[i] = (Math.random() - 0.5) * 9000;
    starPos[i + 1] = Math.random() * 4500 + 40;
    starPos[i + 2] = (Math.random() - 0.5) * 9000;
  }
  starGeom.setAttribute('position', new THREE.BufferAttribute(starPos, 3));
  const starMat = new THREE.PointsMaterial({ color: 0xffffff, size: 4, transparent: true, opacity: 0.65 });
  starField = new THREE.Points(starGeom, starMat);
  scene.add(starField);
}

export function setSunElevation(deg) {
  const rad = (deg * Math.PI) / 180;
  const dist = 1600;
  const y = Math.max(10, Math.sin(rad) * dist);
  const x = Math.cos(rad) * dist;
  sunLight.position.set(x, y, 700);

  const factor = Math.max(0.1, deg / 90);
  sunLight.intensity = factor * 1.6;
  ambientLight.intensity = Math.max(0.3, factor * 0.85);
}

export function toggleDayNight() {
  isNight = !isNight;
  if (isNight) {
    setSunElevation(5);
    scene.background.setHex(0x02040d);
    scene.fog.color.setHex(0x02040d);
    starField.material.opacity = 0.95;
  } else {
    setSunElevation(65);
    scene.background.setHex(0x060b1e);
    scene.fog.color.setHex(0x060b1e);
    starField.material.opacity = 0.65;
  }
  return isNight;
}

export function transitionToPreset(presetKey, durationMs = 1000) {
  const preset = CAMERA_PRESETS[presetKey];
  if (!preset) return;

  new TWEEN.Tween(camera.position)
    .to({ x: preset.pos[0], y: preset.pos[1], z: preset.pos[2] }, durationMs)
    .easing(TWEEN.Easing.Cubic.Out)
    .start();

  new TWEEN.Tween(controls.target)
    .to({ x: preset.target[0], y: preset.target[1], z: preset.target[2] }, durationMs)
    .easing(TWEEN.Easing.Cubic.Out)
    .start();
}

function onWindowResize() {
  camera.aspect = window.innerWidth / window.innerHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(window.innerWidth, window.innerHeight);
}

export function getScene() { return scene; }
export function getCamera() { return camera; }
export function getControls() { return controls; }
export function getRenderer() { return renderer; }
