const app = document.querySelector("#app");

const PALETTE = {
  gray: "#567DBB",
  shrek: "#F49400",
  blue: "#001FFF",
  red: "#FF0000",
  green: "#4DFF00",
  cyan: "#00FFFF",
  magenta: "#680056",
};

const LORE = {
  setup: {
    ken: "Start the table.",
    troll: "Stand clear.",
  },
  fault: {
    ken: "Call an attendant.",
    troll: "The table is paused.",
  },
  attract: {
    ken: "Choose a game.",
    troll: "Avoid the pits.",
  },
  initials: {
    ken: "Enter three letters.",
    troll: "Choose carefully.",
  },
  levelSelect: {
    ken: "Choose any game.",
    troll: "Avoid the pits.",
  },
  loading: {
    ken: "Stand clear while the table resets.",
    troll: "Wait for the board.",
  },
  placement: {
    ken: "Place the ball on cyan, then start.",
    troll: "Clear your hands.",
  },
  placementTiltTutorial: {
    ken: "Place the ball on cyan.",
    troll: "Clear your hands.",
  },
  playing: {
    ken: "Keep the ball moving.",
    troll: "Avoid red and green.",
  },
  survivalPlaying: {
    ken: "Touch new tiles and keep moving.",
    troll: "Avoid fallen tiles.",
  },
  survivalFail: {
    ken: "The ball fell into a pit.",
    troll: "Try again.",
  },
  timeUp: {
    ken: "Time expired.",
    troll: "Try again.",
  },
  levelClear: {
    ken: "Game complete.",
    troll: "Score recorded.",
  },
  levelScore: {
    ken: "Game score.",
    troll: "Choose another game.",
  },
  runSummary: {
    ken: "Game finished.",
    troll: "Choose another game.",
  },
  abandoned: {
    ken: "Game ended.",
    troll: "Choose another game.",
  },
  leaderboard: {
    ken: "High scores.",
    troll: "Play again.",
  },
  abandonOverlay: {
    ken: "End this game?",
    troll: "Your current run will stop.",
  },
  timerLow30: ["Thirty seconds left."],
  timerLow10: ["Ten seconds left."],
};

let game = null;
let attractChoice = 0;
let levelChoice = 0;
let initialsDraft = "AAA";
let initialsCursor = 0;
let abandonOpen = false;
let lastState = "";
let lastTimerSecond = null;
let lastTimerBand = null;
let requestInFlight = false;
let devBallCell = null;
let refreshInFlight = false;
let ballRefreshInFlight = false;
let liveBall = null;
let cabinetConfirmPresses = null;
let cabinetBackPresses = null;
let cabinetNavigationUp = null;
let cabinetNavigationDown = null;
const DEBUG_BALL_OVERLAY = new URLSearchParams(location.search).get("debug") === "1";
const {
  shiftInitials,
  backIntent,
  cabinetButtonIntent,
  cabinetNavigationKeys,
  cellKeyToCoordinates,
  ballOverlayVisible,
  initialsConfirmIntent,
} = window.ArcadeUiLogic;

function cabinetHint(kind, label) {
  const button = kind === "confirm" ? "RIGHT" : "LEFT";
  return `<span class="cabinet-hint ${kind}"><i class="pixel-button ${kind}" aria-hidden="true"></i><span>${button} ${escapeHtml(label)}</span></span>`;
}

function confirmHint(label) { return cabinetHint("confirm", label); }
function backHint(label = "BACK") { return cabinetHint("back", label); }
function joinHints(...hints) { return hints.filter(Boolean).join('<i class="hint-divider">·</i>'); }
function navigationHint(label = "CHOOSE") { return `<span class="navigation-hint">ROLL ↑↓ ${escapeHtml(label)}</span>`; }

function isSurvivalLevel(level = game?.level) {
  return level?.mode === "survival_lava";
}

function isTrackedMode(level = game?.level) {
  return ["survival_lava", "hex_fall", "target_hunt", "food_frenzy"].includes(level?.mode);
}

function levelTimerSeconds(level = game?.level) {
  return level?.survivalSeconds
    ?? level?.modeParams?.survivalSeconds
    ?? level?.modeParams?.roundSeconds
    ?? level?.timeLimitSeconds
    ?? null;
}

function cellKeyToRowCol(key) {
  return [Number(key.slice(1)) - 1, key.charCodeAt(0) - 65];
}

function rowColToCellKey(row, col) {
  if (row < 0 || row > 11 || col < 0 || col > 11) return null;
  return `${String.fromCharCode(65 + col)}${row + 1}`;
}

async function postBallCell(key) {
  if (!key) return;
  devBallCell = key;
  try {
    await fetch("/api/dev/ball-cell", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ key }),
    });
  } catch (_) {
    /* dev fallback — ignore network errors */
  }
}

class ArcadeAudio {
  constructor() {
    this.ctx = null;
    this.master = null;
    this.muted = false;
    this.musicTimer = null;
    this.musicStep = 0;
  }

  enable() {
    if (!this.ctx) {
      this.ctx = new (window.AudioContext || window.webkitAudioContext)();
      this.master = this.ctx.createGain();
      this.master.gain.value = 0.11;
      this.master.connect(this.ctx.destination);
    }
    if (this.ctx.state === "suspended") this.ctx.resume();
  }

  toggleMute() {
    this.enable();
    this.muted = !this.muted;
    this.master.gain.setTargetAtTime(this.muted ? 0 : 0.11, this.ctx.currentTime, 0.02);
  }

  tone(frequency, duration = 0.08, type = "square", volume = 0.7, delay = 0) {
    if (!this.ctx || this.muted) return;
    const start = this.ctx.currentTime + delay;
    const oscillator = this.ctx.createOscillator();
    const gain = this.ctx.createGain();
    oscillator.type = type;
    oscillator.frequency.setValueAtTime(frequency, start);
    gain.gain.setValueAtTime(0.0001, start);
    gain.gain.exponentialRampToValueAtTime(volume, start + 0.008);
    gain.gain.exponentialRampToValueAtTime(0.0001, start + duration);
    oscillator.connect(gain);
    gain.connect(this.master);
    oscillator.start(start);
    oscillator.stop(start + duration + 0.02);
  }

  click() { this.tone(620, 0.045, "square", 0.35); }
  confirm() {
    this.tone(440, 0.07, "square", 0.45);
    this.tone(660, 0.1, "square", 0.45, 0.07);
  }
  fail() {
    this.tone(180, 0.18, "sawtooth", 0.55);
    this.tone(120, 0.25, "sawtooth", 0.55, 0.16);
  }
  success() {
    [523, 659, 784, 1047].forEach((note, index) =>
      this.tone(note, 0.13, "square", 0.52, index * 0.09));
  }
  start() {
    [220, 330, 440].forEach((note, index) =>
      this.tone(note, 0.1, "square", 0.45, index * 0.07));
  }
  warning() { this.tone(880, 0.045, "square", 0.28); }
  trollTaunt() { this.tone(140, 0.12, "sawtooth", 0.4); }

  setMusic(active) {
    if (!active) {
      clearInterval(this.musicTimer);
      this.musicTimer = null;
      return;
    }
    if (this.musicTimer) return;
    const notes = [110, 165, 196, 165, 123, 165, 220, 196];
    this.musicTimer = setInterval(() => {
      if (!this.ctx || this.muted) return;
      this.tone(notes[this.musicStep % notes.length], 0.09, "triangle", 0.15);
      this.musicStep += 1;
    }, 260);
  }
}

const audio = new ArcadeAudio();

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function gauntletTotal() {
  return game?.gauntletLevelCount || game?.catalog?.gauntletLevelCount || 2;
}

function pickLine(pool, seed = 0) {
  if (!pool?.length) return "";
  return pool[Math.abs(seed) % pool.length];
}

function timerTrollLine(remaining) {
  if (remaining <= 10) return pickLine(LORE.timerLow10, remaining);
  if (remaining <= 30) return pickLine(LORE.timerLow30, remaining);
  return game?.level?.trollLine || LORE.playing.troll;
}

function brand() {
  return `<span class="brand"><span class="brand-bars"><i></i><i></i><i></i></span>TILTYTABLE</span>`;
}

function hardwareStatus() {
  const hw = game?.hardware || {};
  const css = hw.error ? "error" : hw.busy ? "busy" : "";
  const label = hw.error ? "CALL ATTENDANT" : hw.busy ? "TILES MOVING" : hw.ready ? "READY" : "STANDBY";
  return `<span class="status"><i class="status-dot ${css}"></i>${label}</span>`;
}

function ballTrackOverlay() {
  const tracking = game?.integrations?.tracking;
  const kinectActive = Boolean(tracking?.enabled);
  if (!ballOverlayVisible(DEBUG_BALL_OVERLAY, game?.state)) return "";
  if (!game?.ball) return "";

  const ball = game.ball;
  const coordinates = Number.isInteger(ball.col) && Number.isInteger(ball.row)
    ? `(${ball.col},${ball.row})`
    : "—";
  const conf = Number(ball.confidence ?? 0);
  const dimmed = !kinectActive;
  const confClass = conf >= 0.75 ? "ok" : conf >= 0.4 ? "warn" : "low";
  const latency = ball.latency?.captureToUiMs ?? ball.latency?.captureToGameMs;
  const averageLatency = ball.latency?.averageCaptureToGameMs;
  const p95Latency = ball.latency?.p95CaptureToGameMs;
  const latencyText = Number.isFinite(latency)
    ? `${latency.toFixed(0)}ms avg ${averageLatency?.toFixed(0) ?? "—"} p95 ${p95Latency?.toFixed(0) ?? "—"}`
    : "—";

  return `
    <aside class="ball-track-overlay ${dimmed ? "dimmed" : ""}" aria-hidden="true">
      <span class="ball-track-label">BALL (X,Y)</span>
      <span class="ball-track-cell">${escapeHtml(coordinates)}</span>
      <span class="ball-track-conf ${confClass}">${conf.toFixed(1)}</span>
      <span class="ball-track-latency">${latencyText}</span>
    </aside>`;
}

function updateBallTrackOverlay(ball, trackingEnabled) {
  if (!ball) return;
  liveBall = ball;
  if (game) game.ball = ball;
  const overlay = document.querySelector(".ball-track-overlay");
  if (!overlay) return;

  overlay.classList.toggle("dimmed", !trackingEnabled);
  const coordinates = Number.isInteger(ball.col) && Number.isInteger(ball.row)
    ? `(${ball.col},${ball.row})`
    : "—";
  const confidence = Number(ball.confidence ?? 0);
  const confidenceNode = overlay.querySelector(".ball-track-conf");
  overlay.querySelector(".ball-track-cell").textContent = coordinates;
  confidenceNode.textContent = confidence.toFixed(1);
  confidenceNode.className = `ball-track-conf ${confidence >= 0.75 ? "ok" : confidence >= 0.4 ? "warn" : "low"}`;
  const latency = ball.latency?.captureToUiMs ?? ball.latency?.captureToGameMs;
  const average = ball.latency?.averageCaptureToGameMs;
  const p95 = ball.latency?.p95CaptureToGameMs;
  overlay.querySelector(".ball-track-latency").textContent = Number.isFinite(latency)
    ? `${latency.toFixed(0)}ms avg ${average?.toFixed(0) ?? "—"} p95 ${p95?.toFixed(0) ?? "—"}`
    : "—";
}

function dialogue(kenText, trollText, compact = false) {
  void kenText;
  void trollText;
  void compact;
  return "";
}

function shell(content, controls = "", dialogueHtml = "") {
  return `
    <section class="scene">
      <header class="topbar">${brand()}${hardwareStatus()}</header>
      <div class="scene-center">${content}</div>
      <footer class="footer">
        <div class="control-strip">${controls}</div>
      </footer>
    </section>
    ${abandonOpen ? abandonOverlay() : ""}
    ${ballTrackOverlay()}
  `;
}

function abandonOverlay() {
  return `
    <div class="overlay">
      <article class="message-card">
        <h1>END RUN?</h1>
        <p class="decision-copy">Choose with the cabinet buttons</p>
        <div class="cabinet-decision">
          <div class="cabinet-choice confirm">${confirmHint("END RUN")}</div>
          <div class="cabinet-choice back">${backHint("KEEP PLAYING")}</div>
        </div>
        ${dialogue(LORE.abandonOverlay.ken, LORE.abandonOverlay.troll, true)}
      </article>
    </div>`;
}

function renderSetup() {
  const fault = game.state === "hardware_fault";
  const lore = fault ? LORE.fault : LORE.setup;
  return shell(`
    <article class="setup-card">
      <h1>${fault ? "GAME PAUSED" : "TILTYTABLE"}</h1>
      <p class="hero-sub">${fault ? "Call an attendant." : "Choose a game and play."}</p>
    </article>`,
    confirmHint(fault ? "TRY AGAIN" : "START"),
    dialogue(lore.ken, lore.troll, true));
}

function leaderboardRows(limit = 8) {
  const rows = (game.leaderboard || []).slice(0, limit);
  const total = gauntletTotal();
  if (!rows.length) {
    return `<p class="empty-score">No scores yet — be the first to free Tilty.</p>`;
  }
  return rows.map((row, index) => `
    <div class="score-row">
      <span class="rank">${String(index + 1).padStart(2, "0")}</span>
      <span class="initials">${escapeHtml(row.initials)}</span>
      <span class="points">${Number(row.score).toLocaleString()}</span>
      <span class="cleared">${row.levelsCleared}/${row.gauntletLevelCount || total}</span>
    </div>`).join("");
}

function renderAttract() {
  return renderLevelSelect();
}

function renderInitials() {
  const chars = initialsDraft.padEnd(3, "A").slice(0, 3).split("");
  return shell(`
    <div>
      <h1>YOUR MARK</h1>
      <p class="hero-sub">Three letters for the escape board</p>
      <p class="initial-progress">LETTER ${initialsCursor + 1} OF 3</p>
      <div class="initials-picker">
        ${chars.map((char, index) => `
          <div class="initial-wheel">
            <div class="initial-box ${index === initialsCursor ? "active" : ""}">${escapeHtml(char)}</div>
          </div>`).join("")}
      </div>
    </div>`,
    joinHints(
      navigationHint("CHANGE LETTER"),
      confirmHint(initialsCursor < 2 ? "NEXT LETTER" : "LOCK IN"),
      backHint(),
    ),
    dialogue(LORE.initials.ken, LORE.initials.troll, true));
}

function renderLevelSelect() {
  return shell(`
    <div style="width:100%">
      <h1 class="screen-title">SELECT GAME</h1>
      <p class="hero-sub">${game.levels.length} game modes</p>
      <div class="menu level-select-menu">
        ${game.levels.map((level, index) => `
          <div class="menu-item ${levelChoice === index ? "selected" : ""}">
            <strong>${String(level.number).padStart(2, "0")} ${escapeHtml(level.title)}</strong>
            <span class="menu-sub">${escapeHtml(level.subtitle)}</span>
          </div>`).join("")}
      </div>
    </div>`,
    joinHints(navigationHint(), confirmHint("SELECT")),
    dialogue(LORE.levelSelect.ken, LORE.levelSelect.troll, true));
}

function legendItem(color, label, detail, extraClass = "") {
  return `
    <div class="legend-item">
      <i class="legend-swatch ${color} ${extraClass}"></i>
      <div><strong>${label}</strong><span>${detail}</span></div>
    </div>`;
}

function modeLegend(level) {
  const common = legendItem("cyan", "CYAN", "Start here");
  if (level.mode === "survival_lava") {
    return common
      + legendItem("gray", "GRAY", "Open floor")
      + legendItem("orange", "ORANGE", "Touched + points")
      + legendItem("red", "BLINKING RED", "Move now", "blinking")
      + legendItem("red", "SOLID RED", "Pit / lose", "pit");
  }
  if (level.mode === "hex_fall") {
    return common
      + legendItem("gray", "GRAY", "Open floor")
      + legendItem("blue", "BLUE", "Claimed +1")
      + legendItem("red", "BLINKING RED", "Falling soon", "blinking")
      + legendItem("red", "SOLID RED", "Pit / lose", "pit");
  }
  if (level.mode === "food_frenzy") {
    return common
      + legendItem("gray", "GRAY", "Open floor")
      + legendItem("blue", "FLASHING BLUE", "Food +1", "blinking")
      + legendItem("white", "WHITE FLASH", "Round clear");
  }
  return common
    + legendItem("gray", "GRAY", "Open floor")
    + legendItem("blue", "FLASHING BLUE", "Food +1", "blinking")
    + legendItem("green", "GREEN", "Wall")
    + legendItem("red", "RED", "Pit / lose", "pit");
}

function renderRules() {
  const level = game.level;
  return shell(`
    <div class="rules-layout">
      <aside class="mode-legend">
        <p class="legend-title">LED GUIDE</p>
        ${modeLegend(level)}
      </aside>
      <div class="rules-copy">
        <h1>${escapeHtml(level.title)}</h1>
        <p class="feature">${escapeHtml(level.feature)}</p>
        ${level.rules.map((rule, index) =>
          `<div class="rule"><b>0${index + 1}</b><span>${escapeHtml(rule)}</span></div>`
        ).join("")}
      </div>
    </div>`,
    joinHints(
      confirmHint("CONTINUE"),
      backHint("GAMES"),
    ),
    dialogue(level.kenLine || LORE.playing.ken, level.trollLine || LORE.playing.troll));
}

function renderLoading() {
  const restarting = game.state === "restarting";
  const applying = game.hardware?.loadPhase === "applying";
  return shell(`
    <div>
      <p class="kicker">GAME ${game.level.number}</p>
      <h1 class="screen-title">${restarting || applying ? "RESETTING BOARD" : "GET READY"}</h1>
      <div class="loading-bars"><i></i><i></i><i></i><i></i><i></i><i></i></div>
      <p class="decision-copy">${applying ? "MOVING ALL 144 TILES · ABOUT 15 SECONDS" : escapeHtml(game.level.title)}</p>
    </div>`,
    `STAND CLEAR`,
    dialogue(LORE.loading.ken, game.level?.trollLine || LORE.loading.troll, true));
}

function tileClass(cell) {
  if (cell.key === game.level.startCell && game.state !== "playing") return "start";
  if (!isTrackedMode() && cell.key === game.level.endCell) return "finish";
  if (cell.sunk || (isTrackedMode() && cell.value === -1)) return "trap";
  const color = String(cell.color || "").toUpperCase();
  if (color === PALETTE.shrek || color === "#FF8C00" || color === "#F49400") return "path";
  if (color === PALETTE.blue || color === "#3366FF") return "points";
  if (color === PALETTE.gray || color === "#C8D0D8") return "floor";
  if (cell.value === 1 || color === PALETTE.green || color === "#00E050") return "wall";
  if (cell.value === -1 || color === PALETTE.red || color === "#FF1A1A") return "trap";
  return "";
}

function sortedCells() {
  return [...(game.mapCells || [])].sort((a, b) => {
    const parse = key => [Number(key.slice(1)), key.charCodeAt(0)];
    const [ar, ac] = parse(a.key);
    const [br, bc] = parse(b.key);
    return ar - br || ac - bc;
  });
}

function boardMarkup(waiting = false) {
  const celebrating = Boolean(game.modeState?.celebrating);
  return `<div class="board-wrap ${celebrating ? "celebrating" : ""}"><div class="board">${
    sortedCells().map(cell => {
      const classes = ["tile", tileClass(cell)];
      if (cell.dynamic) classes.push("dynamic");
      if (cell.dynamicType === "delayed_trap") classes.push("delayed-trap");
      if (waiting && cell.key === game.level.startCell) classes.push("waiting");
      if (waiting && cell.blinkUntilPlay) classes.push("waiting", "points");
      return `<i class="${classes.join(" ")}" title="${cellKeyToCoordinates(cell.key)}"></i>`;
    }).join("")
  }</div></div>`;
}

function placementDialogue(level) {
  const hasBlinkFloor = (game.mapCells || []).some(cell => cell.blinkUntilPlay);
  if (level.number === 1 && hasBlinkFloor) {
    return dialogue(
      level.kenLine || LORE.placementTiltTutorial.ken,
      level.trollLine || LORE.placementTiltTutorial.troll,
      true,
    );
  }
  return dialogue(LORE.placement.ken, level.trollLine || LORE.placement.troll, true);
}

function renderPlacement() {
  const level = game.level;
  const hasBlinkFloor = (game.mapCells || []).some(cell => cell.blinkUntilPlay);
  const ready = Boolean(game.placementReady);
  const instruction = ready
    ? `Ball found on cyan <strong>${cellKeyToCoordinates(level.startCell)}</strong>`
    : hasBlinkFloor
      ? `Set the ball on cyan <strong>${cellKeyToCoordinates(level.startCell)}</strong> — blue rises on start`
      : `Set the ball on cyan <strong>${cellKeyToCoordinates(level.startCell)}</strong>`;
  return shell(`
    <div class="game-layout">
      ${boardMarkup(true)}
      <div class="hud">
        <p class="hud-level">GAME ${level.number} · ${escapeHtml(level.title)}</p>
        <h1>PLACE<br>THE BALL</h1>
        <p class="hud-instruction">${instruction}</p>
        ${level.mode === "target_hunt" ? "" : `
          <div class="hud-stats single">
            <div class="hud-stat"><span>TIME LIMIT</span><strong>${levelTimerSeconds(level)}s</strong></div>
          </div>`}
      </div>
    </div>`,
    joinHints(`<span>ROLL TO TILT</span>`, confirmHint("START"), backHint("END RUN")),
    placementDialogue(level));
}

function renderPlaying() {
  const remaining = game.timer.remainingSeconds;
  const level = game.level;
  const survival = isSurvivalLevel();
  const hex = level.mode === "hex_fall";
  const hunt = level.mode === "target_hunt";
  const frenzy = level.mode === "food_frenzy";
  const tracked = isTrackedMode();
  const visited = game.survival?.tilesVisited ?? 0;
  const heating = Boolean(game.survival?.heating);
  const modeState = game.modeState || {};
  const openTiles = (game.mapCells || []).filter(cell => Number(cell.value) === 0).length;
  const kenLine = hunt
    ? "Reach the flashing blue food."
    : frenzy
      ? "Collect every flashing food before time expires."
    : hex
      ? "Touch new tiles. Move away from red flashes."
      : survival
    ? (heating ? "A touched tile is about to fall." : LORE.survivalPlaying.ken)
    : (remaining <= 30
      ? (remaining <= 10 ? "Ten seconds left." : "Thirty seconds left.")
      : (level.kenLine || LORE.playing.ken));
  const trollLine = tracked ? "Avoid the pits." : timerTrollLine(remaining);
  const modeLabel = hunt
    ? "SNAKE"
    : frenzy
      ? `FOOD FRENZY · ROUND ${modeState.round || 1}`
      : hex
        ? "HEX-A-FALL"
        : survival
          ? "LAVA SURVIVAL"
          : null;
  const instruction = hunt
    ? `Food <strong>${cellKeyToCoordinates(modeState.targetCell)}</strong> · points ${modeState.targetsReached || 0}`
    : frenzy
      ? modeState.celebrating
        ? `Round cleared · next round adds one more food`
        : `Food left <strong>${(modeState.targetCells || []).length}</strong> · collected ${modeState.foodsCollected || 0}`
    : hex
      ? `Tiles touched <strong>${modeState.tilesTouched || 0}</strong> · floor ${openTiles}`
      : survival
        ? `Tiles touched <strong>${visited}</strong> · +${level.pointsPerTile || 0} each`
        : `Reach magenta <strong>${cellKeyToCoordinates(level.endCell)}</strong>`;
  const footer = joinHints(
    `<span>ROLL TO TILT</span>`,
    confirmHint("UNSTICK"),
    backHint("END RUN"),
  );
  return shell(`
    <div class="game-layout">
      ${boardMarkup(false)}
      <div class="hud">
        <p class="hud-level">GAME ${level.number} · ${escapeHtml(level.title)}</p>
        ${modeLabel ? `<p class="hud-kicker ${heating ? "danger" : ""}">${modeLabel}</p>` : ""}
        ${hunt ? "" : `<div class="timer ${remaining <= 10 ? "danger" : remaining <= 20 ? "warn" : ""}">${String(remaining).padStart(2, "0")}</div>`}
        <div class="hud-stats">
          <div class="hud-stat"><span>RUN SCORE</span><strong>${Number(game.score).toLocaleString()}</strong></div>
          ${tracked ? `<div class="hud-stat"><span>${hunt || frenzy ? "FOOD" : "TILES"}</span><strong>${hunt ? (modeState.targetsReached || 0) : frenzy ? (modeState.foodsCollected || 0) : hex ? (modeState.tilesTouched || 0) : visited}</strong></div>` : ""}
        </div>
        <p class="hud-instruction">${instruction}</p>
      </div>
    </div>`,
    footer,
    dialogue(kenLine, trollLine, true));
}

function renderSurvivalFail() {
  const timedOut = game.level?.mode === "food_frenzy";
  return shell(`
    <article class="message-card">
      <p class="kicker">Game ${game.level.number}</p>
      <h1 style="color:var(--red)">${timedOut ? "TIME!" : "PIT!"}</h1>
      <p class="decision-copy">${timedOut ? "The feast got away." : "The ball fell through the floor."}</p>
      <p class="result-number">${Number(game.score).toLocaleString()} PTS</p>
    </article>`,
    joinHints(confirmHint("TRY AGAIN"), backHint("END RUN")),
    dialogue(LORE.survivalFail.ken, game.level?.trollLine || LORE.survivalFail.troll, true));
}

function renderTimeUp() {
  return shell(`
    <article class="message-card">
      <p class="kicker">Chamber ${game.level.number}</p>
      <h1 style="color:var(--red)">TIME UP</h1>
      <p class="result-number">−100</p>
    </article>`,
    joinHints(confirmHint("TRY AGAIN"), backHint("END RUN")),
    dialogue(LORE.timeUp.ken, game.level?.trollLine || LORE.timeUp.troll, true));
}

function renderLevelClear() {
  const survival = ["survival_lava", "hex_fall"].includes(game.level?.mode);
  const sub = survival
    ? "Timer cleared"
    : `${game.lastLevelResult.remainingSeconds}s left`;
  return shell(`
    <article class="message-card">
      <p class="kicker">GAME ${game.level.number}</p>
      <h1>${survival ? "SURVIVED!" : "CLEAR!"}</h1>
      <p class="result-number">+${Number(game.lastLevelResult.score).toLocaleString()}</p>
      <p class="decision-copy">${sub}</p>
    </article>`,
    confirmHint("CONTINUE"),
    dialogue(LORE.levelClear.ken, LORE.levelClear.troll, true));
}

function renderLevelScore() {
  const result = game.lastLevelResult;
  const total = gauntletTotal();
  const isLastGauntlet = game.mode === "gauntlet" && result.levelNumber >= total;
  const nextLabel = game.mode !== "gauntlet"
    ? "GAME SELECT"
    : (isLastGauntlet ? "FINAL SCORE" : "NEXT CHAMBER");
  const resultLevel = game.levels.find(l => l.id === result.levelId);
  const survival = ["survival_lava", "hex_fall"].includes(resultLevel?.mode);
  const touchedTiles = resultLevel?.mode === "hex_fall"
    ? (game.modeState?.tilesTouched ?? "—")
    : (game.survival?.tilesVisited ?? "—");
  const pointsPerTile = resultLevel?.mode === "hex_fall"
    ? (resultLevel?.modeParams?.pointsPerTile || 0)
    : (resultLevel?.pointsPerTile || 0);
  const survivalThirdStat = "<div><span>Timer</span><strong>CLEARED</strong></div>";
  const breakdown = survival
    ? `<div class="result-grid">
        <div><span>Tiles touched</span><strong>${touchedTiles}</strong></div>
        <div><span>Points / tile</span><strong>${pointsPerTile}</strong></div>
        ${survivalThirdStat}
      </div>`
    : `<div class="result-grid">
        <div><span>Clear</span><strong>1,000</strong></div>
        <div><span>Time bonus</span><strong>+${result.remainingSeconds * 10}</strong></div>
        <div><span>Finish</span><strong>CLEARED</strong></div>
      </div>`;
  return shell(`
    <article class="message-card">
      <p class="kicker">Game ${result.levelNumber} score</p>
      <h1>${Number(result.score).toLocaleString()} PTS</h1>
      ${breakdown}
      <p class="next-action-label">${escapeHtml(nextLabel)}</p>
    </article>`,
    confirmHint("CONTINUE"),
    dialogue(LORE.levelScore.ken, LORE.levelScore.troll, true));
}

function renderSummary() {
  const directSelect = game.mode !== "gauntlet";
  const total = gauntletTotal();
  const headline = directSelect
    ? game.level.title
    : `${game.levelsCleared}/${total} GAMES`;
  const next = directSelect ? "GAME SELECT" : "LEADERBOARD";
  return shell(`
    <article class="message-card">
      <p class="kicker">${directSelect ? "GAME COMPLETE" : (game.endedEarly ? "RUN ENDED" : "RUN COMPLETE")}</p>
      <h1>${headline}</h1>
      <p class="result-number">${Number(game.score).toLocaleString()}</p>
      <p class="decision-copy">Choose another game or replay.</p>
      <p class="next-action-label">${escapeHtml(next)}</p>
    </article>`,
    confirmHint("CONTINUE"),
    dialogue(LORE.runSummary.ken, LORE.runSummary.troll, true));
}

function renderAbandoned() {
  return shell(`
    <article class="message-card">
      <h1>GAME ENDED</h1>
      <p class="decision-copy">Choose another game.</p>
    </article>`,
    confirmHint("CONTINUE"),
    dialogue(LORE.abandoned.ken, LORE.abandoned.troll, true));
}

function renderLeaderboard() {
  return shell(`
    <div style="width:min(670px,90vw)">
      <h1 class="screen-title">ESCAPE BOARD</h1>
      <aside class="leader-card" style="margin-top:16px">${leaderboardRows(10)}</aside>
    </div>`,
    confirmHint("TITLE"),
    dialogue(LORE.leaderboard.ken, LORE.leaderboard.troll, true));
}

function render() {
  if (!game) return;
  document.body.dataset.gameState = game.state;
  const renderers = {
    setup: renderSetup,
    hardware_fault: renderSetup,
    attract: renderAttract,
    initials: renderInitials,
    level_select: renderLevelSelect,
    rules: renderRules,
    level_loading: renderLoading,
    restarting: renderLoading,
    placement: renderPlacement,
    playing: renderPlaying,
    time_up: renderTimeUp,
    survival_fail: renderSurvivalFail,
    level_clear: renderLevelClear,
    level_score: renderLevelScore,
    abandoned: renderAbandoned,
    run_summary: renderSummary,
    leaderboard: renderLeaderboard,
  };
  app.innerHTML = (renderers[game.state] || renderSetup)();
}

async function postAction(action, extra = {}) {
  if (requestInFlight) return;
  requestInFlight = true;
  try {
    const response = await fetch("/api/action", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action, ...extra }),
    });
    const payload = await response.json();
    if (!payload.ok) throw new Error(payload.error || "Action failed");
    game = payload.game;
    handleStateAudio();
    render();
  } catch (error) {
    console.error(error);
  } finally {
    requestInFlight = false;
  }
}

async function refresh() {
  if (requestInFlight || refreshInFlight) return;
  refreshInFlight = true;
  try {
    const response = await fetch("/api/state", { cache: "no-store" });
    const payload = await response.json();
    game = payload.game;
    if (liveBall) game.ball = liveBall;
    handleStateAudio();
    render();
    handleCabinetButtons();
  } catch (error) {
    console.error("State refresh failed", error);
  } finally {
    refreshInFlight = false;
  }
}

async function refreshBall() {
  if (ballRefreshInFlight) return;
  ballRefreshInFlight = true;
  try {
    const response = await fetch("/api/ball", { cache: "no-store" });
    const payload = await response.json();
    if (payload.ok) updateBallTrackOverlay(payload.ball, payload.trackingEnabled);
  } catch (error) {
    console.error("Ball refresh failed", error);
  } finally {
    ballRefreshInFlight = false;
  }
}

function handleCabinetButtons() {
  const tilt = game?.integrations?.tilt;
  if (!tilt?.enabled) {
    cabinetConfirmPresses = null;
    cabinetBackPresses = null;
    cabinetNavigationUp = null;
    cabinetNavigationDown = null;
    return;
  }
  const nextConfirm = Number(tilt.confirmPresses || 0);
  const nextBack = Number(tilt.backPresses || 0);
  const nextUp = Number(tilt.navigationUp || 0);
  const nextDown = Number(tilt.navigationDown || 0);
  if (
    cabinetConfirmPresses === null
    || cabinetBackPresses === null
    || cabinetNavigationUp === null
    || cabinetNavigationDown === null
  ) {
    cabinetConfirmPresses = nextConfirm;
    cabinetBackPresses = nextBack;
    cabinetNavigationUp = nextUp;
    cabinetNavigationDown = nextDown;
    return;
  }
  const intent = cabinetButtonIntent(
    cabinetConfirmPresses,
    cabinetBackPresses,
    nextConfirm,
    nextBack,
  );
  const navigationKeys = cabinetNavigationKeys(
    cabinetNavigationUp,
    cabinetNavigationDown,
    nextUp,
    nextDown,
  );
  cabinetConfirmPresses = nextConfirm;
  cabinetBackPresses = nextBack;
  cabinetNavigationUp = nextUp;
  cabinetNavigationDown = nextDown;
  if (intent === "back") {
    document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
  } else if (intent === "confirm") {
    document.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter" }));
  } else {
    navigationKeys.forEach(key => {
      document.dispatchEvent(new KeyboardEvent("keydown", { key }));
    });
  }
}

function handleStateAudio() {
  if (!game) return;
  if (game.state !== lastState) {
    if (game.state === "initials") {
      initialsDraft = "AAA";
      initialsCursor = 0;
    }
    if (game.state === "playing") {
      audio.start();
      if (!game.integrations?.tracking?.enabled) {
        devBallCell = game.level.startCell;
        postBallCell(devBallCell);
      }
    }
    else if (game.state === "level_clear") audio.success();
    else if (game.state === "time_up" || game.state === "survival_fail" || game.state === "abandoned") audio.fail();
    else if (game.state === "hardware_fault") audio.fail();
    else if (lastState) audio.confirm();
    lastState = game.state;
    lastTimerBand = null;
  }
  audio.setMusic(["attract", "initials", "rules", "leaderboard"].includes(game.state));
  if (game.state === "playing" && game.timer.running) {
    const second = game.timer.remainingSeconds;
    if (second !== lastTimerSecond && second <= 10) audio.warning();
    if (second !== lastTimerSecond) {
      const band = second <= 10 ? 10 : second <= 30 ? 30 : null;
      if (band && band !== lastTimerBand) {
        audio.trollTaunt();
        lastTimerBand = band;
      }
    }
    lastTimerSecond = second;
  } else {
    lastTimerSecond = null;
    lastTimerBand = null;
  }
}

function handleBack() {
  if (!game) return;
  const intent = backIntent(game.state, abandonOpen, game.mode);
  if (intent === "close-overlay") {
    abandonOpen = false;
    audio.click();
    render();
  } else if (intent === "abandon") {
    postAction("abandon");
  } else if (intent === "continue") {
    postAction("continue");
  } else if (intent === "level-select") {
    postAction("show-level-select");
  } else if (intent === "open-overlay") {
    abandonOpen = true;
    audio.click();
    render();
  }
}

app.addEventListener("contextmenu", event => {
  event.preventDefault();
});

document.addEventListener("keydown", event => {
  audio.enable();
  const key = event.key;
  if (key.toLowerCase() === "m") {
    audio.toggleMute();
    render();
    return;
  }
  if (!game) return;
  if (["ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight", " ", "Enter"].includes(key)) {
    event.preventDefault();
  }

  if (abandonOpen) {
    if (key === "Escape") {
      handleBack();
    } else if (key === "Enter") {
      abandonOpen = false;
      audio.fail();
      postAction("abandon");
    }
    return;
  }

  if (key === "Escape") {
    handleBack();
    return;
  }

  switch (game.state) {
    case "setup":
    case "hardware_fault":
      if (key === "Enter") postAction("setup");
      break;
    case "attract":
      if (key === "Enter") postAction("show-level-select");
      break;
    case "initials":
      if (key === "ArrowUp" || key === "ArrowDown") {
        initialsDraft = shiftInitials(
          initialsDraft,
          initialsCursor,
          key === "ArrowUp" ? 1 : -1,
        );
        audio.click();
        render();
      } else if (key === "ArrowLeft" || key === "ArrowRight") {
        initialsCursor = Math.max(
          0,
          Math.min(2, initialsCursor + (key === "ArrowRight" ? 1 : -1)),
        );
        audio.click();
        render();
      } else if (/^[a-z]$/i.test(key)) {
        const letters = initialsDraft.padEnd(3, "A").slice(0, 3).split("");
        letters[initialsCursor] = key.toUpperCase();
        initialsDraft = letters.join("");
        initialsCursor = Math.min(2, initialsCursor + 1);
        audio.click();
        render();
      } else if (key === "Backspace") {
        initialsCursor = Math.max(0, initialsCursor - 1);
        initialsDraft = shiftInitials(initialsDraft, initialsCursor, 0);
        audio.click();
        render();
      } else if (key === "Enter") {
        if (initialsConfirmIntent(initialsCursor) === "next") {
          initialsCursor += 1;
          audio.confirm();
          render();
        } else {
          postAction("set-initials", { initials: initialsDraft });
        }
      }
      break;
    case "level_select":
      if (key === "ArrowUp") {
        levelChoice = (levelChoice + game.levels.length - 1) % game.levels.length;
        audio.click();
        render();
      } else if (key === "ArrowDown") {
        levelChoice = (levelChoice + 1) % game.levels.length;
        audio.click();
        render();
      } else if (key === "Enter") {
        postAction("select-level", { levelId: game.levels[levelChoice].id });
      }
      break;
    case "rules":
    case "time_up":
    case "survival_fail":
    case "level_clear":
    case "level_score":
    case "run_summary":
    case "leaderboard":
    case "abandoned":
      if (key === "Enter" || key === " ") postAction("continue");
      break;
    case "placement":
      if (key === "Enter" || key === " ") postAction("confirm-placement");
      break;
    case "playing":
      if (key === "Enter" || key === " ") {
        postAction("unstick");
        break;
      }
      if (!game.integrations?.tracking?.enabled) {
        const current = devBallCell || game.ball?.cell || game.level.startCell;
        const [row, col] = cellKeyToRowCol(current);
        let next = null;
        if (key === "ArrowUp") next = rowColToCellKey(row - 1, col);
        else if (key === "ArrowDown") next = rowColToCellKey(row + 1, col);
        else if (key === "ArrowLeft") next = rowColToCellKey(row, col - 1);
        else if (key === "ArrowRight") next = rowColToCellKey(row, col + 1);
        if (next) {
          audio.click();
          postBallCell(next);
          render();
        } else if (key.toLowerCase() === "c") {
          postBallCell(game.level.endCell);
        } else if (key.toLowerCase() === "r") {
          postAction("restart");
        }
      } else if (key.toLowerCase() === "r") {
        postAction("restart");
      }
      break;
  }
});

refresh();
refreshBall();
setInterval(refresh, 50);
setInterval(refreshBall, 16);
