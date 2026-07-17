const cameraDot    = document.querySelector('#cameraDot');
const cameraStatus = document.querySelector('#cameraStatus');
const cameraMeta   = document.querySelector('#cameraMeta');

const irSection          = document.querySelector('#irSection');
const irFeed             = document.querySelector('#irFeed');
const irSampleFeed       = document.querySelector('#irSampleFeed');
const irOverlay          = document.querySelector('#irOverlay');

const ballPill     = document.querySelector('#ballPill');
const ballX        = document.querySelector('#ballX');
const ballY        = document.querySelector('#ballY');
const ballZ        = document.querySelector('#ballZ');
const ballR        = document.querySelector('#ballR');

const ballWorld           = document.querySelector('#ballWorld');
const poseStatus          = document.querySelector('#poseStatus');
const poseStatusPill      = document.querySelector('#poseStatusPill');
const poseResiduals       = document.querySelector('#poseResiduals');
const poseRms             = document.querySelector('#poseRms');
const poseMax             = document.querySelector('#poseMax');
const poseAge             = document.querySelector('#poseAge');
const poseMarkers         = document.querySelector('#poseMarkers');
const markerThresholdSlider = document.querySelector('#markerThresholdSlider');
const lblMarkerThreshold    = document.querySelector('#lblMarkerThreshold');
const ballThresholdSlider   = document.querySelector('#ballThresholdSlider');
const lblBallThreshold      = document.querySelector('#lblBallThreshold');
const irPixelTooltip        = document.querySelector('#irPixelTooltip');
const irStage               = document.querySelector('#irStage');

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
    _irSampleCtx.drawImage(irSampleFeed, px, py, 1, 1, 0, 0, 1, 1);
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

let stateTimer = null;
let ballStateTimer = null;
let ballStateInFlight = false;

function fmtMm(value) {
  if (value === null || value === undefined) return '--';
  return `${Number(value).toFixed(1)} mm`;
}

function fmtPx(value) {
  if (value === null || value === undefined) return '--';
  return `${Number(value).toFixed(1)} px`;
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
    const state = await response.json();
    renderState(state);
  } catch (error) {
    console.error('fetchState failed:', error);
  }
}

async function fetchBallState() {
  if (ballStateInFlight) return;
  ballStateInFlight = true;
  try {
    const response = await fetch('/api/ball/state', { cache: 'no-store' });
    renderBall(await response.json());
  } catch (error) {
    console.error('fetchBallState failed:', error);
  } finally {
    ballStateInFlight = false;
  }
}

function renderState(state) {
  cameraDot.className = `status-dot ${state.camera.status}`;
  cameraStatus.textContent = state.camera.status;
  const fps = state.camera.fps ? `${state.camera.fps.toFixed(1)} fps` : 'fps pending';
  cameraMeta.textContent = state.camera.error || `Active Brightness, ${fps}`;

  renderBall(state.ball);
  renderTablePose(state.table_pose);
}

fetchState();
fetchBallState();
stateTimer = window.setInterval(fetchState, 350);
ballStateTimer = window.setInterval(fetchBallState, 33);

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
  } else if (ball.detected && ball.cell) {
    const staleTag = ball.pose_stale ? ` (stale, ${ball.pose_age_s?.toFixed(1)}s)` : '';
    ballWorld.textContent = `cell=(${ball.cell.row},${ball.cell.col})${staleTag}`;
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
    poseStatusPill.textContent = 'tracking';
    poseStatusPill.className   = 'pill detected';
    poseStatus.textContent = 'fit ok';
  }

  poseRms.textContent   = fmtPx(pose.rms_residual_px);
  poseMax.textContent   = fmtPx(pose.max_residual_px);
  poseAge.textContent   = pose.age_s === null || pose.age_s === undefined
    ? '--'
    : `${Number(pose.age_s).toFixed(1)} s`;
  const markerCount = Object.keys(pose.matched_points || {}).length;
  poseMarkers.textContent = markerCount ? `${markerCount} / 6` : '--';

  if (pose.tracking) {
    poseResiduals.innerHTML = '';
    const points = pose.matched_points || {};
    for (const [name, info] of Object.entries(points)) {
      const row = document.createElement('div');
      row.className = 'calib-residual-row' + (info.residual_px > 10.0 ? ' bad' : '');
      row.innerHTML = `<span>${name}</span><span>${info.residual_px.toFixed(1)} px</span>`;
      poseResiduals.appendChild(row);
    }
  }

  if (pose.marker_ir_threshold !== undefined && document.activeElement !== markerThresholdSlider) {
    markerThresholdSlider.value    = pose.marker_ir_threshold;
    lblMarkerThreshold.textContent = pose.marker_ir_threshold;
  }
}

function searchingLabel(rejectCounts) {
  const names = { shape: 'not circular', fill: 'not filled', size: 'wrong size' };
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
