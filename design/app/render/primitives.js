import { FRAME_OUTER, MODULES } from '../constants.js';
export function addBox(THREE, group, options) {
  const material = new THREE.MeshStandardMaterial({
    color: options.color,
    roughness: options.roughness ?? 0.7,
    metalness: options.metalness ?? 0.12,
    transparent: options.opacity !== undefined && options.opacity < 1,
    opacity: options.opacity ?? 1,
  });
  const mesh = new THREE.Mesh(new THREE.BoxGeometry(options.width, options.height, options.depth), material);
  mesh.position.set(options.x, options.y, options.z);
  if (options.rotationX) mesh.rotation.x = options.rotationX;
  if (options.rotationY) mesh.rotation.y = options.rotationY;
  if (options.rotationZ) mesh.rotation.z = options.rotationZ;
  group.add(mesh);
  return mesh;
}
export function addCylinder(THREE, group, options) {
  const material = new THREE.MeshStandardMaterial({
    color: options.color,
    roughness: options.roughness ?? 0.55,
    metalness: options.metalness ?? 0.14,
    transparent: options.opacity !== undefined && options.opacity < 1,
    opacity: options.opacity ?? 1,
  });
  const mesh = new THREE.Mesh(new THREE.CylinderGeometry(options.radiusTop, options.radiusBottom, options.height, options.segments ?? 24), material);
  mesh.position.set(options.x, options.y, options.z);
  if (options.rotationX) mesh.rotation.x = options.rotationX;
  if (options.rotationY) mesh.rotation.y = options.rotationY;
  if (options.rotationZ) mesh.rotation.z = options.rotationZ;
  group.add(mesh);
  return mesh;
}

export function addSphere(THREE, group, options) {
  const material = new THREE.MeshStandardMaterial({
    color: options.color,
    roughness: options.roughness ?? 0.16,
    metalness: options.metalness ?? 0.7,
    transparent: options.opacity !== undefined && options.opacity < 1,
    opacity: options.opacity ?? 1,
  });
  const mesh = new THREE.Mesh(new THREE.SphereGeometry(options.radius, options.widthSegments ?? 24, options.heightSegments ?? 24), material);
  mesh.position.set(options.x, options.y, options.z);
  group.add(mesh);
  return mesh;
}

export function addModuleGuides(THREE, group, model, y = 0.8, opacity = 0.24) {
  const start = -FRAME_OUTER / 2 + model.rim;
  const end = start + model.gridExtent;
  const material = new THREE.LineBasicMaterial({ color: 0xffb35a, transparent: true, opacity });
  for (let index = 0; index <= MODULES; index += 1) {
    const position = start + index * model.moduleSize;
    const vertical = new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(position, y, start), new THREE.Vector3(position, y, end)]);
    const horizontal = new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(start, y, position), new THREE.Vector3(end, y, position)]);
    group.add(new THREE.Line(vertical, material.clone()));
    group.add(new THREE.Line(horizontal, material.clone()));
  }
}
export function disposeObject(object) {
  object.traverse((node) => {
    if (node.geometry) node.geometry.dispose();
    if (node.material) {
      if (Array.isArray(node.material)) node.material.forEach((material) => material.dispose());
      else node.material.dispose();
    }
  });
}
