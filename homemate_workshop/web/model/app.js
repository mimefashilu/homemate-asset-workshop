import * as THREE from 'three';
import { OrbitControls } from '/vendor/OrbitControls.js';

const stage = document.querySelector('#stage');
const loading = document.querySelector('#loading');
const modelSelector = document.querySelector('#model');
const variantSelector = document.querySelector('#variant');
const targetFrameSelector = document.querySelector('#target-frame');
const candidateStatus = document.querySelector('#candidate-status');
const scene = new THREE.Scene();
scene.background = new THREE.Color(0xe7eef0);
const camera = new THREE.PerspectiveCamera(35, 1, 0.05, 200);
const renderer = new THREE.WebGLRenderer({ antialias: true, preserveDrawingBuffer: true });
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
renderer.outputColorSpace = THREE.SRGBColorSpace;
stage.appendChild(renderer.domElement);
const controls = new OrbitControls(camera, renderer.domElement);
controls.enableDamping = true;
controls.minDistance = 0.5;
controls.maxDistance = 35;
scene.add(new THREE.HemisphereLight(0xffffff, 0x677573, 2.0));
const key = new THREE.DirectionalLight(0xffffff, 2.1); key.position.set(-5, 8, -8); scene.add(key);
const fill = new THREE.DirectionalLight(0xc7e1ec, 1.1); fill.position.set(5, 1, 3); scene.add(fill);
const modelRoot = new THREE.Group(); scene.add(modelRoot);
const textureLoader = new THREE.TextureLoader();
const groups = {};
const cache = new Map();
let manifest;
let token = "";
let bounds;
let mode = 'texture';
let requestId = 0;
let comparisonVisible = false;

function resourceUrl(url) {
  return url.startsWith('/') ? url : `/${url}`;
}

const visibleFace = new Set(['b_p_kao01_A', 'b_p_mimi01_A', 'b_p_ha01_A', 'b_p_sita01_M', 'b_p_eyeline01_A', 'b_p_eyeline02_A', 'b_p_mayu01_A', 'b_p_mayu02_A', 'b_p_eye01_L', 'b_p_eye01_R', 'b_p_eye02_L', 'b_p_eye02_R', 'b_p_eye03_L', 'b_p_eye03_R']);

function resize() { const width = stage.clientWidth; const height = stage.clientHeight; renderer.setSize(width, height); camera.aspect = width / height; camera.updateProjectionMatrix(); }
new ResizeObserver(resize).observe(stage); resize();

function setView(view = 'front') {
  if (!bounds) return;
  const center = bounds.getCenter(new THREE.Vector3());
  const size = bounds.getSize(new THREE.Vector3());
  const distance = Math.max(size.y / (2 * Math.tan(THREE.MathUtils.degToRad(camera.fov / 2))), Math.max(size.x, size.z) / (2 * Math.tan(THREE.MathUtils.degToRad(camera.fov / 2)) * camera.aspect)) * 1.25;
  controls.target.copy(center);
  const directions = { front: [0, 0.05, -1], side: [-1, 0.05, 0], back: [0, 0.05, 1] };
  camera.position.copy(center).add(new THREE.Vector3(...directions[view]).normalize().multiplyScalar(distance));
  camera.near = Math.max(0.02, distance / 1000); camera.far = distance + 150; camera.updateProjectionMatrix(); controls.update();
}

async function loadModel(record, variant = 'original') {
  const key = `${record.id}:${variant}`;
  if (cache.has(key)) return cache.get(key);
  const candidate = record.candidates?.find(item => item.id === variant);
  const url = candidate?.url || (variant === 'original' ? record.url : (record.variants?.[variant] || record.url));
  const response = await fetch(resourceUrl(url)); if (!response.ok) throw new Error(`模型读取失败：${record.name}`);
  const data = await response.json();
  let originalData = data;
  if (variant !== 'original') {
    const originalResponse = await fetch(resourceUrl(record.url));
    if (originalResponse.ok) originalData = await originalResponse.json();
  }
  const originalMeshes = new Map((originalData.meshes || []).map((mesh, index) => [`${mesh.name}:${index}`, mesh]));
  const textures = await Promise.all(data.materials.map(async material => {
    if (!material.map) return null;
    const texture = await textureLoader.loadAsync(resourceUrl(material.map));
    texture.colorSpace = THREE.SRGBColorSpace; texture.wrapS = texture.wrapT = THREE.RepeatWrapping;
    texture.anisotropy = Math.min(renderer.capabilities.getMaxAnisotropy(), 8); return texture;
  }));
  const group = new THREE.Group(); group.name = record.id;
  for (const mesh of data.meshes) {
    if (record.category === 'face' && !visibleFace.has(mesh.name)) continue;
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.Float32BufferAttribute(mesh.positions, 3));
    geometry.setAttribute('normal', new THREE.Float32BufferAttribute(mesh.normals, 3));
    geometry.setAttribute('uv', new THREE.Float32BufferAttribute(mesh.uv, 2)); geometry.setIndex(mesh.indices);
    const materialData = data.materials[mesh.material]; const map = textures[mesh.material];
    const textured = new THREE.MeshStandardMaterial({ map, color: 0xffffff, roughness: 0.85, side: THREE.DoubleSide, alphaTest: materialData.transparent ? 0.25 : 0 });
    const clay = new THREE.MeshStandardMaterial({ color: record.category === 'face' ? 0xd7ddd8 : 0x688d98, roughness: 0.9, side: THREE.DoubleSide });
    const wire = new THREE.MeshBasicMaterial({ color: record.category === 'face' ? 0x85918f : 0x1a605e, wireframe: true, side: THREE.DoubleSide });
    let heatmap = new THREE.MeshBasicMaterial({ color: 0x3b82f6, side: THREE.DoubleSide });
    const originalMesh = originalMeshes.get(`${mesh.name}:${data.meshes.indexOf(mesh)}`);
    if (variant !== 'original' && originalMesh && originalMesh.positions.length === mesh.positions.length) {
      const colors = new Float32Array(mesh.positions.length);
      let maximum = 0;
      for (let index = 0; index < mesh.positions.length; index += 3) {
        const dx = mesh.positions[index] - originalMesh.positions[index];
        const dy = mesh.positions[index + 1] - originalMesh.positions[index + 1];
        const dz = mesh.positions[index + 2] - originalMesh.positions[index + 2];
        maximum = Math.max(maximum, Math.hypot(dx, dy, dz));
      }
      const scale = Math.max(maximum, 0.000001);
      for (let index = 0; index < mesh.positions.length; index += 3) {
        const dx = mesh.positions[index] - originalMesh.positions[index];
        const dy = mesh.positions[index + 1] - originalMesh.positions[index + 1];
        const dz = mesh.positions[index + 2] - originalMesh.positions[index + 2];
        const amount = Math.min(1, Math.hypot(dx, dy, dz) / scale);
        const red = Math.min(1, amount * 2);
        const green = Math.min(1, (1 - Math.abs(amount - 0.5) * 2) * 1.15);
        const blue = Math.min(1, (1 - amount) * 2);
        colors[index] = red; colors[index + 1] = green; colors[index + 2] = blue;
      }
      geometry.setAttribute('color', new THREE.BufferAttribute(colors, 3));
      heatmap = new THREE.MeshBasicMaterial({ vertexColors: true, side: THREE.DoubleSide });
    }
    const object = new THREE.Mesh(geometry, textured); object.name = mesh.name; object.userData = { materials: { texture: textured, clay, wire }, triangles: mesh.indices.length / 3 }; group.add(object);
    object.userData.materials.heatmap = heatmap;
  }
  cache.set(key, group); return group;
}

function updateStats() { let vertices = 0; let triangles = 0; for (const group of Object.values(groups)) { if (!group || !group.visible || group.userData.reference) continue; for (const mesh of group.children) { vertices += mesh.geometry.attributes.position.count; triangles += mesh.userData.triangles; } } document.querySelector('#vertices').textContent = vertices.toLocaleString(); document.querySelector('#triangles').textContent = triangles.toLocaleString(); }
function applyMode() { for (const group of Object.values(groups)) { if (!group || group.userData.reference) continue; for (const mesh of group.children) mesh.material = mesh.userData.materials[mode] || mesh.userData.materials.texture; } }
function showTexture(record) { const texture = [...record.textures].sort((a, b) => b.width * b.height - a.width * a.height)[0]; if (!texture) return; const image = document.querySelector('#texture'); const link = document.querySelector('#texture-link'); image.src = resourceUrl(texture.url); link.href = resourceUrl(texture.url); document.querySelector('#texture-size').textContent = `${texture.width} × ${texture.height}`; }

function populateVariants(record) {
  const previous = variantSelector.value;
  variantSelector.replaceChildren(new Option('原版', 'original'));
  if (record.variants?.mild) variantSelector.add(new Option('轻度样本', 'mild'));
  if (record.variants?.medium) variantSelector.add(new Option('中度样本', 'medium'));
  for (const candidate of record.candidates || []) variantSelector.add(new Option(`候选 · ${candidate.name}`, candidate.id));
  const hasPrevious = [...variantSelector.options].some(option => option.value === previous);
  variantSelector.value = hasPrevious ? previous : 'original';
}

function populateTargetFrames(record) {
  targetFrameSelector.replaceChildren(new Option('全部头发网格', '*'));
  for (const frame of record.frames || []) {
    if (frame.name) targetFrameSelector.add(new Option(frame.name, frame.name));
  }
}

function updateCandidateReport(record, variant) {
  document.querySelector('#install-candidate').disabled = true;
  document.querySelector('#download-candidate').href = `/api/models/candidate/${encodeURIComponent(variant)}/download`;
  document.querySelector('#install-candidate').dataset.candidate = variant;
  const candidate = record.candidates?.find(item => item.id === variant);
  const panel = document.querySelector('#report-panel');
  const preflightButton = document.querySelector('#preflight');
  document.querySelector('#preflight-status').textContent = '';
  if (!candidate) { panel.hidden = true; preflightButton.hidden = true; return; }
  const report = candidate.report || {};
  document.querySelector('#report-text').textContent = `${candidate.name} · 修改顶点 ${report.changed_vertices || 0} · 最大位移 ${(report.max_displacement || 0).toFixed(4)} · 仅修改几何字段`;
  panel.hidden = false; preflightButton.hidden = false; preflightButton.dataset.candidate = candidate.id;
}

function makeReference(group) {
  group = group.clone();
  group.userData.reference = true;
  for (const mesh of group.children) {
    mesh.material = new THREE.MeshBasicMaterial({ color: 0xf05b5b, wireframe: true, transparent: true, opacity: 0.72, depthWrite: false, side: THREE.DoubleSide });
  }
  group.visible = comparisonVisible;
  return group;
}

async function refreshManifest() {
  const response = await fetch('/api/models/manifest');
  if (!response.ok) throw new Error('模型清单读取失败');
  manifest = await response.json();
}

async function selectModel(id, preserveView = false) {
  const current = ++requestId; loading.hidden = false; loading.textContent = '正在读取模型…'; modelSelector.disabled = true; variantSelector.disabled = true;
  try {
    const record = manifest.models.find(model => model.id === id); if (!record) throw new Error('模型不存在'); const face = manifest.models.find(model => model.category === 'face'); const front = manifest.models.find(model => model.category === 'front');
    const variant = variantSelector.value; const alternate = variant !== 'original'; const back = record.category === 'back' ? record : manifest.models.find(model => model.category === 'back'); const frontRecord = record.category === 'front' ? record : front;
    const [selected, faceGroup, frontGroup, reference] = await Promise.all([back ? loadModel(back, variant) : Promise.resolve(new THREE.Group()), loadModel(face, 'original'), loadModel(frontRecord, variant), alternate ? loadModel(record, 'original') : Promise.resolve(null)]);
    if (current !== requestId) return;
    for (const key of Object.keys(groups)) if (groups[key]) modelRoot.remove(groups[key]);
    groups.face = faceGroup; groups.front = frontGroup; groups.back = selected; groups.reference = reference ? makeReference(reference) : null; faceGroup.visible = document.querySelector('#face').checked; frontGroup.visible = document.querySelector('#front').checked; selected.visible = document.querySelector('#back').checked; modelRoot.add(faceGroup, frontGroup, selected); if (groups.reference) modelRoot.add(groups.reference); bounds = new THREE.Box3().setFromObject(modelRoot); applyMode(); updateStats(); showTexture(record); const candidate = record.candidates?.find(item => item.id === variant); document.querySelector('#current-variant').textContent = candidate ? `候选 · ${candidate.name}` : variant === 'original' ? '原版' : variant === 'mild' ? '轻度' : '中度'; updateCandidateReport(record, variant); document.querySelector('#compare').disabled = !alternate; document.querySelector('#compare').textContent = comparisonVisible && alternate ? '隐藏原版轮廓' : '叠加原版轮廓'; if (!preserveView) setView(); loading.hidden = true;
  } catch (error) { loading.hidden = false; loading.textContent = error.message; console.error(error); }
  finally { if (current === requestId) { modelSelector.disabled = false; variantSelector.disabled = false; } }
}

document.querySelectorAll('input[name="mode"]').forEach(input => input.addEventListener('change', () => { mode = input.value; applyMode(); }));
document.querySelectorAll('[data-view]').forEach(button => button.addEventListener('click', () => setView(button.dataset.view)));
document.querySelector('#reset').addEventListener('click', () => setView());
document.querySelector('#rotate').addEventListener('click', event => { controls.autoRotate = !controls.autoRotate; event.currentTarget.setAttribute('aria-pressed', String(controls.autoRotate)); });
document.querySelector('#compare').addEventListener('click', () => {
  if (!groups.reference) return;
  comparisonVisible = !comparisonVisible;
  groups.reference.visible = comparisonVisible;
  document.querySelector('#compare').textContent = comparisonVisible ? '隐藏原版轮廓' : '叠加原版轮廓';
});
document.querySelector('#focus-model').addEventListener('click', () => setView());
for (const category of ['face', 'front', 'back']) document.getElementById(category).addEventListener('change', event => { if (groups[category]) groups[category].visible = event.target.checked; updateStats(); });
modelSelector.addEventListener('change', () => {
  const record = manifest.models.find(model => model.id === modelSelector.value);
  if (record) { populateVariants(record); populateTargetFrames(record); }
  selectModel(modelSelector.value);
});
variantSelector.addEventListener('change', () => selectModel(modelSelector.value, true));

document.querySelector('#create-candidate').addEventListener('click', async () => {
  const model = manifest.models.find(record => record.id === modelSelector.value);
  if (!model) return;
  const operation = document.querySelector('#operation').value;
  const strength = Number(document.querySelector('#strength').value);
  const iterations = Number(document.querySelector('#iterations').value);
  const maxDisplacement = Number(document.querySelector('#max-displacement').value);
  const parameters = operation === 'smooth' ? { strength, iterations, max_displacement: maxDisplacement } : { amount: strength, max_displacement: maxDisplacement };
  const target = targetFrameSelector.value === '*' ? ['*'] : [targetFrameSelector.value];
  const button = document.querySelector('#create-candidate');
  button.disabled = true; candidateStatus.textContent = '正在生成候选并校验几何结构…';
  try {
    const response = await fetch('/api/models/candidate', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Studio-Token': token }, body: JSON.stringify({ model: model.id, operation, parameters, target_frames: target }) });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || '候选模型生成失败');
    await refreshManifest();
    const updated = manifest.models.find(record => record.id === model.id);
    populateVariants(updated); populateTargetFrames(updated); variantSelector.value = payload.id;
    candidateStatus.textContent = `已生成：${payload.name}，未写入游戏资源。`;
    await selectModel(model.id, true);
  } catch (error) {
    candidateStatus.textContent = error.message;
  } finally {
    button.disabled = false;
  }
});

document.querySelector('#preflight').addEventListener('click', async event => {
  const button = event.currentTarget;
  const identifier = button.dataset.candidate;
  if (!identifier) return;
  const status = document.querySelector('#preflight-status');
  button.disabled = true; status.textContent = '正在检查候选文件、原始模型和游戏状态…';
  try {
    const response = await fetch(`/api/models/candidate/${identifier}/preflight`);
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || '安装前检查失败');
    const failed = payload.checks.filter(check => !check.ok);
    status.textContent = payload.ready ? '检查通过：可以进入安装审查。' : `检查未通过：${failed.map(check => check.detail).join('；')}`;
    status.dataset.ready = String(payload.ready);
    document.querySelector('#install-candidate').disabled = !payload.ready;
  } catch (error) {
    status.textContent = error.message; status.dataset.ready = 'false';
  } finally {
    button.disabled = false;
  }
});

async function init() {
  const state = await fetch('/api/workshop').then(response => response.json()); token = state.token;
  modelSelector.replaceChildren(); cache.clear();
  const response = await fetch('/api/models/manifest'); if (!response.ok) throw new Error('模型清单读取失败'); manifest = await response.json();
  document.querySelector('#source-status').textContent = `资源包：${manifest.archive} · 候选预览与备份安装`;
  for (const record of manifest.models.filter(model => model.category !== 'face')) modelSelector.add(new Option(`${record.name} · ${record.category === 'front' ? '刘海与侧发' : '后发'}`, record.id));
  if (!manifest.models.length) { loading.hidden = false; loading.textContent = '请先点击“准备 / 更新模型资源”，从自己的游戏生成模型预览。'; modelSelector.disabled = true; variantSelector.disabled = true; return; }
  const initial = manifest.models.find(model => model.category === 'back') || manifest.models.find(model => model.category === 'front'); modelSelector.value = initial.id; populateVariants(initial); populateTargetFrames(initial); await selectModel(initial.id); window.lucide?.createIcons();
}

renderer.setAnimationLoop(() => { controls.update(); renderer.render(scene, camera); }); init().catch(error => { loading.hidden = false; loading.textContent = error.message; });


async function modelAction(path, data = {}) {
  const response = await fetch('/api/models/' + path, {method:'POST', headers:{'Content-Type':'application/json','X-Studio-Token':token}, body:JSON.stringify(data)});
  const payload = await response.json();
  if (!response.ok || payload.error) throw new Error(payload.error || '操作失败');
  return payload;
}
document.querySelector('#prepare-models').onclick = async event => {
  const button = event.currentTarget; button.disabled = true; loading.hidden = false; loading.textContent = '正在准备当前游戏的模型预览…';
  try { await modelAction('prepare'); await init(); }
  catch (error) { loading.textContent = error.message; }
  finally { button.disabled = false; }
};
document.querySelector('#recommended').onclick = async event => {
  const button = event.currentTarget; button.disabled = true; candidateStatus.textContent = '正在生成轻度平滑候选…';
  try { const result = await modelAction('recommended'); await refreshManifest(); const record = manifest.models.find(model => model.id === modelSelector.value); populateVariants(record); candidateStatus.textContent = `已准备 ${result.candidate_ids.length} 个轻度候选，未写入游戏。`; }
  catch (error) { candidateStatus.textContent = error.message; }
  finally { button.disabled = false; }
};
document.querySelector('#install-candidate').onclick = async event => {
  const button = event.currentTarget;
  if (!confirm('安装当前候选模型？请完全退出游戏。工坊会备份原资源包，其他候选不会写入。')) return;
  button.disabled = true; candidateStatus.textContent = '正在验证、备份并安装模型…';
  try { const result = await modelAction('install', {candidates:[button.dataset.candidate]}); candidateStatus.textContent = `已安装 ${result.count} 个模型，备份：${result.backup}。可在首页恢复。`; }
  catch (error) { candidateStatus.textContent = error.message; }
  finally { button.disabled = true; }
};
