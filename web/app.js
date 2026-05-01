const servoList = document.querySelector('#servoList');
const template = document.querySelector('#servoTemplate');
const depthStage = document.querySelector('#depthStage');
const depthImg = document.querySelector('#depthFeed');
const overlay = document.querySelector('#depthOverlay');
const clickHint = document.querySelector('#clickHint');
const cameraDot = document.querySelector('#cameraDot');
const cameraStatus = document.querySelector('#cameraStatus');
const cameraMeta = document.querySelector('#cameraMeta');
const controlStatus = document.querySelector('#controlStatus');
const startControl = document.querySelector('#startControl');
const stopControl = document.querySelector('#stopControl');

let selectedChannel = 0;
let latestState = null;
let stateTimer = null;
let isDrawingBox = false;
let dragStart = null;
let draftBox = null;

function fmtMm(value) {
  if (value === null || value === undefined) return '--';
  return `${Number(value).toFixed(1)} mm`;
}

function fmtDeg(value) {
  if (value === null || value === undefined) return '--';
  return `${Number(value).toFixed(1)} deg`;
}

async function postJson(path, body = {}) {
  const response = await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  const data = await response.json();
  if (!response.ok || data.ok === false) {
    throw new Error(data.error || `Request failed: ${response.status}`);
  }
  return data;
}

async function fetchState() {
  try {
    const response = await fetch('/api/state', { cache: 'no-store' });
    latestState = await response.json();
    renderState(latestState);
  } catch (error) {
    controlStatus.textContent = `state error: ${error.message}`;
  }
}

function renderState(state) {
  cameraDot.className = `status-dot ${state.camera.status}`;
  cameraStatus.textContent = state.camera.status;
  const dims = state.depth_image.width && state.depth_image.height
    ? `${state.depth_image.width} x ${state.depth_image.height}`
    : 'waiting for depth size';
  const fps = state.camera.fps ? `${state.camera.fps.toFixed(1)} fps` : 'fps pending';
  cameraMeta.textContent = state.camera.error || `${dims}, ${fps}`;

  controlStatus.textContent = state.control.error
    ? `${state.control.message}: ${state.control.error}`
    : state.control.message;
  controlStatus.dataset.running = state.control.running ? 'true' : 'false';

  for (const servo of state.servos) {
    renderServo(servo);
  }
  clickHint.innerHTML = `Selected channel: <strong>${selectedChannel}</strong>. Drag on the depth map to set its depth box.`;
  drawOverlay();
}

function renderServo(servo) {
  let card = servoList.querySelector(`[data-channel="${servo.channel}"]`);
  if (!card) {
    card = template.content.firstElementChild.cloneNode(true);
    card.dataset.channel = servo.channel;
    card.style.setProperty('--servo-color', servo.color);
    servoList.appendChild(card);
    wireServoCard(card, servo.channel);
  }

  card.classList.toggle('selected', servo.channel === selectedChannel);
  card.querySelector('.select-servo').textContent = `Channel ${servo.channel}`;

  const targetInput = card.querySelector('.target-input');
  const boxX = card.querySelector('.box-x');
  const boxY = card.querySelector('.box-y');
  const boxWidth = card.querySelector('.box-width');
  const boxHeight = card.querySelector('.box-height');

  if (document.activeElement !== targetInput) targetInput.value = Math.round(servo.target_depth_mm);
  if (document.activeElement !== boxX) boxX.value = servo.box.x;
  if (document.activeElement !== boxY) boxY.value = servo.box.y;
  if (document.activeElement !== boxWidth) boxWidth.value = servo.box.width;
  if (document.activeElement !== boxHeight) boxHeight.value = servo.box.height;

  card.querySelector('.current-depth').textContent = fmtMm(servo.current_depth_mm);
  card.querySelector('.current-error').textContent = fmtMm(servo.current_error_mm);
  card.querySelector('.angle').textContent = fmtDeg(servo.angle_deg);
  card.querySelector('.valid-pixels').textContent = `${servo.valid_pixels}/${servo.total_pixels}`;
}

function wireServoCard(card, channel) {
  card.querySelector('.select-servo').addEventListener('click', () => {
    selectedChannel = channel;
    renderState(latestState);
  });

  card.querySelector('.target-input').addEventListener('change', async (event) => {
    const target = Number(event.target.value);
    if (!Number.isFinite(target) || target <= 0) return;
    await postJson(`/api/servos/${channel}/target`, { target_depth_mm: target });
    await fetchState();
  });

  card.querySelector('.apply-box').addEventListener('click', async () => {
    const box = {
      x: Number(card.querySelector('.box-x').value),
      y: Number(card.querySelector('.box-y').value),
      width: Number(card.querySelector('.box-width').value),
      height: Number(card.querySelector('.box-height').value),
    };
    if (!isIntegerBox(box)) return;
    await postJson(`/api/servos/${channel}/box`, box);
    await fetchState();
  });
}

function isIntegerBox(box) {
  return Number.isInteger(box.x)
    && Number.isInteger(box.y)
    && Number.isInteger(box.width)
    && Number.isInteger(box.height)
    && box.width > 0
    && box.height > 0;
}

function depthCoordinateFromEvent(event) {
  const rect = depthImg.getBoundingClientRect();
  const width = latestState?.depth_image?.width || depthImg.naturalWidth;
  const height = latestState?.depth_image?.height || depthImg.naturalHeight;
  if (!width || !height || !rect.width || !rect.height) return null;
  const x = Math.floor((event.clientX - rect.left) * width / rect.width);
  const y = Math.floor((event.clientY - rect.top) * height / rect.height);
  return {
    x: Math.max(0, Math.min(width - 1, x)),
    y: Math.max(0, Math.min(height - 1, y)),
  };
}

function boxFromPoints(a, b) {
  const x0 = Math.min(a.x, b.x);
  const y0 = Math.min(a.y, b.y);
  const x1 = Math.max(a.x, b.x);
  const y1 = Math.max(a.y, b.y);
  return {
    x: x0,
    y: y0,
    width: x1 - x0 + 1,
    height: y1 - y0 + 1,
  };
}

depthStage.addEventListener('pointerdown', (event) => {
  const point = depthCoordinateFromEvent(event);
  if (!point) return;
  event.preventDefault();
  isDrawingBox = true;
  dragStart = point;
  draftBox = boxFromPoints(point, point);
  depthStage.setPointerCapture(event.pointerId);
  drawOverlay();
});

depthStage.addEventListener('pointermove', (event) => {
  if (!isDrawingBox || !dragStart) return;
  const point = depthCoordinateFromEvent(event);
  if (!point) return;
  event.preventDefault();
  draftBox = boxFromPoints(dragStart, point);
  drawOverlay();
});

depthStage.addEventListener('pointerup', async (event) => {
  if (!isDrawingBox || !draftBox) return;
  event.preventDefault();
  const box = draftBox;
  isDrawingBox = false;
  dragStart = null;
  draftBox = null;
  drawOverlay();

  try {
    await postJson(`/api/servos/${selectedChannel}/box`, box);
    await fetchState();
  } catch (error) {
    controlStatus.textContent = `box error: ${error.message}`;
  }
});

depthStage.addEventListener('pointercancel', () => {
  isDrawingBox = false;
  dragStart = null;
  draftBox = null;
  drawOverlay();
});

function drawOverlay() {
  const state = latestState;
  const rect = depthImg.getBoundingClientRect();
  const width = state?.depth_image?.width || depthImg.naturalWidth;
  const height = state?.depth_image?.height || depthImg.naturalHeight;
  if (!state || !width || !height || rect.width < 1 || rect.height < 1) return;

  const dpr = window.devicePixelRatio || 1;
  overlay.width = Math.round(rect.width * dpr);
  overlay.height = Math.round(rect.height * dpr);
  overlay.style.width = `${rect.width}px`;
  overlay.style.height = `${rect.height}px`;

  const ctx = overlay.getContext('2d');
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, rect.width, rect.height);
  ctx.lineWidth = 2;
  ctx.font = '800 13px Trebuchet MS, sans-serif';
  ctx.textBaseline = 'middle';

  for (const servo of state.servos) {
    drawBox(ctx, servo.box, servo.color, servo.channel, servo.channel === selectedChannel, rect, width, height);
  }

  if (draftBox) {
    const selectedServo = state.servos.find((servo) => servo.channel === selectedChannel);
    drawBox(ctx, draftBox, selectedServo?.color || '#ffcc4d', selectedChannel, true, rect, width, height, true);
  }
}

function drawBox(ctx, box, color, channel, selected, rect, imageWidth, imageHeight, draft = false) {
  const x = box.x * rect.width / imageWidth;
  const y = box.y * rect.height / imageHeight;
  const width = box.width * rect.width / imageWidth;
  const height = box.height * rect.height / imageHeight;

  ctx.save();
  ctx.strokeStyle = color;
  ctx.lineWidth = selected ? 3 : 2;
  ctx.globalAlpha = draft ? 0.36 : 0.18;
  ctx.fillStyle = color;
  ctx.fillRect(x, y, width, height);
  ctx.globalAlpha = 1;
  ctx.strokeRect(x, y, width, height);

  const labelX = Math.min(rect.width - 34, Math.max(4, x + 6));
  const labelY = Math.min(rect.height - 16, Math.max(12, y + 13));
  ctx.fillStyle = color;
  ctx.fillRect(labelX, labelY - 12, 30, 22);
  ctx.fillStyle = '#12201c';
  ctx.fillText(String(channel), labelX + 10, labelY);
  ctx.restore();
}

startControl.addEventListener('click', async () => {
  try {
    await postJson('/api/control/start');
    await fetchState();
  } catch (error) {
    controlStatus.textContent = `start error: ${error.message}`;
  }
});

stopControl.addEventListener('click', async () => {
  try {
    await postJson('/api/control/stop');
    await fetchState();
  } catch (error) {
    controlStatus.textContent = `stop error: ${error.message}`;
  }
});

window.addEventListener('resize', drawOverlay);
depthImg.addEventListener('load', drawOverlay);

fetchState();
stateTimer = window.setInterval(fetchState, 350);
