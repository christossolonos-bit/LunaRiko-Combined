import * as THREE from 'three';
import { VR_CONFIG } from '../config.js';
import { mapBoneToRegion, sendClickInteraction } from '../interaction/touchRegionMap.js';
import { pulseXRController } from './xrHaptics.js';

/**
 * VRTouchDetector - Proximity-based touch detection on VRM avatar from VR controllers.
 *
 * Primary: auto-triggers when a controller hand gets close enough to a VRM bone.
 * Secondary: trigger button extends the detection range for intentional interaction.
 * Provides haptic feedback and updates hand indicator visuals based on proximity.
 */
export class VRTouchDetector {
  constructor(renderer, vrm, { controllerGrips, handIndicators }, { onTouch } = {}) {
    this.renderer = renderer;
    this.vrm = vrm;
    this.controllerGrips = controllerGrips;
    this.handIndicators = handIndicators;
    this.onTouch = onTouch || null;

    // Config
    this.touchRadius = VR_CONFIG.touchRadius;
    this.triggerTouchRadius = VR_CONFIG.triggerTouchRadius;
    this.cooldown = VR_CONFIG.touchCooldown;
    this.hapticIntensity = VR_CONFIG.hapticIntensity;
    this.hapticDuration = VR_CONFIG.hapticDuration;

    // Per-controller state
    this.lastTouchTime = [0, 0];
    this.activeTouches = [false, false];

    // Cache VRM bone references for proximity checks
    this.bones = this._cacheBones();

    // Reusable vectors (avoid GC pressure in the update loop)
    this._gripPos = new THREE.Vector3();
    this._bonePos = new THREE.Vector3();

    // Color constants for hand indicators
    this._colorIdle = new THREE.Color(0x88ccff);
    this._colorNear = new THREE.Color(0xffcc44);
    this._colorTouch = new THREE.Color(0x44ff88);

    this._enabled = false;

    console.log(`VR Touch Detector initialized (${this.bones.length} bones cached)`);
  }

  enable() { this._enabled = true; }
  disable() { this._enabled = false; }

  _cacheBones() {
    const bones = [];
    const humanoid = this.vrm.humanoid;
    if (!humanoid) return bones;

    const boneNames = [
      'head', 'neck', 'upperChest', 'chest', 'spine', 'hips',
      'leftShoulder', 'leftUpperArm', 'leftLowerArm', 'leftHand',
      'rightShoulder', 'rightUpperArm', 'rightLowerArm', 'rightHand',
      'leftUpperLeg', 'leftLowerLeg', 'leftFoot',
      'rightUpperLeg', 'rightLowerLeg', 'rightFoot',
    ];

    for (const humanoidName of boneNames) {
      const node = humanoid.getNormalizedBoneNode(humanoidName);
      if (node) {
        bones.push({ name: node.name, node });
      }
    }
    return bones;
  }

  /** Call every frame from the animate loop */
  update() {
    if (!this._enabled) return;

    const now = Date.now();

    for (let i = 0; i < this.controllerGrips.length; i++) {
      const grip = this.controllerGrips[i];
      if (!grip) continue;

      // Get controller grip world position
      grip.getWorldPosition(this._gripPos);

      // Find closest bone
      const closest = this._findClosestBone(this._gripPos);

      // Update hand indicator visuals
      this._updateHandVisual(i, closest.distance);

      // Check proximity touch (only if cooldown elapsed)
      if (now - this.lastTouchTime[i] >= this.cooldown) {
        if (closest.distance < this.touchRadius && !this.activeTouches[i]) {
          this._triggerTouch(i, closest.bone, closest.distance, now);
        }
      }

      // Track whether controller is currently in touch zone
      this.activeTouches[i] = closest.distance < this.touchRadius;
    }
  }

  /**
   * Handle trigger (select) button press - extends touch range.
   * Called from vrManager when trigger is pressed.
   */
  handleTrigger(controllerIndex) {
    if (!this._enabled) return;

    const grip = this.controllerGrips[controllerIndex];
    if (!grip) return;

    const now = Date.now();
    if (now - this.lastTouchTime[controllerIndex] < this.cooldown) return;

    grip.getWorldPosition(this._gripPos);
    const closest = this._findClosestBone(this._gripPos);

    if (closest.distance < this.triggerTouchRadius && closest.bone) {
      this._triggerTouch(controllerIndex, closest.bone, closest.distance, now);
    }
  }

  _findClosestBone(position) {
    let closestBone = null;
    let closestDist = Infinity;

    for (const bone of this.bones) {
      bone.node.getWorldPosition(this._bonePos);
      const dist = position.distanceTo(this._bonePos);
      if (dist < closestDist) {
        closestDist = dist;
        closestBone = bone;
      }
    }

    return { bone: closestBone, distance: closestDist };
  }

  _triggerTouch(controllerIndex, bone, distance, now) {
    this.activeTouches[controllerIndex] = true;
    this.lastTouchTime[controllerIndex] = now;

    const region = mapBoneToRegion(bone.name);
    console.log(`VR Touch: Hand ${controllerIndex} -> ${bone.name} (${region}, ${distance.toFixed(3)}m)`);

    sendClickInteraction(bone.name, region);
    this._haptic(controllerIndex);

    if (this.onTouch) {
      this.onTouch(region, bone.name, controllerIndex);
    }
  }

  _haptic(controllerIndex) {
    pulseXRController(this.renderer, controllerIndex, this.hapticIntensity, this.hapticDuration);
  }

  _updateHandVisual(controllerIndex, distance) {
    const indicator = this.handIndicators[controllerIndex];
    if (!indicator) return;

    const { sphere, glow } = indicator.userData;
    if (!sphere || !glow) return;

    let color;
    let glowOpacity;

    if (distance < this.touchRadius) {
      // Touching - green
      color = this._colorTouch;
      glowOpacity = 0.35;
    } else if (distance < this.triggerTouchRadius) {
      // Near - interpolate from idle to near color
      const t = 1.0 - (distance - this.touchRadius) / (this.triggerTouchRadius - this.touchRadius);
      color = this._colorIdle.clone().lerp(this._colorNear, t);
      glowOpacity = 0.1 + t * 0.15;
    } else {
      // Idle - blue
      color = this._colorIdle;
      glowOpacity = 0.1;
    }

    sphere.material.color.copy(color);
    glow.material.color.copy(color);
    glow.material.opacity = glowOpacity;
  }
}
