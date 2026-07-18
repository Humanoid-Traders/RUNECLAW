/**
 * RUNECLAW 3D agent — a real-time WebGL viewer for the AI Viking agent model.
 *
 * Loads a glTF-binary model from /mascot/agent.glb (which brings its own
 * rune-disc base + holographics) and lights it with room-environment PBR plus a
 * white key and a rune-blue rim/fill, with gentle auto-rotation and
 * drag-to-orbit. It plays the model's idle animation clip if it has one,
 * otherwise a soft idle bob. Framing is measured from the true SKINNED pose
 * (see skinnedUnionBox) so a rigged model that floats/spreads its arms stays
 * fully in shot. It pauses when off-screen or the tab is hidden, honours
 * prefers-reduced-motion, and — crucially — does NOTHING when no model is
 * present yet, so shipping this never changes the site until the artwork
 * (agent.glb) is dropped in.
 *
 * Mount targets: any element with [data-rc-agent3d]. Self-contained: three.js is
 * vendored under /vendor/three (no CDN); the page must declare the import map.
 */
import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { RoomEnvironment } from 'three/addons/environments/RoomEnvironment.js';

const MODEL_URL = '/mascot/agent.glb';
const RUNE = 0x3fb6ff;

async function modelExists() {
  try {
    const r = await fetch(MODEL_URL, { method: 'HEAD' });
    return r.ok && !/text\/html/.test(r.headers.get('content-type') || '');
  } catch (e) { return false; }
}

export function mountAgent(host) {
  if (!host || host.__rc3d) return null;
  host.__rc3d = true;
  const reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, powerPreference: 'high-performance' });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.15;
  renderer.domElement.style.width = '100%';
  renderer.domElement.style.height = '100%';
  renderer.domElement.style.display = 'block';
  host.appendChild(renderer.domElement);

  const scene = new THREE.Scene();
  const pmrem = new THREE.PMREMGenerator(renderer);
  scene.environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;

  const camera = new THREE.PerspectiveCamera(36, 1, 0.1, 100);
  camera.position.set(0, 1.05, 4.4);

  // Lights — a white key plus a rune-blue rim/fill so the metal reads cold-steel.
  scene.add(new THREE.AmbientLight(0x8ea0c0, 0.5));
  const key = new THREE.DirectionalLight(0xffffff, 2.2); key.position.set(3, 5, 4); scene.add(key);
  const rim = new THREE.DirectionalLight(RUNE, 3.0); rim.position.set(-4, 2, -3); scene.add(rim);
  const fill = new THREE.PointLight(RUNE, 12, 24); fill.position.set(0, 1.2, 4); scene.add(fill);

  // The model brings its own rune-disc base + holographics; we just light,
  // frame and rotate it.
  const controls = new OrbitControls(camera, renderer.domElement);
  controls.enableZoom = false; controls.enablePan = false;
  controls.enableDamping = true; controls.dampingFactor = 0.08;
  controls.autoRotate = !reduce; controls.autoRotateSpeed = 1.0;
  controls.minPolarAngle = Math.PI * 0.30; controls.maxPolarAngle = Math.PI * 0.62;

  let mixer = null, model = null;
  host.setAttribute('data-rc3d-state', 'loading');

  // Box3.setFromObject reads BIND-POSE geometry — it is blind to skeletal
  // skinning, so a rigged model that floats or spreads its arms in its idle
  // pose gets mis-framed when you measure the rest pose. Measure the real
  // SKINNED pose instead: advance each clip, refresh the skeleton, and union
  // computeBoundingBox() across the motion so the agent stays fully in shot
  // whichever expression (idle / analyze / alert / execute) is playing.
  function skinnedUnionBox(mx, clips) {
    const box = new THREE.Box3(), tmp = new THREE.Box3();
    const sample = () => {
      model.updateMatrixWorld(true);
      model.traverse((o) => {
        if (o.isSkinnedMesh && o.skeleton) {
          o.skeleton.update(); o.computeBoundingBox();
          tmp.copy(o.boundingBox).applyMatrix4(o.matrixWorld);
        } else if (o.isMesh) { tmp.setFromObject(o); } else { return; }
        box.union(tmp);
      });
    };
    if (mx && clips.length) {
      for (const clip of clips) {
        const a = mx.clipAction(clip); a.stop(); a.reset(); a.play();
        for (let i = 0; i <= 6; i++) { a.time = (clip.duration * i) / 6; mx.update(0); sample(); }
        a.stop();
      }
    } else { sample(); }
    return box;
  }

  // Fit the camera to the measured animated extent. Recomputed on resize so the
  // wide-armed silhouette stays framed on square, portrait and landscape stages.
  function reframe() {
    if (!model || !model.__fit) return;
    const f = model.__fit, half = (camera.fov * Math.PI / 180) / 2;
    const dist = Math.max(f.sy / 2 / Math.tan(half), f.sx / 2 / Math.tan(half) / camera.aspect) * 1.12;
    const dir = new THREE.Vector3().subVectors(camera.position, controls.target);
    if (dir.lengthSq() === 0) dir.set(0, 0, 1);
    dir.normalize().multiplyScalar(dist);
    controls.target.set(0, f.cy, 0);
    camera.position.copy(controls.target).add(dir);
    controls.update();
  }

  new GLTFLoader().load(MODEL_URL, (gltf) => {
    model = gltf.scene;
    const clips = gltf.animations || [];
    if (clips.length) mixer = new THREE.AnimationMixer(model);

    // 1) Scale to a stable on-screen size from the true animated extent.
    let ab = skinnedUnionBox(mixer, clips);
    const size = ab.getSize(new THREE.Vector3());
    model.scale.setScalar(2.4 / (Math.max(size.x, size.y, size.z) || 1));
    // 2) Re-measure at the new scale and centre horizontally over the origin.
    ab = skinnedUnionBox(mixer, clips);
    const c = ab.getCenter(new THREE.Vector3());
    model.position.x -= c.x; model.position.z -= c.z;
    // 3) Final measure drives the camera fit (see reframe()).
    ab = skinnedUnionBox(mixer, clips);
    const s2 = ab.getSize(new THREE.Vector3()), ctr = ab.getCenter(new THREE.Vector3());
    scene.add(model);
    model.__baseY = model.position.y;
    model.__fit = { sx: s2.x, sy: s2.y, cy: ctr.y };
    camera.position.set(0, ctr.y, 10); // head-on start; reframe() sets the distance
    controls.target.set(0, ctr.y, 0);
    reframe();

    // Default expression: idle loops; the agent breathes on its own.
    if (mixer) {
      const idle = clips.find((cl) => cl.name === 'idle') || clips[0];
      clips.forEach((cl) => mixer.clipAction(cl).stop());
      mixer.clipAction(idle).reset().play();
    }
    host.setAttribute('data-rc3d-state', 'ready');
  }, undefined, () => { host.setAttribute('data-rc3d-state', 'error'); });

  const clock = new THREE.Clock();
  let running = false, raf = 0, t0 = 0;
  function resize() {
    const w = host.clientWidth || 320, h = host.clientHeight || 360;
    renderer.setSize(w, h, false);
    camera.aspect = w / h; camera.updateProjectionMatrix();
    reframe();
  }
  function frame() {
    raf = 0;
    const dt = clock.getDelta(); t0 += dt;
    if (mixer) mixer.update(dt);
    else if (model && !reduce) model.position.y = (model.__baseY || 0) + Math.sin(t0 * 1.6) * 0.025;
    controls.update();
    renderer.render(scene, camera);
    if (running) raf = requestAnimationFrame(frame);
  }
  function play() { if (!running) { running = true; if (!raf) raf = requestAnimationFrame(frame); } }
  function pause() { running = false; if (raf) { cancelAnimationFrame(raf); raf = 0; } }

  resize();
  if (window.ResizeObserver) new ResizeObserver(resize).observe(host);
  else window.addEventListener('resize', resize);
  document.addEventListener('visibilitychange', () => document.hidden ? pause() : play());
  if ('IntersectionObserver' in window) {
    new IntersectionObserver((es) => es[0].isIntersecting ? play() : pause(), { threshold: 0.01 }).observe(host);
  } else { play(); }
  play();
  if (reduce) { controls.autoRotate = false; }

  return { play, pause, get model() { return model; } };
}

export async function autoMount() {
  const hosts = Array.prototype.slice.call(document.querySelectorAll('[data-rc-agent3d]'));
  if (!hosts.length) return;
  // Non-breaking: with no model committed yet, leave the page exactly as-is.
  if (!(await modelExists())) {
    hosts.forEach(h => h.setAttribute('data-rc3d-state', 'absent'));
    return;
  }
  hosts.forEach(mountAgent);
}

window.RCAgent3D = { mountAgent: mountAgent, autoMount: autoMount };
autoMount();
