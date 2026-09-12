import * as THREE from 'three';
import { TransformControls } from 'three/addons/controls/TransformControls.js';

/**
 * Click a light, drag it in 3D.
 *
 * Lights are invisible, so each one has a small marker sphere (see rig.js) that
 * exists only to be clicked. This raycasts those markers, hangs the arrow gizmo
 * off whichever was hit, and writes the dragged position back into the light's
 * definition — the def stays the source of truth, exactly as when you type the
 * numbers into the panel.
 *
 * Two things that are easy to get wrong and are handled here: OrbitControls has
 * to be switched off for the duration of a drag or the camera spins along with
 * the arrow, and a click that lands on a gizmo handle must NOT be treated as a
 * "pick something else" click, or grabbing an arrow instantly deselects.
 */
export function createGizmo({ scene, camera, controls, domElement, rig, onMove }) {
  const gizmo = new TransformControls(camera, domElement);
  gizmo.setSize(0.7);
  scene.add(gizmo.getHelper());

  let selected = null;

  gizmo.addEventListener('dragging-changed', (e) => { controls.enabled = !e.value; });
  gizmo.addEventListener('objectChange', () => {
    if (!selected) return;
    selected.def.position = selected.marker.position.toArray();
    rig.sync(selected);
    onMove(selected);
  });

  function select(entry) {
    selected = entry ?? null;
    if (selected?.marker) {
      gizmo.attach(selected.marker);
      console.log(`[lightlab] selected "${selected.def.name}" — drag the arrows to move it`);
    } else {
      gizmo.detach();
    }
    return selected;
  }

  const raycaster = new THREE.Raycaster();
  const pointer = new THREE.Vector2();
  domElement.addEventListener('pointerdown', (ev) => {
    if (gizmo.axis) return;   // a gizmo handle is under the cursor — that's a drag
    pointer.x = (ev.clientX / window.innerWidth) * 2 - 1;
    pointer.y = -(ev.clientY / window.innerHeight) * 2 + 1;
    raycaster.setFromCamera(pointer, camera);
    const hit = raycaster.intersectObjects(rig.markers(), false)[0];
    select(hit ? rig.entryForMarker(hit.object) : null);
  });

  return { select, isSelected: (entry) => selected === entry };
}
