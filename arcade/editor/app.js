(() => {
  "use strict";

  const TILE_TYPES = {
    floor: { label: "Floor", value: 0, color: "#567DBB" },
    path: { label: "Path", value: 0, color: "#F49400" },
    wall: { label: "Wall", value: 1, color: "#4DFF00" },
    pit: { label: "Pit", value: -1, color: "#FF0000" },
    target: { label: "Target", value: 0, color: "#001FFF" },
    off: { label: "Off", value: 0, color: "#000000" },
  };
  const MODE_DEFAULTS = {
    reach_end: {},
    survival_lava: { survivalSeconds: 40, dwellSeconds: 1.5, warnSeconds: 2, pointsPerTile: 25, pitConfirmSeconds: 0.5 },
    hex_fall: { survivalSeconds: 45, touchGraceSeconds: 0.35, warnSeconds: 1.25, pitConfirmSeconds: 0.5, collapseEverySeconds: 0, collapseCount: 0 },
    target_hunt: { startingSeconds: 20, targetBonusSeconds: 5, targetConfirmSeconds: 0.3, pointsPerTarget: 100, spawnPitCount: 1, spawnWallCount: 1 },
  };
  const MODE_FIELDS = {
    survival_lava: [
      ["survivalSeconds", "Survival seconds"], ["dwellSeconds", "Touch grace"],
      ["warnSeconds", "Warning seconds"], ["pointsPerTile", "Points / tile"],
      ["pitConfirmSeconds", "Pit confirm"],
    ],
    hex_fall: [
      ["survivalSeconds", "Survival seconds"], ["touchGraceSeconds", "Touch grace"],
      ["warnSeconds", "Warning seconds"], ["pitConfirmSeconds", "Pit confirm"],
      ["collapseEverySeconds", "Collapse interval"], ["collapseCount", "Collapse count"],
    ],
    target_hunt: [
      ["startingSeconds", "Starting seconds"], ["targetBonusSeconds", "Target time bonus"],
      ["targetConfirmSeconds", "Target confirm"], ["pointsPerTarget", "Points / target"],
      ["spawnPitCount", "Pits / target"], ["spawnWallCount", "Walls / target"],
    ],
  };
  const cellKeys = Array.from({ length: 12 }, (_, row) =>
    Array.from({ length: 12 }, (_, col) => `${String.fromCharCode(65 + col)}${row + 1}`)
  ).flat();
  const $ = (selector) => document.querySelector(selector);
  const deepClone = (value) => JSON.parse(JSON.stringify(value));

  function makePackage() {
    const cells = Object.fromEntries(cellKeys.map((key) => [key, { ...TILE_TYPES.floor }]));
    Object.values(cells).forEach((cell) => delete cell.label);
    return {
      version: 1, seed: 1,
      meta: {
        id: "new-level", number: 1, title: "New Chamber", subtitle: "Describe this challenge",
        timeLimitSeconds: 60, startCell: "A1", endCell: "L12",
        feature: "Describe what changes on the physical table.",
        rules: ["Guide the ball through the chamber."],
        kenLine: "I'll explain the rules when you're ready.",
        trollLine: "You built this trap yourself!",
      },
      mode: "reach_end", modeParams: {}, cells,
    };
  }

  let state = makePackage();
  let selected = "A1";
  let activeTool = "paint";
  let activeTile = "floor";
  let dragging = false;
  let history = [];
  let future = [];
  let sim = null;
  let timer = null;

  function keyToRowCol(key) {
    const match = /^([A-L])(1[0-2]|[1-9])$/.exec(String(key).toUpperCase());
    if (!match) throw new Error(`Invalid cell ${key}`);
    return [Number(match[2]) - 1, match[1].charCodeAt(0) - 65];
  }
  function rowColToKey(row, col) {
    if (row < 0 || row > 11 || col < 0 || col > 11) throw new Error("Cell outside board");
    return `${String.fromCharCode(65 + col)}${row + 1}`;
  }
  function seededRandom(seed) {
    let value = (Number(seed) || 1) >>> 0;
    return () => {
      value = (value * 1664525 + 1013904223) >>> 0;
      return value / 4294967296;
    };
  }
  function neighbors(key) {
    const [row, col] = keyToRowCol(key);
    return [[row - 1, col], [row + 1, col], [row, col - 1], [row, col + 1]]
      .filter(([r, c]) => r >= 0 && r < 12 && c >= 0 && c < 12)
      .map(([r, c]) => rowColToKey(r, c));
  }
  function reachable(start, cells) {
    if (!cells[start] || cells[start].value !== 0) return new Set();
    const seen = new Set([start]), queue = [start];
    while (queue.length) {
      const current = queue.shift();
      neighbors(current).forEach((next) => {
        if (!seen.has(next) && cells[next].value === 0) { seen.add(next); queue.push(next); }
      });
    }
    return seen;
  }

  function snapshot() {
    history.push(deepClone(state));
    if (history.length > 80) history.shift();
    future = [];
  }
  function undo() {
    if (!history.length) return;
    future.push(deepClone(state)); state = history.pop(); resetSimulation(); renderAll();
  }
  function redo() {
    if (!future.length) return;
    history.push(deepClone(state)); state = future.pop(); resetSimulation(); renderAll();
  }

  function paintCell(key) {
    if (sim?.playing) { setBall(key); return; }
    selected = key;
    if (activeTool === "eyedropper") {
      const cell = state.cells[key];
      activeTile = Object.keys(TILE_TYPES).find((name) =>
        TILE_TYPES[name].value === cell.value && TILE_TYPES[name].color.toLowerCase() === cell.color.toLowerCase()
      ) || "floor";
      activeTool = "paint"; renderTools(); renderInspector(); return;
    }
    if (activeTool === "start" || activeTool === "end") {
      snapshot(); state.meta[activeTool === "start" ? "startCell" : "endCell"] = key;
      renderAll(); return;
    }
    if (activeTool === "fill") {
      snapshot();
      const original = JSON.stringify(state.cells[key]);
      const replacement = TILE_TYPES[activeTile];
      const queue = [key], visited = new Set();
      while (queue.length) {
        const current = queue.pop();
        if (visited.has(current) || JSON.stringify(state.cells[current]) !== original) continue;
        visited.add(current);
        state.cells[current] = { value: replacement.value, color: replacement.color };
        neighbors(current).forEach((next) => queue.push(next));
      }
      renderAll(); return;
    }
    const tile = TILE_TYPES[activeTile];
    state.cells[key] = { value: tile.value, color: tile.color };
    renderBoard(); renderInspector();
  }

  function cellRole(cell) {
    if (cell.value === 1) return "Wall";
    if (cell.value === -1) return "Pit";
    const match = Object.values(TILE_TYPES).find((item) => item.color.toLowerCase() === cell.color.toLowerCase());
    return match?.label || "Floor";
  }
  function renderTools() {
    $("#tileTools").innerHTML = Object.entries(TILE_TYPES).map(([name, tile]) =>
      `<button class="tool ${activeTile === name ? "active" : ""}" data-tile="${name}">
        <span>${tile.label}</span><span class="tool-chip" style="background:${tile.color}"></span>
      </button>`
    ).join("");
    document.querySelectorAll("[data-tile]").forEach((button) => button.onclick = () => {
      activeTile = button.dataset.tile; activeTool = "paint"; renderTools();
    });
    document.querySelectorAll("[data-tool]").forEach((button) =>
      button.classList.toggle("active", button.dataset.tool === activeTool)
    );
  }
  function displayCells() { return sim ? sim.cells : state.cells; }
  function renderBoard() {
    const cells = displayCells();
    $("#board").innerHTML = cellKeys.map((key) => {
      const cell = cells[key], classes = ["cell"];
      if (key === selected) classes.push("selected");
      if (cell.value === 1) classes.push("wall");
      if (cell.value === -1) classes.push("sunk");
      if (key === state.meta.startCell) classes.push("start");
      if (key === state.meta.endCell) classes.push("end");
      if (sim?.ball === key) classes.push("ball");
      if (sim?.target === key) classes.push("target");
      return `<button class="${classes.join(" ")}" data-key="${key}" role="gridcell" style="background:${cell.color}"></button>`;
    }).join("");
    document.querySelectorAll(".cell").forEach((cell) => {
      cell.onpointerdown = () => { dragging = true; if (activeTool === "paint") snapshot(); paintCell(cell.dataset.key); };
      cell.onpointerenter = () => { if (dragging && activeTool === "paint") paintCell(cell.dataset.key); };
      cell.onclick = () => { if (!dragging) paintCell(cell.dataset.key); };
    });
    $("#levelTitle").textContent = state.meta.title;
    $("#selectedLabel").textContent = selected;
    $("#modeLabel").textContent = state.mode.replaceAll("_", " ").toUpperCase();
  }
  function renderModeFields() {
    const fields = MODE_FIELDS[state.mode] || [];
    $("#modeFields").innerHTML = fields.map(([key, label]) =>
      `<label>${label}<input type="number" step="0.05" data-mode-param="${key}" value="${state.modeParams[key] ?? ""}"></label>`
    ).join("") || `<p class="eyebrow">NO MODE-SPECIFIC PARAMETERS</p>`;
    document.querySelectorAll("[data-mode-param]").forEach((input) => input.onchange = () => {
      snapshot(); state.modeParams[input.dataset.modeParam] = Number(input.value); resetSimulation(); validateAndRender();
    });
  }
  function renderDynamicFields() {
    const dynamic = state.cells[selected].dynamic;
    if (!dynamic) { $("#dynamicFields").innerHTML = ""; return; }
    if ((dynamic.type || "cycle") === "cycle") {
      $("#dynamicFields").innerHTML = `<label>Interval seconds<input id="dynInterval" type="number" step=".1" value="${dynamic.intervalSeconds || 2}"></label>`;
      $("#dynInterval").onchange = (event) => { dynamic.intervalSeconds = Number(event.target.value); };
    } else {
      $("#dynamicFields").innerHTML = `
        <label>Arm delay<input id="dynArm" type="number" step=".1" value="${dynamic.armDelaySeconds || 4}"></label>
        <label>Warning duration<input id="dynWarn" type="number" step=".1" value="${dynamic.warnDurationSeconds || 6}"></label>`;
      $("#dynArm").onchange = (event) => { dynamic.armDelaySeconds = Number(event.target.value); };
      $("#dynWarn").onchange = (event) => { dynamic.warnDurationSeconds = Number(event.target.value); };
    }
  }
  function renderInspector() {
    const cell = state.cells[selected];
    $("#cellKey").textContent = selected; $("#cellRole").textContent = cellRole(cell);
    $("#cellValue").value = String(cell.value); $("#cellColor").value = cell.color;
    $("#dynamicType").value = cell.dynamic?.type || (cell.dynamic ? "cycle" : "");
    $("#metaId").value = state.meta.id; $("#metaNumber").value = state.meta.number;
    $("#metaTitle").value = state.meta.title; $("#metaSubtitle").value = state.meta.subtitle;
    $("#seed").value = state.seed; $("#modeSelect").value = state.mode;
    $("#startCell").value = state.meta.startCell; $("#endCell").value = state.meta.endCell;
    $("#timeLimit").value = state.meta.timeLimitSeconds;
    $("#feature").value = state.meta.feature; $("#rules").value = state.meta.rules.join("\n");
    $("#kenLine").value = state.meta.kenLine; $("#trollLine").value = state.meta.trollLine;
    renderModeFields(); renderDynamicFields();
  }

  function validatePackage(pkg = state) {
    const errors = [];
    if (pkg.version !== 1) errors.push("Version must be 1.");
    if (!["reach_end", "survival_lava", "hex_fall", "target_hunt"].includes(pkg.mode)) errors.push("Unsupported mode.");
    if (Object.keys(pkg.cells || {}).length !== 144 || cellKeys.some((key) => !pkg.cells[key])) errors.push("Board must contain A1 through L12.");
    if (!cellKeys.includes(pkg.meta.startCell) || !cellKeys.includes(pkg.meta.endCell)) errors.push("Start/end cells must be valid.");
    if (!pkg.meta.id.trim() || !pkg.meta.title.trim()) errors.push("ID and title are required.");
    Object.entries(pkg.cells || {}).forEach(([key, cell]) => {
      if (![-1, 0, 1].includes(cell.value)) errors.push(`${key}: invalid servo value.`);
      if (!/^#[0-9a-f]{6}$/i.test(cell.color)) errors.push(`${key}: invalid color.`);
    });
    (MODE_FIELDS[pkg.mode] || []).forEach(([key]) => {
      if (!Number.isFinite(Number(pkg.modeParams[key])) || Number(pkg.modeParams[key]) < 0) errors.push(`${key} must be non-negative.`);
    });
    return errors;
  }
  function validateAndRender() {
    const errors = validatePackage();
    $("#diagnostics").innerHTML = errors.length
      ? errors.map((error) => `<span class="diagnostic">${error}</span>`).join("")
      : `<span class="diagnostic ok">Package valid: 144 cells, ${state.mode.replaceAll("_", " ")}</span>`;
    return errors;
  }

  function resetSimulation() {
    if (timer) clearInterval(timer);
    timer = null;
    sim = {
      playing: false, time: 0, cells: deepClone(state.cells), ball: state.meta.startCell,
      target: null, touched: {}, events: [], rng: seededRandom(state.seed), remaining: modeDuration(),
      hits: 0, nextCollapse: Number(state.modeParams.collapseEverySeconds || 0),
    };
    if (state.mode === "target_hunt") chooseTarget();
    $("#timeSlider").max = modeDuration(); $("#timeSlider").value = 0; $("#timeOutput").value = "0.0s";
    $("#ballCell").value = sim.ball; renderBoard(); renderEvents();
  }
  function modeDuration() {
    if (state.mode === "survival_lava" || state.mode === "hex_fall") return Number(state.modeParams.survivalSeconds || 45);
    if (state.mode === "target_hunt") return Number(state.modeParams.startingSeconds || 20);
    return Number(state.meta.timeLimitSeconds || 60);
  }
  function event(text) { sim.events.unshift(`${sim.time.toFixed(1)}s ${text}`); sim.events = sim.events.slice(0, 8); }
  function renderEvents() { $("#eventStrip").innerHTML = (sim?.events || []).map((item) => `<span class="event">${item}</span>`).join(""); }
  function chooseTarget() {
    const options = [...reachable(sim.ball, sim.cells)].filter((key) => key !== sim.ball);
    sim.target = options.length ? options[Math.floor(sim.rng() * options.length)] : null;
    if (sim.target) event(`target ${sim.target}`);
  }
  function safeObstacle(value, color) {
    const options = cellKeys.filter((key) => key !== sim.ball && key !== sim.target && sim.cells[key].value === 0);
    for (let i = options.length - 1; i > 0; i--) { const j = Math.floor(sim.rng() * (i + 1)); [options[i], options[j]] = [options[j], options[i]]; }
    const candidate = options.find((key) => {
      const before = sim.cells[key]; sim.cells[key] = { ...before, value, color };
      const okay = reachable(sim.ball, sim.cells).size > 1;
      if (!okay) sim.cells[key] = before;
      return okay;
    });
    if (candidate) event(`${value === -1 ? "pit" : "wall"} ${candidate}`);
  }
  function setBall(key) {
    key = String(key).toUpperCase();
    if (!cellKeys.includes(key)) return;
    sim.ball = key; $("#ballCell").value = key;
    if ((state.mode === "survival_lava" || state.mode === "hex_fall") && !sim.touched[key]) {
      sim.touched[key] = sim.time; sim.cells[key].color = "#F49400"; event(`touch ${key}`);
    }
    if (state.mode === "target_hunt" && key === sim.target) {
      sim.hits += 1; sim.remaining += Number(state.modeParams.targetBonusSeconds || 5); event(`target claimed ${key}`);
      const old = sim.target; if (old) sim.cells[old].color = "#567DBB";
      for (let i = 0; i < Number(state.modeParams.spawnPitCount || 1); i++) safeObstacle(-1, "#FF0000");
      for (let i = 0; i < Number(state.modeParams.spawnWallCount || 1); i++) safeObstacle(1, "#4DFF00");
      chooseTarget();
    }
    renderBoard(); renderEvents();
  }
  function simulationStep(dt = 0.1) {
    sim.time += dt; sim.remaining = Math.max(0, sim.remaining - dt);
    if (state.mode === "survival_lava" || state.mode === "hex_fall") {
      const grace = Number(state.modeParams.dwellSeconds ?? state.modeParams.touchGraceSeconds ?? 1);
      const warn = Number(state.modeParams.warnSeconds || 1);
      Object.entries(sim.touched).forEach(([key, touchedAt]) => {
        const age = sim.time - touchedAt;
        if (age >= grace + warn && sim.cells[key].value !== -1) {
          sim.cells[key] = { value: -1, color: "#FF0000" }; event(`sink ${key}`);
        } else if (age >= grace) sim.cells[key].color = Math.floor(age * 6) % 2 ? "#FF0000" : "#000000";
      });
      if (state.mode === "hex_fall" && sim.nextCollapse > 0 && sim.time >= sim.nextCollapse) {
        for (let i = 0; i < Number(state.modeParams.collapseCount || 0); i++) safeObstacle(-1, "#FF0000");
        sim.nextCollapse += Number(state.modeParams.collapseEverySeconds || 0);
      }
    }
    $("#timeSlider").value = Math.min(sim.time, Number($("#timeSlider").max));
    $("#timeOutput").value = `${sim.time.toFixed(1)}s / ${sim.remaining.toFixed(1)}s left`;
    renderBoard(); renderEvents();
    if (sim.remaining <= 0) { sim.playing = false; if (timer) clearInterval(timer); timer = null; $("#playBtn").textContent = "Play simulation"; event("timer ended"); }
  }
  function togglePlay() {
    sim.playing = !sim.playing; $("#playBtn").textContent = sim.playing ? "Pause" : "Play simulation";
    if (sim.playing) timer = setInterval(() => simulationStep(0.1), 100);
    else { clearInterval(timer); timer = null; }
  }

  function bindInputs() {
    const metaBindings = {
      metaId: "id", metaNumber: "number", metaTitle: "title", metaSubtitle: "subtitle",
      startCell: "startCell", endCell: "endCell", timeLimit: "timeLimitSeconds",
      feature: "feature", kenLine: "kenLine", trollLine: "trollLine",
    };
    Object.entries(metaBindings).forEach(([id, key]) => $(`#${id}`).onchange = (event) => {
      snapshot(); state.meta[key] = ["number", "timeLimitSeconds"].includes(key) ? Number(event.target.value) : event.target.value;
      resetSimulation(); renderAll();
    });
    $("#rules").onchange = (event) => { snapshot(); state.meta.rules = event.target.value.split("\n").map((s) => s.trim()).filter(Boolean); validateAndRender(); };
    $("#seed").onchange = (event) => { snapshot(); state.seed = Number(event.target.value); resetSimulation(); };
    $("#modeSelect").onchange = (event) => {
      snapshot(); state.mode = event.target.value; state.modeParams = deepClone(MODE_DEFAULTS[state.mode]); resetSimulation(); renderAll();
    };
    $("#cellValue").onchange = (event) => { snapshot(); state.cells[selected].value = Number(event.target.value); renderAll(); };
    $("#cellColor").onchange = (event) => { snapshot(); state.cells[selected].color = event.target.value.toUpperCase(); renderAll(); };
    $("#dynamicType").onchange = (event) => {
      snapshot(); const type = event.target.value;
      if (!type) delete state.cells[selected].dynamic;
      else if (type === "cycle") state.cells[selected].dynamic = { type, intervalSeconds: 2, pattern: [{ value: 1, color: "#4DFF00" }, { value: 0, color: "#F49400" }] };
      else state.cells[selected].dynamic = { type, armDelaySeconds: 4, warnDurationSeconds: 6, initialIntervalSeconds: 1.2, minIntervalSeconds: 0.12 };
      renderInspector(); validateAndRender();
    };
    document.querySelectorAll("[data-tool]").forEach((button) => button.onclick = () => { activeTool = button.dataset.tool; renderTools(); });
    $("#undoBtn").onclick = undo; $("#redoBtn").onclick = redo;
    $("#validateBtn").onclick = validateAndRender;
    $("#playBtn").onclick = togglePlay; $("#stepBtn").onclick = () => simulationStep(.1); $("#resetBtn").onclick = resetSimulation;
    $("#ballCell").onchange = (event) => setBall(event.target.value);
    $("#timeSlider").oninput = (event) => {
      const target = Number(event.target.value); resetSimulation();
      while (sim.time + .099 < target) simulationStep(.1);
    };
    $("#fileInput").onchange = async (event) => {
      const file = event.target.files[0]; if (!file) return;
      const imported = JSON.parse(await file.text()); const errors = validatePackage(imported);
      if (errors.length) { $("#diagnostics").innerHTML = errors.map((error) => `<span class="diagnostic">${error}</span>`).join(""); return; }
      history = []; future = []; state = imported; selected = state.meta.startCell; resetSimulation(); renderAll();
    };
    $("#downloadBtn").onclick = () => {
      const errors = validateAndRender(); if (errors.length) return;
      const blob = new Blob([JSON.stringify(state, null, 2) + "\n"], { type: "application/json" });
      const link = document.createElement("a"); link.href = URL.createObjectURL(blob);
      link.download = `${state.meta.id}.level.json`; link.click(); URL.revokeObjectURL(link.href);
    };
    window.onpointerup = () => { dragging = false; };
    window.onkeydown = (event) => {
      if (event.target.matches("input,textarea,select")) return;
      const keymap = { p: "paint", f: "fill", i: "eyedropper", s: "start", e: "end" };
      if (keymap[event.key.toLowerCase()]) { activeTool = keymap[event.key.toLowerCase()]; renderTools(); }
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "z") { event.preventDefault(); event.shiftKey ? redo() : undo(); }
    };
  }
  function renderAll() { renderTools(); renderBoard(); renderInspector(); validateAndRender(); }

  window.EditorLogic = { keyToRowCol, rowColToKey, seededRandom, reachable, validatePackage };
  bindInputs(); resetSimulation(); renderAll();
})();
