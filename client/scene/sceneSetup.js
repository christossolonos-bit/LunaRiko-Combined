import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';

import { RENDER } from '../config.js';

/**
 * Creates and returns the core Three.js scene objects: renderer, scene, camera, controls.
 * Tweak the defaults below or pass overrides to customize.
 */

const DEFAULTS = {
  // Renderer
  antialias: true,
  alpha: true, // transparent background (for OBS chroma key, etc.)
  shadowMap: true,

  // Camera
  fov: 30,
  near: 0.1,
  far: 50,
  cameraPosition: [0, 1, 0.9],
  controlsTarget: [0, 1.1, 0],

  // Scene
  background: null, // null = transparent, or set to a THREE.Color

  // Lighting
  lights: [
    { type: 'directional', color: 0xffffff, intensity: 1, position: [3, 15, -5] },
    { type: 'ambient', color: 0xffffff, intensity: 2.1 },
  ],

  // Debug helpers (set to true to enable)
  gridHelper: false,
  axesHelper: false,
};

export function createScene(overrides = {}) {
  const opts = { ...DEFAULTS, ...overrides };

  // --- Renderer ---
  const renderer = new THREE.WebGLRenderer({
    antialias: opts.antialias,
    alpha: opts.alpha,
  });
  renderer.setSize(window.innerWidth, window.innerHeight);
  // Pixel ratio is the single biggest cost lever on a phone — see RENDER in
  // client/config.js. 0 means "no cap", i.e. the device's own ratio.
  const dpr = RENDER.pixelRatioCap
    ? Math.min(window.devicePixelRatio, RENDER.pixelRatioCap)
    : window.devicePixelRatio;
  renderer.setPixelRatio(dpr);
  console.log(`[scene] pixel ratio ${dpr} (device ${window.devicePixelRatio}, cap ${RENDER.pixelRatioCap || 'off'})`);
  if (opts.shadowMap) {
    renderer.shadowMap.enabled = true;
    renderer.shadowMap.type = THREE.PCFSoftShadowMap;
  }
  document.body.appendChild(renderer.domElement);

  // The GPU can drop the WebGL context — a driver update, waking from sleep,
  // another program taking the GPU — and the result is a black canvas with no
  // error anyone would see. Every bit of state lives on the server, so a
  // reload is a complete recovery; there is nothing to restore by hand.
  renderer.domElement.addEventListener('webglcontextlost', () => {
    console.error('[scene] WebGL context lost — reloading the page');
    location.reload();
  });

  // --- Scene ---
  const scene = new THREE.Scene();
  if (opts.background !== null) {
    scene.background = opts.background instanceof THREE.Color
      ? opts.background
      : new THREE.Color(opts.background);
  }

  // --- Camera ---
  const camera = new THREE.PerspectiveCamera(
    opts.fov,
    window.innerWidth / window.innerHeight,
    opts.near,
    opts.far,
  );
  camera.position.set(...opts.cameraPosition);

  // --- Controls ---
  const controls = new OrbitControls(camera, renderer.domElement);
  controls.target.set(...opts.controlsTarget);
  controls.update();

  // --- Lighting ---
  // Reads the exact shape the lighting sandbox (lightlab.html) copies out, so
  // a rig tuned there can be pasted straight into DEFAULTS.lights above.
  for (const lightDef of opts.lights) {
    let light;
    if (lightDef.type === 'directional') {
      light = new THREE.DirectionalLight(lightDef.color, lightDef.intensity);
    } else if (lightDef.type === 'ambient') {
      light = new THREE.AmbientLight(lightDef.color, lightDef.intensity);
    } else if (lightDef.type === 'point') {
      light = new THREE.PointLight(lightDef.color, lightDef.intensity);
    } else if (lightDef.type === 'spot') {
      light = new THREE.SpotLight(lightDef.color, lightDef.intensity);
      if (lightDef.angle !== undefined) light.angle = lightDef.angle;
      if (lightDef.penumbra !== undefined) light.penumbra = lightDef.penumbra;
    } else if (lightDef.type === 'hemisphere') {
      light = new THREE.HemisphereLight(
        lightDef.color, lightDef.groundColor ?? 0x444444, lightDef.intensity);
    }
    if (!light) {
      console.warn(`[scene] unknown light type "${lightDef.type}" — skipped`);
      continue;
    }

    if (lightDef.position) light.position.set(...lightDef.position);

    // Where a light AIMS is half of what it looks like, and three.js aims
    // every directional/spot at the origin unless told otherwise — a key light
    // pointed at the floor is a different picture from one pointed at her
    // face. Omit `target` and you get the old origin-aimed behaviour.
    if (lightDef.target && light.target) {
      light.target.position.set(...lightDef.target);
      scene.add(light.target);
    }

    // Kept in step with client/lightlab/rig.js by hand (5 lines, and the
    // sandbox must stay deletable without touching this file). If the shadows
    // look different here than in the lab, these are the numbers that drifted.
    if (lightDef.castShadow && light.shadow) {
      light.castShadow = true;
      light.shadow.mapSize.set(2048, 2048);
      light.shadow.bias = -0.0008;
      if (light.shadow.camera.isOrthographicCamera) {
        const c = light.shadow.camera;
        c.left = -3; c.right = 3; c.top = 3; c.bottom = -3;
        c.near = 0.1; c.far = 20;
        c.updateProjectionMatrix();
      }
    }

    scene.add(light);
  }

  // --- Debug helpers ---
  if (opts.gridHelper) {
    const size = typeof opts.gridHelper === 'number' ? opts.gridHelper : 50;
    scene.add(new THREE.GridHelper(size, size));
  }
  if (opts.axesHelper) {
    const size = typeof opts.axesHelper === 'number' ? opts.axesHelper : 5;
    scene.add(new THREE.AxesHelper(size));
  }

  // --- Resize handler ---
  window.addEventListener('resize', () => {
    camera.aspect = window.innerWidth / window.innerHeight;
    camera.updateProjectionMatrix();
    renderer.setSize(window.innerWidth, window.innerHeight);
  });

  return { renderer, scene, camera, controls };
}

/**
 * Tone mapping, exposure and shadows — the "film" half of how a scene looks.
 *
 * Split out from createScene because it is the part you change at runtime: the
 * lighting sandbox drives it from sliders, and app.js applies it once from a
 * scene preset. Both read the same names, so a value tuned in the sandbox means
 * the same thing on the real page.
 */
// Names come from scenePresets.js; the constant is derived, so there is no
// second list here to fall out of step with it.
const toneMappingConst = (name) =>
  THREE[name === 'None' ? 'NoToneMapping' : `${name}ToneMapping`] ?? THREE.NoToneMapping;

export function applyFilmSettings(renderer, scene, settings = {}) {
  const { toneMapping = 'None', exposure = 1, shadows = true } = settings;
  renderer.toneMapping = toneMappingConst(toneMapping);
  renderer.toneMappingExposure = exposure;
  renderer.shadowMap.enabled = shadows;
  // Turning the shadow map on or off changes every material's shader program.
  if (scene) scene.traverse((o) => { if (o.isMesh && o.material) o.material.needsUpdate = true; });
  console.log(`[scene] tone=${toneMapping} exposure=${exposure} shadows=${shadows}`);
}
