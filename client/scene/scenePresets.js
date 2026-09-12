/**
 * Named scene presets — a whole tuned look saved as one object.
 *
 * A preset holds everything the lighting sandbox (lightlab.html) can author:
 * which room, how it is seated, the light rig, the film settings, the toon
 * shading on the character, and the depth of field. Load one with
 * `?scene=<name>` on the normal page:
 *
 *   http://127.0.0.1:5173/?scene=classroom   <- the tuned look
 *   http://127.0.0.1:5173/                   <- plain, nothing here is read
 *
 * With no `?scene=` NOTHING in this file runs. That is deliberate: a scene is
 * something you opt into, never something that changes the default page.
 *
 * ---------------------------------------------------------------------------
 * THE EXAMPLE PRESET NEEDS A ROOM FILE YOU DOWNLOAD
 * ---------------------------------------------------------------------------
 * `classroom` below is a complete, working preset — every number in it was
 * tuned in the sandbox — but the room model itself is not in this package,
 * because it is a third-party download and not ours to redistribute.
 *
 * To see it: search Sketchfab (https://sketchfab.com) for an anime classroom,
 * download the GLB, put it in client/backgrounds/glb/, and set the `url`
 * below to its filename. Any room works — `autoFit` measures whatever you
 * give it. A missing file is harmless: the page logs one error and runs with
 * no room, exactly as if you had not asked for a scene.
 *
 * To author your own: open lightlab.html, tune, press "copy setup", and paste
 * the result in as a new entry. This file is the single source of truth for
 * these numbers — the sandbox reads it too, so what you tune and what ships
 * are the same values rather than two copies that drift apart.
 */

/**
 * Tone-mapping modes, by name. Lives here rather than in sceneSetup.js because
 * that module pulls in config.js, which only runs in a browser — and the
 * sandbox's headless self-check needs to read this list without one.
 */
export const TONE_MAPPING_NAMES = [
  'None', 'Linear', 'Reinhard', 'Cineon', 'ACESFilmic', 'AgX', 'Neutral',
];

export const SCENE_PRESETS = {
  classroom: {
    label: 'Anime classroom — warm lamp key, cool fill, spot rim',

    room: {
      // Not shipped — see the note at the top of this file. Point this at
      // whatever room GLB you drop into client/backgrounds/glb/.
      url: './backgrounds/glb/anime_class_room.glb',
      // Auto-fit measures the GLB and scales it so its total height is
      // `targetHeight`, then drops its lowest point onto y=0 and centres it on
      // the avatar. `offset` seats it from there — these rooms rarely put their
      // floor at the bottom of their bounding box.
      autoFit: true,
      targetHeight: 11.7,
      scaleMul: 1.1,
      // Measured with rays against the loaded GLB, not guessed.
      //   y -0.467  its floor sits that far above the bounding-box bottom
      //             (the box reaches down past the classroom into roof and
      //             exterior shell), so without this she stands UNDER the
      //             floor with only her boots showing.
      //   z +5      the classroom's origin is inside a wall. Open floor with a
      //             clear line to a camera 3 m back runs from z -3 to -9, so
      //             the ROOM moves +5 to bring that space onto her, rather
      //             than moving her off the origin — walk_to, teleport_to and
      //             NAMED_LOCATIONS are all world coordinates around 0.
      offset: [0, -0.467, 5],
      rotationY: 0.06,
      fixDepth: false,
    },

    // Colours are CSS strings so one format works for both the sandbox's
    // colour pickers and THREE.Color, which takes either.
    lights: [
      { name: 'ambient', type: 'directional', color: '#ffb08f', intensity: 1.28,
        position: [0, 0, 0], target: [0, 1.1, 0], castShadow: true },
      { name: 'key', type: 'directional', color: '#e89e4a', intensity: 1.54,
        position: [1.828, 2.066, -4.749], target: [-3.2, 1.05, -2.95], castShadow: true },
      { name: 'fill', type: 'directional', color: '#9fc0ff', intensity: 0.43,
        position: [0.844, 2.021, -4.3], target: [3.8, 1.55, 2.45], castShadow: false },
      { name: 'rim', type: 'spot', color: '#ffe6f0', intensity: 5.23,
        position: [1.1, 2.019, -1.8], target: [1.55, 1.7, -5],
        angle: 0.524, penumbra: 0.4, castShadow: false },
    ],

    renderer: {
      toneMapping: 'ACESFilmic',
      exposure: 1.12,
      shadows: true,
      grid: false,   // sandbox-only helper; never drawn on the normal page
    },

    // A framing that works in this room: slightly off-centre, most of her in
    // shot, desks receding behind. Replace it with your own (see the bottom
    // of this file).
    camera: {
      position: [0.8, 1.05, 3.9],
      target: [0, 1.0, 0],
      fov: 22,
    },

    avatar: {
      position: [1.5, 0, 1],
      rotationY: 3.141592653589793,
    },

    // Deltas and multipliers on whatever the VRM shipped with, except the rim
    // block. All-zero means untouched — see core/mtoonTuner.js.
    mtoon: {
      enabled: true,
      shadeTint: '#9fb4f9',
      shadeStrength: 0.47,
      shadingShift: 0.16,
      shadingToony: -0.12,
      rimColor: '#fecde0',
      rimMix: 0.06,
      rimFresnel: 10.6,
      rimLift: 0,
    },

    // Depth of field. Costs an extra render pass and makes the canvas opaque —
    // see core/bokeh.js.
    bokeh: {
      enabled: true,
      autoFocus: false,
      focus: 1.95,
      strength: 0.15,
      maxBlur: 0.0115,
    },
  },
};

/**
 * The preset named by `?scene=` on the current URL, or null when there is none.
 *
 * Returns null loudly rather than falling back to a preset: a typo'd name must
 * not silently relight the scene, and the un-presetted page is the one every
 * other character runs.
 */
export function getScenePreset(character = null) {
  const name = new URLSearchParams(location.search).get('scene');
  if (!name) return null;

  const preset = SCENE_PRESETS[name];
  if (!preset) {
    console.warn(`[scene] no preset called "${name}" — known: ${Object.keys(SCENE_PRESETS).join(', ')}. `
      + 'Running with the normal lighting.');
    return null;
  }
  if (character && preset.tunedFor && preset.tunedFor !== character) {
    console.warn(`[scene] preset "${name}" was tuned for ${preset.tunedFor}, `
      + `but this page is ${character}. Loading it anyway — the lights do not care, `
      + 'but the framing and the toon shading were picked for a different model.');
  }
  console.log(`[scene] preset "${name}" — ${preset.label}`);
  return preset;
}

/**
 * ---------------------------------------------------------------------------
 * Replacing the camera angle with your own
 * ---------------------------------------------------------------------------
 *   1. http://127.0.0.1:5173/lightlab.html
 *   2. Orbit to the angle you want.
 *   3. Camera & film -> "remember this camera".   <- this is the step that is
 *      easy to miss; without it the copied setup carries the DEFAULT position
 *      and only the fov slider's value is really yours.
 *   4. "copy setup", then paste the `camera:` line into the preset above.
 *
 * The same goes for the room `offset`. The two numbers in this preset were
 * measured with rays rather than eyeballed, so if you move the room, re-check
 * that her feet still meet the floor.
 */
