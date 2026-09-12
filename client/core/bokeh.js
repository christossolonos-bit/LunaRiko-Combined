import { EffectComposer } from 'three/addons/postprocessing/EffectComposer.js';
import { RenderPass } from 'three/addons/postprocessing/RenderPass.js';
import { BokehPass } from 'three/addons/postprocessing/BokehPass.js';
import { OutputPass } from 'three/addons/postprocessing/OutputPass.js';

/**
 * Depth of field — the camera-lens blur that throws the background out of
 * focus and leaves the character sharp.
 *
 * This is the one effect that cannot be done with lights: it needs the frame
 * rendered once, then blurred by how far each pixel is from the camera. So the
 * scene goes through an EffectComposer instead of straight to the canvas.
 *
 * Two things that matter:
 *
 * - When it is OFF, nothing changes — we call `renderer.render` exactly as
 *   before and no render targets are even allocated. The composer is built the
 *   first time it is switched on. An effect you are not using should not cost
 *   you a frame buffer, and it keeps the plain path the tested one.
 *
 * - It makes the canvas OPAQUE. The bokeh shader writes alpha 1.0 on every
 *   pixel, so the page background stops showing through. Harmless here (this
 *   page has a dark ground anyway), but it means depth of field and the
 *   transparent Electron desktop window are mutually exclusive as written — if
 *   that combination is ever wanted, the shader's `gl_FragColor.a` is why.
 *
 * - `OutputPass` is not optional. three.js skips tone mapping when it renders
 *   into a buffer rather than the canvas, so without it every image would jump
 *   brighter and flatter the moment you enabled bokeh. OutputPass reads
 *   `renderer.toneMapping` / `toneMappingExposure` each frame, which is why the
 *   film sliders keep working through the composer with no extra wiring.
 */

// The shader blurs by `(focus - distance) * aperture`, in metres, and the
// result is a UV offset. At room scale a couple of metres out of focus wants
// roughly a 1% screen offset, so a full-strength slider lands near 0.008.
const APERTURE_AT_FULL_STRENGTH = 0.008;

export class Bokeh {
  constructor({ renderer, scene, camera, controls = null }) {
    this.renderer = renderer;
    this.scene = scene;
    this.camera = camera;
    this.controls = controls;   // for auto-focus; optional
    this.composer = null;
    this.pass = null;
    this.settings = null;
  }

  _build() {
    const { renderer, scene, camera } = this;
    const composer = new EffectComposer(renderer);
    composer.setPixelRatio(renderer.getPixelRatio());
    composer.setSize(window.innerWidth, window.innerHeight);
    composer.addPass(new RenderPass(scene, camera));

    const pass = new BokehPass(scene, camera, { focus: 3, aperture: 0, maxblur: 0.01 });
    composer.addPass(pass);
    composer.addPass(new OutputPass());

    this.composer = composer;
    this.pass = pass;
    console.log('[bokeh] composer built (RenderPass -> BokehPass -> OutputPass)');
  }

  apply(settings) {
    this.settings = settings;
    if (!settings.enabled) return;
    if (!this.composer) this._build();
    this.pass.uniforms.aperture.value = settings.strength * APERTURE_AT_FULL_STRENGTH;
    this.pass.uniforms.maxblur.value = settings.maxBlur;
    console.log(`[bokeh] on — focus ${settings.focus.toFixed(2)}m `
      + `strength ${settings.strength} maxblur ${settings.maxBlur}`
      + (settings.autoFocus ? ' (auto-focus follows the orbit target)' : ''));
  }

  /**
   * Draw the frame — through the composer when depth of field is on, straight
   * to the canvas otherwise. With it off this is exactly `renderer.render`, so
   * it is safe to call unconditionally from any render loop.
   */
  render() {
    const s = this.settings;
    if (!s?.enabled || !this.composer) {
      this.renderer.render(this.scene, this.camera);
      return;
    }
    // Auto-focus keeps whatever the camera orbits around sharp as you move.
    this.pass.uniforms.focus.value = (s.autoFocus && this.controls)
      ? this.camera.position.distanceTo(this.controls.target)
      : s.focus;
    this.composer.render();
  }

  setSize(width, height) {
    this.composer?.setSize(width, height);
  }
}
