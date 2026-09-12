import GUI from 'three/addons/libs/lil-gui.module.min.js';

import { createGizmo } from './gizmo.js';
import { LIGHT_TYPES, INTENSITY_MAX } from './rig.js';
import {
  ROOM_CHOICES, TONE_MAPPINGS, DEFAULT_SETUP,
  clone, saveSetup, clearSetup, toPasteable, normaliseLight,
} from './setup.js';

/**
 * The control panel, and the click-and-drag gizmo that goes with it.
 *
 * The panel only ever edits `setup`; after any edit it calls back into the
 * host (`ctx`) to push that value at the renderer / rig / avatar and then
 * saves. Nothing in here owns state.
 *
 * Character and room are the two exceptions: they swap by writing the setup
 * and reloading the page. Disposing a VRM and a 20 MB room GLB cleanly is
 * fiddly, a reload is one line, and the setup survives it because it is saved
 * on every change anyway.
 */
export function buildPanel({ setup, rig, mtoon, ctx }) {
  const gui = new GUI({ title: 'Light Lab', width: 320 });
  const commit = () => saveSetup(setup);

  // ---------------------------------------------------------------- gizmo --
  // The dragged light's x/y/z fields, so a drag updates the numbers you can
  // also type into. Repointed every time the light folders are rebuilt.
  let posControllers = [];

  const gizmo = createGizmo({
    scene: ctx.scene, camera: ctx.camera, controls: ctx.controls,
    domElement: ctx.renderer.domElement, rig,
    onMove: () => {
      for (const c of posControllers) c.updateDisplay();
      commit();
    },
  });

  function select(entry) {
    posControllers = [];
    gizmo.select(entry);
  }

  // ---------------------------------------------------------------- scene --
  const fScene = gui.addFolder('Scene');
  const roomNames = Object.keys(ROOM_CHOICES);
  const picks = {
    character: setup.character,
    room: roomNames.find((n) => ROOM_CHOICES[n] === setup.room.url) ?? 'none',
  };

  fScene.add(picks, 'character', ctx.characters)
    .name('character (reloads)')
    .onChange((v) => { setup.character = v; commit(); location.search = `?character=${v}`; });

  fScene.add(picks, 'room', roomNames)
    .name('room (reloads)')
    .onChange((v) => { setup.room.url = ROOM_CHOICES[v]; commit(); location.reload(); });

  fScene.add(setup.room, 'autoFit').name('auto-fit size').onChange(() => { ctx.applyRoom(); commit(); });
  fScene.add(setup.room, 'targetHeight', 0.5, 40, 0.1).name('room height (m)').onChange(() => { ctx.applyRoom(); commit(); });
  fScene.add(setup.room, 'scaleMul', 0.05, 20, 0.01).name('room scale x').onChange(() => { ctx.applyRoom(); commit(); });
  fScene.add(setup.room, 'rotationY', -Math.PI, Math.PI, 0.01).name('room turn').onChange(() => { ctx.applyRoom(); commit(); });
  for (const [i, axis] of ['x', 'y', 'z'].entries()) {
    fScene.add(setup.room.offset, String(i), -20, 20, 0.05)
      .name(axis === 'y' ? 'room up/down' : `room ${axis}`)
      .onChange(() => { ctx.applyRoom(); commit(); });
  }
  fScene.close();

  // ------------------------------------------------------------- renderer --
  const fRender = gui.addFolder('Camera & film');
  fRender.add(setup.renderer, 'toneMapping', TONE_MAPPINGS)
    .name('tone mapping').onChange(() => { ctx.applyRenderer(); commit(); });
  fRender.add(setup.renderer, 'exposure', 0, 3, 0.01)
    .name('exposure').onChange(() => { ctx.applyRenderer(); commit(); });
  fRender.add(setup.renderer, 'shadows')
    .name('shadows').onChange(() => { ctx.applyRenderer(); commit(); });
  fRender.add(setup.renderer, 'grid')
    .name('floor grid').onChange(() => { ctx.applyGrid(); commit(); });
  fRender.add(setup.camera, 'fov', 12, 80, 1)
    .name('lens (fov)').onChange(() => { ctx.applyCamera(); commit(); });
  fRender.add({ save: () => { ctx.readCamera(); commit(); console.log('[lightlab] camera position saved'); } }, 'save')
    .name('remember this camera');
  fRender.close();

  // --------------------------------------------------------------- avatar --
  const fAvatar = gui.addFolder('Avatar');
  for (const [i, axis] of ['x', 'y', 'z'].entries()) {
    fAvatar.add(setup.avatar.position, String(i), -5, 5, 0.02).name(axis)
      .onChange(() => { ctx.applyAvatar(); commit(); });
  }
  fAvatar.add(setup.avatar, 'rotationY', -Math.PI, Math.PI, 0.01).name('turn')
    .onChange(() => { ctx.applyAvatar(); commit(); });
  fAvatar.close();

  // --------------------------------------------------------------- lights --
  const fLights = gui.addFolder('Lights');
  let lightFolders = [];

  function lightFolder(entry) {
    const def = entry.def;
    const f = fLights.addFolder(`${def.name}  (${def.type})`);

    f.add(def, 'name').name('name').onFinishChange(() => { rebuildLights(); commit(); });
    f.add(def, 'type', LIGHT_TYPES).name('type').onChange(() => {
      const fresh = rig.rebuild(entry);
      if (gizmo.isSelected(entry)) select(fresh);
      rebuildLights();
      commit();
    });
    f.addColor(def, 'color').name('colour').onChange(() => { rig.sync(entry); commit(); });
    f.add(def, 'intensity', 0, INTENSITY_MAX[def.type] ?? 10, 0.01)
      .name('intensity').onChange(() => { rig.sync(entry); commit(); });

    if (def.type !== 'ambient') {
      const pos = [];
      for (const [i, axis] of ['x', 'y', 'z'].entries()) {
        pos.push(f.add(def.position, String(i), -12, 12, 0.05).name(axis)
          .onChange(() => { rig.sync(entry); commit(); }));
      }
      f.add({ grab: () => { select(entry); pushPosControllers(pos); } }, 'grab').name('◈ drag this one');
      if (gizmo.isSelected(entry)) pushPosControllers(pos);
    }

    if (def.type === 'directional' || def.type === 'spot') {
      const t = f.addFolder('aims at');
      for (const [i, axis] of ['x', 'y', 'z'].entries()) {
        t.add(def.target, String(i), -5, 5, 0.05).name(axis)
          .onChange(() => { rig.sync(entry); commit(); });
      }
      t.close();
    }

    if (def.type === 'spot') {
      f.add(def, 'angle', 0.05, 1.5, 0.01).name('cone width').onChange(() => { rig.sync(entry); commit(); });
      f.add(def, 'penumbra', 0, 1, 0.01).name('edge softness').onChange(() => { rig.sync(entry); commit(); });
    }

    if (def.type === 'hemisphere') {
      def.groundColor ??= '#404040';
      f.addColor(def, 'groundColor').name('bounce from below')
        .onChange(() => { rig.sync(entry); commit(); });
    }

    if (def.type === 'directional' || def.type === 'spot' || def.type === 'point') {
      f.add(def, 'castShadow').name('casts shadow').onChange(() => { rig.sync(entry); commit(); });
    }

    f.add({ remove: () => {
      rig.remove(entry);
      setup.lights.splice(setup.lights.indexOf(def), 1);
      if (gizmo.isSelected(entry)) select(null);
      rebuildLights();
      commit();
    } }, 'remove').name('✕ delete');

    return f;
  }

  function pushPosControllers(list) { posControllers = list; }

  function rebuildLights() {
    for (const f of lightFolders) f.destroy();
    lightFolders = rig.entries.map(lightFolder);
  }

  fLights.add({ add: () => {
    const def = normaliseLight({
      name: `light${setup.lights.length + 1}`, type: 'point',
      intensity: 40, position: [1.5, 1.6, 1.5],
    });
    setup.lights.push(def);
    select(rig.add(def));
    rebuildLights();
    commit();
  } }, 'add').name('+ add a light');

  fLights.add(rig, 'helpersVisible').name('show light markers')
    .onChange((v) => rig.setHelpersVisible(v));

  rebuildLights();

  // ------------------------------------------------------- character/toon --
  const fToon = gui.addFolder('Character shading (MToon)');
  const toon = () => { mtoon.apply(setup.mtoon); commit(); };
  fToon.add(setup.mtoon, 'enabled').name('enabled').onChange(toon);
  fToon.addColor(setup.mtoon, 'shadeTint').name('shadow tint').onChange(toon);
  fToon.add(setup.mtoon, 'shadeStrength', 0, 1, 0.01).name('shadow tint amount').onChange(toon);
  fToon.add(setup.mtoon, 'shadingShift', -0.5, 0.5, 0.01).name('shade line position').onChange(toon);
  fToon.add(setup.mtoon, 'shadingToony', -0.5, 0.5, 0.01).name('shade line hardness').onChange(toon);
  fToon.addColor(setup.mtoon, 'rimColor').name('rim colour').onChange(toon);
  fToon.add(setup.mtoon, 'rimMix', 0, 1, 0.01).name('rim amount').onChange(toon);
  fToon.add(setup.mtoon, 'rimFresnel', 0.5, 12, 0.1).name('rim thinness').onChange(toon);
  fToon.add(setup.mtoon, 'rimLift', 0, 1, 0.01).name('rim lift').onChange(toon);

  // -------------------------------------------------------- depth of field --
  const fDof = gui.addFolder('Depth of field (bokeh)');
  const dof = () => { ctx.bokeh.apply(setup.bokeh); commit(); };
  fDof.add(setup.bokeh, 'enabled').name('enabled').onChange(dof);
  fDof.add(setup.bokeh, 'autoFocus').name('focus on what you orbit').onChange(dof);
  fDof.add(setup.bokeh, 'focus', 0.2, 20, 0.05).name('focus distance (m)').onChange(dof);
  fDof.add(setup.bokeh, 'strength', 0, 1, 0.01).name('blur amount').onChange(dof);
  fDof.add(setup.bokeh, 'maxBlur', 0, 0.03, 0.0005).name('max blur').onChange(dof);
  fDof.close();

  // ---------------------------------------------------------------- files --
  gui.add({ copy: () => {
    const text = toPasteable(setup);
    console.log(text);
    navigator.clipboard?.writeText(text)
      .then(() => console.log('[lightlab] setup copied to the clipboard'))
      .catch(() => console.warn('[lightlab] clipboard blocked — copy it out of the log above'));
  } }, 'copy').name('📋 copy setup');

  gui.add({ reset: () => {
    if (!confirm('Throw away this setup and go back to the starting rig?')) return;
    clearSetup();
    location.reload();
  } }, 'reset').name('↺ reset to default');

  return { gizmo, select };
}
