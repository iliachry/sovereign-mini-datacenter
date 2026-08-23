/**
 * Sovereign Mini Datacenter — Space DTN (RFC 9171) & Orbital Satellite Tracking
 */

const SpaceManager = (function () {
  let spaceGroup, satelliteMesh, orbitRingMesh, spaceBeamMesh, satLight, satLabel;
  let dataPulses = [];
  let satAngle = 0.75;
  const orbitRadiusX = 880;
  const orbitRadiusZ = 780;
  const orbitY = 560;
  let pulseSpeed = 0.008;

  function createSatelliteHUDLabel() {
    const canvas = document.createElement('canvas');
    canvas.width = 384;
    canvas.height = 96;
    const ctx = canvas.getContext('2d');

    // Rounded background pill
    ctx.fillStyle = 'rgba(15, 23, 42, 0.85)';
    ctx.strokeStyle = '#c084fc';
    ctx.lineWidth = 3;
    ctx.beginPath();
    ctx.roundRect(6, 6, 372, 84, 16);
    ctx.fill();
    ctx.stroke();

    // Beacon dot
    ctx.fillStyle = '#10b981';
    ctx.beginPath();
    ctx.arc(36, 48, 10, 0, 2 * Math.PI);
    ctx.fill();

    // Satellite Callout Text
    ctx.fillStyle = '#f8fafc';
    ctx.font = 'bold 24px monospace';
    ctx.fillText('SOVEREIGN-LEO-1', 60, 42);

    ctx.fillStyle = '#38bdf8';
    ctx.font = 'bold 18px monospace';
    ctx.fillText('550 km · S-BAND ACTIVE', 60, 72);

    const texture = new THREE.CanvasTexture(canvas);
    const spriteMat = new THREE.SpriteMaterial({ map: texture, transparent: true, opacity: 0.95 });
    const sprite = new THREE.Sprite(spriteMat);
    sprite.scale.set(160, 40, 1);
    sprite.position.set(0, 95, 0);
    return sprite;
  }

  function init(scene) {
    spaceGroup = new THREE.Group();
    scene.add(spaceGroup);

    // 1. Glowing Orbital Trajectory Ring
    const orbitCurve = new THREE.EllipseCurve(0, 0, orbitRadiusX, orbitRadiusZ, 0, 2 * Math.PI, false, 0);
    const orbitPoints = orbitCurve.getPoints(120).map((p) => new THREE.Vector3(p.x, orbitY, p.y));
    const orbitGeom = new THREE.BufferGeometry().setFromPoints(orbitPoints);
    const orbitMat = new THREE.LineDashedMaterial({
      color: 0xc084fc,
      dashSize: 28,
      gapSize: 16,
      linewidth: 3,
      transparent: true,
      opacity: 0.7,
    });
    orbitRingMesh = new THREE.Line(orbitGeom, orbitMat);
    orbitRingMesh.computeLineDistances();
    spaceGroup.add(orbitRingMesh);

    // 2. High-Fidelity Sovereign LEO Satellite Relay (Large & Prominent)
    const satGroup = new THREE.Group();

    // Satellite Bus Body with Gleaming Gold MLI Thermal Foil
    const satBody = new THREE.Mesh(
      new THREE.BoxGeometry(110, 75, 75),
      MaterialsRegistry.get('satelliteGoldFoil')
    );
    satBody.castShadow = true;
    satGroup.add(satBody);

    // Large Silver Parabolic / Horn Antenna Dish
    const satDish = new THREE.Mesh(
      new THREE.ConeGeometry(38, 20, 32, 1, true),
      new THREE.MeshStandardMaterial({ color: 0xf8fafc, metalness: 0.95, roughness: 0.1, side: THREE.DoubleSide })
    );
    satDish.position.set(0, -48, 0);
    satDish.rotation.x = Math.PI;
    satGroup.add(satDish);

    // Emissive Feedhorn
    const feedhorn = new THREE.Mesh(
      new THREE.CylinderGeometry(4, 4, 25, 16),
      MaterialsRegistry.get('ledPurple')
    );
    feedhorn.position.set(0, -58, 0);
    satGroup.add(feedhorn);

    // Large High-Efficiency Dual GaAs Blue Solar Wings
    const wingFrameMat = MaterialsRegistry.get('solarFrame');
    const wingCellMat = MaterialsRegistry.get('satelliteSolarCells');

    [-175, 175].forEach((x) => {
      const wingFrame = new THREE.Mesh(new THREE.BoxGeometry(220, 6, 85), wingFrameMat);
      wingFrame.position.set(x, 0, 0);
      satGroup.add(wingFrame);

      const wingCells = new THREE.Mesh(new THREE.BoxGeometry(210, 4, 75), wingCellMat);
      wingCells.position.set(x, 2, 0);
      satGroup.add(wingCells);
    });

    // Wingtip Flashing Beacons
    const beaconR = new THREE.Mesh(new THREE.SphereGeometry(4, 12, 12), MaterialsRegistry.get('ledRed'));
    beaconR.position.set(-290, 0, 0);
    satGroup.add(beaconR);

    const beaconG = new THREE.Mesh(new THREE.SphereGeometry(4, 12, 12), MaterialsRegistry.get('ledGreen'));
    beaconG.position.set(290, 0, 0);
    satGroup.add(beaconG);

    // Cyan Ion Engine Thruster Plume at Rear
    const ionCone = new THREE.Mesh(
      new THREE.ConeGeometry(14, 40, 16, 1, true),
      MaterialsRegistry.get('ionPlume')
    );
    ionCone.position.set(0, 0, -55);
    ionCone.rotation.x = -Math.PI / 2;
    satGroup.add(ionCone);

    // Dedicated Point Light attached to Satellite for Brilliant Illumination
    satLight = new THREE.PointLight(0xfef08a, 1.8, 900);
    satLight.position.set(0, 30, 40);
    satGroup.add(satLight);

    // Floating 3D Holographic Label above Satellite
    satLabel = createSatelliteHUDLabel();
    satGroup.add(satLabel);

    satelliteMesh = satGroup;
    satelliteMesh.position.set(orbitRadiusX * Math.cos(satAngle), orbitY, orbitRadiusZ * Math.sin(satAngle));
    spaceGroup.add(satelliteMesh);

    // 3. Volumetric Glowing RF Laser/Microwave Beam
    const beamGeom = new THREE.CylinderGeometry(14, 5, 1, 16, 1, true);
    const beamMat = new THREE.MeshBasicMaterial({
      color: 0xc084fc,
      transparent: true,
      opacity: 0.5,
      side: THREE.DoubleSide,
      depthWrite: false,
    });
    spaceBeamMesh = new THREE.Mesh(beamGeom, beamMat);
    spaceGroup.add(spaceBeamMesh);

    // 4. Data Transmission Pulses (RFC 9171 DTN Bundles)
    for (let i = 0; i < 5; i++) {
      const pulse = new THREE.Mesh(
        new THREE.RingGeometry(8, 18, 20),
        new THREE.MeshBasicMaterial({ color: 0x38bdf8, transparent: true, opacity: 0.85, side: THREE.DoubleSide, depthWrite: false })
      );
      pulse.userData.offset = i * 0.2;
      dataPulses.push(pulse);
      spaceGroup.add(pulse);
    }
  }

  function update() {
    if (!satelliteMesh || !spaceBeamMesh) return;

    // Orbital progress around Earth trajectory
    satAngle += 0.0007;
    const satX = orbitRadiusX * Math.cos(satAngle);
    const satZ = orbitRadiusZ * Math.sin(satAngle);
    satelliteMesh.position.set(satX, orbitY, satZ);
    satelliteMesh.rotation.y = -satAngle + Math.PI / 2;

    // Connect beam between ground phased array terminal (0, 280, 0) and Satellite
    const groundPos = new THREE.Vector3(0, 280, 0);
    const satPos = satelliteMesh.position;

    const distance = groundPos.distanceTo(satPos);
    const midPoint = new THREE.Vector3().addVectors(groundPos, satPos).multiplyScalar(0.5);

    spaceBeamMesh.position.copy(midPoint);
    spaceBeamMesh.scale.set(1, distance, 1);

    const orientation = new THREE.Matrix4();
    orientation.lookAt(groundPos, satPos, new THREE.Vector3(0, 1, 0));
    orientation.multiply(new THREE.Matrix4().makeRotationX(Math.PI / 2));
    spaceBeamMesh.quaternion.setFromRotationMatrix(orientation);

    // Animate travelling DTN pulses
    dataPulses.forEach((pulse) => {
      pulse.userData.offset = (pulse.userData.offset + pulseSpeed) % 1.0;
      const pulsePos = new THREE.Vector3().lerpVectors(groundPos, satPos, pulse.userData.offset);
      pulse.position.copy(pulsePos);
      pulse.quaternion.copy(spaceBeamMesh.quaternion);
      pulse.scale.set(1 + pulse.userData.offset * 2.0, 1 + pulse.userData.offset * 2.0, 1);
    });
  }

  function triggerSpaceTransmission() {
    pulseSpeed = 0.025;
    spaceBeamMesh.material.opacity = 0.9;
    spaceBeamMesh.material.color.setHex(0x38bdf8);

    setTimeout(() => {
      pulseSpeed = 0.008;
      spaceBeamMesh.material.opacity = 0.5;
      spaceBeamMesh.material.color.setHex(0xc084fc);
    }, 2500);
  }

  return {
    init,
    update,
    triggerSpaceTransmission,
    getSatellite: () => satelliteMesh,
  };
})();
