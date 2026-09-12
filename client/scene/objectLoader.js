import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';

/**
 * Loads GLB objects from a config list into the scene and returns a registry
 * keyed by name. Positions are returned live via getPosition() so the AI
 * always sees the current world location even if the object is moved later.
 */

const loader = new GLTFLoader();

async function loadOne(entry) {
  const gltf = await loader.loadAsync(entry.url);
  const object3d = gltf.scene;

  const [px, py, pz] = entry.position ?? [0, 0, 0];
  const [rx, ry, rz] = entry.rotation ?? [0, 0, 0];
  const scale = entry.scale ?? [1, 1, 1];
  const [sx, sy, sz] = Array.isArray(scale) ? scale : [scale, scale, scale];

  object3d.position.set(px, py, pz);
  object3d.rotation.set(rx, ry, rz);
  object3d.scale.set(sx, sy, sz);
  object3d.name = entry.name;

  object3d.traverse((child) => {
    if (child.isMesh) {
      child.castShadow = true;
      child.receiveShadow = true;
    }
  });

  return object3d;
}

export async function loadSceneObjects(scene, config) {
  const registry = new Map();

  for (const entry of config) {
    try {
      const object3d = await loadOne(entry);
      scene.add(object3d);

      registry.set(entry.name, {
        name: entry.name,
        url: entry.url,
        object3d,
        getPosition() {
          const v = new THREE.Vector3();
          object3d.getWorldPosition(v);
          return { x: v.x, y: v.y, z: v.z };
        },
        getRotation() {
          return {
            x: object3d.rotation.x,
            y: object3d.rotation.y,
            z: object3d.rotation.z,
          };
        },
        getScale() {
          return {
            x: object3d.scale.x,
            y: object3d.scale.y,
            z: object3d.scale.z,
          };
        },
      });

      const p = registry.get(entry.name).getPosition();
      console.log(`📦 Loaded "${entry.name}" at (${p.x.toFixed(2)}, ${p.y.toFixed(2)}, ${p.z.toFixed(2)})`);
    } catch (err) {
      console.error(`Failed to load scene object "${entry.name}" from ${entry.url}:`, err);
    }
  }

  return registry;
}

/** Convenience: dump all object positions as plain JSON (for AI context). */
export function describeObjects(registry) {
  const out = {};
  for (const [name, obj] of registry) {
    out[name] = { position: obj.getPosition() };
  }
  return out;
}
