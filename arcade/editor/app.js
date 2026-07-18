(() => {
  "use strict";
  const { cellKeys, neighbors, dynamicState } = window.MazeEditorLogic;
  const $ = (selector) => document.querySelector(selector);
  const clone = (value) => JSON.parse(JSON.stringify(value));

  const PRESETS = {
    neutral: { label: "Neutral", value: 0, color: "#567DBB" },
    path: { label: "Path", value: 0, color: "#F49400" },
    raised: { label: "Raised", value: 1, color: "#4DFF00" },
    lowered: { label: "Lowered", value: -1, color: "#FF0000" },
  };

  let cells = null;
  let selected = "A1";
  let activePreset = null;
  let fillMode = false;
  let dirty = false;
  let history = [];
  let future = [];
  let previewStarted = performance.now();

  function stateName(value) {
    return value === 1 ? "Raised" : value === -1 ? "Lowered" : "Neutral";
  }

  function setStatus(message, kind = "") {
    const node = $("#status");
    node.textContent = message;
    node.className = `status ${kind}`;
  }

  function remember() {
    history.push(clone(cells));
    if (history.length > 80) history.shift();
    future = [];
  }

  function changed(message = "Unsaved changes") {
    dirty = true;
    setStatus(message);
    renderDiagnostics();
  }

  function mutate(callback, message) {
    remember();
    callback();
    changed(message);
    render();
  }

  function applyPreset(key) {
    if (!activePreset) return;
    const preset = PRESETS[activePreset];
    cells[key] = { ...cells[key], value: preset.value, color: preset.color };
  }

  function paint(key, record = false) {
    selected = key;
    if (fillMode && activePreset) {
      remember();
      const original = JSON.stringify(cells[key]);
      const queue = [key];
      const seen = new Set();
      while (queue.length) {
        const current = queue.pop();
        if (seen.has(current) || JSON.stringify(cells[current]) !== original) continue;
        seen.add(current);
        applyPreset(current);
        neighbors(current).forEach((neighbor) => queue.push(neighbor));
      }
      fillMode = false;
      changed(`Filled ${seen.size} cells`);
      render();
      return;
    }
    if (activePreset) {
      if (record) remember();
      applyPreset(key);
      changed(`Painted ${key}`);
    }
    render();
  }

  function renderPresets() {
    $("#presets").innerHTML = `
      <button class="preset ${activePreset === null ? "active" : ""}" data-preset="">Select only</button>
      ${Object.entries(PRESETS).map(([key, preset]) => `
        <button class="preset ${activePreset === key ? "active" : ""}" data-preset="${key}">
          <i class="swatch" style="background:${preset.color}"></i><span>${preset.label}</span>
        </button>`).join("")}`;
    document.querySelectorAll("[data-preset]").forEach((button) => {
      button.onclick = () => {
        activePreset = button.dataset.preset || null;
        fillMode = false;
        renderPresets();
      };
    });
  }

  function displayCell(cell) {
    if (!$("#previewToggle").checked || !cell.dynamic) return cell;
    return dynamicState(cell, (performance.now() - previewStarted) / 1000);
  }

  function renderBoard() {
    if (!cells) return;
    $("#board").innerHTML = cellKeys.map((key) => {
      const source = cells[key];
      const shown = displayCell(source);
      const classes = ["cell", stateName(shown.value).toLowerCase()];
      if (key === selected) classes.push("selected");
      if (source.dynamic) classes.push("dynamic");
      if (key === "A1") classes.push("start");
      if (key === "L12") classes.push("end");
      return `<button class="${classes.join(" ")}" data-key="${key}" style="background:${shown.color}"><span>${key}</span></button>`;
    }).join("");
    document.querySelectorAll(".cell").forEach((button) => {
      button.onclick = () => {
        paint(button.dataset.key, true);
      };
    });
  }

  function bindNumber(id, object, key, message) {
    $(`#${id}`).onchange = (event) => mutate(
      () => { object[key] = Number(event.target.value); },
      message,
    );
  }

  function renderDynamic() {
    const cell = cells[selected];
    const dynamic = cell.dynamic;
    $("#dynamicType").value = dynamic?.type || "";
    if (!dynamic) {
      $("#dynamicFields").innerHTML = `<p class="help">Static cells keep their selected physical state and color.</p>`;
      return;
    }
    if (dynamic.type === "cycle") {
      $("#dynamicFields").innerHTML = `
        <label>Seconds per state<input id="cycleInterval" type="number" min="0.05" step="0.05" value="${dynamic.intervalSeconds}"></label>
        <h2>Cycle states</h2>
        ${dynamic.pattern.map((state, index) => `
          <div class="pattern-row">
            <label>State ${index + 1}<select data-pattern-value="${index}">
              <option value="1" ${state.value === 1 ? "selected" : ""}>Raised</option>
              <option value="0" ${state.value === 0 ? "selected" : ""}>Neutral</option>
              <option value="-1" ${state.value === -1 ? "selected" : ""}>Lowered</option>
            </select></label>
            <label>Color<input data-pattern-color="${index}" type="color" value="${state.color}"></label>
            <button data-remove-state="${index}" title="Remove state">×</button>
          </div>`).join("")}
        <button id="addStateBtn" class="add-state">Add cycle state</button>`;
      bindNumber("cycleInterval", dynamic, "intervalSeconds", `Changed ${selected} cycle speed`);
      document.querySelectorAll("[data-pattern-value]").forEach((input) => {
        input.onchange = (event) => mutate(
          () => { dynamic.pattern[Number(input.dataset.patternValue)].value = Number(event.target.value); },
          `Changed ${selected} cycle state`,
        );
      });
      document.querySelectorAll("[data-pattern-color]").forEach((input) => {
        input.onchange = (event) => mutate(
          () => { dynamic.pattern[Number(input.dataset.patternColor)].color = event.target.value.toUpperCase(); },
          `Changed ${selected} cycle color`,
        );
      });
      document.querySelectorAll("[data-remove-state]").forEach((button) => {
        button.onclick = () => {
          if (dynamic.pattern.length <= 2) {
            setStatus("A repeating cycle needs at least two states", "error");
            return;
          }
          mutate(
            () => dynamic.pattern.splice(Number(button.dataset.removeState), 1),
            `Removed a ${selected} cycle state`,
          );
        };
      });
      $("#addStateBtn").onclick = () => mutate(
        () => dynamic.pattern.push({ value: 0, color: "#F49400" }),
        `Added a ${selected} cycle state`,
      );
      return;
    }

    const fields = [
      ["armDelay", "Arm delay (seconds)", "armDelaySeconds", 0.1],
      ["warnDuration", "Warning duration", "warnDurationSeconds", 0.1],
      ["initialInterval", "Initial blink interval", "initialIntervalSeconds", 0.05],
      ["minInterval", "Fastest blink interval", "minIntervalSeconds", 0.01],
    ];
    $("#dynamicFields").innerHTML = fields.map(([id, label, key, step]) =>
      `<label>${label}<input id="${id}" type="number" min="0" step="${step}" value="${dynamic[key]}"></label>`
    ).join("") + `
      <label>Trap color<input id="trapColor" type="color" value="${dynamic.trapColor}"></label>
      <label>Safe color<input id="floorColor" type="color" value="${dynamic.floorColor}"></label>`;
    fields.forEach(([id, , key]) => bindNumber(id, dynamic, key, `Changed ${selected} trap timing`));
    for (const [id, key] of [["trapColor", "trapColor"], ["floorColor", "floorColor"]]) {
      $(`#${id}`).onchange = (event) => mutate(
        () => { dynamic[key] = event.target.value.toUpperCase(); },
        `Changed ${selected} trap color`,
      );
    }
  }

  function renderInspector() {
    if (!cells) return;
    const cell = cells[selected];
    $("#cellKey").textContent = selected;
    $("#cellSummary").textContent = `${stateName(cell.value)}${cell.dynamic ? " · dynamic" : ""}`;
    $("#cellValue").value = cell.value;
    $("#cellColor").value = cell.color;
    renderDynamic();
  }

  function validate() {
    const errors = [];
    if (!cells || Object.keys(cells).length !== 144 || cellKeys.some((key) => !cells[key])) {
      return ["Map must contain exactly 144 cells (A1–L12)."];
    }
    for (const key of cellKeys) {
      const cell = cells[key];
      if (![-1, 0, 1].includes(cell.value)) errors.push(`${key}: invalid physical state`);
      if (!/^#[0-9A-F]{6}$/i.test(cell.color)) errors.push(`${key}: invalid LED color`);
      if (cell.dynamic?.type === "cycle") {
        if (!(Number(cell.dynamic.intervalSeconds) > 0)) errors.push(`${key}: cycle interval must be positive`);
        if (!Array.isArray(cell.dynamic.pattern) || cell.dynamic.pattern.length < 2) errors.push(`${key}: cycle needs two states`);
      }
      if (cell.dynamic?.type === "delayed_trap") {
        if (!(Number(cell.dynamic.warnDurationSeconds) > 0)) errors.push(`${key}: warning duration must be positive`);
        if (!(Number(cell.dynamic.initialIntervalSeconds) > 0)) errors.push(`${key}: initial interval must be positive`);
        if (!(Number(cell.dynamic.minIntervalSeconds) > 0)) errors.push(`${key}: minimum interval must be positive`);
      }
    }
    return errors;
  }

  function renderDiagnostics() {
    const errors = validate();
    $("#diagnostics").innerHTML = errors.length
      ? errors.slice(0, 8).map((error) => `<div class="diagnostic">${error}</div>`).join("")
      : `<div class="diagnostic ok">Map is valid</div>`;
    return errors;
  }

  function render() {
    renderPresets();
    renderBoard();
    renderInspector();
    renderDiagnostics();
  }

  async function loadMaze() {
    setStatus("Loading installed Maze…");
    try {
      const response = await fetch("/api/editor/maze", { cache: "no-store" });
      const payload = await response.json();
      if (!response.ok || !payload.ok) throw new Error(payload.error || "Load failed");
      cells = payload.cells;
      selected = "A1";
      history = [];
      future = [];
      dirty = false;
      previewStarted = performance.now();
      render();
      setStatus(`Loaded ${payload.map}`, "ok");
    } catch (error) {
      setStatus(error.message, "error");
    }
  }

  async function saveMaze() {
    const errors = renderDiagnostics();
    if (errors.length) {
      setStatus("Fix map errors before saving", "error");
      return;
    }
    setStatus("Saving Maze…");
    try {
      const response = await fetch("/api/editor/maze", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ cells }),
      });
      const payload = await response.json();
      if (!response.ok || !payload.ok) throw new Error(payload.error || "Save failed");
      cells = payload.cells;
      dirty = false;
      history = [];
      future = [];
      render();
      setStatus(`Saved ${payload.map}. It will apply on the next Maze load.`, "ok");
    } catch (error) {
      setStatus(error.message, "error");
    }
  }

  function bind() {
    $("#reloadBtn").onclick = () => {
      if (!dirty || window.confirm("Discard unsaved changes and reload?")) loadMaze();
    };
    $("#saveBtn").onclick = saveMaze;
    $("#exportBtn").onclick = () => {
      const blob = new Blob([JSON.stringify(cells, null, 2) + "\n"], { type: "application/json" });
      const link = document.createElement("a");
      link.href = URL.createObjectURL(blob);
      link.download = "arcade-level-4.json";
      link.click();
      URL.revokeObjectURL(link.href);
    };
    $("#fileInput").onchange = async (event) => {
      try {
        const parsed = JSON.parse(await event.target.files[0].text());
        const imported = parsed.cells || parsed;
        if (Object.keys(imported).length !== 144) throw new Error("Imported map must have 144 cells");
        remember();
        cells = imported;
        selected = "A1";
        changed("Imported map — review and Save Maze to install it");
        render();
      } catch (error) {
        setStatus(error.message, "error");
      } finally {
        event.target.value = "";
      }
    };
    $("#undoBtn").onclick = () => {
      if (!history.length) return;
      future.push(clone(cells));
      cells = history.pop();
      changed("Undid change");
      render();
    };
    $("#redoBtn").onclick = () => {
      if (!future.length) return;
      history.push(clone(cells));
      cells = future.pop();
      changed("Redid change");
      render();
    };
    $("#fillBtn").onclick = () => {
      if (!activePreset) {
        setStatus("Choose a paint preset before filling", "error");
        return;
      }
      fillMode = true;
      setStatus("Click a cell to fill its connected region");
    };
    $("#clearDynamicBtn").onclick = () => {
      if (!cells[selected].dynamic) return;
      mutate(() => delete cells[selected].dynamic, `Made ${selected} static`);
    };
    $("#cellValue").onchange = (event) => mutate(
      () => { cells[selected].value = Number(event.target.value); },
      `Changed ${selected} physical state`,
    );
    $("#cellColor").onchange = (event) => mutate(
      () => { cells[selected].color = event.target.value.toUpperCase(); },
      `Changed ${selected} LED color`,
    );
    $("#dynamicType").onchange = (event) => {
      const kind = event.target.value;
      mutate(() => {
        if (!kind) delete cells[selected].dynamic;
        else if (kind === "cycle") cells[selected].dynamic = {
          type: "cycle", intervalSeconds: 2.5,
          pattern: [
            { value: cells[selected].value, color: cells[selected].color },
            { value: cells[selected].value === 1 ? 0 : 1, color: cells[selected].value === 1 ? "#F49400" : "#4DFF00" },
          ],
        };
        else cells[selected].dynamic = {
          type: "delayed_trap", armDelaySeconds: 4, warnDurationSeconds: 6,
          initialIntervalSeconds: 1.2, minIntervalSeconds: 0.12,
          trapColor: "#FF0000", floorColor: cells[selected].color,
        };
      }, `Changed ${selected} dynamic behavior`);
    };
    $("#previewToggle").onchange = () => {
      previewStarted = performance.now();
      renderBoard();
    };
    $("#restartPreviewBtn").onclick = () => {
      previewStarted = performance.now();
      renderBoard();
    };
    window.onbeforeunload = (event) => {
      if (!dirty) return undefined;
      event.preventDefault();
      return "Unsaved maze changes";
    };
    window.onkeydown = (event) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") {
        event.preventDefault();
        saveMaze();
      }
    };
    setInterval(() => {
      if (cells && $("#previewToggle").checked) renderBoard();
    }, 125);
  }

  bind();
  renderPresets();
  loadMaze();
})();
