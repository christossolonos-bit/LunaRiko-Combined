import * as THREE from 'three';

/**
 * The lights, as a live rig you can add to, remove from and drag around.
 *
 * Each entry pairs a light definition (the plain object that gets serialised
 * back into sceneSetup.js) with the THREE light it drives, a small coloured
 * marker sphere that exists ONLY so there is something to click and drag, and
 * the stock three.js helper that shows where the light is aiming.
 *
 * The def is the source of truth. `sync()` pushes it into the THREE objects;
 * dragging pushes the marker's position back into the def. Nothing else writes.
 */

export const LIGHT_TYPES = ['directional', 'point', 'spot', 'ambient', 'hemisphere'];

/** Point and spot lights fall off with distance², so they need far bigger numbers. */
export const INTENSITY_MAX = { point: 300, spot: 300, directional: 10, ambient: 10, hemisphere: 10 };

const MARKER_GEO = new THREE.SphereGeometry(0.06, 12, 12);

function makeLight(type) {
  switch (type) {
    case 'directional': return new THREE.DirectionalLight(0xffffff, 1);
    case 'point':       return new THREE.PointLight(0xffffff, 1);
    case 'spot':        return new THREE.SpotLight(0xffffff, 1);
    case 'ambient':     return new THREE.AmbientLight(0xffffff, 1);
    case 'hemisphere':  return new THREE.HemisphereLight(0xffffff, 0x444444, 1);
    default:            throw new Error(`unknown light type: ${type}`);
  }
}

function makeHelper(light, type) {
  try {
    if (type === 'directional') return new THREE.DirectionalLightHelper(light, 0.3);
    if (type === 'point')       return new THREE.PointLightHelper(light, 0.15);
    if (type === 'spot')        return new THREE.SpotLightHelper(light);
    if (type === 'hemisphere')  return new THREE.HemisphereLightHelper(light, 0.3);
  } catch (e) {
    console.warn(`[rig] no helper for ${type}:`, e);
  }
  return null;   // ambient has no position, so nothing to draw
}

export class LightRig {
  constructor(scene) {
    this.scene = scene;
    this.entries = [];
    this.helpersVisible = true;
    // Markers and helpers must never darken the render — they live on a layer
    // the camera can be told to ignore, which is how "hide gizmos" works.
    this.gizmos = new THREE.Group();
    this.gizmos.name = 'lightlab_gizmos';
    scene.add(this.gizmos);
  }

  /** Build the rig from a list of defs, replacing whatever is there. */
  setAll(defs) {
    while (this.entries.length) this.remove(this.entries[0]);
    for (const def of defs) this.add(def);
    console.log(`[rig] ${this.entries.length} lights: ${this.entries.map((e) => e.def.name).join(', ')}`);
  }

  add(def) {
    const light = makeLight(def.type);
    light.name = `light_${def.name}`;
    this.scene.add(light);
    if (light.target) this.scene.add(light.target);

    let marker = null;
    let helper = null;
    if (def.type !== 'ambient') {
      marker = new THREE.Mesh(MARKER_GEO, new THREE.MeshBasicMaterial({ color: def.color }));
      marker.name = `marker_${def.name}`;
      marker.userData.lightEntry = true;
      this.gizmos.add(marker);
      helper = makeHelper(light, def.type);
      if (helper) this.gizmos.add(helper);
    }

    const entry = { def, light, marker, helper };
    this.entries.push(entry);
    this.sync(entry);
    return entry;
  }

  remove(entry) {
    const i = this.entries.indexOf(entry);
    if (i < 0) return;
    this.entries.splice(i, 1);
    this.scene.remove(entry.light);
    if (entry.light.target) this.scene.remove(entry.light.target);
    entry.light.dispose?.();
    if (entry.marker) { this.gizmos.remove(entry.marker); entry.marker.material.dispose(); }
    if (entry.helper) { this.gizmos.remove(entry.helper); entry.helper.dispose?.(); }
    console.log(`[rig] removed ${entry.def.name}`);
  }

  /** Type changed — tear the THREE side down and rebuild it around the same def. */
  rebuild(entry) {
    const def = entry.def;
    const i = this.entries.indexOf(entry);
    this.remove(entry);
    const fresh = this.add(def);
    // keep panel order stable so the gui folders don't jump around
    this.entries.splice(this.entries.indexOf(fresh), 1);
    this.entries.splice(i, 0, fresh);
    return fresh;
  }

  /** Push the def into the THREE objects. Called on every panel change. */
  sync(entry) {
    const { def, light, marker, helper } = entry;
    light.color.set(def.color);
    light.intensity = def.intensity;

    if (def.type !== 'ambient') {
      light.position.set(...def.position);
      if (marker) {
        marker.position.set(...def.position);
        marker.material.color.set(def.color);
      }
    }
    if (light.target) {
      light.target.position.set(...def.target);
      light.target.updateMatrixWorld();
    }
    if (light.isHemisphereLight) light.groundColor.set(def.groundColor ?? '#404040');
    if (light.isSpotLight) {
      light.angle = def.angle ?? Math.PI / 6;
      light.penumbra = def.penumbra ?? 0.4;
      light.decay = def.decay ?? 2;
    }
    if (light.isPointLight) light.decay = def.decay ?? 2;

    // Shadows are only ever enabled on a light the user asked for one on: each
    // shadow-casting light is a whole extra render pass of the scene.
    // Guard on `.shadow`, not on `castShadow`: ambient and hemisphere inherit
    // castShadow from Object3D but have no shadow camera to configure.
    if (light.shadow) {
      light.castShadow = !!def.castShadow;
      if (light.castShadow) {
        // A POINT light shines every direction, so its shadow is a CUBE — six
        // renders of the whole scene per frame, not one. At 2048 that is ~25
        // million shadow texels a frame for one light. Six 512s is a twentieth
        // of that and looks the same at avatar scale.
        const res = light.isPointLight ? 512 : 2048;
        light.shadow.mapSize.set(res, res);
        light.shadow.bias = -0.0008;
        if (light.shadow.camera.isOrthographicCamera) {
          const c = light.shadow.camera;
          c.left = -3; c.right = 3; c.top = 3; c.bottom = -3;
          c.near = 0.1; c.far = 20;
          c.updateProjectionMatrix();
        }
      }
    }
    helper?.update?.();
  }

  syncAll() { for (const e of this.entries) this.sync(e); }

  setHelpersVisible(v) {
    this.helpersVisible = v;
    this.gizmos.visible = v;
  }

  /** Called each frame — directional/spot helpers do not follow on their own. */
  update() {
    if (!this.helpersVisible) return;
    for (const e of this.entries) e.helper?.update?.();
  }

  /** Every draggable marker, for the click-to-select raycast. */
  markers() { return this.entries.filter((e) => e.marker).map((e) => e.marker); }

  entryForMarker(marker) { return this.entries.find((e) => e.marker === marker) || null; }
}
