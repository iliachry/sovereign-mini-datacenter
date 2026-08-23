/**
 * Sovereign Mini Datacenter — PBR Materials & Shaders Registry
 */

const MaterialsRegistry = (function () {
  const materials = {};

  function init() {
    // 1. Structural Anodized Aluminum (Rack frame, extruded profiles)
    materials.aluminum = new THREE.MeshStandardMaterial({
      color: 0x64748b,
      metalness: 0.85,
      roughness: 0.25,
      envMapIntensity: 1.0,
    });

    // 2. EIA-310-D Powder-Coated Steel (Internal vertical rails, 1U-9U markings)
    materials.steelRail = new THREE.MeshStandardMaterial({
      color: 0x0f172a,
      metalness: 0.92,
      roughness: 0.18,
    });

    // 3. Matte Chassis Dark Steel (Server enclosures, PDU, switch bodies)
    materials.chassisBody = new THREE.MeshStandardMaterial({
      color: 0x1e293b,
      metalness: 0.8,
      roughness: 0.35,
    });

    // 4. Tinted Acrylic / Tempered Glass Door
    materials.glassDoor = new THREE.MeshPhysicalMaterial({
      color: 0x090f1d,
      metalness: 0.1,
      roughness: 0.05,
      transmission: 0.85,
      transparent: true,
      opacity: 0.7,
      ior: 1.5,
    });

    // 5. NVIDIA Jetson Gold Heatsink
    materials.goldHeatsink = new THREE.MeshStandardMaterial({
      color: 0xd97706,
      metalness: 0.95,
      roughness: 0.2,
    });

    // 6. Liquid Cooling Acrylic & Copper Blocks
    materials.copperBlock = new THREE.MeshStandardMaterial({
      color: 0xb45309,
      metalness: 0.95,
      roughness: 0.15,
    });

    materials.coolantTube = new THREE.MeshPhysicalMaterial({
      color: 0x38bdf8,
      metalness: 0.1,
      roughness: 0.1,
      transmission: 0.9,
      transparent: true,
      opacity: 0.85,
      emissive: 0x0284c7,
      emissiveIntensity: 0.3,
    });

    // 7. Victron Energy Signature Marine Blue
    materials.victronBlue = new THREE.MeshStandardMaterial({
      color: 0x0284c7,
      metalness: 0.45,
      roughness: 0.3,
    });

    // 8. Solar Photovoltaic Monocrystalline Cells
    materials.solarCells = new THREE.MeshStandardMaterial({
      color: 0x172554,
      metalness: 0.85,
      roughness: 0.12,
    });

    materials.solarFrame = new THREE.MeshStandardMaterial({
      color: 0x334155,
      metalness: 0.9,
      roughness: 0.25,
    });

    // 9. Space Phased Array Aerodynamic Composite Face
    materials.spaceTerminalBody = new THREE.MeshStandardMaterial({
      color: 0x0f172a,
      metalness: 0.9,
      roughness: 0.2,
    });

    materials.phasedArrayAperture = new THREE.MeshStandardMaterial({
      color: 0x1e1b4b,
      metalness: 0.7,
      roughness: 0.3,
      emissive: 0x4c1d95,
      emissiveIntensity: 0.3,
    });

    // 10. Status LEDs & Emissives
    materials.ledGreen = new THREE.MeshBasicMaterial({ color: 0x10b981 });
    materials.ledBlue = new THREE.MeshBasicMaterial({ color: 0x38bdf8 });
    materials.ledPurple = new THREE.MeshBasicMaterial({ color: 0xc084fc });
    materials.ledAmber = new THREE.MeshBasicMaterial({ color: 0xf59e0b });
    materials.ledRed = new THREE.MeshBasicMaterial({ color: 0xf43f5e });

    // 11. Thermal Heatmap Material Generator
    materials.thermalShader = new THREE.MeshStandardMaterial({
      color: 0xef4444,
      roughness: 0.5,
      metalness: 0.1,
      emissive: 0x7f1d1d,
      emissiveIntensity: 0.4,
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
