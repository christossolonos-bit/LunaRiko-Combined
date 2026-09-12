import * as THREE from 'three';

/**
 * The character's toon shader, as tunable knobs.
 *
 * A VRM is drawn with MToon, which is a CARTOON shader: instead of a smooth
 * gradient from lit to unlit it has a hard line with a flat "shade" colour on
 * the dark side. Normal lights barely move that, which is why lighting a VRM
 * with lights alone always looks flat — the ramp itself is a material setting.
 *
 * Every knob below is a DELTA or a MULTIPLIER on what the model shipped with,
 * except the rim block (models almost always ship that at zero). So all-zeros
 * means "untouched", and `reset()` really does restore the original look.
 */

const KEYS = [
  'shadeColorFactor', 'shadingShiftFactor', 'shadingToonyFactor',
  'parametricRimColorFactor', 'parametricRimFresnelPowerFactor',
  'parametricRimLiftFactor', 'rimLightingMixFactor',
];

export class MToonTuner {
  constructor(vrm) {
    this.materials = [];
    this.originals = new Map();

    vrm.scene.traverse((o) => {
      if (!o.isMesh) return;
      const mats = Array.isArray(o.material) ? o.material : [o.material];
      for (const m of mats) {
        if (!m?.isMToonMaterial || this.originals.has(m)) continue;
        const snap = {};
        for (const k of KEYS) {
          const v = m[k];
          snap[k] = v?.isColor ? v.clone() : v;
        }
        this.originals.set(m, snap);
        this.materials.push(m);
      }
    });

    console.log(`[mtoon] ${this.materials.length} MToon materials found`
      + (this.materials.length ? '' : ' — this model is not toon-shaded, the sliders will do nothing'));
    this._tint = new THREE.Color();
    this._tmp = new THREE.Color();
  }

  apply(s) {
    if (!s.enabled) return this.reset();

    this._tint.set(s.shadeTint);
    for (const m of this.materials) {
      const o = this.originals.get(m);

      // Shade colour: start from the model's own, then lerp it toward
      // (its own x the tint). Strength 0 leaves it exactly as it shipped.
      this._tmp.copy(o.shadeColorFactor).multiply(this._tint);
      m.shadeColorFactor.copy(o.shadeColorFactor).lerp(this._tmp, s.shadeStrength);

      m.shadingShiftFactor = THREE.MathUtils.clamp(o.shadingShiftFactor + s.shadingShift, -1, 1);
      m.shadingToonyFactor = THREE.MathUtils.clamp(o.shadingToonyFactor + s.shadingToony, 0, 1);

      // Rim: absolute, because a model that ships with rim already on is rare
      // and this is the knob that produces the anime edge-glow.
      m.parametricRimColorFactor.set(s.rimColor);
      m.rimLightingMixFactor = s.rimMix;
      m.parametricRimFresnelPowerFactor = s.rimFresnel;
      m.parametricRimLiftFactor = s.rimLift;

      m.needsUpdate = true;
    }
  }

  reset() {
    for (const m of this.materials) {
      const o = this.originals.get(m);
      for (const k of KEYS) {
        if (m[k]?.isColor) m[k].copy(o[k]);
        else m[k] = o[k];
      }
      m.needsUpdate = true;
    }
    console.log('[mtoon] restored every material to how it loaded');
  }
}
