import * as THREE from 'three';
import { STANDING_ARMS } from '../config.js';

export class AnimationManager {
  constructor(vrm, audioMgr, renderer, scene, camera, controls, standingArms = STANDING_ARMS) {
    this.vrm        = vrm;
    this.audioMgr   = audioMgr;
    this.renderer   = renderer;
    this.scene      = scene;
    this.camera     = camera;
    this.controls   = controls;
    this.standingArms = standingArms || STANDING_ARMS;
    this.clock      = new THREE.Clock();
    this.isPlaying  = false;
    this.isVRMAPlaying = false;
    this.isMixamoPlaying = false;

    // ========== STATE SYSTEM ==========
    this.state = 'idle'; // idle, listening, thinking, talking
    this.previousState = 'idle';
    this.stateTimer = 0;
    this.isTransitioning = false;
    this.transitionTimer = 0;
    this.transitionDuration = 0.5; // How long the smooth reset takes

    // Movement lock - prevents new movements after state change
    this.movementLocked = false;
    this.movementLockTimer = 0;
    this.movementLockDuration = 1.0; // Default 1 second lock after transition

    // Head state - current and target with smooth interpolation
    this.headTimer  = 0;
    this.nextHead   = 0;
    this.headTgt    = { x: 0, y: 0, z: 0 };
    this.headCur    = { x: 0, y: 0, z: 0 };
    this.headCenter = { x: 0, y: 0, z: 0 };
    this.headVelocity = { x: 0, y: 0, z: 0 }; // For smooth acceleration/deceleration

    // Separate eye target tracking (for eye-leads-head)
    this.eyeTargetCur = { x: 0, y: 0, z: 5 };
    this.eyeTargetTgt = { x: 0, y: 0, z: 5 };
    this.eyeLeadTimer = 0; // Timer for when eyes moved to new target
    this.eyeHasReachedTarget = true;

    // Eye lookAt target — world-space on the scene, tracks the camera so eyes
    // look at the viewer (works for singing-bind models unlike a local offset).
    this.eyeLookAtTarget = new THREE.Object3D();
    this.eyeLookAtTarget.name = 'eyeLookAtTarget';
    this.scene.add(this.eyeLookAtTarget);
    if (this.vrm.lookAt) {
      this.vrm.lookAt.target = this.eyeLookAtTarget;
      this.vrm.lookAt.autoUpdate = true;
    }

    this.eyeTimer = 0;
    this.eyeTgtPos = new THREE.Vector3(0, 0, 5);

    this.bodyTimer  = 0;
    this.nextBody   = 0;
    this.bodyTgt    = { x: 0 };
    this.bodyCur    = { x: 0 };

    this.blinkTimer = 0;
    this.nextBlink  = 0;
    this.blinkVal   = 0;

    this._resetFaceAndEyes();

    // Idle state tracking
    this.idleLookingAtUser = false;
    this.idleLookAtUserTimer = 0;

    // Listening state tracking
    this.listeningSideLook = false;
    this.listeningSideLookTimer = 0;
    this.listeningSideLookDuration = 0;
    this.listeningSideDirection = 1; // 1 or -1

    // Talking state tracking
    this.talkingNodPhase = 0;
    this.talkingCurrentNodFreq = 2.0;
    this.talkingCurrentNodIntensity = 0.2;
    this.talkingNextNodChange = 0;

    // Thinking state tracking
    this.thinkingLookingAtUser = false;
    this.thinkingLookAtUserTimer = 0;

    this.config = {
      // head motion range
      headNod: 0.2,
      headTurn: 0.13,
      headTilt: 0.15,

      // frequency and speed (idle vs talk)
      headFreqIdle: 1.8, headFreqTalk: 0.8,  // lower is faster
      headEaseIdle: 0.02, headEaseTalk: 0.04,

      // body motion
      sway: 0.1,
      swayFreqIdle: 2.8, swayFreqTalk: 1.8,
      swayEaseIdle: 0.01, swayEaseTalk: 0.02,

      // blink
      blinkMin: 0.5,
      blinkMax: 3.0,
      blinkSpeed: 8.0,

      // Transition settings
      transitionLockDuration: 0.5, // Lock movements for 1 second after transition
      transitionEaseSpeed: 0.08,   // Speed of smooth reset to center (direct lerp, not physics)

      // Smooth movement physics (for idle state - slow, smooth movements)
      headAcceleration: 0.005,      // Base acceleration — raised from 0.001; update-order fix makes this effective
      headDamping: 0.88,           // Velocity damping — lowered from 0.85; less decay so head reaches targets

      // State-specific acceleration multipliers (relative to base)
      stateAcceleration: {
        idle: 1.0,        // 0.015 effective — smooth, slow look-around
        listening: 5.0,   // 0.12 effective — responsive nods
        thinking: 1.8,    // 0.03 effective — moderate
        talking: 1.5,    // 0.15 effective — snappy emphasis nods
      },

      // State-specific config
      stateConfig: {
        idle: {
          lookDuration: 3.0,           // Hold a look for 3 seconds
          lookChangeChance: 0.3,       // Chance per second to change look
          headEase: 0.014,             // Smooth easing
          headRangeX: 0.25,            // Max nod range
          headRangeY: 0.75,             // Max turn range to sides
          headRangeZ: 0.18,            // Max tilt range
          eyeRange: 7.0,               // Eye look distance from center
          // NEW: Look at user/reset settings
          lookAtUserChance: 0.35,      // 35% chance to look back at user after each look
          lookAtUserDurationMin: 1.5,  // Min duration looking at user
          lookAtUserDurationMax: 3.5,  // Max duration looking at user
          lookAtUserEyeReset: true     // Also reset eyes to center when looking at user
        },
        listening: {
          nodIntensity: 0.15,          // Gentler nods
          nodCount: 2,                 // Do 2 nods
          nodDuration: 2.0,           // Duration of each nod
          nodsChance: 0.3,             // Chance per 2 seconds to nod
          headEase: 0.005,              // Smooth easing
          eyeRange: 5.0,               // Smaller eye range - focused on user
          // NEW: Side glance settings
          sideLookChance: 0.15,        // Chance per second to glance to side
          sideLookDurationMin: 1.0,    // Min duration of side look
          sideLookDurationMax: 3.0,    // Max duration of side look
          sideLookHeadTurn: 0.15,      // How much head turns during side look
          sideLookEyeRange: 4.0,       // Eye movement during side look
          focusOnUser: true            // Default: eyes stay on user
        },
        thinking: {
          lookDuration: 1.5,           // Hold contemplative looks (shorter for more movement)
          lookChangeChance: 0.35,      // More frequent look changes
          headEase: 0.02,              // Very smooth
          headRangeX: 0.12,            // Subtle nod movements
          headRangeY: 0.25,            // Good turn for thinking
          headRangeZ: 0.12,            // Subtle tilts
          eyeRange: 6.0,               // Look far away while thinking
          lookUpBias: 0.6,             // Bias toward looking up (0-1)
          // Eye leads head settings
          eyeLeadTime: 0.1,            // Eyes move 0.2s before head
          eyeLeadAmount: 1.1,         // Eyes move 15% further than head target
          eyeHeadSync: 0.8,            // How often eyes and head move same direction (0-1)
          // Look at user reset (like idle)
          lookAtUserChance: 0.3,       // 30% chance to look back at user
          lookAtUserDurationMin: 1.0,  // Min duration looking at user
          lookAtUserDurationMax: 2.0   // Max duration looking at user
        },
        talking: {
          nodIntensity: 0.4,          // Base nod intensity
          nodFrequency: 1.4,           // Base nods per second
          nodVariation: 0.6,           // Variation in nod strength
          headEase: 0.055,             // Moderate easing
          occasionalTurn: 0.2,         // Occasional head turns
          eyeRange: 6.0,               // Normal eye range while talking
          // NEW: Variable nodding settings
          nodIntensityVariation: 0.1,  // +/- 40% intensity variation
          nodFrequencyVariation: 0.1,  // +/- 50% frequency variation
          nodChangeInterval: 1.5,      // Change nod params every 1.5 seconds
          // NEW: Head tilt while talking
          tiltChance: 0.25,            // Chance per second for head tilt
          tiltIntensity: 0.08,         // How much to tilt
          tiltDuration: 1.2            // How long tilt lasts
        }
      }
    };

    // DON'T START THE ANIMATION LOOP HERE - LET THE MAIN LOOP HANDLE IT
    // this.animate();
  }

  _resetFaceAndEyes() {
    try { this.vrm.humanoid?.resetNormalizedPose?.(); } catch (e) {}
    const em = this.vrm.expressionManager;
    if (em?.expressions) {
      for (const expr of em.expressions) {
        try { em.setValue(expr.expressionName, 0); } catch (e) {}
      }
    }
    for (const name of ['leftEye', 'rightEye']) {
      const node = this.vrm.humanoid?.getNormalizedBoneNode?.(name);
      if (node) node.rotation.set(0, 0, 0);
    }
    if (this.camera) {
      this.eyeLookAtTarget.position.copy(this.camera.position);
    }
  }

  /** Keep look-at aimed at the camera, with small idle wander offsets. */
  _syncLookAtToCamera() {
    if (!this.camera || !this.eyeLookAtTarget) return;
    const cam = this.camera.position;
    // eyeTgtPos.x/y are wander offsets (roughly -range..range); scale down for world space
    const ox = (this.eyeTgtPos.x || 0) * 0.08;
    const oy = (this.eyeTgtPos.y || 0) * 0.08;
    this.eyeLookAtTarget.position.set(cam.x + ox, cam.y + oy, cam.z);
  }

  // ========== STATE MANAGEMENT ==========
  setState(newState) {
    if (!['idle', 'listening', 'thinking', 'talking'].includes(newState)) {
      console.warn(`❌ Invalid state: ${newState}`);
      return;
    }
    if (this.state === newState) {
      console.log(`⚠️ Already in ${newState} state`);
      return;
    }

    console.log(`✅ [AnimationManager] State transition: ${this.state} -> ${newState}`);

    // Store previous state
    this.previousState = this.state;
    this.state = newState;
    this.stateTimer = 0;

    // Start smooth transition - head will ease back to center
    this.isTransitioning = true;
    this.transitionTimer = 0;

    // Set target to center for smooth reset
    this.headTgt = { x: 0, y: 0, z: 0 };
    this.eyeTargetTgt = { x: 0, y: 0, z: 5 };
    this.eyeTgtPos.set(0, 0, 5);

    // Reset state-specific timers
    this.headTimer = 0;
    this.eyeTimer = 0;
    this.nextHead = 0;
    this.eyeLeadTimer = 0;

    // Reset state-specific tracking
    this.idleLookingAtUser = false;
    this.idleLookAtUserTimer = 0;
    this.listeningSideLook = false;
    this.listeningSideLookTimer = 0;
    this.talkingNextNodChange = 0;
    this.thinkingLookingAtUser = false;
    this.thinkingLookAtUserTimer = 0;
    this._pendingHeadTarget = null;

    // Movement lock will be activated after transition completes
    this.movementLocked = false;
    this.movementLockTimer = 0;
  }

  // Set the movement lock duration (can be called from outside)
  setMovementLockDuration(duration) {
    this.movementLockDuration = Math.max(0, duration);
    this.config.transitionLockDuration = this.movementLockDuration;
    console.log(`🔒 Movement lock duration set to ${duration}s`);
  }

  getState() {
    return this.state;
  }

  // NEW: Method to set VRMA state
  setVRMAPlaying(isPlaying) {
    this.isVRMAPlaying = isPlaying;
  }

  // NEW: Method to set Mixamo state
  setMixamoPlaying(isPlaying) {
    this.isMixamoPlaying = isPlaying;
  }

  play() {
    this.audioMgr.resetMouth();
    this.audioMgr.audioElement.currentTime = 0;
    this.audioMgr.audioElement.play().catch(() => {});
    this.isPlaying = true;
  }

  stop() {
    this.audioMgr.audioElement.pause();
    this.isPlaying = false;
    this.audioMgr.resetMouth();
  }

  rand(min, max) {
    return min + Math.random() * (max - min);
  }

  getCurrentParams() {
    const talking = this.isPlaying && this.audioMgr.audioElement && !this.audioMgr.audioElement.ended;
    return {
      headFreq: talking ? this.config.headFreqTalk : this.config.headFreqIdle,
      headEase: talking ? this.config.headEaseTalk : this.config.headEaseIdle,
      swayFreq: talking ? this.config.swayFreqTalk : this.config.swayFreqIdle,
      swayEase: talking ? this.config.swayEaseTalk : this.config.swayEaseIdle
    };
  }

  // ========== HELPER METHODS FOR STATES ==========

  // Smooth ease function with acceleration/deceleration (ease in-out)
  smoothEase(current, target, velocity, acceleration, damping, deltaTime) {
    const diff = target - current;
    const force = diff * acceleration;
    const newVelocity = (velocity + force) * damping;
    const newValue = current + newVelocity * deltaTime * 60; // Normalize for 60fps
    return { value: newValue, velocity: newVelocity };
  }

  // Apply smooth physics-based movement to head (with state-specific acceleration)
  updateHeadWithPhysics(deltaTime) {
    // Get state-specific acceleration multiplier
    const stateMultiplier = this.config.stateAcceleration[this.state] || 1.0;
    const acc = this.config.headAcceleration * stateMultiplier;
    const damp = this.config.headDamping;

    // Update each axis with physics
    const xResult = this.smoothEase(this.headCur.x, this.headTgt.x, this.headVelocity.x, acc, damp, deltaTime);
    const yResult = this.smoothEase(this.headCur.y, this.headTgt.y, this.headVelocity.y, acc, damp, deltaTime);
    const zResult = this.smoothEase(this.headCur.z, this.headTgt.z, this.headVelocity.z, acc, damp, deltaTime);

    this.headCur.x = xResult.value;
    this.headCur.y = yResult.value;
    this.headCur.z = zResult.value;

    this.headVelocity.x = xResult.velocity;
    this.headVelocity.y = yResult.velocity;
    this.headVelocity.z = zResult.velocity;
  }

  centerHead(deltaTime, easeSpeed = 0.04) {
    // Smoothly center the head to neutral position using DIRECT LERP (not physics)
    // This ensures transitions complete reliably regardless of physics settings
    this.headCur.x += (0 - this.headCur.x) * easeSpeed;
    this.headCur.y += (0 - this.headCur.y) * easeSpeed;
    this.headCur.z += (0 - this.headCur.z) * easeSpeed;

    // Also dampen velocity during centering
    this.headVelocity.x *= 0.9;
    this.headVelocity.y *= 0.9;
    this.headVelocity.z *= 0.9;

    // Also center eyes
    this.eyeTgtPos.set(0, 0, 5);
    this.eyeLookAtTarget.position.lerp(this.eyeTgtPos, easeSpeed * 1.5);

    // Check if centered (within threshold)
    const threshold = 0.02;
    const isCentered = Math.abs(this.headCur.x) < threshold &&
                       Math.abs(this.headCur.y) < threshold &&
                       Math.abs(this.headCur.z) < threshold;

    return isCentered;
  }

  // Check if movement is currently locked
  isMovementLocked() {
    return this.movementLocked || this.isTransitioning;
  }

  updateIdleState(deltaTime, cfg) {
    // Idle: head looks around smoothly, with chance to look back at user

    this.headTimer += deltaTime;
    this.eyeTimer += deltaTime;

    // If currently looking at user, count down the timer
    if (this.idleLookingAtUser) {
      this.idleLookAtUserTimer -= deltaTime;

      if (this.idleLookAtUserTimer <= 0) {
        // Done looking at user, time to look somewhere else
        this.idleLookingAtUser = false;
        this.headTimer = 0; // Reset timer to trigger new look soon
      }
    } else {
      // Normal idle behavior - look around
      if (this.headTimer > cfg.lookDuration) {
        // Chance to look back at user/center
        if (Math.random() < cfg.lookAtUserChance) {
          // Look at user - reset to center
          this.idleLookingAtUser = true;
          this.idleLookAtUserTimer = this.rand(cfg.lookAtUserDurationMin, cfg.lookAtUserDurationMax);

          // Set targets to center (looking at camera/user)
          this.headTgt.x = 0;
          this.headTgt.y = 0;
          this.headTgt.z = 0;

          // Reset eyes to center if configured
          if (cfg.lookAtUserEyeReset) {
            this.eyeTgtPos.set(0, 0, 5);
          }

          this.headTimer = 0;
        } else if (Math.random() < cfg.lookChangeChance) {
          // Random look direction (not at user)
          const angle = Math.random() * Math.PI * 2;

          // Add some variation to make it more natural
          const rangeMultiplier = 0.6 + Math.random() * 0.4; // 60-100% of max range

          this.headTgt.x = Math.sin(angle) * cfg.headRangeX * rangeMultiplier;
          this.headTgt.y = Math.cos(angle) * cfg.headRangeY * rangeMultiplier;
          this.headTgt.z = this.rand(-cfg.headRangeZ, cfg.headRangeZ) * rangeMultiplier;

          // Eyes look in same direction (slightly exaggerated)
          this.eyeTgtPos.x = Math.sin(angle) * cfg.eyeRange;
          this.eyeTgtPos.y = Math.cos(angle) * cfg.eyeRange * 0.4;
          this.eyeTgtPos.z = 5 + Math.cos(angle) * 1.5;

          this.headTimer = 0;
        }
      }
    }

    // Use physics-based movement for smooth acceleration/deceleration
    this.updateHeadWithPhysics(deltaTime);

    // Update eye look target smoothly
    this.eyeLookAtTarget.position.lerp(this.eyeTgtPos, 0.025);
  }

  updateListeningState(deltaTime, cfg) {
    // Listening: focus on user with occasional side glances and DETERMINISTIC nods

    this.headTimer += deltaTime;
    this.eyeTimer += deltaTime;

    // Handle side look behavior
    if (this.listeningSideLook) {
      this.listeningSideLookTimer -= deltaTime;

      if (this.listeningSideLookTimer <= 0) {
        // End side look - return to focusing on user
        this.listeningSideLook = false;
        this.headTgt.y = 0; // Return head to center
        this.eyeTgtPos.set(0, 0, 5); // Eyes back to user
      }
    } else {
      // Check for triggering a side glance
      if (Math.random() < cfg.sideLookChance * deltaTime) {
        this.listeningSideLook = true;
        this.listeningSideLookTimer = this.rand(cfg.sideLookDurationMin, cfg.sideLookDurationMax);
        this.listeningSideDirection = Math.random() < 0.5 ? -1 : 1;

        // Turn head slightly to side
        this.headTgt.y = cfg.sideLookHeadTurn * this.listeningSideDirection;

        // Eyes glance to side
        this.eyeTgtPos.x = cfg.sideLookEyeRange * this.listeningSideDirection;
        this.eyeTgtPos.y = 0;
        this.eyeTgtPos.z = 5;
      }
    }

    // DETERMINISTIC NODDING - nods happen on a schedule, not by random chance
    // Nod cycle: every 2-3 seconds, do a series of nods
    const nodCycleDuration = 2.5; // Full cycle takes 2.5 seconds
    const nodActivePortion = 0.4; // Nods happen in first 40% of cycle (1 second)
    const cycleTime = this.stateTimer % nodCycleDuration;
    const cyclePhase = cycleTime / nodCycleDuration;

    // Only nod when not doing a side look
    if (!this.listeningSideLook) {
      if (cyclePhase < nodActivePortion) {
        // During nod portion: create smooth nod motion
        // Map cyclePhase [0, nodActivePortion] to [0, 2*PI*nodCount] for multiple nods
        const nodProgress = cyclePhase / nodActivePortion;
        const nodPhase = nodProgress * Math.PI * 2 * cfg.nodCount;
        const nodAmount = Math.sin(nodPhase) * this.config.headNod * cfg.nodIntensity;
        this.headTgt.x = nodAmount;
      } else {
        // Between nod cycles - smoothly return to neutral
        this.headTgt.x *= 0.9;
      }

      // Keep eyes on user when not side-looking
      if (cfg.focusOnUser) {
        // Subtle micro-movements while focused
        if (this.eyeTimer > 2.0) {
          // Very small eye movements to simulate natural focus
          this.eyeTgtPos.x = this.rand(-1, 1);
          this.eyeTgtPos.y = this.rand(-0.5, 0.5);
          this.eyeTgtPos.z = 5;
          this.eyeTimer = 0;
        }
      }
    }

    // Use physics-based movement (with state-specific acceleration)
    this.updateHeadWithPhysics(deltaTime);

    // Update eye look target
    this.eyeLookAtTarget.position.lerp(this.eyeTgtPos, 0.03);
  }

  updateThinkingState(deltaTime, cfg) {
    // Thinking: look away thoughtfully with eyes leading head, with chance to look at user

    this.headTimer += deltaTime;
    this.eyeTimer += deltaTime;
    this.eyeLeadTimer += deltaTime;

    // Handle look-at-user state (similar to idle)
    if (this.thinkingLookingAtUser) {
      this.thinkingLookAtUserTimer -= deltaTime;

      if (this.thinkingLookAtUserTimer <= 0) {
        // Done looking at user, time to think/look away again
        this.thinkingLookingAtUser = false;
        this.headTimer = 0;
      }
    } else {
      // Check if it's time to pick a new look direction
      if (this.headTimer > cfg.lookDuration) {
        // Chance to look at user (brief focus before thinking again)
        if (Math.random() < cfg.lookAtUserChance) {
          this.thinkingLookingAtUser = true;
          this.thinkingLookAtUserTimer = this.rand(cfg.lookAtUserDurationMin, cfg.lookAtUserDurationMax);

          // Eyes move to user first
          this.eyeTgtPos.set(0, 0, 5);

          // Head will follow after eye lead time
          this._pendingHeadTarget = { x: 0, y: 0, z: 0 };
          this.eyeLeadTimer = 0;
          this.headTimer = 0;
        } else if (Math.random() < cfg.lookChangeChance) {
          // Look away thoughtfully
          // Bias toward looking up and to the sides (contemplative)
          const angle = (Math.random() * Math.PI * 1.6) - (Math.PI * 0.3);
          const upBias = cfg.lookUpBias * 0.25;

          // Calculate intended head target
          const newHeadX = (Math.sin(angle) * cfg.headRangeX) + upBias;
          const newHeadY = Math.cos(angle) * cfg.headRangeY;
          const newHeadZ = this.rand(-cfg.headRangeZ, cfg.headRangeZ);

          // Eyes move FIRST - set eye target immediately
          const eyeSync = Math.random() < cfg.eyeHeadSync;
          if (eyeSync) {
            // Eyes move in same direction as head will go
            this.eyeTgtPos.x = Math.sin(angle) * cfg.eyeRange * cfg.eyeLeadAmount;
            this.eyeTgtPos.y = (cfg.eyeRange * 0.6 + upBias * 10) * cfg.eyeLeadAmount;
            this.eyeTgtPos.z = 4;
          } else {
            // Occasional divergent eye movement
            const divergeAngle = Math.random() * Math.PI * 2;
            this.eyeTgtPos.x = Math.sin(divergeAngle) * cfg.eyeRange * 0.6;
            this.eyeTgtPos.y = cfg.eyeRange * 0.4;
            this.eyeTgtPos.z = 5;
          }

          // Store pending head target (applied after eye lead time)
          this._pendingHeadTarget = { x: newHeadX, y: newHeadY, z: newHeadZ };
          this.eyeLeadTimer = 0;
          this.headTimer = 0;
        }
      }
    }

    // Apply pending head target after eye lead time
    if (this._pendingHeadTarget && this.eyeLeadTimer >= cfg.eyeLeadTime) {
      this.headTgt.x = this._pendingHeadTarget.x;
      this.headTgt.y = this._pendingHeadTarget.y;
      this.headTgt.z = this._pendingHeadTarget.z;
      this._pendingHeadTarget = null;
    }

    // Use physics-based movement (with state-specific acceleration)
    this.updateHeadWithPhysics(deltaTime);

    // Eyes move faster than head (they lead)
    this.eyeLookAtTarget.position.lerp(this.eyeTgtPos, 0.04);
  }

  updateTalkingState(deltaTime, cfg) {
    // Talking: variable nodding with randomness in intensity/frequency, plus head tilts

    this.headTimer += deltaTime;
    this.eyeTimer += deltaTime;
    this.talkingNodPhase += deltaTime;

    // Periodically change nod parameters for natural variation
    if (this.stateTimer > this.talkingNextNodChange) {
      // Randomize nod frequency within configured variation
      const freqVariation = 1 + (Math.random() * 2 - 1) * cfg.nodFrequencyVariation;
      this.talkingCurrentNodFreq = cfg.nodFrequency * freqVariation;

      // Randomize nod intensity within configured variation
      const intensityVariation = 1 + (Math.random() * 2 - 1) * cfg.nodIntensityVariation;
      this.talkingCurrentNodIntensity = cfg.nodIntensity * intensityVariation;

      // Set next change time
      this.talkingNextNodChange = this.stateTimer + cfg.nodChangeInterval * (0.7 + Math.random() * 0.6);
    }

    // Calculate nod with current (randomized) parameters
    // Add micro-pauses by occasionally flattening the nod
    const nodActive = Math.random() > 0.15; // 85% of time actively nodding
    if (nodActive) {
      const nodPhase = (this.talkingNodPhase * this.talkingCurrentNodFreq * Math.PI * 2) % (Math.PI * 2);

      // Create non-uniform nodding - more variation in the sine wave
      const rawNod = Math.sin(nodPhase);
      const nodVariation = rawNod * (0.7 + Math.random() * 0.3); // Add per-frame randomness
      const nodStrength = nodVariation * this.talkingCurrentNodIntensity * cfg.nodVariation;
      this.headTgt.x = nodStrength * this.config.headNod;
    } else {
      // Brief pause in nodding
      this.headTgt.x *= 0.9;
    }

    // Head tilts while talking (for emphasis)
    if (Math.random() < cfg.tiltChance * deltaTime) {
      // Apply a tilt in random direction
      const tiltDirection = Math.random() < 0.5 ? -1 : 1;
      this.headTgt.z = cfg.tiltIntensity * tiltDirection * (0.6 + Math.random() * 0.4);
    }

    // Occasional head turns for emphasis (less frequent than tilts)
    if (Math.random() < cfg.occasionalTurn * deltaTime * 0.5) {
      this.headTgt.y = this.rand(-this.config.headTurn * 0.15, this.config.headTurn * 0.15);
    }

    // Gradual decay of turns and tilts back to center (not instant)
    this.headTgt.y *= 0.95;
    this.headTgt.z *= 0.94;

    // Eye gaze - mostly on user with subtle shifts
    if (this.eyeTimer > 2.5) {
      // Small variations while maintaining user focus
      const eyeShift = Math.random() * 0.4 - 0.2;
      this.eyeTgtPos.x = Math.sin(eyeShift) * cfg.eyeRange * 0.3;
      this.eyeTgtPos.y = this.rand(-1, 1);
      this.eyeTgtPos.z = 5;
      this.eyeTimer = 0;
    }

    // Use physics-based movement for natural acceleration/deceleration
    this.updateHeadWithPhysics(deltaTime);

    // Update eye look target
    this.eyeLookAtTarget.position.lerp(this.eyeTgtPos, 0.025);
  }

  applyEyeMovement() {
    // Apply eye movement to VRM if supported
    if (!this.vrm.expressionManager) return;

    // Try different possible eye expression names that VRM models might have
    const eyeExpressions = ['eyeLookUp', 'eyeLookDown', 'eyeLookLeft', 'eyeLookRight',
                             'eye_look_up', 'eye_look_down', 'eye_look_left', 'eye_look_right'];

    try {
      const upVal = Math.max(0, Math.min(1, this.eyeCur.y));
      const downVal = Math.max(0, Math.min(1, -this.eyeCur.y));
      const leftVal = Math.max(0, Math.min(1, -this.eyeCur.x));
      const rightVal = Math.max(0, Math.min(1, this.eyeCur.x));

      // Try to set eye expressions
      ['eyeLookUp', 'eye_look_up'].forEach(expr => {
        try { this.vrm.expressionManager.setValue(expr, upVal); } catch (e) {}
      });
      ['eyeLookDown', 'eye_look_down'].forEach(expr => {
        try { this.vrm.expressionManager.setValue(expr, downVal); } catch (e) {}
      });
      ['eyeLookLeft', 'eye_look_left'].forEach(expr => {
        try { this.vrm.expressionManager.setValue(expr, leftVal); } catch (e) {}
      });
      ['eyeLookRight', 'eye_look_right'].forEach(expr => {
        try { this.vrm.expressionManager.setValue(expr, rightVal); } catch (e) {}
      });
    } catch (e) {
      // Silently ignore if eyes not supported
    }
  }

  // MODIFIED: Remove the animation loop, make this a simple update method
  update(deltaTime) {
    if (!this.vrm) return;

    // ALWAYS HANDLE BLINKING - regardless of state or VRMA/Mixamo
    this.blinkTimer += deltaTime;
    if (this.blinkTimer > this.nextBlink) {
      this.blinkTimer = 0;
      this.nextBlink = this.rand(this.config.blinkMin, this.config.blinkMax);
    }
    this.blinkVal += (this.blinkTimer < 0.1 ? deltaTime : -deltaTime) * this.config.blinkSpeed;
    this.blinkVal = Math.max(0, Math.min(1, this.blinkVal));
    this._setBlink(this.blinkVal);
    this.vrm.expressionManager.setValue('neutral', 1.0);

    // Handle audio/lip sync regardless of animation state
    if (this.isPlaying && this.audioMgr.audioElement) {
      this.audioMgr.updateLipSync(this.audioMgr.audioElement.currentTime);
      if (this.audioMgr.audioElement.ended) this.stop();
    }

    // ========== TRANSITION HANDLING ==========
    // Smooth transition: ease head to center, then lock movements for a duration

    if (this.isTransitioning) {
      this.transitionTimer += deltaTime;

      // Phase 1: Smooth reset to center with physics-based easing
      const isCentered = this.centerHead(deltaTime, this.config.transitionEaseSpeed);

      // Check if transition is complete (head centered and enough time passed)
      const minTransitionTime = 0.4; // Minimum time for smooth visual
      if (isCentered && this.transitionTimer >= minTransitionTime) {
        // Transition complete - start movement lock
        this.isTransitioning = false;
        this.movementLocked = true;
        this.movementLockTimer = 0;

        // Reset velocity for clean start
        this.headVelocity = { x: 0, y: 0, z: 0 };

        console.log(`🔒 Transition complete, movement locked for ${this.config.transitionLockDuration}s`);
      }
    } else if (this.movementLocked) {
      // Phase 2: Movement lock - head stays centered
      this.movementLockTimer += deltaTime;

      // Keep head and eyes centered during lock
      this.headTgt = { x: 0, y: 0, z: 0 };
      this.updateHeadWithPhysics(deltaTime);
      this.eyeTgtPos.set(0, 0, 5);
      this._syncLookAtToCamera();

      if (this.movementLockTimer >= this.config.transitionLockDuration) {
        // Lock period over - allow state animations
        this.movementLocked = false;
        console.log(`🔓 Movement unlocked, ${this.state} animations starting`);
      }
    } else {
      // ========== STATE-SPECIFIC ANIMATIONS ==========
      // Only run when not transitioning and not locked
      const stateCfg = this.config.stateConfig[this.state];

      if (this.state === 'idle') {
        this.updateIdleState(deltaTime, stateCfg);
      } else if (this.state === 'listening') {
        this.updateListeningState(deltaTime, stateCfg);
      } else if (this.state === 'thinking') {
        this.updateThinkingState(deltaTime, stateCfg);
      } else if (this.state === 'talking') {
        this.updateTalkingState(deltaTime, stateCfg);
      }
    }

    this.stateTimer += deltaTime;

    // Apply head and neck rotations from state animations
    // This runs regardless of VRMA/Mixamo playing
    const neck = this.vrm.humanoid.getNormalizedBoneNode('neck');
    if (neck) neck.rotation.set(this.headCur.x, this.headCur.y, this.headCur.z);

    // Eyes always look at the viewer
    this._syncLookAtToCamera();

    // SKIP BODY / ARMS OVERRIDE WHILE A ONE-OFF GESTURE IS PLAYING
    if (this.isVRMAPlaying || this.isMixamoPlaying) {
      return;
    }

    this._enforceStandingArms();

    // Body sway (gentle regardless of state)
    this.bodyTimer += deltaTime;
    const swayFreq = 2.8;
    const swayEase = 0.01;
    if (this.bodyTimer > swayFreq) {
      this.bodyTgt.x = this.rand(-this.config.sway * 0.5, this.config.sway * 0.5);
      this.bodyTimer = 0;
    }
    this.bodyCur.x += (this.bodyTgt.x - this.bodyCur.x) * swayEase;
    const spine = this.vrm.humanoid.getNormalizedBoneNode('spine');
    if (spine) spine.rotation.x = this.bodyCur.x;

    // DON'T CALL vrm.update() OR renderer.render() HERE - LET THE MAIN LOOP HANDLE IT
  }

  _enforceStandingArms() {
    const bone = (name) => this.vrm.humanoid?.getNormalizedBoneNode(name);
    const leftUpper = bone('leftUpperArm');
    const rightUpper = bone('rightUpperArm');
    const leftLower = bone('leftLowerArm');
    const rightLower = bone('rightLowerArm');
    const leftHand = bone('leftHand');
    const rightHand = bone('rightHand');
    const L = this.standingArms?.leftUpper || [0, 0, -1.2];
    const R = this.standingArms?.rightUpper || [0, 0, 1.2];
    if (leftUpper) leftUpper.rotation.set(L[0], L[1], L[2]);
    if (rightUpper) rightUpper.rotation.set(R[0], R[1], R[2]);
    if (leftLower) leftLower.rotation.set(0, 0, 0);
    if (rightLower) rightLower.rotation.set(0, 0, 0);
    if (leftHand) leftHand.rotation.set(0, 0, 0);
    if (rightHand) rightHand.rotation.set(0, 0, 0);
  }

  _setBlink(val) {
    const em = this.vrm.expressionManager;
    if (!em) return;
    for (const name of ['blink', 'Blink', 'blinkLeft', 'blinkRight', 'blink_l', 'blink_r']) {
      try { em.setValue(name, val); } catch (e) {}
    }
  }
}