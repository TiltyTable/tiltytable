import * as THREE from 'https://esm.sh/three@0.160.1';
import { OrbitControls } from 'https://esm.sh/three@0.160.1/examples/jsm/controls/OrbitControls';
import { disposeObject } from './primitives.js';
export function createSceneHost({ host, builder, initialCamera }) {
  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0x12171d);
  const camera = new THREE.PerspectiveCamera(42, 1, 1, 5000);
  camera.position.set(...initialCamera);
  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  host.appendChild(renderer.domElement);
  const controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;
  controls.dampingFactor = 0.08;
  controls.minDistance = 220;
  controls.maxDistance = 1600;
  controls.maxPolarAngle = Math.PI / 2.02;
  scene.add(new THREE.AmbientLight(0xcbd8e6, 0.72));
  scene.add(new THREE.HemisphereLight(0xf0f5ff, 0x22303c, 1.55));
  const key = new THREE.DirectionalLight(0xfff0cb, 1.35); key.position.set(340, 560, 240); scene.add(key);
  const fill = new THREE.DirectionalLight(0x92beff, 0.85); fill.position.set(-360, 220, -300); scene.add(fill);
  const rim = new THREE.DirectionalLight(0xa8ffcb, 0.45); rim.position.set(0, 160, -420); scene.add(rim);
  let currentGroup = null; let frameId = 0;
  const currentTarget = new THREE.Vector3();
  let currentPreset = 'isometric';
  function tick() { frameId = window.requestAnimationFrame(tick); controls.update(); renderer.render(scene, camera); }
  function resize() { const width = host.clientWidth || 600; const height = host.clientHeight || 420; renderer.setSize(width, height, false); camera.aspect = width / height; camera.updateProjectionMatrix(); }
  function getPresetDirection(preset) {
    if (preset === 'top') return new THREE.Vector3(0.001, 1, 0.001).normalize();
    if (preset === 'side') return new THREE.Vector3(1, 0.22, 0).normalize();
    return new THREE.Vector3(1, 0.72, 1).normalize();
  }
  function setCameraPreset(preset) {
    currentPreset = preset;
    const direction = getPresetDirection(preset);
    const fallbackDistance = new THREE.Vector3(...initialCamera).distanceTo(currentTarget);
    const distance = Math.min(controls.maxDistance, Math.max(controls.minDistance, camera.position.distanceTo(currentTarget) || fallbackDistance || 600));
    camera.position.copy(currentTarget).addScaledVector(direction, distance);
    camera.lookAt(currentTarget);
    controls.update();
  }
  function render(payload) {
    if (currentGroup) { disposeObject(currentGroup); scene.remove(currentGroup); }
    const built = builder({ THREE, ...payload });
    currentGroup = built.group;
    scene.add(currentGroup);
    const target = built.target ?? { x: 0, y: 0, z: 0 };
    currentTarget.set(target.x, target.y, target.z);
    controls.target.copy(currentTarget);
    setCameraPreset(currentPreset);
    resize();
  }
  function dispose() {
    window.cancelAnimationFrame(frameId);
    if (currentGroup) { disposeObject(currentGroup); scene.remove(currentGroup); }
    controls.dispose(); renderer.dispose(); renderer.domElement.remove();
  }
  resize(); tick();
  return { render, resize, dispose, setCameraPreset };
}
