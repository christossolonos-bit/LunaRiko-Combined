import * as THREE from 'three';

import { VRM_PATH, WS_URL, TRANSPARENT_MODE, RENDER, CHARACTER, BACKDROP, CAMERA_DEFAULT } from './config.js';
import { createScene, applyFilmSettings } from './scene/sceneSetup.js';
import { getScenePreset } from './scene/scenePresets.js';
import { loadSceneObjects, describeObjects } from './scene/objectLoader.js';
import { SCENE_OBJECTS } from './scene/objectsConfig.js';
import { loadRoom, fitRoom } from './scene/roomLoader.js';
import { ROOM_CONFIG } from './scene/roomConfig.js';
import { loadBackdrop } from './scene/backdrop.js';
import { createLocationRegistry } from './scene/locationRegistry.js';
import { NAMED_LOCATIONS, DEBUG_MARKERS } from './scene/locationsConfig.js';
import { loadVRM } from './core/vrmLoader.js';
import { MToonTuner } from './core/mtoonTuner.js';
import { Bokeh } from './core/bokeh.js';
import { AudioManager } from './core/audioManager.js';
import { PlaybackController } from './core/playbackController.js';
import { AnimationManager } from './animation/animationManager.js';
import { MovementController } from './animation/movementController.js';
import { showSubtitleStreaming, setSubtitlesEnabled, getSubtitlesEnabled } from './overlay/subtitles.js';
import { initFpsMeter } from './overlay/fpsMeter.js';
import { initWebcam, takePictureAndUpload } from './interaction/webcam.js';
import { connectWS, initUI } from './ui.js';
import { initVRMClickDetector } from './interaction/vrmClickDetector.js';
import { isVRSupported } from './vr/vrManager.js';

// --- WebSocket + UI (early init, no dependencies) ---
function handleServerMessage(msg) {
  console.log("📩 Received from server:", msg);
}
connectWS(handleServerMessage);
initUI();

// Inside the Electron desktop shell the page background goes transparent so
// only the avatar is drawn over the desktop — see TRANSPARENT_MODE in config.js.
if (TRANSPARENT_MODE) document.body.classList.add('transparent');

// --- Subtitle toggle ---
const subtitleBtn = document.getElementById('toggle-subtitles-button');
if (subtitleBtn) {
  subtitleBtn.addEventListener('click', () => {
    const nowEnabled = !getSubtitlesEnabled();
    setSubtitlesEnabled(nowEnabled);
    subtitleBtn.classList.toggle('off', !nowEnabled);
  });
}

// Webcam, for the video button in the UI. Harmless if you deny the prompt —
// the rest of the page carries on without it.
await initWebcam();

// --- App State ---
let vrm = null;
let renderer = null;
let scene = null;
let camera = null;
let controls = null;
let audioMgr = null;
let animationMgr = null;
let movementController = null;
let playbackController = null;
let vrManager = null;
let vrPositionTracker = null;
let bokeh = null;
const clock = new THREE.Clock();

(async () => {
  // --- Check VR support ---
  const vrSupported = await isVRSupported();
  if (vrSupported) console.log('WebXR VR supported - VR mode available');

  // --- Scene preset (`?scene=<name>`; null when there is none) ---
  // Everything preset-related below is behind `if (preset)`. With no ?scene=
  // this file behaves exactly as it did before presets existed.
  const preset = getScenePreset(CHARACTER);

  // --- Scene Setup ---
  // Adjust these options to change the scene configuration.
  // See scene/sceneSetup.js DEFAULTS for all available options.
  const useBackdrop = !TRANSPARENT_MODE && BACKDROP?.url && !preset;
  const cam = CAMERA_DEFAULT;
  // Desktop overlay: always use the character's front-facing camera (centered
  // on her face), even without a backdrop. Browser mode keeps prior behavior.
  const desktopCam = TRANSPARENT_MODE && cam ? {
    cameraPosition: cam.position,
    controlsTarget: cam.target,
    fov: cam.fov ?? 30,
  } : {};
  ({ renderer, scene, camera, controls } = createScene({
    ...(useBackdrop ? {
      background: 0x1a1520,
      alpha: false,
      lights: [
        { type: 'ambient', color: 0xffe8f0, intensity: 1.35 },
        { type: 'directional', color: 0xffd4b8, intensity: 1.15, position: [2.2, 3.5, 2.5] },
        { type: 'directional', color: 0xb8c8ff, intensity: 0.35, position: [-2.5, 2.0, -1.5] },
      ],
      ...(cam ? {
        cameraPosition: cam.position,
        controlsTarget: cam.target,
        fov: cam.fov ?? 30,
      } : {}),
    } : {
      ...desktopCam,
      // Soft key from the front so she reads clearly over the desktop.
      lights: TRANSPARENT_MODE ? [
        { type: 'ambient', color: 0xffffff, intensity: 1.6 },
        { type: 'directional', color: 0xfff0e8, intensity: 1.1, position: [0.4, 2.2, 2.8] },
        { type: 'directional', color: 0xc8d8ff, intensity: 0.35, position: [-2.0, 1.5, -1.0] },
      ] : undefined,
    }),
    ...(preset ? {
      lights: preset.lights,
      fov: preset.camera.fov,
      cameraPosition: preset.camera.position,
      controlsTarget: preset.camera.target,
    } : {}),
  }));
  if (preset) applyFilmSettings(renderer, scene, preset.renderer);

  if (useBackdrop) {
    document.body.style.background = '#1a1520';
    await loadBackdrop(scene, BACKDROP);
  }

  // Remember orbit framing per character so startup matches the last good view.
  // Desktop overlay always resets to front-center — don't restore a spun cam.
  const camKey = `riko.cam.${CHARACTER}`;
  if (!TRANSPARENT_MODE) {
    try {
      const saved = JSON.parse(localStorage.getItem(camKey) || 'null');
      if (saved?.position && saved?.target) {
        camera.position.set(...saved.position);
        controls.target.set(...saved.target);
        if (saved.fov) {
          camera.fov = saved.fov;
          camera.updateProjectionMatrix();
        }
        controls.update();
        console.log(`[cam] restored ${camKey}`, saved);
      }
    } catch (e) {}
  } else {
    console.log('[cam] transparent mode — front-facing center framing');
  }
  const persistCam = () => {
    if (TRANSPARENT_MODE) return;
    const payload = {
      position: camera.position.toArray(),
      target: controls.target.toArray(),
      fov: camera.fov,
    };
    localStorage.setItem(camKey, JSON.stringify(payload));
    console.log(`[cam] saved ${camKey}`, payload);
  };
  controls.addEventListener('end', persistCam);
  window.addEventListener('beforeunload', persistCam);
  window.saveView = persistCam;

  // --- Room (environment GLB) ---
  if (preset?.room?.url) {
    // The preset carries its own room and its own seating, so ROOM_CONFIG is
    // bypassed entirely rather than merged — two sources for one room is how
    // you end up debugging a transform that comes from the other file.
    const room = await loadRoom(scene, {
      enabled: true,
      url: preset.room.url,
      position: [0, 0, 0], rotation: [0, 0, 0], scale: [1, 1, 1],
      fixDepth: preset.room.fixDepth,
    });
    fitRoom(room, preset.room);
  } else {
    await loadRoom(scene, ROOM_CONFIG);
  }

  // --- Scene Objects (GLB props) ---
  const sceneObjects = await loadSceneObjects(scene, SCENE_OBJECTS);
  window.sceneObjects = sceneObjects;
  window.describeSceneObjects = () => describeObjects(sceneObjects);

  // --- Named locations (waypoints + scene objects merged) ---
  const locations = createLocationRegistry({
    scene,
    namedLocations: NAMED_LOCATIONS,
    sceneObjects,
    debugMarkers: DEBUG_MARKERS,
  });
  window.locations = locations;
  console.log('📍 Locations registered:', locations.describe());

  // --- Load VRM ---
  const vrmData = await loadVRM(VRM_PATH, scene);
  vrm = vrmData.vrm;
  // resetNormalizedPose restores the FILE rest pose — for Luna that is a
  // singing/Y bind (arms up), not a T-pose. Arms-at-sides come from
  // AnimationManager._enforceStandingArms with per-character angles.
  try { vrm.humanoid?.resetNormalizedPose?.(); } catch (e) {}

  if (preset) {
    // MToon is the character's own cartoon shader — most of how she reads under
    // a light rig is here, not in the lights. See core/mtoonTuner.js.
    if (preset.mtoon) new MToonTuner(vrm).apply(preset.mtoon);
    if (preset.avatar) {
      vrm.scene.position.set(...preset.avatar.position);
      vrm.scene.rotation.y = preset.avatar.rotationY;
    }
    // vrmLoader turns shadow casting on but not receiving; without this she
    // never shadows herself, which is most of what sells a hard key light.
    vrm.scene.traverse((o) => { if (o.isMesh) o.receiveShadow = true; });
  }

  // Desktop overlay: face the camera (front) and sit on the primary monitor
  // center — not in the crack between two screens when the window spans both.
  if (TRANSPARENT_MODE && !preset) {
    vrm.scene.position.set(0, 0, 0);
    vrm.scene.rotation.set(0, 0, 0);
    if (cam) {
      camera.position.set(...cam.position);
      controls.target.set(...cam.target);
      if (cam.fov) {
        camera.fov = cam.fov;
        camera.updateProjectionMatrix();
      }
    }
    try {
      const layout = await window.lunaDesktop?.getLayout?.();
      if (layout?.primaryCenterX != null) {
        const dist = camera.position.distanceTo(controls.target);
        const vFov = (camera.fov * Math.PI) / 180;
        const worldHalfH = Math.tan(vFov / 2) * dist;
        const worldHalfW = worldHalfH * camera.aspect;
        const ndcX = layout.primaryCenterX * 2 - 1;
        const offsetX = ndcX * worldHalfW;
        camera.position.x += offsetX;
        controls.target.x += offsetX;
        console.log(
          `[cam] primary-center offset x=${offsetX.toFixed(3)} ` +
          `(primaryCenterX=${layout.primaryCenterX.toFixed(3)})`,
        );
      }
    } catch (e) {
      console.warn('[cam] layout offset skipped', e);
    }
    controls.update();
    // Keep her facing the viewer — light orbit only, no spin-away default.
    controls.enablePan = false;
    controls.minPolarAngle = Math.PI * 0.35;
    controls.maxPolarAngle = Math.PI * 0.55;
    controls.minAzimuthAngle = -0.35;
    controls.maxAzimuthAngle = 0.35;

    // Hub drag moves the VRM on-screen via camera viewOffset (Fluffy-style).
    const placeKey = `riko.overlay.place.${CHARACTER}`;
    let place = { x: 0, y: 0 };
    try {
      const saved = JSON.parse(localStorage.getItem(placeKey) || 'null');
      if (saved && Number.isFinite(saved.x) && Number.isFinite(saved.y)) {
        place = { x: saved.x, y: saved.y };
      }
    } catch {
      /* ignore */
    }

    const clampPlace = (x, y) => {
      const w = window.innerWidth;
      const h = window.innerHeight;
      const mx = Math.max(60, w * 0.42);
      const my = Math.max(60, h * 0.42);
      return {
        x: Math.max(-mx, Math.min(mx, x)),
        y: Math.max(-my, Math.min(my, y)),
      };
    };

    const applyPlace = () => {
      const w = window.innerWidth;
      const h = window.innerHeight;
      // Positive place.x/y = avatar appears further right/down.
      // setViewOffset shifts the frustum the opposite way.
      camera.setViewOffset(w, h, -place.x, -place.y, w, h);
      camera.updateProjectionMatrix();
      bokeh?.setSize?.(w, h);
    };

    window.lunaAvatar = {
      getOffset: () => ({ ...place }),
      setOffset: (x, y, { persist = false } = {}) => {
        place = clampPlace(x, y);
        applyPlace();
        if (persist) {
          localStorage.setItem(placeKey, JSON.stringify(place));
        }
        return { ...place };
      },
      nudge: (dx, dy, opts) => window.lunaAvatar.setOffset(place.x + dx, place.y + dy, opts),
    };
    place = clampPlace(place.x, place.y);
    applyPlace();
    window.dispatchEvent(new CustomEvent('luna-avatar-ready', { detail: { ...place } }));
    console.log('[cam] overlay placement', place);
  }

  // --- Depth of field ---
  // Always constructed, never costly: with no preset (or bokeh off) `render()`
  // is exactly `renderer.render(scene, camera)` and no buffers are allocated.
  bokeh = new Bokeh({ renderer, scene, camera, controls });
  if (preset?.bokeh) bokeh.apply(preset.bokeh);
  window.addEventListener('resize', () => {
    bokeh.setSize(window.innerWidth, window.innerHeight);
    // Re-apply hub-linked view offset after canvas resize / monitor switch.
    if (TRANSPARENT_MODE && window.lunaAvatar?.getOffset) {
      const o = window.lunaAvatar.getOffset();
      window.lunaAvatar.setOffset(o.x, o.y);
    }
  });

  // --- Click Detection ---
  initVRMClickDetector(renderer, camera, vrm, (region, partName) => {
    console.log(`📩 Region clicked: ${region}`);
  });

  // --- Managers ---
  audioMgr = new AudioManager(vrm);
  animationMgr = new AnimationManager(vrm, audioMgr, renderer, scene, camera, controls);
  animationMgr.setState('idle');

  // --- Movement Controller ---
  movementController = new MovementController(vrm, scene, animationMgr);
  await movementController.loadIdleAnimation('./animations/mixamo/Idle.fbx');
  await movementController.loadWalkAnimation('./animations/mixamo/Walking_inplace.fbx');
  await movementController.loadAdditiveAnimation('./animations/mixamo/Waving.fbx', 'wave');

  // --- VR Setup (conditional) ---
  if (vrSupported) {
    const { VRManager } = await import('./vr/vrManager.js');
    const { VRPositionTracker } = await import('./vr/vrPositionTracker.js');

    vrManager = new VRManager(renderer, scene, camera, controls);
    vrManager.init();

    // Enable proximity-based touch interaction on the VRM in VR
    vrManager.initTouchDetection(vrm, {
      onTouch: (region, boneName, handIndex) => {
        console.log(`VR touch: ${region} (hand ${handIndex})`);
      },
    });

    vrPositionTracker = new VRPositionTracker(camera, vrManager.getDolly());

    window.addEventListener('vr-session-start', () => vrPositionTracker.enable());
    window.addEventListener('vr-session-end', () => vrPositionTracker.disable());
    window.addEventListener('vr-unlock-audio', async () => {
      try { await playbackController?.unlockOnce(); } catch (e) {}
    });

    window.vrManager = vrManager;
    window.vrPositionTracker = vrPositionTracker;
  }

  // --- Playback Controller ---
  playbackController = new PlaybackController(audioMgr);
  playbackController.initPersistent();

  window.playbackController = playbackController;
  window.audioMgr = audioMgr;
  window.animationMgr = animationMgr;

  // Browsers refuse to play audio until the page has been clicked once. This
  // does that unlock on the first click, tap, or key press.
  const onFirstGesture = async () => {
    try { await playbackController.unlockOnce(); } catch (e) {}
    window.removeEventListener('pointerdown', onFirstGesture, { capture: true });
    window.removeEventListener('touchend', onFirstGesture, { capture: true });
    window.removeEventListener('keydown', onFirstGesture, { capture: true });
  };
  window.addEventListener('pointerdown', onFirstGesture, { capture: true });
  window.addEventListener('touchend', onFirstGesture, { capture: true });
  window.addEventListener('keydown', onFirstGesture, { capture: true });

  // --- Animation Loop ---
  clock.start();
  const fpsTick = initFpsMeter();   // no-op unless the page was opened with ?fps=1

  function animate() {
    const deltaTime = clock.getDelta();
    fpsTick();

    // movementController first so the mixer writes bone values, then animationMgr
    // overwrites the neck rotation with the state-based head movement. Reversed
    // order had the mixer always winning, making head micro-animations invisible.
    if (movementController) movementController.update(deltaTime);
    if (animationMgr) animationMgr.update(deltaTime);
    if (vrManager) vrManager.update();
    if (vrPositionTracker) vrPositionTracker.update();

    vrm.update(deltaTime);

    // A headset renders through its own pipeline; a post-processing composer
    // would break stereo, so VR always draws direct.
    if (vrManager && vrManager.isInVRSession) renderer.render(scene, camera);
    else bokeh.render();

    if (!vrManager || !vrManager.isInVRSession) {
      controls.update();
    }
  }

  if (vrSupported) {
    // VR drives its own loop at the headset's rate — no budget applies.
    renderer.setAnimationLoop(animate);
  } else {
    // Frame budget (RENDER.maxFps in config.js; 0 = uncapped). rAF still paces
    // the loop — we just skip the render on frames that arrive early, which is
    // where the GPU time actually goes.
    const minFrameMs = RENDER.maxFps ? 1000 / RENDER.maxFps : 0;
    let lastFrameMs = -Infinity;
    (function loop(now = 0) {
      requestAnimationFrame(loop);
      if (now - lastFrameMs < minFrameMs) return;
      lastFrameMs = now;
      animate();
    })();
  }

  // The avatar is on screen and can be driven. If you never see this line in
  // the console, the VRM did not finish loading.
  console.log('[app] avatar ready');

  // --- Playback queue ---
  // One clip at a time, in arrival order. `depth` is logged because a queue
  // that keeps growing is the signal that clips are arriving faster than the
  // link can fetch them — the thing to watch on a slow connection.
  let playbackChain = Promise.resolve();
  let queueDepth = 0;
  function enqueuePlayback(task) {
    queueDepth += 1;
    if (queueDepth > 1) console.log(`[playback] queued behind ${queueDepth - 1} clip(s)`);
    playbackChain = playbackChain
      .then(task)
      .catch((e) => console.error('[playback] queued clip failed:', e))
      .finally(() => { queueDepth -= 1; });
    return playbackChain;
  }

  // --- WebSocket Message Handler ---
  const onAvatarMessage = async ({ data }) => {
    let msg;
    try {
      msg = JSON.parse(data);
      console.log('📨 Message received:', msg);
    } catch { return; }

    // --- Movement commands ---
    if (msg.type === 'walk_to') {
      const { x, y, z, speed } = msg;
      if (speed) movementController.setSpeed(speed);
      movementController.walkTo(x, y, z);
    }

    if (msg.type === 'stop_movement') {
      movementController.stop();
    }

    if (msg.type === 'teleport_to') {
      const { x, y, z } = msg;
      movementController.teleportTo(x, y, z);
    }

    if (msg.type === 'set_speed') {
      movementController.setSpeed(msg.speed);
    }

    if (msg.type === 'load_walk_animation') {
      await movementController.loadWalkAnimation(msg.url);
    }

    if (msg.type === 'load_idle_animation') {
      await movementController.loadIdleAnimation(msg.url);
    }

    // --- Additive blending ---
    if (msg.type === 'set_additive_weight') {
      const { anim_name, weight, duration } = msg;
      movementController.setAdditiveWeight(anim_name, weight, duration || 0.25);
    }

    if (msg.type === 'play_additive_once') {
      const { anim_name, fade_in, fade_out } = msg;
      movementController.playAdditiveOnce(anim_name, fade_in || 0.25, fade_out || 0.25);
    }

    if (msg.type === 'load_additive_animation') {
      const { url, name } = msg;
      await movementController.loadAdditiveAnimation(url, name);
    }

    if (msg.type === 'load_and_play_additive') {
      const { url, name, weight = 1.0, play_once = false, fade_in = 0.25, fade_out = 0.25 } = msg;
      try {
        const loaded = await movementController.loadAdditiveAnimation(url, name);
        if (loaded) {
          if (play_once) {
            movementController.playAdditiveOnce(name, fade_in, fade_out);
          } else {
            movementController.setAdditiveWeight(name, weight, fade_in);
          }
        }
      } catch (err) {
        console.error(`Failed to load and play additive animation ${name}:`, err);
      }
    }

    // --- State control ---
    if (msg.type === 'set_state') {
      const { state } = msg;
      if (animationMgr && ['idle', 'listening', 'thinking', 'talking'].includes(state)) {
        animationMgr.setState(state);
      }
    }

    if (msg.type === 'set_movement_lock_duration') {
      const { duration } = msg;
      if (animationMgr && typeof duration === 'number') {
        animationMgr.setMovementLockDuration(duration);
      }
    }

    // --- Audio playback ---
    if (msg.type === 'start_animation') {
      const { audio_path, expression = 'neutral', audio_text, audio_duraction } = msg;

      // Queued, never played on arrival. The server paces clips with a sleep
      // that starts when it POSTs /talk, but the browser still has to DOWNLOAD
      // each one before a sample is heard. On a phone using cellular data that
      // download outlasts the server's pad, and playing on arrival reset the
      // <audio> element on top of a clip still sounding — the mid-word cut-off.
      // Chaining makes the CLIENT the authority on when a clip is really done.
      enqueuePlayback(async () => {
        const queuedAt = performance.now();
        audioMgr.setExpression(expression);

        if (msg.skip_browser_audio) {
          // Discord bot is speaking this clip in VC — webpage stays silent.
          console.log(`[playback] skip browser audio (Discord VC) — ${audio_path}`);
          if (audio_text && audio_duraction) {
            showSubtitleStreaming(audio_text, audio_duraction);
          }
          animationMgr.play();
          const holdMs = Math.max(200, Number(audio_duraction) * 1000 || 1000);
          await new Promise((r) => setTimeout(r, holdMs));
          return;
        }

        try { await playbackController.unlockOnce(); } catch (e) {}
        const ok = await playbackController.playAudioUrl(audio_path);
        const audibleMs = Math.round(performance.now() - queuedAt);

        // Subtitles start with the AUDIO, not with the message. On a slow link
        // those are seconds apart, which is why they used to run ahead.
        if (audio_text && audio_duraction) {
          showSubtitleStreaming(audio_text, audio_duraction);
        }
        animationMgr.play();

        if (!ok) {
          console.warn(`[playback] failed after ${audibleMs}ms (animation still runs)`);
          return;
        }
        console.log(`[playback] audible after ${audibleMs}ms (fetch+buffer) — ${audio_path}`);
        await playbackController.waitUntilEnded(audio_duraction);
        console.log(`[playback] done after ${Math.round(performance.now() - queuedAt)}ms `
                  + `(server said ${audio_duraction}s)`);
      });
    }

    // --- Sound effect (one-off, off the speech channel) ---
    // Its own throwaway <audio>, NOT playbackController's persistent element:
    // that one is the lipsync source and a strict FIFO, so an effect routed
    // through it would flap the mouth to a siren and delay her reply by the
    // length of the clip. This just sounds over whatever is happening.
    if (msg.type === 'play_sfx') {
      const { audio_path } = msg;
      const sfx = new Audio(audio_path);
      sfx.play()
        .then(() => console.log(`[sfx] playing ${audio_path}`))
        .catch((e) => console.warn(`[sfx] blocked for ${audio_path}:`, e));
    }

    // --- VRMA animation ---
    if (msg.type === 'start_vrma') {
      const {
        animation_url,
        play_once = false,
        crop_start = 0.0,
        crop_end = 0.0,
        lock_position = false,
        track_position = true,
      } = msg;
      await movementController.playVRMA(animation_url, {
        play_once, crop_start, crop_end, lock_position, track_position,
      });
    }

    // --- Mixamo animation ---
    if (msg.type === 'start_mixamo') {
      const { animation_url, play_once = false, lock_position = false, track_position = true } = msg;
      await movementController.playMixamo(animation_url, {
        play_once, lock_position, track_position,
      });
    }

    // --- Webcam ---
    if (msg.type === 'take_picture') {
      await takePictureAndUpload();
    }
  };

  // This socket drives lipsync, emotes and animations, so it has to come back
  // by itself. Restart the server, put the machine to sleep, or leave the tab
  // open long enough on a flaky connection, and without a retry you come back
  // to an avatar that renders perfectly and never speaks again.
  let wsRetryMs = 1000;
  function connectAvatarWS() {
    const ws = new WebSocket(WS_URL);
    ws.onopen = () => {
      wsRetryMs = 1000;
      console.log('✅ WebSocket connected');
    };
    ws.onerror = err => console.error('WS error', err);
    ws.onmessage = onAvatarMessage;
    ws.onclose = () => {
      console.warn(`[ws] avatar socket closed — reconnecting in ${wsRetryMs}ms`);
      setTimeout(connectAvatarWS, wsRetryMs);
      wsRetryMs = Math.min(wsRetryMs * 2, 10000);   // back off, don't hammer
    };
  }
  connectAvatarWS();

})();
