/**
 * Sovereign Mini Datacenter — PBR Materials & Shaders Registry
 */

const MaterialsRegistry = (function () {
  const materials = {};

  function init() {
    // 1. Structural Anodized Aluminum (Rack frame, extruded profiles)
    materials.aluminum = new THREE.MeshStandardMaterial({
      color: 0x78889b,
      metalness: 0.88,
      roughness: 0.22,
      envMapIntensity: 1.2,
    });

    // 2. EIA-310-D Powder-Coated Steel (Internal vertical rails, 1U-9U markings)
    materials.steelRail = new THREE.MeshStandardMaterial({
      color: 0x182234,
      metalness: 0.92,
      roughness: 0.18,
    });

    // 3. Matte Chassis Dark Steel (Server enclosures, PDU, switch bodies)
    materials.chassisBody = new THREE.MeshStandardMaterial({
      color: 0x1e293b,
      metalness: 0.82,
      roughness: 0.28,
    });

    // 4. Tinted Acrylic / Tempered Glass Door
    materials.glassDoor = new THREE.MeshPhysicalMaterial({
      color: 0x0f172a,
      metalness: 0.1,
      roughness: 0.05,
      transmission: 0.85,
      transparent: true,
      opacity: 0.65,
      ior: 1.5,
    });

    // 5. NVIDIA Jetson Gold Heatsink
    materials.goldHeatsink = new THREE.MeshStandardMaterial({
      color: 0xf59e0b,
      metalness: 0.96,
      roughness: 0.15,
      emissive: 0x78350f,
      emissiveIntensity: 0.15,
    });

    // 6. Liquid Cooling Acrylic & Copper Blocks
    materials.copperBlock = new THREE.MeshStandardMaterial({
      color: 0xd97706,
      metalness: 0.95,
      roughness: 0.15,
    });

    materials.coolantTube = new THREE.MeshPhysicalMaterial({
      color: 0x38bdf8,
      metalness: 0.1,
      roughness: 0.05,
      transmission: 0.92,
      transparent: true,
      opacity: 0.88,
      emissive: 0x0284c7,
      emissiveIntensity: 0.65,
    });

    // 7. Victron Energy Signature Marine Blue
    materials.victronBlue = new THREE.MeshStandardMaterial({
      color: 0x0284c7,
      metalness: 0.5,
      roughness: 0.25,
    });

    // 8. Solar Photovoltaic Monocrystalline Cells
    materials.solarCells = new THREE.MeshStandardMaterial({
      color: 0x1d4ed8,
      metalness: 0.88,
      roughness: 0.1,
      emissive: 0x172554,
      emissiveIntensity: 0.25,
    });

    materials.solarFrame = new THREE.MeshStandardMaterial({
      color: 0x475569,
      metalness: 0.9,
      roughness: 0.2,
    });

    // 9. Space Satellite High-Fidelity Materials
    materials.satelliteGoldFoil = new THREE.MeshStandardMaterial({
      color: 0xfbbf24,
      metalness: 0.98,
      roughness: 0.12,
      emissive: 0xb45309,
      emissiveIntensity: 0.35,
    });

    materials.satelliteSolarCells = new THREE.MeshStandardMaterial({
      color: 0x2563eb,
      metalness: 0.92,
      roughness: 0.08,
      emissive: 0x1d4ed8,
      emissiveIntensity: 0.45,
    });

    materials.ionPlume = new THREE.MeshBasicMaterial({
      color: 0x38bdf8,
      transparent: true,
      opacity: 0.85,
    });

    // 10. Space Phased Array Aerodynamic Composite Face
    materials.spaceTerminalBody = new THREE.MeshStandardMaterial({
      color: 0x1e293b,
      metalness: 0.9,
      roughness: 0.2,
    });

    materials.phasedArrayAperture = new THREE.MeshStandardMaterial({
      color: 0x312e81,
      metalness: 0.75,
      roughness: 0.25,
      emissive: 0x6366f1,
      emissiveIntensity: 0.5,
    });

    // 11. Status LEDs & Emissives
    materials.ledGreen = new THREE.MeshBasicMaterial({ color: 0x10b981 });
    materials.ledBlue = new THREE.MeshBasicMaterial({ color: 0x38bdf8 });
    materials.ledPurple = new THREE.MeshBasicMaterial({ color: 0xc084fc });
    materials.ledAmber = new THREE.MeshBasicMaterial({ color: 0xf59e0b });
    materials.ledRed = new THREE.MeshBasicMaterial({ color: 0xf43f5e });

    // 12. Foundation Pad Perimeter Neon Glow
    materials.padGlow = new THREE.MeshBasicMaterial({
      color: 0x10b981,
      transparent: true,
      opacity: 0.7,
    });

    // 13. Thermal Heatmap Material Generator
    materials.thermalShader = new THREE.MeshStandardMaterial({
      color: 0xef4444,
      roughness: 0.5,
      metalness: 0.1,
      emissive: 0x7f1d1d,
      emissiveIntensity: 0.5,
    });
  }

  function createScreenCanvasTexture(title, subtitle, statusText) {
    const canvas = document.createElement('canvas');
    canvas.width = 512;
    canvas.height = 256;
    const ctx = canvas.getContext('2d');

    // Cyber Dark Background
    ctx.fillStyle = '#020617';
    ctx.fillRect(0, 0, canvas.width, canvas.height);

    // Accent Header
    ctx.fillStyle = '#10b981';
    ctx.fillRect(0, 0, canvas.width, 24);

    ctx.fillStyle = '#020617';
    ctx.font = 'bold 14px monospace';
    ctx.fillText('SOVEREIGN OS — NODE TELEMETRY', 12, 18);

    // Dynamic Text Content
    ctx.fillStyle = '#38bdf8';
    ctx.font = 'bold 22px monospace';
    ctx.fillText(title || 'NVIDIA JETSON ORIN AGX', 16, 64);

    ctx.fillStyle = '#94a3b8';
    ctx.font = '16px monospace';
    ctx.fillText(subtitle || 'Load: 24% | Temp: 42.1°C', 16, 96);

    ctx.fillStyle = '#10b981';
    ctx.font = 'bold 18px monospace';
    ctx.fillText(statusText || 'STATUS: 100% OFF-GRID NOMINAL', 16, 136);

    // Waveform line
    ctx.strokeStyle = '#38bdf8';
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(16, 200);
    for (let x = 16; x < 496; x += 16) {
      const y = 200 + Math.sin(x * 0.05) * 15 + (Math.random() - 0.5) * 6;
      ctx.lineTo(x, y);
    }
    ctx.stroke();

    const texture = new THREE.CanvasTexture(canvas);
    return new THREE.MeshBasicMaterial({ map: texture });
  }

  return {
    init,
    get: (name) => materials[name],
    createScreenCanvasTexture,
  };
})();
