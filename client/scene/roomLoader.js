import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';

/**
 * Loads a single room (environment) GLB into the scene.
 * Shares loader/pattern with objectLoader but applies room-specific tweaks:
 *   - fixDepth: per-mesh depthWrite / polygonOffset to suppress z-fighting
 *     (ported from client/reference/app-RIKOROOM.js)
 * Works automatically in VR — the room lives in the same scene graph as the
 * VR dolly and controllers.
 */

const loader = new GLTFLoader();

export async function loadRoom(scene, config) {
  if (!config || !config.enabled || !config.url) return null;

  try {
    const gltf = await loader.loadAsync(config.url);
    const model = gltf.scene;

    const [px, py, pz] = config.position ?? [0, 0, 0];
    const [rx, ry, rz] = config.rotation ?? [0, 0, 0];
    const s = config.scale ?? [1, 1, 1];
    const [sx, sy, sz] = Array.isArray(s) ? s : [s, s, s];

    model.position.set(px, py, pz);
    model.rotation.set(rx, ry, rz);
    model.scale.set(sx, sy, sz);
    model.name = 'room';

    model.traverse((child) => {
      if (!child.isMesh) return;
      child.castShadow = false;
      child.receiveShadow = true;

      if (config.fixDepth) {
        child.geometry.computeBoundingBox();
        child.geometry.computeBoundingSphere();
        child.geometry.computeVertexNormals();

        if (child.material) {
          child.material.depthWrite = true;
          child.material.depthTest = true;
          child.material.polygonOffset = true;
          child.material.polygonOffsetFactor = -1;
          child.material.polygonOffsetUnits = -1;
        }

        child.updateMatrix();
        child.updateMatrixWorld(true);
      }
    });

    scene.add(model);
    console.log(`🏠 Room loaded: ${config.url}`);
    return model;
  } catch (err) {
    console.error(`Failed to load room from ${config.url}:`, err);
    return null;
  }
}

/**
 * Re-seat a loaded room: always floor-aligned and centred on the avatar, with
 * `autoFit` controlling only the SCALE.
 *
 * Downloaded rooms come in wildly different units — one of the classrooms in
 * this repo is ~32 units tall — so a per-room table of magic transforms would
 * be a table to maintain. This measures instead.
 *
 * It is a starting guess, not a solve: a room whose bounding box includes
 * ground or scenery BELOW its floor will seat the avatar on top of that. The
 * `offset` is what corrects it, and lightlab.html is where you find the number.
 *
 * @param {THREE.Object3D} model - the room returned by loadRoom
 * @param {Object} opts - autoFit, targetHeight, scaleMul, rotationY, offset
 */
export function fitRoom(model, opts = {}) {
  if (!model) return;
  const {
    autoFit = true, targetHeight = 3, scaleMul = 1,
    rotationY = 0, offset = [0, 0, 0],
  } = opts;

  model.position.set(0, 0, 0);
  model.rotation.set(0, rotationY, 0);
  model.scale.setScalar(1);
  model.updateMatrixWorld(true);

  let s = scaleMul;
  if (autoFit) {
    const raw = new THREE.Box3().setFromObject(model);
    const h = raw.max.y - raw.min.y;
    if (h > 1e-4) s *= targetHeight / h;
  }
  model.scale.setScalar(s);
  model.updateMatrixWorld(true);

  const box = new THREE.Box3().setFromObject(model);
  const c = box.getCenter(new THREE.Vector3());
  model.position.set(
    -c.x + offset[0],
    -box.min.y + offset[1],
    -c.z + offset[2],
  );

  const size = box.getSize(new THREE.Vector3());
  console.log(`[room] fitted x${s.toFixed(4)} -> `
    + `${size.x.toFixed(1)} x ${size.y.toFixed(1)} x ${size.z.toFixed(1)} m`);
}
