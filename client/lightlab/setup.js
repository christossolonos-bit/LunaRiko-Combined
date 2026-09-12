/**
 * The lighting sandbox's whole document, in one plain object.
 *
 * Everything the panel edits lives here and nowhere else, so "save", "reset"
 * and "copy to clipboard" are all one-liners over the same shape. The `lights`
 * array is deliberately the SAME shape sceneSetup.js DEFAULTS.lights expects —
 * that is what makes the copy-paste back into the real app work.
 *
 * Persisted to localStorage on every change: the character and room dropdowns
 * swap by reloading the page (disposing GLBs/VRMs by hand is fiddly and this
 * is a sandbox), and a reload that lost your lighting would be useless.
 */

import { SCENE_PRESETS } from '../scene/scenePresets.js';

const STORAGE_KEY = 'riko.lightlab.setup.v1';

export const ROOM_CHOICES = {
  'anime classroom': './backgrounds/glb/anime_class_room.glb',
  'japanese classroom': './backgrounds/glb/japanese_classroom.glb',
  'riko room': './backgrounds/glb/RikoRoomFixed.glb',
  'tiny isometric': './backgrounds/glb/tiny_isometric_room.glb',
  'none': '',
};

// One list, shared with the real page — the panel can't offer a mode the
// renderer would silently ignore.
export { TONE_MAPPING_NAMES as TONE_MAPPINGS } from '../scene/scenePresets.js';

/**
 * What the sandbox opens on: the shipped `classroom` preset.
 *
 * The numbers live in scene/scenePresets.js, not here, because the real page
 * reads them too — one copy, so what you tune and what ships cannot drift
 * apart. Editing a preset changes both what `?reset=1` returns to AND what
 * `?scene=` loads.
 */
const BASE_PRESET = 'classroom';

export const DEFAULT_SETUP = {
  character: SCENE_PRESETS[BASE_PRESET].tunedFor ?? 'riko',
  ...structuredClone(SCENE_PRESETS[BASE_PRESET]),
};
delete DEFAULT_SETUP.tunedFor;
delete DEFAULT_SETUP.label;
// The grid is a sandbox helper the real page never draws, so it is on here
// regardless of what the preset says.
DEFAULT_SETUP.renderer.grid = true;

/** Deep-ish clone that is good enough for a config object of arrays + scalars. */
export const clone = (o) => JSON.parse(JSON.stringify(o));

/**
 * Give a light definition every field ANY type could need, whatever type it
 * currently is.
 *
 * The panel binds one control per field, and lil-gui refuses to bind a property
 * that is `undefined` — it logs and hands back nothing, so the next `.name(...)`
 * in the chain throws and takes the whole panel down. A def written for a
 * directional light has no `angle`, so switching it to a spot did exactly that.
 *
 * Carrying a few unused keys is the cheap fix, and it makes a switch between
 * ANY two types safe — including types added later. The unused ones are not
 * written out: toPasteable only emits the keys that matter for the type.
 */
export function normaliseLight(def) {
  return {
    name: 'light',
    type: 'directional',
    color: '#ffffff',
    intensity: 1,
    position: [0, 2, 2],
    target: [0, 1.1, 0],
    castShadow: false,
    angle: Math.PI / 6,     // spot
    penumbra: 0.4,          // spot
    decay: 2,               // point + spot
    groundColor: '#404040', // hemisphere
    ...def,
  };
}

const normalise = (setup) => {
  setup.lights = setup.lights.map(normaliseLight);
  return setup;
};

/** Merge saved values over the defaults so a new field never breaks an old save. */
function merge(base, saved) {
  if (Array.isArray(base) || saved === null || typeof saved !== 'object') return saved;
  const out = { ...base };
  for (const k of Object.keys(saved)) out[k] = merge(base[k], saved[k]);
  return out;
}

export function loadSetup() {
  // Escape hatch. Every change is saved, so a setup that kills the page comes
  // straight back on reload — and the "reset to default" button lives in a
  // panel that never renders. `?reset=1` is the way back in.
  if (typeof location !== 'undefined' && new URLSearchParams(location.search).has('reset')) {
    clearSetup();
    console.warn('[lightlab] ?reset=1 — saved setup cleared, back to the default rig');
    return normalise(clone(DEFAULT_SETUP));
  }
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) {
      console.log('[lightlab] no saved setup — starting from the default rig');
      return normalise(clone(DEFAULT_SETUP));
    }
    const setup = merge(clone(DEFAULT_SETUP), JSON.parse(raw));
    console.log(`[lightlab] restored saved setup (${setup.lights.length} lights)`);
    return normalise(setup);
  } catch (e) {
    console.warn('[lightlab] saved setup unreadable, using defaults:', e);
    return normalise(clone(DEFAULT_SETUP));
  }
}

export function saveSetup(setup) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(setup));
  } catch (e) {
    console.warn('[lightlab] could not save setup:', e);
  }
}

export function clearSetup() {
  try { localStorage.removeItem(STORAGE_KEY); } catch (e) {}
}

const hex = (c) => `0x${c.replace('#', '').toLowerCase()}`;

const vec = (a) => `[${a.map((v) => +v.toFixed(3)).join(', ')}]`;

/**
 * Render the setup as text you can paste back into the real app.
 *
 * The `lights:` array is emitted in sceneSetup.js's exact shape, keys included
 * — `target`, `angle`, `penumbra` and `groundColor` are all read there, so a
 * paste reproduces what you tuned rather than an origin-aimed approximation.
 */
export function toPasteable(setup) {
  const lines = setup.lights.map((l) => {
    const bits = [`type: '${l.type}'`, `color: ${hex(l.color)}`, `intensity: ${+l.intensity.toFixed(3)}`];
    if (l.type !== 'ambient') bits.push(`position: ${vec(l.position)}`);
    if (l.type === 'directional' || l.type === 'spot') bits.push(`target: ${vec(l.target)}`);
    if (l.type === 'spot') bits.push(`angle: ${+l.angle.toFixed(3)}, penumbra: ${+l.penumbra.toFixed(2)}`);
    if (l.type === 'hemisphere') bits.push(`groundColor: ${hex(l.groundColor ?? '#404040')}`);
    if (l.castShadow) bits.push('castShadow: true');
    return `    { ${bits.join(', ')} },   // ${l.name}`;
  });

  return [
    '// ---- paste over `lights:` in client/scene/sceneSetup.js DEFAULTS ----',
    '  lights: [',
    ...lines,
    '  ],',
    '',
    '// ---- and these two, once, after the renderer is made ----',
    `//   renderer.toneMapping = THREE.${setup.renderer.toneMapping}ToneMapping;`,
    `//   renderer.toneMappingExposure = ${setup.renderer.exposure};`,
    '',
    '// ---- MToon: no home in the real app yet. Feed it to MToonTuner, or set',
    '//      these on the VRM materials after loadVRM(). ----',
    `//   ${JSON.stringify(setup.mtoon)}`,
    '',
    '// ---- depth of field: needs client/lightlab/bokeh.js in the render loop,',
    '//      there is no value to paste for this one ----',
    `//   ${JSON.stringify(setup.bokeh)}`,
    '',
    '// ---- room / camera the rig was tuned against ----',
    `//   room:   ${JSON.stringify(setup.room)}`,
    `//   camera: ${JSON.stringify(setup.camera)}`,
  ].join('\n');
}
