(() => {
  "use strict";
  const { cellKeys, seededRandom, reachable, reachableDistances, moveCell } = window.TiltyEditorLogic;
  const $ = (selector) => document.querySelector(selector);
  const clone = (value) => JSON.parse(JSON.stringify(value));

  const TILES = {
    floor: { label: "Floor", value: 0, color: "#567DBB" },
    path: { label: "Path", value: 0, color: "#F49400" },
    wall: { label: "Wall", value: 1, color: "#4DFF00" },
    pit: { label: "Pit", value: -1, color: "#FF0000" },
  };
  const MODES = {
    survival_lava: {
      label: "Lava Survival",
      short: "Keep moving while every touched tile heats, warns, then sinks.",
      steps: ["Touching starts a tile timer.", "Red flashing means leave now.", "Survive until time reaches zero."],
      defaults: { survivalSeconds: 40, dwellSeconds: 1.2, warnSeconds: 1.4, pointsPerTile: 25, pitConfirmSeconds: 0.5 },
      fields: [["survivalSeconds", "Survive for"], ["dwellSeconds", "Safe after touch"], ["warnSeconds", "Red warning"], ["pointsPerTile", "Points per new tile"]],
    },
    hex_fall: {
      label: "Hex-A-Fall",
      short: "Random floor tiles disappear while flashing points reward movement.",
      steps: ["Collect flashing blue points.", "Random tiles flash, then collapse.", "Score from survival time and points."],
      defaults: { survivalSeconds: 45, pitConfirmSeconds: 0.5, collapseEverySeconds: 3, collapseCount: 1, collapseWarnSeconds: 1, pointValue: 100, survivalPointsPerSecond: 10, pointConfirmSeconds: 0.15 },
      fields: [["survivalSeconds", "Survive for"], ["collapseEverySeconds", "Collapse every"], ["collapseCount", "Tiles per collapse"], ["collapseWarnSeconds", "Flash before falling"], ["pointValue", "Points per pickup"], ["survivalPointsPerSecond", "Points per second"]],
    },
    target_hunt: {
      label: "Snake",
      short: "Reach flashing targets for time; every success adds a permanent wall and pit.",
      steps: ["Chase a distant flashing blue target.", "Time rewards are capped.", "The run ends when no meaningful route remains."],
      defaults: { startingSeconds: 20, targetBonusSeconds: 5, targetConfirmSeconds: 0.2, pointsPerTarget: 100, spawnPitCount: 1, spawnWallCount: 1, maxTimeSeconds: 30, minimumReachableCells: 8, minimumTargetDistance: 4 },
      fields: [["startingSeconds", "Starting time"], ["targetBonusSeconds", "Time per target"], ["maxTimeSeconds", "Maximum time"], ["pointsPerTarget", "Points per target"], ["spawnPitCount", "New pits"], ["spawnWallCount", "New walls"], ["minimumReachableCells", "Minimum floor area"], ["minimumTargetDistance", "Minimum target distance"]],
    },
  };

  function newPackage() {
    const cells = Object.fromEntries(cellKeys.map((key) => [key, { value: 0, color: TILES.floor.color }]));
    return {
      version: 1, seed: 1,
      meta: {
        id: "new-level", number: 1, title: "New Chamber", subtitle: "A physical table challenge",
        timeLimitSeconds: 60, startCell: "A1", endCell: "L12",
        feature: "Describe what changes on the table.", rules: ["Guide the ball through the chamber."],
        kenLine: "Watch the table and keep the ball moving.", trollLine: "Let's see how long you last.",
      },
      mode: "survival_lava", modeParams: clone(MODES.survival_lava.defaults), cells,
    };
  }

  let level = newPackage();
  let view = "build";
  let selected = "A1";
  let tile = "floor";
  let fillMode = false;
  let setStart = false;
  let dragging = false;
  let history = [], future = [];
  let sim = null, ticker = null;

  function pushHistory() { history.push(clone(level)); if (history.length > 60) history.shift(); future = []; }
  function undo() { if (!history.length) return; future.push(clone(level)); level = history.pop(); resetTest(); render(); }
  function redo() { if (!future.length) return; history.push(clone(level)); level = future.pop(); resetTest(); render(); }
  function role(cell) { return cell.value === 1 ? "Wall" : cell.value === -1 ? "Pit" : Object.values(TILES).find((item) => item.color === cell.color)?.label || "Floor"; }

  function paint(key, record = false) {
    if (view !== "build") return;
    selected = key;
    if (record) pushHistory();
    if (setStart) { level.meta.startCell = key; setStart = false; render(); return; }
    if (fillMode) {
      const before = JSON.stringify(level.cells[key]), replacement = TILES[tile], queue = [key], seen = new Set();
      while (queue.length) {
        const current = queue.pop();
        if (seen.has(current) || JSON.stringify(level.cells[current]) !== before) continue;
        seen.add(current); level.cells[current] = { value: replacement.value, color: replacement.color };
        const [colLetter, rowText] = [current[0], current.slice(1)];
        const row = Number(rowText) - 1, col = colLetter.charCodeAt(0) - 65;
        [[row-1,col],[row+1,col],[row,col-1],[row,col+1]].forEach(([r,c]) => {
          if (r >= 0 && r < 12 && c >= 0 && c < 12) queue.push(`${String.fromCharCode(65+c)}${r+1}`);
        });
      }
      fillMode = false;
    } else {
      level.cells[key] = { value: TILES[tile].value, color: TILES[tile].color };
    }
    render();
  }

  function validate() {
    const errors = [];
    if (Object.keys(level.cells).length !== 144 || cellKeys.some((key) => !level.cells[key])) errors.push("Board must have all 144 tiles.");
    if (!level.meta.id.trim() || !level.meta.title.trim()) errors.push("Level name and ID are required.");
    if (!cellKeys.includes(level.meta.startCell)) errors.push("Choose a valid start tile.");
    Object.entries(level.cells).forEach(([key, cell]) => {
      if (![-1,0,1].includes(cell.value)) errors.push(`${key} has an invalid servo state.`);
    });
    return errors;
  }

  function renderModes() {
    $("#modeCards").innerHTML = Object.entries(MODES).map(([key, mode]) =>
      `<button class="mode-card ${level.mode === key ? "active" : ""}" data-mode="${key}">
        <strong>${mode.label}</strong><small>${mode.short}</small>
      </button>`
    ).join("");
    document.querySelectorAll("[data-mode]").forEach((button) => button.onclick = () => {
      pushHistory(); level.mode = button.dataset.mode; level.modeParams = clone(MODES[level.mode].defaults); resetTest(); render();
    });
    const mode = MODES[level.mode];
    $("#modeFields").innerHTML = mode.fields.map(([key, label]) =>
      `<label>${label}<input data-param="${key}" type="number" min="0" step=".1" value="${level.modeParams[key]}"></label>`
    ).join("");
    document.querySelectorAll("[data-param]").forEach((input) => input.onchange = () => {
      level.modeParams[input.dataset.param] = Number(input.value); resetTest(); updateHud();
    });
    $("#modeExplanation").innerHTML = `<p>${mode.short}</p><ol>${mode.steps.map((step) => `<li>${step}</li>`).join("")}</ol>`;
  }
  function renderTiles() {
    $("#tileTools").innerHTML = Object.entries(TILES).map(([key, item]) =>
      `<button class="tile-choice ${tile === key ? "active" : ""}" data-tile="${key}">
        <span class="color-chip" style="background:${item.color}"></span>${item.label}
      </button>`
    ).join("");
    $("#cellChoices").innerHTML = Object.entries(TILES).map(([key, item]) =>
      `<button class="tile-choice ${role(level.cells[selected]).toLowerCase() === item.label.toLowerCase() ? "active" : ""}" data-cell-tile="${key}">
        <span class="color-chip" style="background:${item.color}"></span>${item.label}
      </button>`
    ).join("");
    document.querySelectorAll("[data-tile]").forEach((button) => button.onclick = () => { tile = button.dataset.tile; renderTiles(); });
    document.querySelectorAll("[data-cell-tile]").forEach((button) => button.onclick = () => { pushHistory(); tile = button.dataset.cellTile; paint(selected); });
  }
  function renderBoard() {
    const cells = sim ? sim.cells : level.cells;
    $("#board").innerHTML = cellKeys.map((key) => {
      const cell = cells[key], classes = ["cell"];
      if (view === "build" && key === selected) classes.push("selected");
      if (cell.value === 1) classes.push("wall");
      if (cell.value === -1) classes.push("sunk");
      if (key === level.meta.startCell) classes.push("start");
      if (key === level.meta.endCell) classes.push("end");
      if (sim?.ball === key) classes.push("ball");
      if (sim?.target === key) classes.push("target");
      return `<button class="${classes.join(" ")}" data-key="${key}" style="background:${cell.color}"></button>`;
    }).join("");
    document.querySelectorAll(".cell").forEach((button) => {
      button.onpointerdown = () => { dragging = true; paint(button.dataset.key, true); };
      button.onpointerenter = () => { if (dragging) paint(button.dataset.key); };
      button.onclick = () => { selected = button.dataset.key; renderInspector(); renderBoard(); };
    });
  }
  function renderInspector() {
    const cell = level.cells[selected];
    $("#cellKey").textContent = selected; $("#cellRole").textContent = role(cell);
    $("#cellValue").value = cell.value; $("#cellColor").value = cell.color;
    $("#dynamicType").value = cell.dynamic?.type || "";
    $("#dynamicFields").innerHTML = cell.dynamic
      ? `<label>Interval / delay<input id="dynamicTime" type="number" step=".1" value="${cell.dynamic.intervalSeconds || cell.dynamic.armDelaySeconds || 2}"></label>`
      : "";
    $("#metaTitle").value = level.meta.title; $("#metaId").value = level.meta.id;
    $("#metaNumber").value = level.meta.number; $("#seed").value = level.seed;
    $("#metaSubtitle").value = level.meta.subtitle; $("#feature").value = level.meta.feature;
    $("#rules").value = level.meta.rules.join("\n"); $("#kenLine").value = level.meta.kenLine; $("#trollLine").value = level.meta.trollLine;
    renderTiles();
  }
  function renderDiagnostics() {
    const errors = validate();
    $("#diagnostics").innerHTML = errors.length
      ? errors.map((error) => `<div class="diagnostic">${error}</div>`).join("")
      : `<div class="diagnostic ok">Ready to play test and download.</div>`;
    return errors;
  }
  function render() {
    document.body.classList.toggle("playing", view === "play");
    $("#buildTab").classList.toggle("active", view === "build"); $("#playTab").classList.toggle("active", view === "play");
    $("#buildControls").classList.toggle("hidden", view !== "build"); $("#playControls").classList.toggle("hidden", view !== "play");
    $("#pageTitle").textContent = view === "build" ? "Build the board" : "Play the game";
    $("#selectedLabel").textContent = `Selected ${selected}`;
    renderModes(); renderBoard(); renderInspector(); renderDiagnostics(); updateHud();
  }

  function resetTest() {
    if (ticker) clearInterval(ticker); ticker = null;
    sim = {
      playing: false, ended: false, won: false, time: 0, score: 0,
      remaining: Number(level.modeParams.survivalSeconds || level.modeParams.startingSeconds || 40),
      cells: clone(level.cells), ball: level.meta.startCell, target: null, touched: {}, pendingCollapse: {},
      rng: seededRandom(level.seed), nextCollapse: Number(level.modeParams.collapseEverySeconds || 0),
    };
    if (level.mode === "target_hunt") chooseTarget();
    if (level.mode === "hex_fall") chooseHexPoint();
    $("#playBtn").textContent = "Start test"; $("#gameMessage").textContent = "Press Start test, then use the arrow keys.";
    updateHud(); renderBoard();
  }
  function chooseTarget() {
    const distances = reachableDistances(sim.ball, sim.cells);
    const minimumArea = Number(level.modeParams.minimumReachableCells || 8);
    const minimumDistance = Number(level.modeParams.minimumTargetDistance || 4);
    const options = Object.entries(distances)
      .filter(([, distance]) => distance >= minimumDistance)
      .map(([key]) => key);
    if (Object.keys(distances).length < minimumArea) {
      sim.target = null;
      return;
    }
    sim.target = options.length ? options[Math.floor(sim.rng() * options.length)] : null;
  }
  function chooseHexPoint() {
    const options = [...reachable(sim.ball, sim.cells)]
      .filter((key) => key !== sim.ball && sim.cells[key].value === 0);
    sim.target = options.length ? options[Math.floor(sim.rng() * options.length)] : null;
  }
  function placeObstacle(value, color) {
    const options = cellKeys.filter((key) => key !== sim.ball && key !== sim.target && sim.cells[key].value === 0);
    for (let i = options.length - 1; i > 0; i--) { const j = Math.floor(sim.rng() * (i + 1)); [options[i], options[j]] = [options[j], options[i]]; }
    for (const key of options) {
      const old = sim.cells[key]; sim.cells[key] = { ...old, value, color };
      if (
        reachable(sim.ball, sim.cells).size
        >= Number(level.modeParams.minimumReachableCells || 8)
      ) return;
      sim.cells[key] = old;
    }
  }
  function safeHexCandidate() {
    const pending = new Set(Object.keys(sim.pendingCollapse));
    const active = cellKeys.filter((key) => sim.cells[key].value === 0 && !pending.has(key));
    const options = active.filter((key) => key !== sim.ball && key !== sim.target);
    for (let i = options.length - 1; i > 0; i--) { const j = Math.floor(sim.rng() * (i + 1)); [options[i], options[j]] = [options[j], options[i]]; }
    for (const candidate of options) {
      const blocked = [...pending, candidate];
      blocked.forEach((key) => { sim.cells[key]._savedValue = sim.cells[key].value; sim.cells[key].value = -1; });
      const remaining = new Set(active.filter((key) => key !== candidate));
      const connected = reachable(sim.ball, sim.cells);
      blocked.forEach((key) => { sim.cells[key].value = sim.cells[key]._savedValue; delete sim.cells[key]._savedValue; });
      if (connected.size === remaining.size) return candidate;
    }
    return null;
  }
  function hitTarget() {
    sim.score += Number(level.modeParams.pointsPerTarget || 100);
    sim.remaining = Math.min(
      Number(level.modeParams.maxTimeSeconds || 30),
      sim.remaining + Number(level.modeParams.targetBonusSeconds || 5),
    );
    for (let i = 0; i < Number(level.modeParams.spawnPitCount || 1); i++) placeObstacle(-1, "#FF0000");
    for (let i = 0; i < Number(level.modeParams.spawnWallCount || 1); i++) placeObstacle(1, "#4DFF00");
    chooseTarget();
    if (!sim.target) endTest(false, "No distant reachable target remains. The snake is trapped.");
    else $("#gameMessage").textContent = `Target reached. ${sim.remaining.toFixed(1)} seconds left.`;
  }
  function endTest(won, message) {
    sim.playing = false; sim.ended = true; sim.won = won;
    if (ticker) clearInterval(ticker); ticker = null;
    $("#playBtn").textContent = "Play again"; $("#gameMessage").textContent = message; updateHud();
  }
  function moveBall(deltaRow, deltaCol) {
    if (!sim?.playing) return;
    const move = moveCell(sim.ball, deltaRow, deltaCol, sim.cells);
    if (move.blocked) { $("#gameMessage").textContent = "A wall blocks that direction."; return; }
    sim.ball = move.key;
    if (sim.cells[sim.ball].value === -1) { renderBoard(); endTest(false, "The ball fell into a pit."); return; }
    if (level.mode === "target_hunt" && sim.ball === sim.target) hitTarget();
    if (level.mode === "hex_fall" && sim.ball === sim.target) {
      sim.hits += 1;
      chooseHexPoint();
      $("#gameMessage").textContent = "Point collected. Keep moving.";
    }
    if (level.mode === "survival_lava" && !sim.touched[sim.ball]) {
      sim.touched[sim.ball] = sim.time;
      sim.cells[sim.ball].color = "#F49400";
      sim.score += Number(level.modeParams.pointsPerTile || 25);
    }
    renderBoard(); updateHud();
  }
  function tick() {
    if (!sim.playing) return;
    sim.time += .1; sim.remaining = Math.max(0, sim.remaining - .1);
    if (level.mode === "survival_lava") {
      const grace = Number(level.modeParams.dwellSeconds ?? 1);
      const warning = Number(level.modeParams.warnSeconds || 1);
      Object.entries(sim.touched).forEach(([key, touchedAt]) => {
        const age = sim.time - touchedAt;
        if (age >= grace + warning) sim.cells[key] = { value: -1, color: "#FF0000" };
        else if (age >= grace) sim.cells[key].color = Math.floor(age * 8) % 2 ? "#FF0000" : "#000000";
      });
      if (sim.cells[sim.ball].value === -1) { renderBoard(); endTest(false, "The floor disappeared under the ball."); return; }
    } else if (level.mode === "hex_fall") {
      Object.entries(sim.pendingCollapse).forEach(([key, sinkAt]) => {
        if (sim.time >= sinkAt) {
          sim.cells[key] = { value: -1, color: "#FF0000" };
          delete sim.pendingCollapse[key];
        } else {
          sim.cells[key].color = Math.floor(sim.time * 7) % 2 ? "#FF0000" : "#000000";
        }
      });
      if (sim.nextCollapse > 0 && sim.time >= sim.nextCollapse) {
        for (let i = 0; i < Number(level.modeParams.collapseCount || 1); i++) {
          const candidate = safeHexCandidate();
          if (!candidate) break;
          sim.pendingCollapse[candidate] = sim.time + Number(level.modeParams.collapseWarnSeconds || 1);
          sim.cells[candidate].color = "#FF0000";
        }
        sim.nextCollapse += Number(level.modeParams.collapseEverySeconds || 3);
      }
      if (sim.cells[sim.ball].value === -1) { renderBoard(); endTest(false, "The floor disappeared under the ball."); return; }
      sim.score = Math.floor(sim.time) * Number(level.modeParams.survivalPointsPerSecond || 10)
        + sim.hits * Number(level.modeParams.pointValue || 100);
    }
    if (sim.remaining <= 0) endTest(level.mode !== "target_hunt", level.mode === "target_hunt" ? "Time ran out." : "You survived.");
    renderBoard(); updateHud();
  }
  function startTest() {
    if (sim.ended) resetTest();
    sim.playing = !sim.playing;
    $("#playBtn").textContent = sim.playing ? "Pause" : "Resume";
    $("#gameMessage").textContent = sim.playing ? "Arrow keys move the ball. Stay alive." : "Paused.";
    if (sim.playing) { $("#board").focus(); ticker = setInterval(tick, 100); }
    else { clearInterval(ticker); ticker = null; }
  }
  function updateHud() {
    $("#hudMode").textContent = MODES[level.mode].label.toUpperCase();
    $("#hudTime").textContent = sim ? sim.remaining.toFixed(1) : "—";
    $("#hudScore").textContent = sim?.score || 0; $("#hudTarget").textContent = sim?.target || "—";
  }
  function switchView(next) {
    view = next;
    if (view === "play") resetTest(); else { if (ticker) clearInterval(ticker); ticker = null; sim = null; }
    render();
  }

  function bind() {
    $("#buildTab").onclick = () => switchView("build"); $("#playTab").onclick = () => switchView("play");
    $("#playBtn").onclick = startTest; $("#resetBtn").onclick = resetTest;
    $("#fillBtn").onclick = () => { fillMode = true; setStart = false; };
    $("#startBtn").onclick = () => { setStart = true; fillMode = false; };
    $("#undoBtn").onclick = undo; $("#redoBtn").onclick = redo;
    $("#validateBtn").onclick = renderDiagnostics;
    $("#cellValue").onchange = (event) => { pushHistory(); level.cells[selected].value = Number(event.target.value); render(); };
    $("#cellColor").onchange = (event) => { pushHistory(); level.cells[selected].color = event.target.value.toUpperCase(); render(); };
    $("#dynamicType").onchange = (event) => {
      pushHistory(); const type = event.target.value;
      if (!type) delete level.cells[selected].dynamic;
      else if (type === "cycle") level.cells[selected].dynamic = { type, intervalSeconds: 2, pattern: [{ value: 1, color: "#4DFF00" }, { value: 0, color: "#F49400" }] };
      else level.cells[selected].dynamic = { type, armDelaySeconds: 4, warnDurationSeconds: 6 };
      render();
    };
    const bindings = { metaTitle: "title", metaId: "id", metaNumber: "number", metaSubtitle: "subtitle", feature: "feature", kenLine: "kenLine", trollLine: "trollLine" };
    Object.entries(bindings).forEach(([id, key]) => $(`#${id}`).onchange = (event) => { level.meta[key] = key === "number" ? Number(event.target.value) : event.target.value; render(); });
    $("#seed").onchange = (event) => { level.seed = Number(event.target.value); resetTest(); };
    $("#rules").onchange = (event) => { level.meta.rules = event.target.value.split("\n").map((line) => line.trim()).filter(Boolean); };
    $("#fileInput").onchange = async (event) => {
      const file = event.target.files[0]; if (!file) return;
      const imported = JSON.parse(await file.text());
      if (!imported.cells || Object.keys(imported.cells).length !== 144) { $("#gameMessage").textContent = "That package does not contain a complete board."; return; }
      level = imported; selected = level.meta.startCell; history = []; future = []; switchView("build");
    };
    $("#downloadBtn").onclick = () => {
      if (renderDiagnostics().length) return;
      const blob = new Blob([JSON.stringify(level, null, 2) + "\n"], { type: "application/json" });
      const link = document.createElement("a"); link.href = URL.createObjectURL(blob); link.download = `${level.meta.id}.level.json`; link.click(); URL.revokeObjectURL(link.href);
    };
    window.onpointerup = () => { dragging = false; };
    window.onkeydown = (event) => {
      if (view === "play" && ["ArrowUp","ArrowDown","ArrowLeft","ArrowRight"].includes(event.key)) {
        event.preventDefault();
        const movement = { ArrowUp: [-1,0], ArrowDown: [1,0], ArrowLeft: [0,-1], ArrowRight: [0,1] }[event.key];
        moveBall(...movement); return;
      }
      if (event.target.matches("input,textarea,select")) return;
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "z") { event.preventDefault(); event.shiftKey ? redo() : undo(); }
      if (event.key === " ") { event.preventDefault(); if (view === "play") startTest(); }
    };
  }

  bind(); resetTest(); render();
})();
