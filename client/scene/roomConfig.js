/**
 * Room (environment) configuration — the simple way to put her somewhere.
 *
 * One room at a time. `enabled: false` runs with no room at all, which is the
 * default and what most people want to start with.
 *
 * position / rotation / scale are [x, y, z]. Rotation is in RADIANS, not
 * degrees (3.14 is a half turn).
 *
 * `fixDepth: true` is a workaround for rooms that flicker — it forces proper
 * depth sorting on every mesh. Try it if walls and floors fight each other.
 *
 * ---------------------------------------------------------------------------
 * WHERE TO GET ROOMS
 * ---------------------------------------------------------------------------
 * https://sketchfab.com has a large free GLB library. Download one, drop the
 * file into client/backgrounds/glb/, point `url` at it, set `enabled: true`,
 * and refresh the page.
 *
 * If the file is missing the page logs one error and carries on with no room,
 * so a wrong filename here can never break anything else.
 *
 * Expect to nudge the numbers. Downloaded rooms come in wildly different
 * scales, and their origin is often inside a wall rather than on open floor.
 * `?scene=` presets (client/scene/scenePresets.js) measure the room instead of
 * guessing — worth graduating to once you have a room you like.
 */

export const ROOM_CONFIG = {
  enabled: false,

  url: './backgrounds/glb/your_room.glb',
  position: [0, 0, 0],
  rotation: [0, 0, 0],
  scale: [1, 1, 1],
  fixDepth: false,
};
