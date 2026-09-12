/**
 * Standalone check for the parts of the sandbox that are real logic rather
 * than wiring: the rig builds every light type, and the paste-back text comes
 * out in the shape sceneSetup.js actually reads.
 *
 *   cd client && node lightlab/selfcheck.mjs
 *
 * Runs headless — no browser, no WebGL, no server. The panel, the MToon tuner
 * and the bokeh composer are not covered: all three need a live GL context, so
 * the browser is the test for those.
 *
 * These assert on BEHAVIOUR, not on which lights happen to ship. The default
 * rig is somebody's tuned look and will keep changing.
 */
import assert from 'node:assert';
import * as THREE from 'three';

import { LightRig, LIGHT_TYPES } from './rig.js';
import { DEFAULT_SETUP, clone, toPasteable, loadSetup, saveSetup, normaliseLight } from './setup.js';

const store = new Map();
globalThis.localStorage = {
  getItem: (k) => store.get(k) ?? null,
  setItem: (k, v) => store.set(k, v),
  removeItem: (k) => store.delete(k),
};

// --- the rig builds and syncs every type without throwing ---
{
  const scene = new THREE.Scene();
  const rig = new LightRig(scene);
  const lights = loadSetup().lights;
  rig.setAll(lights);

  assert.equal(rig.entries.length, lights.length, 'the rig dropped a light');
  // ambient is the one type with no position, so it is the one with no marker
  const positioned = lights.filter((l) => l.type !== 'ambient').length;
  assert.equal(rig.markers().length, positioned, 'a marker per light that has a position');

  for (const e of rig.entries) {
    if (e.def.type === 'ambient') continue;
    assert.deepEqual(e.light.position.toArray(), e.def.position, `${e.def.name}: position not applied`);
    if (e.light.target) {
      assert.deepEqual(e.light.target.position.toArray(), e.def.target, `${e.def.name}: aim not applied`);
    }
    assert.equal(e.light.castShadow, !!e.def.castShadow, `${e.def.name}: castShadow not applied`);
  }

  // a point light's shadow is a cube — six renders — so it must not use the 2048 map
  const p = rig.add(normaliseLight({ name: 'p', type: 'point', castShadow: true }));
  assert.equal(p.light.shadow.mapSize.x, 512, 'point shadows must stay small');
  rig.remove(p);

  // every type survives a rebuild — this is what the panel's type dropdown does
  for (const type of LIGHT_TYPES) {
    const e = rig.entries[1];
    e.def.type = type;
    rig.entries[1] = rig.rebuild(e);
    assert.equal(rig.entries.length, lights.length, `rebuild to ${type} lost a light`);
    assert.ok(rig.entries.every((x) => x.def && x.def.name), `rebuild to ${type} left an entry with no def`);
  }

  rig.setAll([]);
  assert.equal(rig.entries.length, 0, 'setAll([]) should clear the rig');
}

// --- every light def carries every field, whatever its type ---
// The panel binds one control per field and lil-gui throws on an undefined
// property, so a def missing `angle` blew the whole panel up the moment you
// switched that light to a spot. This is that bug, in assert form.
{
  const REQUIRED = ['name', 'type', 'color', 'intensity', 'position', 'target',
    'castShadow', 'angle', 'penumbra', 'decay', 'groundColor'];

  for (const light of loadSetup().lights) {
    for (const k of REQUIRED) {
      assert.notEqual(light[k], undefined,
        `default light "${light.name}" has no "${k}" — switching it to another type will throw`);
    }
  }

  const bare = normaliseLight({ name: 'x', type: 'spot' });
  for (const k of REQUIRED) assert.notEqual(bare[k], undefined, `normaliseLight left "${k}" undefined`);
  assert.equal(bare.type, 'spot', 'normaliseLight must not clobber what the def already said');
}

// --- the paste-back text matches sceneSetup.js's lights format ---
// Built from a synthetic rig with one of every type, so this tests the
// formatter rather than whichever lights happen to ship today.
{
  const setup = clone(DEFAULT_SETUP);
  setup.lights = [
    normaliseLight({ name: 'amb', type: 'ambient', color: '#8fb4ff', intensity: 0.25 }),
    normaliseLight({ name: 'k', type: 'directional', color: '#ffd9b0', intensity: 2,
      position: [2.5, 3, 2.2], target: [0, 1.1, 0], castShadow: true }),
    normaliseLight({ name: 's', type: 'spot', color: '#ffffff', intensity: 5,
      position: [1, 2, 3], target: [0, 1, 0], angle: 0.524, penumbra: 0.4 }),
    normaliseLight({ name: 'h', type: 'hemisphere', color: '#ffffff', intensity: 1,
      groundColor: '#402020' }),
  ];
  const text = toPasteable(setup);

  assert.ok(text.includes("{ type: 'ambient', color: 0x8fb4ff, intensity: 0.25 },"),
    'ambient must carry no position');
  assert.ok(text.includes("position: [2.5, 3, 2.2], target: [0, 1.1, 0], castShadow: true }"),
    'a directional must keep its position, its aim and its shadow');
  assert.ok(text.includes("angle: 0.524, penumbra: 0.4"), 'a spot must keep its cone');
  assert.ok(text.includes("groundColor: 0x402020"), 'a hemisphere must keep its ground colour');
  assert.ok(!text.includes("angle: 0.524, penumbra: 0.4 },   // k"), 'spot-only keys must not leak onto other types');
  assert.ok(text.includes('THREE.ACESFilmicToneMapping'), 'tone mapping note missing');
  assert.ok(text.includes('"maxBlur"'), 'bokeh settings missing from the paste-back');
}

// --- a saved setup missing a newer field still loads ---
{
  store.clear();
  localStorage.setItem('riko.lightlab.setup.v1',
    JSON.stringify({ character: 'ino', renderer: { exposure: 2.0 } }));
  const merged = loadSetup();
  assert.equal(merged.character, 'ino', 'saved value lost');
  assert.equal(merged.renderer.exposure, 2.0, 'saved nested value lost');
  assert.equal(merged.renderer.toneMapping, 'ACESFilmic', 'missing field not filled from defaults');
  assert.equal(merged.lights.length, DEFAULT_SETUP.lights.length, 'lights not filled from defaults');
  // the whole point: a setup saved before bokeh existed must still get one
  assert.notEqual(merged.bokeh, undefined, 'a pre-bokeh saved setup must gain the defaults');
  assert.equal(merged.bokeh.strength, DEFAULT_SETUP.bokeh.strength, 'bokeh defaults not filled in');

  saveSetup(merged);
  assert.equal(loadSetup().renderer.exposure, 2.0, 'round-trip lost a value');
}

console.log('lightlab selfcheck OK');
