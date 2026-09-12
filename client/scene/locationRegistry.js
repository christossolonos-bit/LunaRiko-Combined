import * as THREE from 'three';

/**
 * Unified registry for named coordinates in the scene.
 *
 * Sources:
 *   - NAMED_LOCATIONS (static waypoints from locationsConfig.js)
 *   - sceneObjects registry (live GLB props from objectLoader.js)
 *
 * Scene-object positions are read live, so moving an object updates its
 * resolved location automatically.
 */

function addDebugMarker(scene, name, position, color = 0xff3366) {
  const marker = new THREE.Mesh(
    new THREE.SphereGeometry(0.05, 16, 16),
    new THREE.MeshBasicMaterial({ color }),
  );
  marker.position.set(...position);
  marker.name = `__marker_${name}`;
  scene.add(marker);
}

export function createLocationRegistry({
  scene,
  namedLocations = [],
  sceneObjects = null,
  debugMarkers = false,
}) {
  const staticEntries = new Map();

  for (const loc of namedLocations) {
    const [x, y, z] = loc.position;
    staticEntries.set(loc.name, {
      name: loc.name,
      description: loc.description ?? '',
      source: 'named',
      getPosition: () => ({ x, y, z }),
    });

    if (debugMarkers && scene) addDebugMarker(scene, loc.name, loc.position);
  }

  function get(name) {
    if (staticEntries.has(name)) return staticEntries.get(name);
    if (sceneObjects?.has(name)) {
      const obj = sceneObjects.get(name);
      return {
        name,
        description: `Scene object (${obj.url})`,
        source: 'object',
        getPosition: obj.getPosition,
      };
    }
    return null;
  }

  function all() {
    const names = new Set(staticEntries.keys());
    if (sceneObjects) for (const n of sceneObjects.keys()) names.add(n);
    return [...names].map(get).filter(Boolean);
  }

  function describe() {
    const out = {};
    for (const loc of all()) {
      out[loc.name] = {
        position: loc.getPosition(),
        source: loc.source,
        description: loc.description,
      };
    }
    return out;
  }

  return { get, all, describe };
}
