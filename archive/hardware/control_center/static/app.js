const root = document.getElementById('app');

const state = {
  dashboard: null,
  flash: {
    tone: 'info',
    message: 'Loading control center...',
  },
  flashTimeout: null,
  serialPortDraft: 'auto',
  profileDrafts: {},
  dirtyProfiles: new Set(),
  cameraDraft: null,
  cameraDirty: false,
  cycleDraft: {
    cycles: 1,
    steps: 40,
    delay_ms: 40,
    hold_ms: 200,
  },
  pollTimer: null,
  lastStreamKey: '',
};

function escapeHtml(value) {
  return String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
}

function midpoint(minimum, maximum) {
  return Math.round((Number(minimum) + Number(maximum)) / 2);
}

function formatTime(timestamp) {
  if (!timestamp) return 'Never';
  const date = new Date(timestamp);
  if (Number.isNaN(date.getTime())) return 'Unknown';
  return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

function setFlash(message, tone = 'info', persist = false) {
  state.flash = { message, tone };
  renderBanner();
  if (state.flashTimeout) {
    window.clearTimeout(state.flashTimeout);
    state.flashTimeout = null;
  }
  if (!persist) {
    state.flashTimeout = window.setTimeout(() => {
      state.flash = { message: '', tone: 'info' };
      renderBanner();
    }, 3500);
  }
}

function apiRequest(path, options = {}) {
  const headers = new Headers(options.headers || {});
  if (options.body && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json');
  }

  return fetch(path, { ...options, headers })
    .then(async (response) => {
      const raw = await response.text();
      let payload = {};
      if (raw) {
        try {
          payload = JSON.parse(raw);
        } catch {
          payload = {};
        }
      }
      if (!response.ok || payload.ok === false) {
        const error = new Error(payload.error || payload.message || `Request failed (${response.status})`);
        error.dashboard = payload.dashboard;
        throw error;
      }
      return payload;
    });
}

function createProfileDraft(profile) {
  return {
    name: profile.name,
    min_us: profile.min_us,
    max_us: profile.max_us,
    home_deg: profile.home_deg,
    invert: Boolean(profile.invert),
    states_us: {
      wall: profile.states_us.wall ?? '',
      floor: profile.states_us.floor ?? '',
      hole: profile.states_us.hole ?? '',
    },
    angle: profile.live?.last_angle_deg ?? profile.home_deg,
    pulse_us: profile.live?.last_us ?? profile.states_us.floor ?? midpoint(profile.min_us, profile.max_us),
  };
}

function createCameraDraft(camera) {
  return {
    device: camera.device || 'auto',
    width: camera.width,
    height: camera.height,
    fps: camera.fps,
    jpeg_quality: camera.jpeg_quality,
  };
}

function mergeDashboard(dashboard) {
  state.dashboard = dashboard;

  const channelKeys = new Set();
  dashboard.servo.profiles.forEach((profile) => {
    const key = String(profile.channel);
    channelKeys.add(key);
    if (!state.profileDrafts[key] || !state.dirtyProfiles.has(key)) {
      state.profileDrafts[key] = createProfileDraft(profile);
    } else {
      state.profileDrafts[key].angle = profile.live?.last_angle_deg ?? state.profileDrafts[key].angle;
      state.profileDrafts[key].pulse_us = profile.live?.last_us ?? state.profileDrafts[key].pulse_us;
    }
  });

  Object.keys(state.profileDrafts).forEach((key) => {
    if (!channelKeys.has(key)) {
      delete state.profileDrafts[key];
      state.dirtyProfiles.delete(key);
    }
  });

  if (!state.cameraDraft || !state.cameraDirty) {
    state.cameraDraft = createCameraDraft(dashboard.camera);
  }

  if (
    state.serialPortDraft === 'auto' ||
    !dashboard.servo.available_ports.some((port) => port.device === state.serialPortDraft)
  ) {
    state.serialPortDraft = dashboard.servo.port || dashboard.servo.configured_port || 'auto';
  }

  render();
  syncCameraStream();
}

function ensureShell() {
  if (root.dataset.initialized === 'true') return;

  root.innerHTML = `
    <div class="app-shell">
      <header class="hero panel">
        <div class="hero-copy">
          <div class="eyebrow">Raspberry Pi Hosted</div>
          <h1>Marble Maze Control Center</h1>
          <p>Operate the Arduino servo bridge, edit calibration, teach named positions, and monitor the USB webcam physically attached to the Raspberry Pi that is hosting this app.</p>
        </div>
        <div class="hero-side">
          <div id="hero-meta" class="hero-meta"></div>
          <div class="button-row hero-actions">
            <button class="button button-secondary" data-action="refresh-dashboard" type="button">Refresh</button>
            <a class="button button-secondary" id="snapshot-link" href="/api/camera/snapshot.jpg" target="_blank" rel="noreferrer">Snapshot</a>
          </div>
        </div>
      </header>

      <section id="banner-slot"></section>
      <section id="metrics-slot" class="metric-grid"></section>

      <div class="top-grid">
        <section id="servo-slot" class="panel"></section>

        <section class="panel camera-panel">
          <div class="panel-header">
            <div>
              <h2>Pi USB Webcam</h2>
              <p>Live MJPEG preview captured on the Pi host, not from the viewing browser.</p>
            </div>
          </div>
          <div class="camera-stage">
            <img id="camera-stream" class="camera-stream" alt="USB webcam live preview">
            <div id="camera-overlay" class="camera-overlay"></div>
          </div>
          <div id="camera-slot" class="panel-body"></div>
        </section>
      </div>

      <section id="profiles-slot" class="panel"></section>
      <section id="events-slot" class="panel"></section>
    </div>
  `;

  root.addEventListener('input', handleFieldInput);
  root.addEventListener('change', handleFieldInput);
  root.addEventListener('click', handleClick);
  root.dataset.initialized = 'true';
}

function renderHeroMeta() {
  if (!state.dashboard) return;
  const { host, servo, camera, server_time: serverTime } = state.dashboard;
  const heroMeta = document.getElementById('hero-meta');
  heroMeta.innerHTML = `
    <div class="meta-chip"><span>Host</span><strong>${escapeHtml(host.hostname || 'Unknown')}</strong></div>
    <div class="meta-chip"><span>Server</span><strong>${escapeHtml(formatTime(serverTime))}</strong></div>
    <div class="meta-chip"><span>Serial</span><strong>${escapeHtml(servo.port || servo.configured_port || 'Auto')}</strong></div>
    <div class="meta-chip"><span>Camera</span><strong>${escapeHtml(camera.resolved_device || camera.device || 'Auto')}</strong></div>
  `;

  const snapshotLink = document.getElementById('snapshot-link');
  snapshotLink.href = `/api/camera/snapshot.jpg?ts=${Date.now()}`;
}

function renderBanner() {
  const bannerSlot = document.getElementById('banner-slot');
  if (!bannerSlot || !state.dashboard) return;

  const servoError = state.dashboard.servo.last_error;
  const cameraError = state.dashboard.camera.last_error;
  let tone = state.flash.message ? state.flash.tone : 'info';
  let message = state.flash.message;

  if (!message) {
    if (servoError) {
      tone = 'error';
      message = servoError;
    } else if (cameraError && state.dashboard.camera.backend_available) {
      tone = 'warn';
      message = cameraError;
    } else {
      tone = 'info';
      message = 'Use the global controls to connect hardware, then tune and drive each servo from its own card.';
    }
  }

  bannerSlot.innerHTML = `
    <div class="banner banner-${escapeHtml(tone)}">
      <strong>${tone === 'error' ? 'Attention' : tone === 'warn' ? 'Camera' : 'Ready'}</strong>
      <span>${escapeHtml(message)}</span>
    </div>
  `;
}

function renderMetrics() {
  if (!state.dashboard) return;
  const metricsSlot = document.getElementById('metrics-slot');
  const { host, servo, camera } = state.dashboard;
  metricsSlot.innerHTML = `
    <article class="metric-card">
      <span>Pi Host</span>
      <strong>${escapeHtml(host.hostname || 'Unknown')}</strong>
      <small>${escapeHtml(host.model || host.platform || 'Host platform unavailable')}</small>
    </article>
    <article class="metric-card">
      <span>Servo Bridge</span>
      <strong>${servo.connected ? 'Connected' : 'Disconnected'}</strong>
      <small>${escapeHtml(servo.port || servo.configured_port || 'Auto-select')}</small>
    </article>
    <article class="metric-card">
      <span>Configured Servos</span>
      <strong>${servo.servo_count}</strong>
      <small>${servo.enabled_count} currently enabled</small>
    </article>
    <article class="metric-card">
      <span>Saved States</span>
      <strong>${servo.saved_state_total}</strong>
      <small>Across wall, floor, and hole presets</small>
    </article>
    <article class="metric-card">
      <span>Camera Stream</span>
      <strong>${camera.streaming ? 'Live' : 'Idle'}</strong>
      <small>${escapeHtml(camera.resolved_device || camera.device || 'Awaiting /dev/video device')}</small>
    </article>
  `;
}

function renderServoPanel() {
  if (!state.dashboard) return;
  const servoSlot = document.getElementById('servo-slot');
  const servo = state.dashboard.servo;
  const ports = servo.available_ports || [];

  servoSlot.innerHTML = `
    <div class="panel-header">
      <div>
        <h2>Servo Bridge</h2>
        <p>Serial discovery, batch commands, and push-to-device calibration updates.</p>
      </div>
      <div class="status-pill ${servo.connected ? 'is-good' : 'is-muted'}">${servo.connected ? 'Connected' : 'Disconnected'}</div>
    </div>

    <div class="panel-body section-stack">
      <div class="field-grid">
        <label class="field">
          <span>Arduino Serial Port</span>
          <select data-global-field="serial_port">
            <option value="auto"${state.serialPortDraft === 'auto' ? ' selected' : ''}>Auto-select first matching device</option>
            ${ports.map((port) => `
              <option value="${escapeHtml(port.device)}"${state.serialPortDraft === port.device ? ' selected' : ''}>
                ${escapeHtml(`${port.device} - ${port.description}`)}
              </option>
            `).join('')}
          </select>
        </label>

        <div class="field readonly-field">
          <span>Last Status Poll</span>
          <strong>${escapeHtml(formatTime(servo.last_status_at))}</strong>
        </div>
      </div>

      <div class="button-row">
        <button class="button" data-action="connect-serial" type="button">Connect</button>
        <button class="button button-secondary" data-action="disconnect-serial" type="button">Disconnect</button>
        <button class="button button-secondary" data-action="apply-config" type="button">Apply Config</button>
        <button class="button button-secondary" data-action="save-config" type="button">Save Config</button>
      </div>

      <div class="button-row">
        <button class="button button-secondary" data-action="home-all" type="button">Home All</button>
        <button class="button button-secondary" data-action="enable-all" type="button">Enable All</button>
        <button class="button button-secondary" data-action="disable-all" type="button">Disable All</button>
      </div>

      <div class="subpanel">
        <div class="subpanel-header">
          <h3>Cycle All</h3>
          <p>Exercise every configured servo across its configured min and max pulse range.</p>
        </div>
        <div class="field-grid compact-grid">
          <label class="field">
            <span>Cycles</span>
            <input data-cycle-field="cycles" type="number" min="1" max="100" value="${escapeHtml(state.cycleDraft.cycles)}">
          </label>
          <label class="field">
            <span>Steps</span>
            <input data-cycle-field="steps" type="number" min="1" max="400" value="${escapeHtml(state.cycleDraft.steps)}">
          </label>
          <label class="field">
            <span>Delay ms</span>
            <input data-cycle-field="delay_ms" type="number" min="0" max="5000" value="${escapeHtml(state.cycleDraft.delay_ms)}">
          </label>
          <label class="field">
            <span>Hold ms</span>
            <input data-cycle-field="hold_ms" type="number" min="0" max="5000" value="${escapeHtml(state.cycleDraft.hold_ms)}">
          </label>
        </div>
        <div class="button-row">
          <button class="button" data-action="run-cycle" type="button">Run Cycle</button>
        </div>
      </div>

      <div class="helper-text">
        <div><strong>Config file:</strong> ${escapeHtml(servo.config_path)}</div>
        ${servo.port_scan_error ? `<div class="error-text"><strong>Port scan:</strong> ${escapeHtml(servo.port_scan_error)}</div>` : ''}
      </div>
    </div>
  `;
}

function renderCameraPanel() {
  if (!state.dashboard || !state.cameraDraft) return;
  const cameraSlot = document.getElementById('camera-slot');
  const camera = state.dashboard.camera;
  const overlay = document.getElementById('camera-overlay');
  let overlayMessage = '';

  if (!camera.backend_available) {
    overlayMessage = 'Install opencv-python-headless to enable webcam streaming.';
  } else if (!camera.available_devices.length) {
    overlayMessage = 'No webcam devices detected.';
  } else if (!camera.streaming) {
    overlayMessage = camera.last_error || 'Preparing camera stream...';
  }

  overlay.textContent = overlayMessage;
  overlay.classList.toggle('is-visible', Boolean(overlayMessage));

  cameraSlot.innerHTML = `
    <div class="field-grid">
      <label class="field">
        <span>Pi Camera Device</span>
        <select data-camera-field="device">
          <option value="auto"${state.cameraDraft.device === 'auto' ? ' selected' : ''}>Auto-select first Pi /dev/video camera</option>
          ${camera.available_devices.map((device) => `
            <option value="${escapeHtml(device.id)}"${state.cameraDraft.device === device.id ? ' selected' : ''}>
              ${escapeHtml(device.label)}
            </option>
          `).join('')}
        </select>
      </label>
      <label class="field">
        <span>Width</span>
        <input data-camera-field="width" type="number" min="160" max="3840" value="${escapeHtml(state.cameraDraft.width)}">
      </label>
      <label class="field">
        <span>Height</span>
        <input data-camera-field="height" type="number" min="120" max="2160" value="${escapeHtml(state.cameraDraft.height)}">
      </label>
      <label class="field">
        <span>FPS</span>
        <input data-camera-field="fps" type="number" min="1" max="60" value="${escapeHtml(state.cameraDraft.fps)}">
      </label>
      <label class="field">
        <span>JPEG Quality</span>
        <input data-camera-field="jpeg_quality" type="number" min="40" max="95" value="${escapeHtml(state.cameraDraft.jpeg_quality)}">
      </label>
    </div>

    <div class="button-row">
      <button class="button" data-action="apply-camera-config" type="button">Apply & Restart</button>
      <button class="button button-secondary" data-action="restart-camera" type="button">Restart Stream</button>
    </div>

    <div class="helper-text">
      <div><strong>Capture source:</strong> ${escapeHtml(camera.device_source || 'Host-side webcam capture')}</div>
      <div><strong>Pi host:</strong> ${escapeHtml(state.dashboard.host.hostname || 'Unknown')}</div>
      <div><strong>Resolved device:</strong> ${escapeHtml(camera.resolved_device || 'Not active')}</div>
      <div><strong>Last frame:</strong> ${escapeHtml(formatTime(camera.last_frame_at))}</div>
      ${camera.frame_size ? `<div><strong>Live frame:</strong> ${escapeHtml(`${camera.frame_size[0]} x ${camera.frame_size[1]}`)}</div>` : ''}
      ${camera.last_error ? `<div class="error-text"><strong>Camera:</strong> ${escapeHtml(camera.last_error)}</div>` : ''}
    </div>
  `;
}

function renderRangeControl({ label, channel, field, minimum, maximum, step, value, sendAction, valueSuffix = '' }) {
  return `
    <div class="range-field">
      <div class="range-header">
        <span>${escapeHtml(label)}</span>
        <strong data-value-for="${escapeHtml(field)}">${escapeHtml(`${value}${valueSuffix}`)}</strong>
      </div>
      <input
        data-channel="${escapeHtml(channel)}"
        data-control-field="${escapeHtml(field)}"
        type="range"
        min="${escapeHtml(minimum)}"
        max="${escapeHtml(maximum)}"
        step="${escapeHtml(step)}"
        value="${escapeHtml(value)}"
      >
      <div class="range-actions">
        <input
          class="range-number"
          data-channel="${escapeHtml(channel)}"
          data-control-field="${escapeHtml(field)}"
          type="number"
          min="${escapeHtml(minimum)}"
          max="${escapeHtml(maximum)}"
          step="${escapeHtml(step)}"
          value="${escapeHtml(value)}"
        >
        <button class="button button-secondary" data-action="${escapeHtml(sendAction)}" data-channel="${escapeHtml(channel)}" type="button">Send</button>
      </div>
    </div>
  `;
}

function renderProfileCard(profile) {
  const draft = state.profileDrafts[String(profile.channel)] || createProfileDraft(profile);
  const dirty = state.dirtyProfiles.has(String(profile.channel));
  const pulseMin = Number(draft.min_us || profile.min_us);
  const pulseMax = Number(draft.max_us || profile.max_us);
  const liveStatus = profile.live
    ? `${profile.live.enabled ? 'Enabled' : 'Disabled'} · ${profile.live.last_us} us · ${profile.live.last_angle_deg} deg`
    : 'No live status yet';

  return `
    <article class="servo-card ${dirty ? 'is-dirty' : ''}">
      <div class="servo-card-header">
        <div>
          <div class="eyebrow">Channel ${profile.channel}</div>
          <h3>${escapeHtml(profile.name)}</h3>
          <p>${escapeHtml(liveStatus)}</p>
        </div>
        <div class="servo-meta">
          <span class="status-pill ${profile.live?.enabled ? 'is-good' : 'is-muted'}">${profile.live?.enabled ? 'PWM On' : 'PWM Off'}</span>
          <span class="status-pill ${dirty ? 'is-warn' : 'is-muted'}">${dirty ? 'Unsaved Edits' : 'Saved'}</span>
        </div>
      </div>

      <div class="servo-grid">
        <section class="subpanel">
          <div class="subpanel-header">
            <h4>Calibration & Presets</h4>
            <p>Edit local profile data, then save it to disk with or without immediately pushing calibration to the Arduino.</p>
          </div>

          <div class="field-grid">
            <label class="field">
              <span>Name</span>
              <input data-channel="${profile.channel}" data-profile-field="name" type="text" value="${escapeHtml(draft.name)}">
            </label>
            <label class="field">
              <span>Min us</span>
              <input data-channel="${profile.channel}" data-profile-field="min_us" type="number" min="100" max="3000" value="${escapeHtml(draft.min_us)}">
            </label>
            <label class="field">
              <span>Max us</span>
              <input data-channel="${profile.channel}" data-profile-field="max_us" type="number" min="100" max="3000" value="${escapeHtml(draft.max_us)}">
            </label>
            <label class="field">
              <span>Home deg</span>
              <input data-channel="${profile.channel}" data-profile-field="home_deg" type="number" min="0" max="180" step="0.1" value="${escapeHtml(draft.home_deg)}">
            </label>
            <label class="field checkbox-field">
              <input data-channel="${profile.channel}" data-profile-field="invert" type="checkbox"${draft.invert ? ' checked' : ''}>
              <span>Invert direction</span>
            </label>
          </div>

          <div class="preset-grid">
            ${['wall', 'floor', 'hole'].map((preset) => `
              <label class="field">
                <span>${preset} us</span>
                <input
                  data-channel="${profile.channel}"
                  data-state-field="${preset}"
                  type="number"
                  min="100"
                  max="3000"
                  value="${escapeHtml(draft.states_us[preset])}"
                  placeholder="Unset"
                >
              </label>
            `).join('')}
          </div>

          <div class="button-row">
            <button class="button" data-action="save-profile" data-channel="${profile.channel}" type="button">Save Local</button>
            <button class="button button-secondary" data-action="save-apply-profile" data-channel="${profile.channel}" type="button">Save + Apply</button>
          </div>
        </section>

        <section class="subpanel">
          <div class="subpanel-header">
            <h4>Motion Controls</h4>
            <p>Drive the live servo with direct angle or pulse commands, then capture those positions into named presets.</p>
          </div>

          ${renderRangeControl({
            label: 'Target Angle',
            channel: profile.channel,
            field: 'angle',
            minimum: 0,
            maximum: 180,
            step: 1,
            value: Number(draft.angle),
            sendAction: 'servo-angle',
            valueSuffix: ' deg',
          })}

          ${renderRangeControl({
            label: 'Target Pulse',
            channel: profile.channel,
            field: 'pulse_us',
            minimum: pulseMin,
            maximum: pulseMax,
            step: 1,
            value: Number(draft.pulse_us),
            sendAction: 'servo-pulse',
            valueSuffix: ' us',
          })}

          <div class="button-row wrap">
            ${[-100, -25, -10, 10, 25, 100].map((delta) => `
              <button
                class="button button-secondary"
                data-action="servo-nudge"
                data-channel="${profile.channel}"
                data-delta="${delta}"
                type="button"
              >${delta > 0 ? `+${delta}` : delta} us</button>
            `).join('')}
          </div>

          <div class="button-row wrap">
            <button class="button button-secondary" data-action="servo-home" data-channel="${profile.channel}" type="button">Home</button>
            <button class="button button-secondary" data-action="servo-enable" data-channel="${profile.channel}" type="button">Enable</button>
            <button class="button button-secondary" data-action="servo-disable" data-channel="${profile.channel}" type="button">Disable</button>
          </div>

          <div class="preset-button-grid">
            ${['wall', 'floor', 'hole'].map((preset) => `
              <div class="preset-row">
                <button class="button button-secondary" data-action="servo-move-state" data-channel="${profile.channel}" data-state="${preset}" type="button">Go ${preset}</button>
                <button class="button button-ghost" data-action="servo-capture-state" data-channel="${profile.channel}" data-state="${preset}" type="button">Capture ${preset}</button>
              </div>
            `).join('')}
          </div>
        </section>
      </div>
    </article>
  `;
}

function renderProfilesPanel() {
  if (!state.dashboard) return;
  const profilesSlot = document.getElementById('profiles-slot');
  profilesSlot.innerHTML = `
    <div class="panel-header">
      <div>
        <h2>Per-Servo Workbench</h2>
        <p>Each card combines local profile editing, named state management, and live motion controls.</p>
      </div>
    </div>
    <div class="panel-body servo-list">
      ${state.dashboard.servo.profiles.map((profile) => renderProfileCard(profile)).join('')}
    </div>
  `;
}

function renderEventsPanel() {
  if (!state.dashboard) return;
  const eventsSlot = document.getElementById('events-slot');
  const events = state.dashboard.events || [];
  eventsSlot.innerHTML = `
    <div class="panel-header">
      <div>
        <h2>Recent Activity</h2>
        <p>Newest hardware and stream events first.</p>
      </div>
    </div>
    <div class="panel-body">
      <div class="event-list">
        ${events.length ? events.map((entry) => `
          <article class="event-item">
            <div class="event-topline">
              <span class="status-pill ${entry.level === 'error' ? 'is-bad' : entry.level === 'warn' ? 'is-warn' : 'is-good'}">${escapeHtml(entry.level)}</span>
              <strong>${escapeHtml(entry.source)}</strong>
              <time>${escapeHtml(formatTime(entry.timestamp))}</time>
            </div>
            <p>${escapeHtml(entry.message)}</p>
          </article>
        `).join('') : '<div class="empty-state">No events yet.</div>'}
      </div>
    </div>
  `;
}

function render() {
  ensureShell();
  if (!state.dashboard) return;
  renderHeroMeta();
  renderBanner();
  renderMetrics();
  renderServoPanel();
  renderCameraPanel();
  renderProfilesPanel();
  renderEventsPanel();
}

function syncCameraStream(force = false) {
  if (!state.dashboard) return;
  const img = document.getElementById('camera-stream');
  if (!img) return;

  const { camera } = state.dashboard;
  const canStream = camera.backend_available && camera.available_devices.length > 0;
  if (!canStream) {
    img.removeAttribute('src');
    state.lastStreamKey = '';
    return;
  }

  const streamKey = [
    camera.device || 'auto',
    camera.resolved_device || '',
    camera.width,
    camera.height,
    camera.fps,
    camera.jpeg_quality,
  ].join('|');

  if (force || state.lastStreamKey !== streamKey || !img.getAttribute('src')) {
    state.lastStreamKey = streamKey;
    img.src = `/camera/stream.mjpg?ts=${Date.now()}`;
  }
}

function normalizeMaybeNumber(value) {
  return value === '' ? '' : Number(value);
}

function handleFieldInput(event) {
  const target = event.target;
  if (!(target instanceof HTMLInputElement || target instanceof HTMLSelectElement)) return;

  const channel = target.dataset.channel;
  const profileField = target.dataset.profileField;
  const stateField = target.dataset.stateField;
  const controlField = target.dataset.controlField;
  const cameraField = target.dataset.cameraField;
  const cycleField = target.dataset.cycleField;
  const globalField = target.dataset.globalField;

  if (channel && profileField) {
    const draft = state.profileDrafts[channel];
    draft[profileField] = target.type === 'checkbox' ? target.checked : target.value;
    state.dirtyProfiles.add(channel);
    return;
  }

  if (channel && stateField) {
    const draft = state.profileDrafts[channel];
    draft.states_us[stateField] = target.value;
    state.dirtyProfiles.add(channel);
    return;
  }

  if (channel && controlField) {
    const draft = state.profileDrafts[channel];
    draft[controlField] = normalizeMaybeNumber(target.value);
    const rangeField = target.closest('.range-field');
    const valueLabel = rangeField?.querySelector(`[data-value-for="${controlField}"]`);
    if (valueLabel) {
      valueLabel.textContent = `${target.value}${controlField === 'angle' ? ' deg' : ' us'}`;
    }

    if (target.type === 'range') {
      const peerInput = rangeField?.querySelector(`input.range-number[data-control-field="${controlField}"]`);
      if (peerInput) {
        peerInput.value = target.value;
      }
    } else if (target.classList.contains('range-number')) {
      const peerRange = rangeField?.querySelector(`input[type="range"][data-control-field="${controlField}"]`);
      if (peerRange) {
        peerRange.value = target.value;
      }
    }
    return;
  }

  if (cameraField) {
    state.cameraDirty = true;
    state.cameraDraft[cameraField] = target.value;
    return;
  }

  if (cycleField) {
    state.cycleDraft[cycleField] = normalizeMaybeNumber(target.value);
    return;
  }

  if (globalField === 'serial_port') {
    state.serialPortDraft = target.value;
  }
}

async function runPost(path, body = {}, { successMessage, clearProfileDirty = null, clearCameraDirty = false, forceStreamRefresh = false } = {}) {
  try {
    const payload = await apiRequest(path, {
      method: 'POST',
      body: JSON.stringify(body),
    });
    if (clearProfileDirty !== null) {
      state.dirtyProfiles.delete(String(clearProfileDirty));
    }
    if (clearCameraDirty) {
      state.cameraDirty = false;
    }
    mergeDashboard(payload.dashboard);
    setFlash(payload.message || successMessage || 'Action completed.', 'success');
    if (forceStreamRefresh) {
      syncCameraStream(true);
    }
  } catch (error) {
    if (error.dashboard) {
      mergeDashboard(error.dashboard);
    }
    setFlash(error.message, 'error', true);
  }
}

function buildProfilePayload(channel) {
  const draft = state.profileDrafts[String(channel)];
  return {
    name: draft.name,
    min_us: Number(draft.min_us),
    max_us: Number(draft.max_us),
    home_deg: Number(draft.home_deg),
    invert: Boolean(draft.invert),
    states_us: {
      wall: draft.states_us.wall === '' ? null : Number(draft.states_us.wall),
      floor: draft.states_us.floor === '' ? null : Number(draft.states_us.floor),
      hole: draft.states_us.hole === '' ? null : Number(draft.states_us.hole),
    },
  };
}

async function handleClick(event) {
  if (!(event.target instanceof Element)) return;
  const button = event.target.closest('[data-action]');
  if (!button) return;

  const { action, channel, delta, state: stateName } = button.dataset;
  event.preventDefault();

  if (action === 'refresh-dashboard') {
    await refreshDashboard(false);
    return;
  }

  if (action === 'connect-serial') {
    await runPost('/api/servo/connect', { port: state.serialPortDraft }, { successMessage: 'Connected to serial bridge.' });
    return;
  }

  if (action === 'disconnect-serial') {
    await runPost('/api/servo/disconnect', {}, { successMessage: 'Disconnected serial bridge.' });
    return;
  }

  if (action === 'apply-config') {
    await runPost('/api/servo/command', { action: 'apply_config' }, { successMessage: 'Applied config to Arduino.' });
    return;
  }

  if (action === 'save-config') {
    await runPost('/api/servo/command', { action: 'save_config' }, { successMessage: 'Saved config file.' });
    return;
  }

  if (action === 'home-all') {
    await runPost('/api/servo/command', { action: 'home_all' }, { successMessage: 'Homed all servos.' });
    return;
  }

  if (action === 'enable-all') {
    await runPost('/api/servo/command', { action: 'enable_all' }, { successMessage: 'Enabled all servos.' });
    return;
  }

  if (action === 'disable-all') {
    await runPost('/api/servo/command', { action: 'disable_all' }, { successMessage: 'Disabled all servos.' });
    return;
  }

  if (action === 'run-cycle') {
    await runPost(
      '/api/servo/command',
      { action: 'cycle_all', ...state.cycleDraft },
      { successMessage: 'Completed cycle-all run.' },
    );
    return;
  }

  if (action === 'apply-camera-config') {
    await runPost('/api/camera/config', state.cameraDraft, {
      successMessage: 'Updated camera configuration.',
      clearCameraDirty: true,
      forceStreamRefresh: true,
    });
    return;
  }

  if (action === 'restart-camera') {
    await runPost('/api/camera/restart', {}, {
      successMessage: 'Restarted camera stream.',
      forceStreamRefresh: true,
    });
    return;
  }

  if (action === 'save-profile') {
    await runPost(`/api/servo/profiles/${channel}`, buildProfilePayload(channel), {
      successMessage: `Saved channel ${channel} profile.`,
      clearProfileDirty: channel,
    });
    return;
  }

  if (action === 'save-apply-profile') {
    await runPost(
      `/api/servo/profiles/${channel}`,
      { ...buildProfilePayload(channel), apply_now: true },
      {
        successMessage: `Saved and applied channel ${channel} profile.`,
        clearProfileDirty: channel,
      },
    );
    return;
  }

  if (action === 'servo-angle') {
    await runPost('/api/servo/command', {
      action: 'angle',
      target: channel,
      angle: Number(state.profileDrafts[String(channel)].angle),
    }, { successMessage: `Moved channel ${channel} by angle.` });
    return;
  }

  if (action === 'servo-pulse') {
    await runPost('/api/servo/command', {
      action: 'pulse',
      target: channel,
      pulse_us: Number(state.profileDrafts[String(channel)].pulse_us),
    }, { successMessage: `Moved channel ${channel} by pulse.` });
    return;
  }

  if (action === 'servo-nudge') {
    await runPost('/api/servo/command', {
      action: 'nudge',
      target: channel,
      delta_us: Number(delta),
    }, { successMessage: `Nudged channel ${channel}.` });
    return;
  }

  if (action === 'servo-home') {
    await runPost('/api/servo/command', { action: 'home', target: channel }, { successMessage: `Homed channel ${channel}.` });
    return;
  }

  if (action === 'servo-enable') {
    await runPost('/api/servo/command', { action: 'enable', target: channel }, { successMessage: `Enabled channel ${channel}.` });
    return;
  }

  if (action === 'servo-disable') {
    await runPost('/api/servo/command', { action: 'disable', target: channel }, { successMessage: `Disabled channel ${channel}.` });
    return;
  }

  if (action === 'servo-move-state') {
    await runPost('/api/servo/command', {
      action: 'move_state',
      target: channel,
      state_name: stateName,
    }, { successMessage: `Moved channel ${channel} to ${stateName}.` });
    return;
  }

  if (action === 'servo-capture-state') {
    await runPost('/api/servo/command', {
      action: 'capture_state',
      target: channel,
      state_name: stateName,
    }, { successMessage: `Captured ${stateName} for channel ${channel}.` });
  }
}

async function refreshDashboard(showMessage = false) {
  try {
    const payload = await apiRequest('/api/dashboard');
    mergeDashboard(payload.dashboard);
    if (showMessage) {
      setFlash('Dashboard refreshed.', 'info');
    }
  } catch (error) {
    if (error.dashboard) {
      mergeDashboard(error.dashboard);
    }
    setFlash(error.message, 'error', true);
  }
}

function startPolling() {
  if (state.pollTimer) {
    window.clearInterval(state.pollTimer);
  }
  state.pollTimer = window.setInterval(() => {
    refreshDashboard(false);
  }, 2500);
}

async function bootstrap() {
  ensureShell();
  await refreshDashboard(false);
  startPolling();
}

bootstrap();
