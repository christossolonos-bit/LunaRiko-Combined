import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { VRMLoaderPlugin } from '@pixiv/three-vrm';
import { VRMAnimationLoaderPlugin, createVRMAnimationClip } from '@pixiv/three-vrm-animation';
import { loadMixamoAnimation } from './loadMixamoAnimation.js';
import { stripRootMotionFromClip, trimAnimationClip } from './clipUtils.js';

/**
 * MovementController - single-mixer home for all VRM body animation.
 *
 * Every action (idle/walk/run, one-off Mixamo/VRMA, additive gestures) lives
 * on `this.mixer`, so any transition is a native Three.js crossfade where the
 * two actions' weights sum to ~1.0 throughout the blend. That's what avoids
 * the rest-pose (T-pose) blend you get when partial weight lands on a mixer
 * with no other active action covering the same bones.
 */
export class MovementController {
  constructor(vrm, scene, animationManager) {
    this.vrm = vrm;
    this.scene = scene;
    this.animationManager = animationManager;

    this.mixer = new THREE.AnimationMixer(vrm.scene);

    // Loader for VRMA files (same plugin stack as the main VRM loader).
    this.vrmaLoader = new GLTFLoader();
    this.vrmaLoader.register((parser) => new VRMLoaderPlugin(parser));
    this.vrmaLoader.register((parser) => new VRMAnimationLoaderPlugin(parser));

    // Movement state
    this.isMoving = false;
    this.targetPosition = null;
    this.moveSpeed = 1.5;
    this.rotationSpeed = 5.0;
    this.arrivalThreshold = 0.1;

    // Base actions
    this.clips = { idle: null, walk: null, run: null };
    this.actions = { idle: null, walk: null, run: null };
    this.currentBaseAction = 'idle';

    // Additive layer (kept for the loaded wave gesture)
    this.additiveClips = new Map();
    this.additiveActions = new Map();

    // Whichever action is currently driving the skeleton (idle/walk/run/external).
    // The `_crossFadeTo` helper keeps this pointing at the most recent crossfade target.
    this._activeAction = null;

    // Current one-off VRMA/Mixamo action (null when on a base action).
    this._externalAction = null;
    this._externalFinishListener = null;

    // Active per-frame world-position compensation during a play_once's crossfade
    // back to idle — used when the VRMA has root motion we want baked into the
    // scene position rather than snapping back when idle takes over.
    this._positionCompensation = null;

    // Transition durations
    this.crossFadeDuration = 0.35;   // base → base (idle ↔ walk)
    this.externalFadeDuration = 0.5; // anything involving a one-off external
    this.baseAnimationSpeed = 1.5;

    this.debug = false;
  }

  // ---------- loading (base animations) ----------

  async loadIdleAnimation(url) {
    try {
      const clip = await loadMixamoAnimation(url, this.vrm, {
        stripRootMotion: true,
        clipName: 'idle',
      });
      this.clips.idle = clip;
      this.actions.idle = this.mixer.clipAction(clip);
      this.actions.idle.setLoop(THREE.LoopRepeat);
      this.actions.idle.clampWhenFinished = false;
      this.setWeight(this.actions.idle, 1.0);
      this.actions.idle.play();
      this.currentBaseAction = 'idle';
      this._activeAction = this.actions.idle;
      console.log('✅ Idle animation loaded and playing');
      return true;
    } catch (err) {
      console.error('Failed to load idle animation:', err);
      return false;
    }
  }

  async loadWalkAnimation(url) {
    try {
      const clip = await loadMixamoAnimation(url, this.vrm, {
        stripRootMotion: true,
        clipName: 'walk',
      });
      this.clips.walk = clip;
      this.actions.walk = this.mixer.clipAction(clip);
      this.actions.walk.setLoop(THREE.LoopRepeat);
      this.actions.walk.clampWhenFinished = false;
      this.setWeight(this.actions.walk, 0.0);
      this.actions.walk.play();
      console.log('✅ Walk animation loaded');
      return true;
    } catch (err) {
      console.error('Failed to load walk animation:', err);
      return false;
    }
  }

  async loadRunAnimation(url) {
    try {
      const clip = await loadMixamoAnimation(url, this.vrm, {
        stripRootMotion: true,
        clipName: 'run',
      });
      this.clips.run = clip;
      this.actions.run = this.mixer.clipAction(clip);
      this.actions.run.setLoop(THREE.LoopRepeat);
      this.actions.run.clampWhenFinished = false;
      this.setWeight(this.actions.run, 0.0);
      this.actions.run.play();
      console.log('✅ Run animation loaded');
      return true;
    } catch (err) {
      console.error('Failed to load run animation:', err);
      return false;
    }
  }

  // ---------- additive layer ----------

  async loadAdditiveAnimation(url, name) {
    const isVRMA = url.toLowerCase().endsWith('.vrma');
    try {
      let clip;
      if (isVRMA) {
        clip = await this._loadRawVRMAClip(url, { stripRootMotion: true });
        clip.name = name;
        THREE.AnimationUtils.makeClipAdditive(clip);
      } else {
        clip = await loadMixamoAnimation(url, this.vrm, {
          stripRootMotion: true,
          makeAdditive: true,
          clipName: name,
        });
      }
      this.additiveClips.set(name, clip);
      const action = this.mixer.clipAction(clip);
      action.setLoop(THREE.LoopRepeat);
      action.blendMode = THREE.AdditiveAnimationBlendMode;
      this.setWeight(action, 0.0);
      action.play();
      this.additiveActions.set(name, action);
      console.log(`✅ Additive animation loaded (${isVRMA ? 'VRMA' : 'Mixamo'}): ${name}`);
      return true;
    } catch (err) {
      console.error(`Failed to load additive animation ${name}:`, err);
      return false;
    }
  }

  setAdditiveWeight(name, weight, duration = 0.25) {
    const action = this.additiveActions.get(name);
    if (!action) return;
    weight = THREE.MathUtils.clamp(weight, 0.0, 1.0);
    if (duration > 0) {
      const currentWeight = action.getEffectiveWeight();
      action._targetWeight = weight;
      action._weightTransitionSpeed = Math.abs(weight - currentWeight) / duration;
    } else {
      this.setWeight(action, weight);
    }
  }

  playAdditiveOnce(name, fadeIn = 0.25, fadeOut = 0.25) {
    const action = this.additiveActions.get(name);
    const clip = this.additiveClips.get(name);
    if (!action || !clip) return;
    action.setLoop(THREE.LoopOnce);
    action.reset();
    this.setWeight(action, 1.0);
    action.play();
    const fadeOutTime = (clip.duration - fadeOut) * 1000;
    setTimeout(() => this.setAdditiveWeight(name, 0.0, fadeOut), fadeOutTime);
  }

  // ---------- utilities ----------

  setWeight(action, weight) {
    if (!action) return;
    action.enabled = true;
    action.setEffectiveTimeScale(1);
    action.setEffectiveWeight(weight);
  }

  /**
   * Native crossfade between any two actions on this mixer. The outgoing action
   * fades 1 → 0 while the incoming fades 0 → 1, sum stays ~1, so the partial
   * weights don't trigger a rest-pose blend.
   */
  _crossFadeTo(newAction, duration) {
    if (!newAction || newAction === this._activeAction) return;
    newAction.reset();
    newAction.setEffectiveTimeScale(1);
    newAction.setEffectiveWeight(1);
    newAction.play();
    if (this._activeAction) {
      this._activeAction.crossFadeTo(newAction, duration, false);
    }
    this._activeAction = newAction;
  }

  _removeExternalFinishListener() {
    if (this._externalFinishListener) {
      try {
        this.mixer.removeEventListener('finished', this._externalFinishListener);
      } catch (e) {}
      this._externalFinishListener = null;
    }
  }

  crossFadeToBase(actionName, duration = this.crossFadeDuration) {
    const newAction = this.actions[actionName];
    if (!newAction) {
      console.warn(`Action ${actionName} not loaded`);
      return;
    }
    if (actionName === this.currentBaseAction && this._activeAction === newAction) return;
    if (this.debug) console.log(`🔄 Crossfade: ${this.currentBaseAction} -> ${actionName}`);
    this._crossFadeTo(newAction, duration);
    this.currentBaseAction = actionName;
  }

  // ---------- one-off external animations ----------

  /**
   * Load + play a VRMA one-off animation with native crossfade from whatever
   * is currently active (idle/walk/another VRMA/Mixamo).
   */
  async playVRMA(url, {
    play_once = false,
    crop_start = 0.0,
    crop_end = 0.0,
    lock_position = false,
    track_position = true,
  } = {}) {
    try {
      let clip = await this._loadRawVRMAClip(url, { stripRootMotion: false });
      if (lock_position) clip = stripRootMotionFromClip(clip, this.vrm);
      const startTime = Math.max(0, parseFloat(crop_start) || 0);
      const endTime = Math.max(0, clip.duration - (parseFloat(crop_end) || 0));
      if (startTime > 0 || (parseFloat(crop_end) || 0) > 0) {
        const trimmed = trimAnimationClip(clip, startTime, endTime);
        if (trimmed) clip = trimmed;
      }
      this._playExternalClip(clip, { play_once, track_position, lock_position, kind: 'vrma' });
    } catch (err) {
      console.error('Failed to load VRMA animation:', err);
    }
  }

  /**
   * Load + play a Mixamo one-off animation with the same crossfade semantics
   * as playVRMA.
   */
  async playMixamo(url, {
    play_once = false,
    lock_position = false,
    track_position = true,
  } = {}) {
    // Server often re-sends Idle.fbx when the queue drains. Treat that as the
    // base idle — NOT an external Mixamo gesture. External mode sets
    // isMixamoPlaying=true which disables the arms-down correction, and on
    // VRMs whose bind pose isn't T-pose (e.g. singing exports) that leaves
    // the arms stuck over the head.
    const isIdleClip = /idle/i.test(String(url)) && !play_once;
    if (isIdleClip && this.actions.idle) {
      this._removeExternalFinishListener();
      const stale = this._externalAction;
      this._externalAction = null;
      this._positionCompensation = null;
      this._crossFadeTo(this.actions.idle, this.crossFadeDuration);
      this.currentBaseAction = 'idle';
      if (this.animationManager) {
        this.animationManager.setMixamoPlaying(false);
        this.animationManager.setVRMAPlaying(false);
      }
      if (stale) {
        setTimeout(() => { try { stale.stop(); } catch (e) {} },
                   this.crossFadeDuration * 1000 + 100);
      }
      return;
    }

    try {
      const clip = await loadMixamoAnimation(url, this.vrm, {
        stripRootMotion: lock_position,
      });
      this._playExternalClip(clip, { play_once, track_position, lock_position, kind: 'mixamo' });
    } catch (err) {
      console.error('Failed to load Mixamo animation:', err);
    }
  }

  async _loadRawVRMAClip(url, { stripRootMotion = true } = {}) {
    const gltf = await this.vrmaLoader.loadAsync(url);
    const vrmAnimation = gltf.userData.vrmAnimations[0];
    let clip = createVRMAnimationClip(vrmAnimation, this.vrm);
    if (stripRootMotion) clip = stripRootMotionFromClip(clip, this.vrm);
    return clip;
  }

  _playExternalClip(clip, { play_once, track_position, lock_position, kind }) {
    // An incoming external takes over from walking, so cancel movement intent.
    this.isMoving = false;
    this.targetPosition = null;
    this._positionCompensation = null;
    this._removeExternalFinishListener();

    const newAction = this.mixer.clipAction(clip);
    if (play_once) {
      newAction.setLoop(THREE.LoopOnce, 0);
      newAction.clampWhenFinished = true;
    } else {
      newAction.setLoop(THREE.LoopRepeat, Infinity);
      newAction.clampWhenFinished = false;
    }

    const previousExternal = this._externalAction;
    this._crossFadeTo(newAction, this.externalFadeDuration);
    this._externalAction = newAction;

    // Stop any superseded external after the crossfade completes so old
    // LoopOnce actions don't keep firing `finished` events or accumulate.
    if (previousExternal && previousExternal !== newAction) {
      const stale = previousExternal;
      setTimeout(() => {
        try { stale.stop(); } catch (e) {}
      }, this.externalFadeDuration * 1000 + 100);
    }

    if (this.animationManager) {
      if (kind === 'vrma') this.animationManager.setVRMAPlaying(true);
      else this.animationManager.setMixamoPlaying(true);
    }

    if (play_once) {
      const listener = (e) => {
        if (e.action !== newAction) return;
        this._handleExternalFinished(newAction, { track_position, lock_position, kind });
      };
      this._externalFinishListener = listener;
      this.mixer.addEventListener('finished', listener);
    }
  }

  _handleExternalFinished(action, { track_position, lock_position, kind }) {
    const hipsNode = this.vrm.humanoid?.getNormalizedBoneNode('hips');
    let targetWorldX = this.vrm.scene.position.x;
    let targetWorldZ = this.vrm.scene.position.z;
    if (track_position && !lock_position && hipsNode) {
      // Use world position so the result is correct regardless of vrm.scene rotation.
      // hipsNode.position is LOCAL to vrm.scene — adding it directly to world coords
      // gives wrong results when the model is rotated (e.g. after walking anywhere
      // other than straight forward). getWorldPosition accounts for all parent transforms.
      const hipsWorldPos = new THREE.Vector3();
      hipsNode.getWorldPosition(hipsWorldPos);
      targetWorldX = hipsWorldPos.x;
      targetWorldZ = hipsWorldPos.z;
    }

    // Reset spring bones (hair/accessories) to prevent the 1-2s freeze that occurs
    // when colliders clip through hair during displacement animations.
    // Remove this line if an instant snap is less desirable than the gradual settle.
    if (this.vrm.springBoneManager) this.vrm.springBoneManager.reset();

    // Crossfade external → idle natively on the same mixer.
    this._crossFadeTo(this.actions.idle, this.externalFadeDuration);
    this.currentBaseAction = 'idle';

    // During the crossfade, hips' local position interpolates between the
    // external's last-frame value and idle's pose. Keep world hips at the
    // baked target by compensating scene.position each frame. When the fade
    // ends, idle is fully driving so hips local is back to the idle pose.
    if (track_position && !lock_position) {
      this._positionCompensation = {
        targetWorldX,
        targetWorldZ,
        endAt: performance.now() + this.externalFadeDuration * 1000 + 50,
      };
    }

    // Stop the external after the crossfade resolves so it doesn't stay
    // around as a zero-weight playing action.
    const stale = action;
    setTimeout(() => {
      try { stale.stop(); } catch (e) {}
      if (this._externalAction === stale) this._externalAction = null;
    }, this.externalFadeDuration * 1000 + 100);

    if (this.animationManager) {
      if (kind === 'vrma') this.animationManager.setVRMAPlaying(false);
      else this.animationManager.setMixamoPlaying(false);
    }

    this._removeExternalFinishListener();
  }

  // ---------- walking ----------

  walkTo(x, y, z) {
    this.targetPosition = new THREE.Vector3(x, y, z);
    this.isMoving = true;

    // If a one-off was playing we crossfade out of it straight into walk.
    this._removeExternalFinishListener();
    const staleExternal = this._externalAction;
    this._externalAction = null;

    this._positionCompensation = null;
    this._crossFadeTo(this.actions.walk, this.externalFadeDuration);
    this.currentBaseAction = 'walk';

    if (staleExternal) {
      setTimeout(() => {
        try { staleExternal.stop(); } catch (e) {}
      }, this.externalFadeDuration * 1000 + 100);
    }

    if (this.animationManager) {
      this.animationManager.setMixamoPlaying(true);
      this.animationManager.setVRMAPlaying(false);
    }

    console.log(`🚶 Walking to: (${x.toFixed(2)}, ${y.toFixed(2)}, ${z.toFixed(2)})`);
  }

  stop() {
    this.isMoving = false;
    this.targetPosition = null;
    this._crossFadeTo(this.actions.idle, this.crossFadeDuration);
    this.currentBaseAction = 'idle';
    if (this.animationManager) {
      this.animationManager.setMixamoPlaying(false);
      this.animationManager.setVRMAPlaying(false);
    }
    console.log('🛑 Movement stopped');
  }

  teleportTo(x, y, z) {
    this.vrm.scene.position.set(x, y, z);
    this._removeExternalFinishListener();
    const staleExternal = this._externalAction;
    this._externalAction = null;
    this._positionCompensation = null;

    this._crossFadeTo(this.actions.idle, this.crossFadeDuration);
    this.currentBaseAction = 'idle';
    this.isMoving = false;
    this.targetPosition = null;

    if (staleExternal) {
      setTimeout(() => {
        try { staleExternal.stop(); } catch (e) {}
      }, this.crossFadeDuration * 1000 + 100);
    }

    if (this.animationManager) {
      this.animationManager.setMixamoPlaying(false);
      this.animationManager.setVRMAPlaying(false);
    }
    console.log(`📍 Teleported to: (${x.toFixed(2)}, ${y.toFixed(2)}, ${z.toFixed(2)})`);
  }

  setSpeed(speed) {
    this.moveSpeed = speed;
    if (this.actions.walk) {
      this.actions.walk.setEffectiveTimeScale(speed / this.baseAnimationSpeed);
    }
    if (this.actions.run) {
      this.actions.run.setEffectiveTimeScale(speed / (this.baseAnimationSpeed * 2));
    }
  }

  faceDirection(x, z) {
    this.vrm.scene.rotation.y = Math.atan2(x, z);
  }

  getPosition() { return this.vrm.scene.position.clone(); }
  getRotation() { return this.vrm.scene.rotation.y; }
  getIsMoving() { return this.isMoving; }

  // ---------- per-frame tick ----------

  update(deltaTime) {
    if (this.mixer) this.mixer.update(deltaTime);

    // Per-frame world-position compensation during external → idle crossfade
    // so root-motion VRMAs don't snap back when idle takes over.
    if (this._positionCompensation) {
      const { targetWorldX, targetWorldZ, endAt } = this._positionCompensation;
      const hipsNode = this.vrm.humanoid?.getNormalizedBoneNode('hips');
      if (hipsNode) {
        // Correct for scene rotation: get the hips actual world position each frame,
        // then nudge the scene so hips land exactly at the captured target.
        // This replaces the old local-space subtraction which broke when the model
        // was rotated after walking in any direction other than straight forward.
        const hipsWorldPos = new THREE.Vector3();
        hipsNode.getWorldPosition(hipsWorldPos);
        this.vrm.scene.position.x += targetWorldX - hipsWorldPos.x;
        this.vrm.scene.position.z += targetWorldZ - hipsWorldPos.z;
      }
      if (performance.now() >= endAt) this._positionCompensation = null;
    }

    this._updateAdditiveWeightTransitions(deltaTime);

    if (this.isMoving && this.targetPosition) {
      this._updateMovement(deltaTime);
    }
  }

  _updateAdditiveWeightTransitions(deltaTime) {
    for (const action of this.additiveActions.values()) {
      if (action._targetWeight !== undefined) {
        this._tickWeight(action, deltaTime);
      }
    }
  }

  _tickWeight(action, deltaTime) {
    const current = action.getEffectiveWeight();
    const target = action._targetWeight;
    const speed = action._weightTransitionSpeed || 1.0;
    if (Math.abs(current - target) < 0.01) {
      action.setEffectiveWeight(target);
      delete action._targetWeight;
      delete action._weightTransitionSpeed;
      return;
    }
    const direction = target > current ? 1 : -1;
    const next = current + direction * speed * deltaTime;
    action.setEffectiveWeight(direction > 0 ? Math.min(next, target) : Math.max(next, target));
  }

  _updateMovement(deltaTime) {
    const currentPos = this.vrm.scene.position;
    const direction = new THREE.Vector3().subVectors(this.targetPosition, currentPos);
    const horizontalDir = new THREE.Vector3(direction.x, 0, direction.z);
    const distance = horizontalDir.length();

    if (distance < this.arrivalThreshold) {
      this.stop();
      return;
    }

    horizontalDir.normalize();

    const targetAngle = Math.atan2(horizontalDir.x, horizontalDir.z);
    const currentRotation = this.vrm.scene.rotation.y;
    let angleDiff = targetAngle - currentRotation;
    while (angleDiff > Math.PI) angleDiff -= Math.PI * 2;
    while (angleDiff < -Math.PI) angleDiff += Math.PI * 2;
    const rotationStep = this.rotationSpeed * deltaTime;
    if (Math.abs(angleDiff) > rotationStep) {
      this.vrm.scene.rotation.y += Math.sign(angleDiff) * rotationStep;
    } else {
      this.vrm.scene.rotation.y = targetAngle;
    }

    const moveStep = this.moveSpeed * deltaTime;
    currentPos.addScaledVector(horizontalDir, Math.min(moveStep, distance));
  }

  dispose() {
    if (this.mixer) this.mixer.stopAllAction();
    this.clips = {};
    this.actions = {};
    this.additiveClips.clear();
    this.additiveActions.clear();
    this._activeAction = null;
    this._externalAction = null;
    this._positionCompensation = null;
  }
}
