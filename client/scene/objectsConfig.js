/**
 * Scene objects configuration.
 *
 * Each entry loads a GLB file into the scene at the given transform.
 * The `name` is used as the key in the registry (must be unique) and is
 * what the AI will reference when asking about object locations.
 *
 * position / rotation / scale are [x, y, z]. Rotation is in radians.
 */

export const SCENE_OBJECTS = [
  // Example — a small fish placed in front of the avatar for testing.
  // Comment out or replace with real props.
  // {
  //   name: 'fish',
  //   url: './backgrounds/glb/BarramundiFish.glb',
  //   position: [0, 0, 0.3],
  //   rotation: [0, 3, 0],
  //   scale: [1, 1, 1],
  // },
  // {
  //   name: 'mattress',
  //   url: './backgrounds/glb/mattress.glb',
  //   position: [0, 0, 0],
  //   rotation: [0, 0, 0],
  //   scale: [1, 1, 1],
  // },
];
