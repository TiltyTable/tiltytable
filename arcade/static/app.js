const app = document.querySelector("#app");

let game = null;
let attractChoice = 0;
let levelChoice = 0;
let initialsDraft = "";
let abandonOpen = false;
let lastState = "";
let lastTimerSecond = null;
let requestInFlight = false;

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

function brand() {
  return `<span class="brand"><span class="brand-bars"><i></i><i></i><i></i></span>TILTYTABLE</span>`;
}

function hardwareStatus() {
  const hw = game?.hardware || {};
  const css = hw.error ? "error" : hw.busy ? "busy" : "";
  const label = hw.error ? "HELP NEEDED" : hw.busy ? "PLEASE WAIT" : hw.ready ? "READY" : "NOT READY";
  return `<span class="status"><i class="status-dot ${css}"></i>${label}</span>`;
}

function shell(content, controls = "") {
  return `
    <section class="scene">
      <header class="topbar">${brand()}${hardwareStatus()}</header>
      <div class="scene-center">${content}</div>
      <footer class="footer">
        <span>${controls}</span>
        <span>${audio.muted ? "AUDIO OFF" : "AUDIO ON"} <span class="key">M</span></span>
      </footer>
    </section>
    ${abandonOpen ? abandonOverlay() : ""}
  `;
}

function abandonOverlay() {
  return `
    <div class="overlay">
      <article class="message-card">
        <h1>END RUN?</h1>
        <p class="decision-copy">CLEARED LEVELS WILL BE SAVED</p>
        <p class="prompt"><span class="key">ENTER</span> END &nbsp; <span class="key">ESC</span> KEEP PLAYING</p>
      </article>
    </div>`;
}

function renderSetup() {
  const fault = game.state === "hardware_fault";
  return shell(`
    <article class="setup-card">
      <h1>${fault ? "GAME PAUSED" : "TILTYTABLE"}</h1>
      ${fault ? `<p class="decision-copy">CALL AN ATTENDANT</p>` : ""}
      <p class="prompt"><span class="key">ENTER</span> ${fault ? "TRY AGAIN" : "START"}</p>
    </article>`, `<span class="key">ENTER</span> ${fault ? "TRY AGAIN" : "START"}`);
}

function leaderboardRows(limit = 8) {
  const rows = (game.leaderboard || []).slice(0, limit);
  if (!rows.length) return `<p class="empty-score">NO SCORES YET — BE THE FIRST.</p>`;
  return rows.map((row, index) => `
    <div class="score-row">
      <span class="rank">${String(index + 1).padStart(2, "0")}</span>
      <span class="initials">${escapeHtml(row.initials)}</span>
      <span class="points">${Number(row.score).toLocaleString()}</span>
      <span class="cleared">${row.levelsCleared}/3</span>
    </div>`).join("");
}

function renderAttract() {
  const choices = ["START RUN", "PRACTICE"];
  return shell(`
    <div class="attract-layout">
      <div class="attract-copy">
        <h1 class="hero-title">TILTY<br>TABLE</h1>
        <div class="menu">
          ${choices.map((choice, index) => `
            <div class="menu-item ${attractChoice === index ? "selected" : ""}">
              <strong>${choice}</strong>
            </div>`).join("")}
        </div>
      </div>
      <aside class="leader-card">
        <h2>High scores</h2>
        ${leaderboardRows(8)}
      </aside>
    </div>`,
    `<span class="key">↑↓</span> CHOOSE <span class="key">ENTER</span> SELECT`);
}

function renderInitials() {
  const chars = initialsDraft.padEnd(3, " ").slice(0, 3).split("");
  return shell(`
    <div>
      <h1>ENTER INITIALS</h1>
      <div class="initials-boxes">
        ${chars.map((char, index) =>
          `<div class="initial-box ${index === Math.min(initialsDraft.length, 2) ? "active" : ""}">${escapeHtml(char)}</div>`
        ).join("")}
      </div>
    </div>`,
    `<span class="key">A-Z</span> TYPE <span class="key">⌫</span> ERASE <span class="key">ENTER</span> CONFIRM`);
}

function renderLevelSelect() {
  return shell(`
    <div style="width:100%">
      <h1 class="screen-title">PRACTICE</h1>
      <div class="menu" style="margin:20px auto 0;max-width:610px">
        ${game.levels.map((level, index) => `
          <div class="menu-item ${levelChoice === index ? "selected" : ""}">
            <strong>0${level.number} ${escapeHtml(level.title)}</strong>
          </div>`).join("")}
      </div>
    </div>`,
    `<span class="key">↑↓</span> CHOOSE <span class="key">ENTER</span> SELECT <span class="key">ESC</span> TITLE`);
}

function renderRules() {
  const level = game.level;
  return shell(`
    <div class="rules-layout">
      <div class="level-stamp"><strong>${level.number}</strong><span>LEVEL</span></div>
      <div class="rules-copy">
        <h1>${escapeHtml(level.title)}</h1>
        <p class="feature">${escapeHtml(level.feature)}</p>
        ${level.rules.map((rule, index) =>
          `<div class="rule"><b>0${index + 1}</b><span>${escapeHtml(rule)}</span></div>`
        ).join("")}
        <p class="prompt"><span class="key">ENTER</span> BUILD LEVEL</p>
      </div>
    </div>`, `<span class="key">ENTER</span> CONTINUE <span class="key">ESC</span> END RUN`);
}

function renderLoading() {
  const restarting = game.state === "restarting";
  return shell(`
    <div>
      <p class="kicker">LEVEL ${game.level.number}</p>
      <h1 class="screen-title">${restarting ? "RESETTING" : "GET READY"}</h1>
      <div class="loading-bars"><i></i><i></i><i></i><i></i><i></i><i></i></div>
      <p class="decision-copy">STAND CLEAR</p>
    </div>`, `STAND CLEAR`);
}

function tileClass(cell) {
  if (cell.key === game.level.startCell) return "start";
  if (cell.key === game.level.endCell) return "finish";
  const color = String(cell.color || "").toUpperCase();
  if (color === "#FF8C00") return "path";
  if (color === "#3366FF") return "points";
  if (cell.value === 1) return "wall";
  if (cell.value === -1) return "trap";
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
  return `<div class="board-wrap"><div class="board">${
    sortedCells().map(cell => {
      const classes = ["tile", tileClass(cell)];
      if (cell.dynamic) classes.push("dynamic");
      if (waiting && cell.key === game.level.startCell) classes.push("waiting");
      return `<i class="${classes.join(" ")}" title="${cell.key}"></i>`;
    }).join("")
  }</div></div>`;
}

function renderPlacement() {
  return shell(`
    <div class="game-layout">
      ${boardMarkup(true)}
      <div class="hud">
        <p class="hud-level">LEVEL ${game.level.number} · ${escapeHtml(game.level.title)}</p>
        <h1>PLACE<br>THE BALL</h1>
        <p class="hud-instruction">BALL ON CYAN <strong>${game.level.startCell}</strong><br>CLEAR HANDS, THEN ENTER</p>
        <div class="hud-stats">
          <div class="hud-stat"><span>TIME LIMIT</span><strong>${game.level.timeLimitSeconds}s</strong></div>
          <div class="hud-stat"><span>RESTARTS</span><strong>${game.restarts}</strong></div>
        </div>
        <p class="prompt"><span class="key">ENTER</span> START</p>
      </div>
    </div>`, `<span class="key">ENTER</span> START <span class="key">ESC</span> END RUN`);
}

function renderPlaying() {
  const remaining = game.timer.remainingSeconds;
  return shell(`
    <div class="game-layout">
      ${boardMarkup(false)}
      <div class="hud">
        <p class="hud-level">LEVEL ${game.level.number} · ${escapeHtml(game.level.title)}</p>
        <div class="timer ${remaining <= 10 ? "danger" : ""}">${String(remaining).padStart(2, "0")}</div>
        <div class="hud-stats">
          <div class="hud-stat"><span>RUN SCORE</span><strong>${Number(game.score).toLocaleString()}</strong></div>
          <div class="hud-stat"><span>RESTARTS</span><strong>${game.restarts}</strong></div>
        </div>
        <p class="hud-instruction">REACH MAGENTA <strong>${game.level.endCell}</strong></p>
      </div>
    </div>`,
    `<span class="key">C</span> FINISH <span class="key">R</span> RESTART <span class="key">ESC</span> END RUN`);
}

function renderTimeUp() {
  return shell(`
    <article class="message-card">
      <p class="kicker">Level ${game.level.number}</p>
      <h1 style="color:var(--red)">TIME UP</h1>
      <p class="result-number">−100</p>
      <p class="prompt"><span class="key">ENTER</span> TRY AGAIN</p>
    </article>`, `<span class="key">ENTER</span> RETRY <span class="key">ESC</span> END RUN`);
}

function renderLevelClear() {
  return shell(`
    <article class="message-card">
      <p class="kicker">LEVEL ${game.level.number}</p>
      <h1>CLEAR!</h1>
      <p class="result-number">+${Number(game.lastLevelResult.score).toLocaleString()}</p>
      <p class="decision-copy">${game.lastLevelResult.remainingSeconds}s LEFT</p>
      <p class="prompt"><span class="key">ENTER</span> SCORE</p>
    </article>`, `<span class="key">ENTER</span> CONTINUE`);
}

function renderLevelScore() {
  const result = game.lastLevelResult;
  return shell(`
    <article class="message-card">
      <p class="kicker">Level ${result.levelNumber} score</p>
      <h1>${Number(result.score).toLocaleString()} PTS</h1>
      <div class="result-grid">
        <div><span>Clear</span><strong>1,000</strong></div>
        <div><span>Time bonus</span><strong>+${result.remainingSeconds * 10}</strong></div>
        <div><span>Restart penalty</span><strong>−${result.restarts * 100}</strong></div>
      </div>
      <p class="prompt"><span class="key">ENTER</span> ${game.mode === "practice" ? "FINISH PRACTICE" : (game.level.number < 3 ? "NEXT LEVEL" : "FINAL SCORE")}</p>
    </article>`, `<span class="key">ENTER</span> CONTINUE`);
}

function renderSummary() {
  return shell(`
    <article class="message-card">
      <p class="kicker">${game.mode === "practice" ? "PRACTICE COMPLETE" : (game.endedEarly ? "RUN ENDED" : "RUN COMPLETE")}</p>
      <h1>${game.mode === "practice" ? game.level.title : `${game.levelsCleared}/3 CLEARED`}</h1>
      <p class="result-number">${game.mode === "practice" ? "—" : Number(game.score).toLocaleString()}</p>
      <p class="prompt"><span class="key">ENTER</span> ${game.mode === "practice" ? "LEVEL SELECT" : "LEADERBOARD"}</p>
    </article>`, `<span class="key">ENTER</span> CONTINUE`);
}

function renderAbandoned() {
  const saved = game.mode === "gauntlet" && game.levelsCleared > 0;
  return shell(`
    <article class="message-card">
      <h1>RUN ENDED</h1>
      <p class="result-number">${saved ? `${game.levelsCleared}/3` : "—"}</p>
      ${saved ? `<p class="decision-copy">${Number(game.score).toLocaleString()} PTS SAVED</p>` : ""}
      <p class="prompt"><span class="key">ENTER</span> CONTINUE</p>
    </article>`, `<span class="key">ENTER</span> CONTINUE`);
}

function renderLeaderboard() {
  return shell(`
    <div style="width:min(670px,90vw)">
      <h1 class="screen-title">HIGH SCORES</h1>
      <aside class="leader-card" style="margin-top:16px">${leaderboardRows(10)}</aside>
      <p class="prompt"><span class="key">ENTER</span> TITLE SCREEN</p>
    </div>`, `<span class="key">ENTER</span> TITLE`);
}

function render() {
  if (!game) return;
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
  if (requestInFlight) return;
  try {
    const response = await fetch("/api/state", { cache: "no-store" });
    const payload = await response.json();
    game = payload.game;
    handleStateAudio();
    render();
  } catch (error) {
    console.error("State refresh failed", error);
  }
}

function handleStateAudio() {
  if (!game) return;
  if (game.state !== lastState) {
    if (game.state === "initials") initialsDraft = "";
    if (game.state === "playing") audio.start();
    else if (game.state === "level_clear") audio.success();
    else if (game.state === "time_up" || game.state === "abandoned") audio.fail();
    else if (game.state === "hardware_fault") audio.fail();
    else if (lastState) audio.confirm();
    lastState = game.state;
  }
  audio.setMusic(["attract", "initials", "rules", "leaderboard"].includes(game.state));
  if (game.state === "playing") {
    const second = game.timer.remainingSeconds;
    if (second !== lastTimerSecond && second <= 10) audio.warning();
    lastTimerSecond = second;
  } else {
    lastTimerSecond = null;
  }
}

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
      abandonOpen = false;
      audio.click();
      render();
    } else if (key === "Enter") {
      abandonOpen = false;
      audio.fail();
      postAction("abandon");
    }
    return;
  }

  if (key === "Escape") {
    if (game.state === "level_select") {
      postAction("abandon");
    } else if (!["setup", "attract", "initials", "leaderboard", "hardware_fault"].includes(game.state)) {
      abandonOpen = true;
      audio.click();
      render();
    } else if (game.state === "initials") {
      postAction("abandon");
    }
    return;
  }

  switch (game.state) {
    case "setup":
    case "hardware_fault":
      if (key === "Enter") postAction("setup");
      break;
    case "attract":
      if (key === "ArrowUp" || key === "ArrowDown") {
        attractChoice = attractChoice ? 0 : 1;
        audio.click();
        render();
      } else if (key === "Enter") {
        postAction(attractChoice === 0 ? "start-gauntlet" : "show-level-select");
      }
      break;
    case "initials":
      if (/^[a-z]$/i.test(key) && initialsDraft.length < 3) {
        initialsDraft += key.toUpperCase();
        audio.click();
        render();
      } else if (key === "Backspace") {
        initialsDraft = initialsDraft.slice(0, -1);
        audio.click();
        render();
      } else if (key === "Enter" && initialsDraft.length === 3) {
        postAction("set-initials", { initials: initialsDraft });
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
      if (key.toLowerCase() === "r") postAction("restart");
      else if (key.toLowerCase() === "c") postAction("complete");
      break;
  }
});

refresh();
setInterval(refresh, 250);
