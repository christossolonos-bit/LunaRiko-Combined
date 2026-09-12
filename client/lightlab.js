import * as THREE from 'three';

import { CHARACTER_DEFAULTS } from './config.js';
import { createScene, applyFilmSettings } from './scene/sceneSetup.js';
import { loadRoom, fitRoom } from './scene/roomLoader.js';
import { loadVRM } from './core/vrmLoader.js';
import { AudioManager } from './core/audioManager.js';
import { AnimationManager } from './animation/animationManager.js';
import { MovementController } from './animation/movementController.js';

import { Bokeh } from './core/bokeh.js';
import { LightRig } from './lightlab/rig.js';
import { MToonTuner } from './core/mtoonTuner.js';
import { buildPanel } from './lightlab/panel.js';
import { loadSetup, saveSetup } from './lightlab/setup.js';

/**
 * Lighting sandbox — a SECOND page, at http://127.0.0.1:5173/lightlab.html
 *
 * Needs no FastAPI server: no websocket, no chat, no audio — just the avatar,
 * a room, and lights you can drag.
 *
 * The dependency runs ONE WAY. This page imports the real app's pieces —
 * scene/sceneSetup, scene/roomLoader, scene/scenePresets, core/mtoonTuner,
 * core/bokeh — and app.js imports nothing from `lightlab/`. So the sandbox is
 * still `rm -rf client/lightlab*` away from never having existed, and the look
 * it authors keeps working because it was never the sandbox's to begin with.
 *
 * What you tune here is saved into scene/scenePresets.js and loaded on the
 * normal page with `?scene=<name>`. Same numbers, same code, both sides.
 */

const setup = loadSetup();

// `?character=` wins over the saved value — that is how the panel's dropdown
// switches, and how you can link straight to one.
const urlChar = new URLSearchParams(location.search).get('character');
if (urlChar && CHARACTER_DEFAULTS[urlChar.toLowerCase()]) setup.character = urlChar.toLowerCase();
if (!CHARACTER_DEFAULTS[setup.character]) setup.character = 'riko';
saveSetup(setup);
console.log(`[lightlab] character=${setup.character} room=${setup.room.url || '(none)'}`);

const clock = new THREE.Clock();
let roomModel = null;

(async () => {
  // Lights are handed over empty: the rig owns every light on this page, so
  // sceneSetup's defaults can't quietly add an ambient on top of the recipe.
  const { renderer, scene, camera, controls } = createScene({
    lights: [],
    fov: setup.camera.fov,
    cameraPosition: setup.camera.position,
    controlsTarget: setup.camera.target,
  });

  function applyRenderer() {
    applyFilmSettings(renderer, scene, setup.renderer);
  }

  // A 1 m grid on y=0. Downloaded rooms rarely put their floor where their
  // bounding box bottom is, so "is she standing on the floor or on top of the
  // building" is a question you want answered by looking, not by guessing.
  const grid = new THREE.GridHelper(10, 10, 0x556070, 0x2a3038);
  grid.material.transparent = true;
  grid.material.opacity = 0.4;
  scene.add(grid);
  function applyGrid() { grid.visible = setup.renderer.grid; }

  function applyCamera() {
    camera.fov = setup.camera.fov;
    camera.updateProjectionMatrix();
  }

  function readCamera() {
    setup.camera.position = camera.position.toArray();
    setup.camera.target = controls.target.toArray();
  }

  function applyRoom() {
    fitRoom(roomModel, setup.room);
  }

  function applyAvatar() {
    if (!vrm) return;
    vrm.scene.position.set(...setup.avatar.position);
    vrm.scene.rotation.y = setup.avatar.rotationY;
  }

  applyRenderer();
  applyGrid();

  // --- Room ---
  if (setup.room.url) {
    roomModel = await loadRoom(scene, {
      enabled: true,
      url: setup.room.url,
      position: [0, 0, 0],
      rotation: [0, 0, 0],
      scale: [1, 1, 1],
      fixDepth: setup.room.fixDepth,
    });
    applyRoom();
  }

  // --- Avatar ---
  const vrmPath = CHARACTER_DEFAULTS[setup.character].vrm_path;
  const { vrm } = await loadVRM(vrmPath, scene);
  // vrmLoader turns casting on but not receiving — without this she never
  // shadows herself, which is most of what sells a hard key light.
  vrm.scene.traverse((o) => { if (o.isMesh) o.receiveShadow = true; });
  applyAvatar();

  // --- Idle animation (the same wiring the real app uses) ---
  const audioMgr = new AudioManager(vrm);
  const animationMgr = new AnimationManager(vrm, audioMgr, renderer, scene, camera, controls);
  animationMgr.setState('idle');
  const movementController = new MovementController(vrm, scene, animationMgr);
  // NOT awaited. The FBX is the slowest thing on the page and lighting is
  // perfectly tunable on a T-posed avatar — blocking the render loop on it
  // means a blank screen whenever it is slow, and a permanently blank screen
  // if it ever fails. It drops in whenever it arrives.
  movementController.loadIdleAnimation('./animations/mixamo/Idle.fbx')
    .catch((e) => console.warn('[lightlab] idle animation did not load:', e));

  // --- Rig + panel ---
  const rig = new LightRig(scene);
  rig.setAll(setup.lights);

  const mtoon = new MToonTuner(vrm);
  mtoon.apply(setup.mtoon);

  const bokeh = new Bokeh({ renderer, scene, camera, controls });
  bokeh.apply(setup.bokeh);
  // sceneSetup owns the renderer/camera resize; the composer has its own buffers.
  window.addEventListener('resize', () => bokeh.setSize(window.innerWidth, window.innerHeight));

  buildPanel({
    setup, rig, mtoon,
    ctx: {
      renderer, scene, camera, controls, vrm,
      characters: Object.keys(CHARACTER_DEFAULTS),
      bokeh,
      applyRenderer, applyRoom, applyAvatar, applyCamera, applyGrid, readCamera,
    },
  });

  window.lightlab = { setup, rig, mtoon, bokeh, scene, camera, renderer, vrm };
  console.log('[lightlab] ready — click a light marker to drag it, or use the panel');

  // --- Loop ---
  clock.start();
  renderer.setAnimationLoop(() => {
    const dt = clock.getDelta();
    // movementController before animationMgr — reversed, the mixer overwrites
    // the state-based neck rotation (see CLAUDE.md "Traps worth knowing").
    movementController.update(dt);
    animationMgr.update(dt);
    rig.update();
    vrm.update(dt);
    controls.update();
    // Draws straight to the canvas unless depth of field is on, in which case
    // the frame goes through the composer instead.
    bokeh.render();
  });
})();
