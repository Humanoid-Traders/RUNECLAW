/**
 * RUNECLAW 3D agent — a real-time WebGL viewer for the AI Viking agent model.
 *
 * Loads a glTF-binary model from /mascot/agent.glb and presents it on a glowing
 * rune-blue disc with a rotating halo, room-environment PBR lighting, gentle
 * auto-rotation and drag-to-orbit. It plays the model's own animation clip if it
 * has one, otherwise a soft idle bob. It pauses when off-screen or the tab is
 * hidden, honours prefers-reduced-motion, and — crucially — does NOTHING when no
 * model is present yet, so shipping this never changes the site until the
 * artwork (agent.glb) is dropped in.
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

  // Rune-disc pedestal + two halo rings.
  const disc = new THREE.Group();
  const ring = (r, tube, op) => new THREE.Mesh(
    new THREE.TorusGeometry(r, tube, 12, 120),
    new THREE.MeshBasicMaterial({ color: RUNE, transparent: true, opacity: op }));
  const d1 = ring(1.15, 0.012, 0.9); d1.rotation.x = Math.PI / 2; disc.add(d1);
  const d2 = ring(0.98, 0.006, 0.5); d2.rotation.x = Math.PI / 2; disc.add(d2);
  const glowDisc = new THREE.Mesh(new THREE.CircleGeometry(1.15, 64),
    new THREE.MeshBasicMaterial({ color: RUNE, transparent: true, opacity: 0.09 }));
  glowDisc.rotation.x = -Math.PI / 2; disc.add(glowDisc);
  disc.position.y = 0; scene.add(disc);
  const halo = ring(1.7, 0.006, 0.35); halo.position.y = 1.0; scene.add(halo);

  const controls = new OrbitControls(camera, renderer.domElement);
  controls.enableZoom = false; controls.enablePan = false;
  controls.enableDamping = true; controls.dampingFactor = 0.08;
  controls.autoRotate = !reduce; controls.autoRotateSpeed = 1.0;
  controls.minPolarAngle = Math.PI * 0.32; controls.maxPolarAngle = Math.PI * 0.6;
  controls.target.set(0, 0.95, 0);

  let mixer = null, model = null;
  host.setAttribute('data-rc3d-state', 'loading');
  new GLTFLoader().load(MODEL_URL, (gltf) => {
    model = gltf.scene;
    // Normalise: centre on origin, scale to a fixed height, sit feet on the disc.
    let box = new THREE.Box3().setFromObject(model);
    const size = box.getSize(new THREE.Vector3());
    const maxDim = Math.max(size.x, size.y, size.z) || 1;
    model.scale.setScalar(1.9 / maxDim);
    box = new THREE.Box3().setFromObject(model);
    const c = box.getCenter(new THREE.Vector3());
    model.position.x -= c.x; model.position.z -= c.z;
    model.position.y -= box.min.y; // feet at y=0
    scene.add(model);
    if (gltf.animations && gltf.animations.length) {
      mixer = new THREE.AnimationMixer(model);
      mixer.clipAction(gltf.animations[0]).play();
    }
    host.setAttribute('data-rc3d-state', 'ready');
  }, undefined, () => { host.setAttribute('data-rc3d-state', 'error'); });

  const clock = new THREE.Clock();
  let running = false, raf = 0, t0 = 0;
  function resize() {
    const w = host.clientWidth || 320, h = host.clientHeight || 360;
    renderer.setSize(w, h, false);
    camera.aspect = w / h; camera.updateProjectionMatrix();
  }
  function frame() {
    raf = 0;
    const dt = clock.getDelta(); t0 += dt;
    if (mixer) mixer.update(dt);
    else if (model && !reduce) model.position.y = (model.__baseY || 0) + Math.sin(t0 * 1.6) * 0.03;
    disc.rotation.y += dt * 0.25; halo.rotation.y -= dt * 0.4;
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
