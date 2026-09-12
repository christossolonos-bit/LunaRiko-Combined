/**
 * Named locations in the 3D space.
 *
 * Unlike SCENE_OBJECTS these have no geometry — they're just labeled
 * coordinates the AI can reference ("walk to the window", "look at the desk").
 *
 * Names must be unique across locations *and* scene objects. Scene objects
 * are auto-merged into the registry, so don't duplicate their names here.
 *
 * Set DEBUG_MARKERS to true to render a small colored sphere at each named
 * location for visual verification.
 * [positive is left of riko, negative is right of riko,positive is up, negative is down,positive is in front of riko, negative is behind]
 */

export const DEBUG_MARKERS = false;

export const NAMED_LOCATIONS = [
  // Examples — edit/replace freely.
  {
    name: 'center',
    position: [0, 0, 0],
    description: 'Center of the room',
  },
  {
    name: 'bed',
    position: [-2, 0.5, -0.3],
    description: 'On the bed',
  },
];
