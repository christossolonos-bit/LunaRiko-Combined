import * as THREE from 'three';

/**
 * Strip root motion (hips X/Z position) from an animation clip.
 * Keeps Y for vertical motion (jumps, crouching).
 */
export function stripRootMotionFromClip(clip, vrm) {
  const hipsNodeName = vrm.humanoid?.getNormalizedBoneNode('hips')?.name;
  if (!hipsNodeName) return clip;

  const newTracks = [];

  for (const track of clip.tracks) {
    if (track.name.includes(hipsNodeName) && track.name.includes('.position')) {
      const newValues = new Float32Array(track.values.length);
      const stride = track.getValueSize();

      for (let i = 0; i < track.values.length; i += stride) {
        newValues[i] = 0;                      // X - zero
        newValues[i + 1] = track.values[i + 1]; // Y - keep
        newValues[i + 2] = 0;                   // Z - zero
      }

      newTracks.push(new track.constructor(track.name, track.times, newValues));
    } else {
      newTracks.push(track);
    }
  }

  return new THREE.AnimationClip(clip.name + '_locked', clip.duration, newTracks);
}

/**
 * Get the position delta from first to last keyframe of the hips track.
 */
export function getAnimationEndPosition(clip, vrm) {
  const hipsNodeName = vrm.humanoid?.getNormalizedBoneNode('hips')?.name;
  if (!hipsNodeName) return null;

  for (const track of clip.tracks) {
    if (track.name.includes(hipsNodeName) && track.name.includes('.position')) {
      const stride = track.getValueSize();
      const lastIndex = track.values.length - stride;

      const startX = track.values[0];
      const startZ = track.values[2];
      const endX = track.values[lastIndex];
      const endZ = track.values[lastIndex + 2];

      return new THREE.Vector3(endX - startX, 0, endZ - startZ);
    }
  }
  return null;
}

/**
 * Apply a position delta to the VRM scene root.
 */
export function applyAnimationEndPosition(vrm, positionDelta) {
  if (!positionDelta) return;
  vrm.scene.position.x += positionDelta.x;
  vrm.scene.position.z += positionDelta.z;
}

/**
 * Get current hips world position.
 */
export function getHipsWorldPosition(vrm) {
  const hipsNode = vrm.humanoid?.getNormalizedBoneNode('hips');
  if (!hipsNode) return null;

  const worldPos = new THREE.Vector3();
  hipsNode.getWorldPosition(worldPos);
  return worldPos;
}

/**
 * Trim an animation clip to [startTime, endTime].
 */
export function trimAnimationClip(clip, startTime, endTime) {
  startTime = Math.max(0, startTime || 0);
  const fullDuration = clip.duration;
  endTime = (typeof endTime === 'number' && endTime >= 0)
    ? Math.min(fullDuration, endTime)
    : fullDuration;

  if (endTime <= startTime) {
    console.warn('trimAnimationClip: invalid range', { startTime, endTime, duration: fullDuration });
    return null;
  }

  const newTracks = [];

  for (const track of clip.tracks) {
    const times = track.times;
    const values = track.values;
    const stride = track.getValueSize();
    const keptTimes = [];
    const keptValues = [];

    for (let i = 0; i < times.length; i++) {
      const t = times[i];
      if (t >= startTime && t <= endTime) {
        keptTimes.push(t - startTime);
        const baseIndex = i * stride;
        for (let s = 0; s < stride; s++) {
          keptValues.push(values[baseIndex + s]);
        }
      }
    }

    if (keptTimes.length > 0) {
      const newTimes = new Float32Array(keptTimes);
      const newValues = new Float32Array(keptValues);

      let newTrack;
      try {
        newTrack = new track.constructor(
          track.name, newTimes, newValues,
          track.getInterpolation ? track.getInterpolation() : undefined,
        );
      } catch {
        newTrack = new track.constructor(track.name, newTimes, newValues);
      }
      newTracks.push(newTrack);
    }
  }

  const newDuration = endTime - startTime;
  const newName = `${clip.name || 'vrma_clip'}_trimmed_${startTime.toFixed(3)}-${endTime.toFixed(3)}`;
  return new THREE.AnimationClip(newName, newDuration, newTracks);
}
