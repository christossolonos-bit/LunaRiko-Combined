import * as THREE from 'three';

function isVideoUrl(url) {
  return /\.(mp4|webm|mov|m4v)(\?|$)/i.test(url);
}

/**
 * Load a looping muted video as a Three.js texture. Browsers require muted
 * for autoplay — click the page once if it still does not start.
 */
function loadVideoTexture(url) {
  return new Promise((resolve, reject) => {
    const video = document.createElement('video');
    video.src = url;
    video.crossOrigin = 'anonymous';
    video.loop = true;
    video.muted = true;
    video.playsInline = true;
    video.autoplay = true;
    video.preload = 'auto';

    const fail = (err) => reject(err || new Error(`video load failed: ${url}`));

    video.addEventListener('loadeddata', () => {
      const texture = new THREE.VideoTexture(video);
      texture.colorSpace = THREE.SRGBColorSpace;
      texture.minFilter = THREE.LinearFilter;
      texture.magFilter = THREE.LinearFilter;
      texture.generateMipmaps = false;
      video.play().catch(() => {
        // Autoplay blocked until user clicks — resume on first pointerdown.
        const resume = () => {
          video.play().catch(() => {});
          window.removeEventListener('pointerdown', resume);
        };
        window.addEventListener('pointerdown', resume, { once: true });
      });
      resolve({ texture, video });
    }, { once: true });

    video.addEventListener('error', () => fail(video.error), { once: true });
  });
}

/**
 * Flat photo/video backdrop behind the avatar — a quick "she's in a room" look
 * without needing a GLB environment. Skipped in transparent/desktop mode.
 *
 * @param {THREE.Scene} scene
 * @param {{
 *   url: string,
 *   width?: number,
 *   height?: number,
 *   position?: [number, number, number],
 *   tint?: string,
 * }} opts
 * @returns {Promise<THREE.Mesh|null>}
 */
export async function loadBackdrop(scene, opts) {
  if (!opts?.url) return null;

  const height = opts.height ?? 4.2;
  const position = opts.position ?? [0, 1.35, -2.4];

  let texture;
  let video = null;
  try {
    if (isVideoUrl(opts.url)) {
      ({ texture, video } = await loadVideoTexture(opts.url));
    } else {
      texture = await new THREE.TextureLoader().loadAsync(opts.url);
      texture.colorSpace = THREE.SRGBColorSpace;
      texture.minFilter = THREE.LinearFilter;
      texture.magFilter = THREE.LinearFilter;
    }
  } catch (err) {
    console.warn(`[backdrop] could not load ${opts.url}:`, err);
    return null;
  }

  const img = video || texture.image;
  const aspect = img?.videoWidth && img?.videoHeight
    ? img.videoWidth / img.videoHeight
    : (img?.width && img?.height ? img.width / img.height : 16 / 9);
  const planeH = height;
  const planeW = planeH * aspect;

  const mat = new THREE.MeshBasicMaterial({
    map: texture,
    depthWrite: false,
    toneMapped: false,
  });
  if (opts.tint) mat.color = new THREE.Color(opts.tint);

  const mesh = new THREE.Mesh(new THREE.PlaneGeometry(planeW, planeH), mat);
  mesh.position.set(...position);
  mesh.name = 'backdrop';
  mesh.userData.video = video;
  mesh.renderOrder = -10;
  scene.add(mesh);
  console.log(`[backdrop] ${opts.url} ${planeW.toFixed(2)}x${planeH.toFixed(2)} at [${position}]${video ? ' (video)' : ''}`);
  return mesh;
}
