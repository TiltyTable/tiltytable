const cameraDot    = document.querySelector('#cameraDot');
const cameraStatus = document.querySelector('#cameraStatus');
const cameraMeta   = document.querySelector('#cameraMeta');
const controlStatus = document.querySelector('#controlStatus');
const clickHint    = document.querySelector('#clickHint');
const servoList    = document.querySelector('#servoList');
const startControl = document.querySelector('#startControl');
const stopControl  = document.querySelector('#stopControl');
const template     = document.querySelector('#servoTemplate');

const irSection          = document.querySelector('#irSection');
const trackerSection     = document.querySelector('#trackerSection');
const irFeed             = document.querySelector('#irFeed');
const trackerFeed        = document.querySelector('#trackerFeed');
const irOverlay          = document.querySelector('#irOverlay');

const ballPill     = document.querySelector('#ballPill');
const ballX        = document.querySelector('#ballX');
const ballY        = document.querySelector('#ballY');
const ballZ        = document.querySelector('#ballZ');
const ballR        = document.querySelector('#ballR');

const depthStage   = document.querySelector('#depthStage');
const depthImg     = document.querySelector('#depthFeed');
const overlay      = document.querySelector('#depthOverlay');

const ballWorld           = document.querySelector('#ballWorld');
const poseStatus          = document.querySelector('#poseStatus');
const poseStatusPill      = document.querySelector('#poseStatusPill');
const poseResiduals       = document.querySelector('#poseResiduals');
const calibDiagnostics    = document.querySelector('#calibDiagnostics');
const markerThresholdSlider = document.querySelector('#markerThresholdSlider');
const lblMarkerThreshold    = document.querySelector('#lblMarkerThreshold');
const ballThresholdSlider   = document.querySelector('#ballThresholdSlider');
const lblBallThreshold      = document.querySelector('#lblBallThreshold');
const irPixelTooltip        = document.querySelector('#irPixelTooltip');
const irStage               = document.querySelector('#irStage');

let lastDiagnostics = null;

// AB pixel hover — draw one pixel from the MJPEG frame to a 1×1 offscreen
// canvas and convert the 8-bit value back to raw IR counts.
const _irSampleCanvas = document.createElement('canvas');
_irSampleCanvas.width = _irSampleCanvas.height = 1;
const _irSampleCtx = _irSampleCanvas.getContext('2d', { willReadFrequently: true });

irStage.addEventListener('mousemove', (e) => {
  const rect = irFeed.getBoundingClientRect();
  const nw = irFeed.naturalWidth;
  const nh = irFeed.naturalHeight;
  if (!nw || !nh) return;
  const px = Math.round((e.clientX - rect.left) / rect.width  * nw);
  const py = Math.round((e.clientY - rect.top)  / rect.height * nh);
  try {
    _irSampleCtx.drawImage(irFeed, px, py, 1, 1, 0, 0, 1, 1);
    const val8 = _irSampleCtx.getImageData(0, 0, 1, 1).data[0];
    const counts = val8 << 8;
    irPixelTooltip.textContent = `${counts} cts`;
    irPixelTooltip.style.display = 'block';
    irPixelTooltip.style.left = `${e.clientX - rect.left}px`;
    irPixelTooltip.style.top  = `${e.clientY - rect.top}px`;
  } catch (_) {}
});
irStage.addEventListener('mouseleave', () => {
  irPixelTooltip.style.display = 'none';
});


// Marker IR threshold slider — same throttled-POST pattern as IR brightness.
let markerThresholdThrottle = null;
let markerThresholdPending  = null;
async function sendMarkerThreshold(v) {
  markerThresholdPending = null;
  markerThresholdThrottle = setTimeout(() => {
    markerThresholdThrottle = null;
    if (markerThresholdPending !== null) sendMarkerThreshold(markerThresholdPending);
  }, 50);
  try { await postJson('/api/pose/threshold', { value: v }); } catch (_) {}
}
markerThresholdSlider.addEventListener('input', () => {
  const v = Number(markerThresholdSlider.value);
  lblMarkerThreshold.textContent = v;
  if (!markerThresholdThrottle) sendMarkerThreshold(v); else markerThresholdPending = v;
  renderDiagnostics(lastDiagnostics);
});

// Ball IR threshold slider.
let ballThresholdThrottle = null;
let ballThresholdPending  = null;
async function sendBallThreshold(v) {
  ballThresholdPending = null;
  ballThresholdThrottle = setTimeout(() => {
    ballThresholdThrottle = null;
    if (ballThresholdPending !== null) sendBallThreshold(ballThresholdPending);
  }, 50);
  try { await postJson('/api/ball/threshold', { value: v }); } catch (_) {}
}
ballThresholdSlider.addEventListener('input', () => {
  const v = Number(ballThresholdSlider.value);
  lblBallThreshold.textContent = v;
  if (!ballThresholdThrottle) sendBallThreshold(v); else ballThresholdPending = v;
});

// Keep IR overlay canvas sized to the image element.
function syncIrCanvas() {
  irOverlay.width  = irFeed.clientWidth  || 1;
  irOverlay.height = irFeed.clientHeight || 1;
}
new ResizeObserver(syncIrCanvas).observe(irFeed);
irFeed.addEventListener('load', syncIrCanvas);

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
  renderTablePose(state.table_pose);
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
  const boxX        = card.querySelector('.box-x');
  const boxY        = card.querySelector('.box-y');
  const boxWidth    = card.querySelector('.box-width');
  const boxHeight   = card.querySelector('.box-height');

  if (document.activeElement !== targetInput) targetInput.value = Math.round(servo.target_depth_mm);
  if (document.activeElement !== boxX)        boxX.value        = servo.box.x;
  if (document.activeElement !== boxY)        boxY.value        = servo.box.y;
  if (document.activeElement !== boxWidth)    boxWidth.value    = servo.box.width;
  if (document.activeElement !== boxHeight)   boxHeight.value   = servo.box.height;

  card.querySelector('.current-depth').textContent = fmtMm(servo.current_depth_mm);
  card.querySelector('.current-error').textContent = fmtMm(servo.current_error_mm);
  card.querySelector('.angle').textContent         = fmtDeg(servo.angle_deg);
  card.querySelector('.valid-pixels').textContent  = `${servo.valid_pixels}/${servo.total_pixels}`;
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
      x:      Number(card.querySelector('.box-x').value),
      y:      Number(card.querySelector('.box-y').value),
      width:  Number(card.querySelector('.box-width').value),
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
  const width  = latestState?.depth_image?.width  || depthImg.naturalWidth;
  const height = latestState?.depth_image?.height || depthImg.naturalHeight;
  if (!width || !height || !rect.width || !rect.height) return null;
  const x = Math.floor((event.clientX - rect.left) * width  / rect.width);
  const y = Math.floor((event.clientY - rect.top)  * height / rect.height);
  return {
    x: Math.max(0, Math.min(width  - 1, x)),
    y: Math.max(0, Math.min(height - 1, y)),
  };
}

function boxFromPoints(a, b) {
  const x0 = Math.min(a.x, b.x);
  const y0 = Math.min(a.y, b.y);
  const x1 = Math.max(a.x, b.x);
  const y1 = Math.max(a.y, b.y);
  return { x: x0, y: y0, width: x1 - x0 + 1, height: y1 - y0 + 1 };
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
  const width  = state?.depth_image?.width  || depthImg.naturalWidth;
  const height = state?.depth_image?.height || depthImg.naturalHeight;
  if (!state || !width || !height || rect.width < 1 || rect.height < 1) return;

  const dpr = window.devicePixelRatio || 1;
  overlay.width  = Math.round(rect.width  * dpr);
  overlay.height = Math.round(rect.height * dpr);
  overlay.style.width  = `${rect.width}px`;
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
  const x      = box.x      * rect.width  / imageWidth;
  const y      = box.y      * rect.height / imageHeight;
  const width  = box.width  * rect.width  / imageWidth;
  const height = box.height * rect.height / imageHeight;

  ctx.save();
  ctx.strokeStyle = color;
  ctx.lineWidth = selected ? 3 : 2;
  ctx.globalAlpha = draft ? 0.36 : 0.18;
  ctx.fillStyle = color;
  ctx.fillRect(x, y, width, height);
  ctx.globalAlpha = 1;
  ctx.strokeRect(x, y, width, height);

  const labelX = Math.min(rect.width  - 34, Math.max(4, x + 6));
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

function renderBall(ball) {
  // The IR feed and brightness slider work regardless of ball tracking (the
  // Kinect always streams raw IR); only the ball readout/tracker debug view
  // depend on --ball-tracking being enabled.
  if (!ball) {
    clearBallOverlay();
    return;
  }


  if (!ball.enabled) {
    ballPill.textContent = 'ball tracking disabled';
    ballPill.className   = 'pill';
    ballX.textContent = '--';
    ballY.textContent = '--';
    ballZ.textContent = '--';
    ballR.textContent = '--';
    ballWorld.textContent = 'not tracking';
    return;
  }

  if (ball.detected) {
    ballPill.textContent = 'detected';
    ballPill.className   = 'pill detected';
    ballX.textContent = fmtMm(ball.position?.x);
    ballY.textContent = fmtMm(ball.position?.y);
    ballZ.textContent = fmtMm(ball.position?.z);
    ballR.textContent = fmtMm(ball.radius_mm);
  } else {
    ballPill.textContent = searchingLabel(ball.reject_counts);
    ballPill.className   = 'pill';
    ballX.textContent = '--';
    ballY.textContent = '--';
    ballZ.textContent = '--';
    ballR.textContent = '--';
  }

  if (!ball.table_tracking) {
    ballWorld.textContent = 'not tracking';
  } else if (ball.detected && ball.position_world) {
    const w = ball.position_world;
    const staleTag = ball.pose_stale ? ` (stale, ${ball.pose_age_s?.toFixed(1)}s)` : '';
    const cellTag = ball.cell ? ` cell=(${ball.cell.row},${ball.cell.col})` : '';
    ballWorld.textContent = `X=${fmtMm(w.x)} Y=${fmtMm(w.y)} Z=${fmtMm(w.z)}${cellTag}${staleTag}`;
  } else {
    ballWorld.textContent = 'tracking — ball not detected';
  }

  if (ball.ball_ir_threshold !== undefined && document.activeElement !== ballThresholdSlider) {
    ballThresholdSlider.value    = ball.ball_ir_threshold;
    lblBallThreshold.textContent = ball.ball_ir_threshold;
  }
}

function renderTablePose(pose) {
  if (!pose) return;

  if (!pose.tracking) {
    poseStatusPill.textContent = 'not tracking';
    poseStatusPill.className   = 'pill';
    poseStatus.textContent = pose.last_error ? `no fit yet: ${pose.last_error}` : 'no pose fit yet';
    poseResiduals.innerHTML = '';
  } else if (pose.stale) {
    poseStatusPill.textContent = `stale (${pose.age_s?.toFixed(1)}s ago)`;
    poseStatusPill.className   = 'pill';
    poseStatus.textContent = pose.last_error ? `holding last pose — ${pose.last_error}` : 'holding last pose';
  } else {
    poseStatusPill.textContent = `tracking, rms ${pose.rms_residual_mm.toFixed(1)}mm`;
    poseStatusPill.className   = 'pill detected';
    poseStatus.textContent = `fit ok — rms ${pose.rms_residual_mm.toFixed(1)}mm, max ${pose.max_residual_mm.toFixed(1)}mm`;
  }

  if (pose.tracking) {
    poseResiduals.innerHTML = '';
    const points = pose.matched_points || {};
    for (const [name, info] of Object.entries(points)) {
      const row = document.createElement('div');
      row.className = 'calib-residual-row' + (info.residual_mm > 10.0 ? ' bad' : '');
      row.innerHTML = `<span>${name}</span><span>${info.residual_mm.toFixed(1)} mm</span>`;
      poseResiduals.appendChild(row);
    }
  }

  if (pose.marker_ir_threshold !== undefined && document.activeElement !== markerThresholdSlider) {
    markerThresholdSlider.value    = pose.marker_ir_threshold;
    lblMarkerThreshold.textContent = pose.marker_ir_threshold;
  }

  lastDiagnostics = pose.diagnostics || null;
  renderDiagnostics(lastDiagnostics);
}

function renderDiagnostics(diagnostics) {
  calibDiagnostics.innerHTML = '';
  if (!diagnostics) return;

  const currentThreshold = Number(markerThresholdSlider.value);
  const title = document.createElement('div');
  title.className = 'diag-title';
  title.textContent = `IR diagnostics — frame max ${diagnostics.ir_max.toFixed(0)}`;
  calibDiagnostics.appendChild(title);

  for (const { threshold, count } of diagnostics.threshold_counts) {
    const row = document.createElement('div');
    const isCurrent = Math.abs(threshold - currentThreshold) < 25;
    row.className = 'diag-row' + (isCurrent ? ' current' : '');
    row.innerHTML = `<span>&ge; ${threshold.toFixed(0)}</span><span>${count} px</span>`;
    calibDiagnostics.appendChild(row);
  }
}

function searchingLabel(rejectCounts) {
  const names = { shape: 'not circular', fill: 'not filled', depth: 'no depth', size: 'wrong size' };
  const rejects = Object.entries(rejectCounts ?? {})
    .filter(([key, count]) => key !== 'accepted' && count > 0)
    .sort((a, b) => b[1] - a[1]);
  if (!rejects.length) return 'searching';
  const [key, count] = rejects[0];
  return `searching — ${names[key] ?? key}×${count}`;
}

function drawBallOnIR(pixel) {
  const rect = irFeed.getBoundingClientRect();
  const nw = irFeed.naturalWidth;
  const nh = irFeed.naturalHeight;
  if (!rect.width || !nw || !nh) return;

  const dpr = window.devicePixelRatio || 1;
  irOverlay.width  = Math.round(rect.width  * dpr);
  irOverlay.height = Math.round(rect.height * dpr);
  irOverlay.style.width  = `${rect.width}px`;
  irOverlay.style.height = `${rect.height}px`;

  const ctx = irOverlay.getContext('2d');
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, rect.width, rect.height);

  const cx = pixel.cx     * rect.width  / nw;
  const cy = pixel.cy     * rect.height / nh;
  const r  = pixel.radius * rect.width  / nw;

  ctx.strokeStyle = '#ff3355';
  ctx.lineWidth   = 2.5;
  ctx.beginPath();
  ctx.arc(cx, cy, r, 0, 2 * Math.PI);
  ctx.stroke();

  ctx.fillStyle = '#ff3355';
  ctx.beginPath();
  ctx.arc(cx, cy, 3.5, 0, 2 * Math.PI);
  ctx.fill();
}

function clearBallOverlay() {
  const ctx = irOverlay.getContext('2d');
  ctx.clearRect(0, 0, irOverlay.width, irOverlay.height);
}
