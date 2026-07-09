import { createStore } from './store.js';
import { mountVerticalActuatorView } from './views/vertical-actuator-view.js';
import { mountHorizontalActuatorView } from './views/horizontal-actuator-view.js';
const store = createStore();
const root = document.getElementById('app');
root.innerHTML = `
  <div class="workspace-shell">
    <header class="workspace-header">
      <div class="workspace-meta">
        <div class="workspace-tag">Marble Maze Workspace</div>
        <h1>Actuator Architecture Explorer</h1>
        <p id="shell-summary">Compare a dense all-interior vertical-actuator field against a regular horizontal lattice that uses the same calculator model but reserves neighboring underfloor bay space beneath fixed tiles.</p>
        <div class="workspace-summary">
          <span class="summary-chip">Separate state and settings per architecture</span>
              <span class="summary-chip">Dedicated 2D editors and live stats</span>
          <span class="summary-chip">Independent Three.js scene builders</span>
        </div>
      </div>
      <nav class="workspace-switcher" aria-label="Architecture switcher">
        <button class="switch-button" data-view="vertical" type="button">
          <strong>Vertical Actuator</strong>
          <span>Dense interior field. Border is the only permanently static structure.</span>
        </button>
        <button class="switch-button" data-view="horizontal" type="button">
          <strong>Horizontal Actuator</strong>
          <span>Same core calculator as Vertical, but fixed floors and walls reserve neighboring underfloor bay space.</span>
        </button>
      </nav>
    </header>
    <main id="view-root"></main>
  </div>
`;
const summary = root.querySelector('#shell-summary');
const viewRoot = root.querySelector('#view-root');
const buttons = Array.from(root.querySelectorAll('[data-view]'));
let cleanup = null;
function updateShell(state) {
  buttons.forEach((button) => button.classList.toggle('is-active', button.dataset.view === state.currentView));
  summary.textContent = state.currentView === 'vertical'
    ? 'Vertical Actuator treats every interior tile as its own motion-capable unit. Use the dense editor to explore open floor, blocker, and hole states.'
    : 'Horizontal Actuator uses the same mechanism calculator inputs as Vertical, but fixed floors and walls stay baked into the lattice and reserve adjacent underfloor bay space.';
}
function mountCurrentView(state) {
  if (cleanup) cleanup();
  cleanup = state.currentView === 'vertical' ? mountVerticalActuatorView(viewRoot, store) : mountHorizontalActuatorView(viewRoot, store);
}
buttons.forEach((button) => button.addEventListener('click', () => store.setView(button.dataset.view)));
store.subscribe((state, previous) => { updateShell(state); if (!previous || state.currentView !== previous.currentView) mountCurrentView(state); });
const initial = store.getState();
updateShell(initial);
mountCurrentView(initial);
