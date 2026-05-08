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
const colorFeed = document.querySelector('#colorFeed');
const colorOverlay = document.querySelector('#colorOverlay');
const ballSection = document.querySelector('#ballSection');
const ballPill = document.querySelector('#ballPill');
const ballX = document.querySelector('#ballX');
const ballY = document.querySelector('#ballY');
const ballZ = document.querySelector('#ballZ');
const ballR = document.querySelector('#ballR');

// ── Calibration elements ───────────────────────────────────────────────────
const calibSection = document.querySelector('#calibSection');
const calibFeed    = document.querySelector('#calibFeed');
const calibCanvas  = document.querySelector('#calibCanvas');
const calibStatus  = document.querySelector('#calibStatus');
const saveCalib    = document.querySelector('#saveCalib');

const calibSliders = {
  hlo: { input: document.querySelector('#sHlo'), label: document.querySelector('#lblHlo') },
  hhi: { input: document.querySelector('#sHhi'), label: document.querySelector('#lblHhi') },
  slo: { input: document.querySelector('#sSlo'), label: document.querySelector('#lblSlo') },
  shi: { input: document.querySelector('#sShi'), label: document.querySelector('#lblShi') },
  vlo: { input: document.querySelector('#sVlo'), label: document.querySelector('#lblVlo') },
  vhi: { input: document.querySelector('#sVhi'), label: document.querySelector('#lblVhi') },
};

let calibStreamLoaded = false;

function applyCalibState(calib) {
  const lo = calib.hsv_low, hi = calib.hsv_high;
  calibSliders.hlo.input.value = lo[0]; calibSliders.hlo.label.textContent = lo[0];
  calibSliders.hhi.input.value = hi[0]; calibSliders.hhi.label.textContent = hi[0];
  calibSliders.slo.input.value = lo[1]; calibSliders.slo.label.textContent = lo[1];
  calibSliders.shi.input.value = hi[1]; calibSliders.shi.label.textContent = hi[1];
  calibSliders.vlo.input.value = lo[2]; calibSliders.vlo.label.textContent = lo[2];
  calibSliders.vhi.input.value = hi[2]; calibSliders.vhi.label.textContent = hi[2];
}

function renderCalibration(calib) {
  if (!calib) { calibSection.hidden = true; return; }
  calibSection.hidden = false;
  if (!calibStreamLoaded) {
    calibFeed.src = '/stream/ball_calibration.mjpg';
    calibStreamLoaded = true;
  }
  // Only sync sliders when no slider is focused (avoid fighting user input).
  if (!Object.values(calibSliders).some(s => document.activeElement === s.input)) {
    applyCalibState(calib);
  }
}

let calibDebounce = null;
Object.values(calibSliders).forEach(({ input, label }) => {
  input.addEventListener('input', () => {
    label.textContent = input.value;
    clearTimeout(calibDebounce);
    calibDebounce = setTimeout(pushCalibBounds, 60);
  });
});

async function pushCalibBounds() {
  try {
    const s = calibSliders;
    const result = await postJson('/api/ball/calibration/bounds', {
      h_lo: +s.hlo.input.value, h_hi: +s.hhi.input.value,
      s_lo: +s.slo.input.value, s_hi: +s.shi.input.value,
      v_lo: +s.vlo.input.value, v_hi: +s.vhi.input.value,
    });
    applyCalibState(result);
    calibStatus.textContent = '';
  } catch (e) {
    calibStatus.textContent = `bounds error: ${e.message}`;
  }
}

saveCalib.addEventListener('click', async () => {
  try {
    const result = await postJson('/api/ball/calibration/save');
    calibStatus.textContent = result.ok ? `Saved ✓` : `Save failed: ${result.error}`;
  } catch (e) {
    calibStatus.textContent = `save error: ${e.message}`;
  }
});

// ── Calibration drag-to-select ─────────────────────────────────────────────
function syncCalibCanvas() {
  calibCanvas.width  = calibFeed.clientWidth  || 1;
  calibCanvas.height = calibFeed.clientHeight || 1;
}
new ResizeObserver(syncCalibCanvas).observe(calibFeed);
calibFeed.addEventListener('load', syncCalibCanvas);

let calibDrag = null;
const calibCtx = calibCanvas.getContext('2d');

calibCanvas.addEventListener('mousedown', e => {
  const r = calibCanvas.getBoundingClientRect();
  calibDrag = { x0: e.clientX - r.left, y0: e.clientY - r.top };
});

calibCanvas.addEventListener('mousemove', e => {
  if (!calibDrag) return;
  const r = calibCanvas.getBoundingClientRect();
  calibDrag.x1 = e.clientX - r.left;
  calibDrag.y1 = e.clientY - r.top;
  calibCtx.clearRect(0, 0, calibCanvas.width, calibCanvas.height);
  calibCtx.strokeStyle = 'rgba(255,255,255,0.85)';
  calibCtx.lineWidth = 1.5;
  calibCtx.strokeRect(calibDrag.x0, calibDrag.y0, calibDrag.x1 - calibDrag.x0, calibDrag.y1 - calibDrag.y0);
});

calibCanvas.addEventListener('mouseup', async e => {
  if (!calibDrag) return;
  const r = calibCanvas.getBoundingClientRect();
  calibDrag.x1 = e.clientX - r.left;
  calibDrag.y1 = e.clientY - r.top;
  calibCtx.clearRect(0, 0, calibCanvas.width, calibCanvas.height);

  // Map CSS coords → stream left-pane natural coords.
  // The stream image is display_width*2 wide; left pane occupies the first half.
  const scaleX = calibFeed.naturalWidth  / calibFeed.clientWidth;
  const scaleY = calibFeed.naturalHeight / calibFeed.clientHeight;
  const x = Math.round(Math.min(calibDrag.x0, calibDrag.x1) * scaleX);
  const y = Math.round(Math.min(calibDrag.y0, calibDrag.y1) * scaleY);
  const w = Math.round(Math.abs(calibDrag.x1 - calibDrag.x0) * scaleX);
  const h = Math.round(Math.abs(calibDrag.y1 - calibDrag.y0) * scaleY);
  calibDrag = null;

  if (w < 4 || h < 4) return;
  try {
    const result = await postJson('/api/ball/calibration/select', { x, y, w, h });
    if (result.ok) {
      applyCalibState(result);
      calibStatus.textContent = 'Bounds updated from selection.';
    } else {
      calibStatus.textContent = 'Selection too small or out of range.';
    }
  } catch (e) {
    calibStatus.textContent = `select error: ${e.message}`;
  }
});

calibCanvas.addEventListener('mouseleave', () => {
  if (!calibDrag) return;
  calibDrag = null;
  calibCtx.clearRect(0, 0, calibCanvas.width, calibCanvas.height);
});

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
  renderBall(state.ball);
  renderCalibration(state.ball_calibration);
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
  ctx.font = '700 12px "JetBrains Mono", monospace';
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
colorFeed.addEventListener('load', () => { if (latestState) renderBall(latestState.ball); });

fetchState();
stateTimer = window.setInterval(fetchState, 350);

function renderBall(ball) {
  if (!ball || !ball.enabled) {
    ballSection.hidden = true;
    clearBallOverlay();
    return;
  }
  ballSection.hidden = false;
  if (ball.detected) {
    ballPill.textContent = 'detected';
    ballPill.className = 'pill detected';
    ballX.textContent = fmtMm(ball.position?.x);
    ballY.textContent = fmtMm(ball.position?.y);
    ballZ.textContent = fmtMm(ball.position?.z);
    ballR.textContent = fmtMm(ball.radius_mm);
    if (ball.pixel) drawBallOnColor(ball.pixel);
  } else {
    ballPill.textContent = 'searching';
    ballPill.className = 'pill';
    ballX.textContent = '--';
    ballY.textContent = '--';
    ballZ.textContent = '--';
    ballR.textContent = '--';
    clearBallOverlay();
  }
}

function drawBallOnColor(pixel) {
  const rect = colorFeed.getBoundingClientRect();
  const nw = colorFeed.naturalWidth;
  const nh = colorFeed.naturalHeight;
  if (!rect.width || !nw || !nh) return;

  const dpr = window.devicePixelRatio || 1;
  colorOverlay.width = Math.round(rect.width * dpr);
  colorOverlay.height = Math.round(rect.height * dpr);
  colorOverlay.style.width = `${rect.width}px`;
  colorOverlay.style.height = `${rect.height}px`;

  const ctx = colorOverlay.getContext('2d');
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, rect.width, rect.height);

  const cx = pixel.cx * rect.width / nw;
  const cy = pixel.cy * rect.height / nh;
  const r  = pixel.radius * rect.width / nw;

  ctx.strokeStyle = '#ff3355';
  ctx.lineWidth = 2.5;
  ctx.beginPath();
  ctx.arc(cx, cy, r, 0, 2 * Math.PI);
  ctx.stroke();

  ctx.fillStyle = '#ff3355';
  ctx.beginPath();
  ctx.arc(cx, cy, 3.5, 0, 2 * Math.PI);
  ctx.fill();
}

function clearBallOverlay() {
  const ctx = colorOverlay.getContext('2d');
  ctx.clearRect(0, 0, colorOverlay.width, colorOverlay.height);
}
