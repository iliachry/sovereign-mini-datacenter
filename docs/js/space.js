/**
 * Sovereign Mini Datacenter — Space DTN (RFC 9171) & Orbital Satellite Tracking
 */

const SpaceManager = (function () {
  let spaceGroup, satelliteMesh, orbitRingMesh, spaceBeamMesh;
  let dataPulses = [];
  let satAngle = 0.8;
  const orbitRadiusX = 1350;
  const orbitRadiusZ = 1150;
  const orbitY = 900;
  const pulseSpeed = 0.008;

  function init(scene) {
    spaceGroup = new THREE.Group();
    scene.add(spaceGroup);

    // 1. Glowing Orbital Trajectory Ring
    const orbitCurve = new THREE.EllipseCurve(0, 0, orbitRadiusX, orbitRadiusZ, 0, 2 * Math.PI, false, 0);
    const orbitPoints = orbitCurve.getPoints(120).map((p) => new THREE.Vector3(p.x, orbitY, p.y));
    const orbitGeom = new THREE.BufferGeometry().setFromPoints(orbitPoints);
    const orbitMat = new THREE.LineDashedMaterial({
      color: 0xc084fc,
      dashSize: 25,
      gapSize: 15,
      linewidth: 2,
      transparent: true,
      opacity: 0.5,
    });
    orbitRingMesh = new THREE.Line(orbitGeom, orbitMat);
    orbitRingMesh.computeLineDistances();
    spaceGroup.add(orbitRingMesh);

    // 2. High-Fidelity Sovereign LEO Satellite Relay
    const satGroup = new THREE.Group();

    const satBody = new THREE.Mesh(
      new THREE.BoxGeometry(60, 40, 40),
      new THREE.MeshStandardMaterial({ color: 0xd97706, metalness: 0.95, roughness: 0.15 })
    );
    satGroup.add(satBody);

    const satDish = new THREE.Mesh(
      new THREE.ConeGeometry(20, 10, 24, 1, true),
      new THREE.MeshStandardMaterial({ color: 0xe2e8f0, metalness: 0.9, side: THREE.DoubleSide })
    );
    satDish.position.set(0, -22, 0);
    satDish.rotation.x = Math.PI;
    satGroup.add(satDish);

    // GaAs Gold/Blue Solar Arrays
    const wingMat = new THREE.MeshStandardMaterial({ color: 0x1e3a8a, metalness: 0.9, roughness: 0.15 });
    [-110, 110].forEach((x) => {
      const wing = new THREE.Mesh(new THREE.BoxGeometry(140, 3, 50), wingMat);
      wing.position.set(x, 0, 0);
      satGroup.add(wing);
    });

    // Beacon Lights
    const beaconR = new THREE.Mesh(
      new THREE.SphereGeometry(2.5, 8, 8),
      new THREE.MeshBasicMaterial({ color: 0xef4444 })
    );
    beaconR.position.set(-185, 0, 0);
    satGroup.add(beaconR);

    const beaconG = new THREE.Mesh(
      new THREE.SphereGeometry(2.5, 8, 8),
      new THREE.MeshBasicMaterial({ color: 0x10b981 })
    );
    beaconG.position.set(185, 0, 0);
    satGroup.add(beaconG);

    satelliteMesh = satGroup;
    satelliteMesh.position.set(orbitRadiusX * Math.cos(satAngle), orbitY, orbitRadiusZ * Math.sin(satAngle));
    spaceGroup.add(satelliteMesh);

    // 3. Volumetric RF Laser/Microwave Beam with Subtle Glow
    const beamGeom = new THREE.CylinderGeometry(10, 3, 1, 16, 1, true);
    const beamMat = new THREE.MeshBasicMaterial({
      color: 0xc084fc,
      transparent: true,
      opacity: 0.35,
      side: THREE.DoubleSide,
      depthWrite: false,
    });
    spaceBeamMesh = new THREE.Mesh(beamGeom, beamMat);
    spaceGroup.add(spaceBeamMesh);

    // 4. Data Transmission Pulses (RFC 9171 Bundles)
    for (let i = 0; i < 4; i++) {
      const pulse = new THREE.Mesh(
        new THREE.RingGeometry(6, 12, 16),
        new THREE.MeshBasicMaterial({ color: 0x38bdf8, transparent: true, opacity: 0.75, side: THREE.DoubleSide, depthWrite: false })
      );
      pulse.userData.offset = i * 0.25;
      dataPulses.push(pulse);
      spaceGroup.add(pulse);
    }
  }

  function update() {
    if (!satelliteMesh || !spaceBeamMesh) return;

    // Slow orbital progress around Earth trajectory
    satAngle += 0.0008;
    const satX = orbitRadiusX * Math.cos(satAngle);
    const satZ = orbitRadiusZ * Math.sin(satAngle);
    satelliteMesh.position.set(satX, orbitY, satZ);
    satelliteMesh.rotation.y = -satAngle;

    // Connect beam between ground terminal (0, 290, 0) and Satellite
    const groundPos = new THREE.Vector3(0, 290, 0);
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
      pulse.scale.set(1 + pulse.userData.offset * 1.8, 1 + pulse.userData.offset * 1.8, 1);
    });
  }

  function triggerSpaceTransmission() {
    pulseSpeed = 0.025;
    spaceBeamMesh.material.opacity = 0.8;
    spaceBeamMesh.material.color.setHex(0x38bdf8);

    setTimeout(() => {
      pulseSpeed = 0.008;
      spaceBeamMesh.material.opacity = 0.35;
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
