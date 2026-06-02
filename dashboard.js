/* ============================================================
   Game Pipeline dashboard - shared engine (V2 + iGaming)
   Parameterized by window.PROJECT. Consumes the Jira-built data
   contract from dashboard-data-{project}.js:
       const GAMES   = [...]
       const SPRINTS = [{id,label,start,end}, ...]
       const REFRESHED_AT = 'YYYY-MM-DD HH:MM'
   ============================================================ */
(function () {
"use strict";

// --- Project parameters (from the thin shell) -------------------------------
const PROJECT = window.PROJECT || {
  key: 'v2', title: 'Game Pipeline', subtitle: '',
  jira_project: 'V2', sprint_field: 'customfield_10020', ls_prefix: 'gp_v2_',
};
const LS = PROJECT.ls_prefix;
const BASE = 'https://ponggamestudios.atlassian.net/browse/';

// --- Data (defensive: tolerate a missing/empty data file) -------------------
// GAMES / SPRINTS / REFRESHED_AT are top-level consts from dashboard-data-*.js
// (shared global lexical scope across classic scripts). Read them directly —
// do NOT redeclare them here or we shadow the globals.
const RAW_GAMES = (typeof GAMES !== 'undefined' && GAMES) ? GAMES.slice() : [];
const RAW_SPRINTS = (typeof SPRINTS !== 'undefined' && SPRINTS) ? SPRINTS : [];
const REFRESHED = (typeof REFRESHED_AT !== 'undefined' && REFRESHED_AT) ? REFRESHED_AT : null;

document.title = PROJECT.title + ' — Pong Game Studios';

// ============================================================
//  TIME / SPRINT ENGINE
// ============================================================
const SPRINT_LIST = RAW_SPRINTS.slice().sort((a, b) => new Date(a.start) - new Date(b.start));
const SPRINT_BY_ID = {};
SPRINT_LIST.forEach(s => { SPRINT_BY_ID[String(s.id)] = s; });

const CHART_START = SPRINT_LIST.length ? new Date(SPRINT_LIST[0].start) : new Date('2026-05-11');
const CHART_END = SPRINT_LIST.length
  ? new Date(SPRINT_LIST[SPRINT_LIST.length - 1].end || SPRINT_LIST[SPRINT_LIST.length - 1].start)
  : new Date('2027-12-07');

// TODAY = snapshot date from REFRESHED_AT (reproducible), else system date.
const TODAY = REFRESHED ? new Date(REFRESHED.slice(0, 10) + 'T00:00:00') : new Date();

function pct(d) {
  const dt = d instanceof Date ? d : new Date(d);
  const total = CHART_END - CHART_START;
  if (!total) return 0;
  return Math.max(0, Math.min(100, ((dt - CHART_START) / total) * 100));
}
function fmtD(d) {
  const dt = d instanceof Date ? d : new Date(d);
  return dt.toLocaleDateString('en-CA', { month: 'short', day: 'numeric', year: '2-digit' });
}
function fmtRange(s, e) {
  const ds = new Date(s), de = new Date(e || s);
  return ds.toLocaleDateString('en-CA', { month: 'short', day: 'numeric' }) +
    ' – ' + de.toLocaleDateString('en-CA', { month: 'short', day: 'numeric' });
}
function sprintEnd(s) {
  if (s.end) return new Date(s.end);
  const d = new Date(s.start); d.setDate(d.getDate() + 13); return d;  // 14-day cadence
}

// ============================================================
//  CONFIGURATION (editable in Plan Mode, persisted, namespaced)
// ============================================================
const DEFAULT_CONFIG = {
  statuses: [
    { key: 'Not Started',   cls: 's-notstart' },
    { key: 'In Pre-Prod',   cls: 's-preprod' },
    { key: 'In Production', cls: 's-prod' },
    { key: 'In QA',         cls: 's-qa' },
    { key: 'Bug Fixing',    cls: 's-bug' },
    { key: 'On Hold',       cls: 's-hold' },
    { key: 'Signed Off',    cls: 's-signed' },
  ],
  stages: [
    { key: 'concept', label: 'Concept', color: '#fde68a' },
    { key: 'art',     label: 'Art',     color: '#fed7aa' },
    { key: 'design',  label: 'Design',  color: '#bfdbfe' },
    { key: 'math',    label: 'Math',    color: '#bbf7d0' },
    { key: 'dev',     label: 'Dev',     color: '#93c5fd' },
    { key: 'sound',   label: 'Sound',   color: '#f5d0e0' },
    { key: 'qa',      label: 'QA',      color: '#fcd34d' },
    { key: 'bugfix',  label: 'Bug Fix', color: '#fecaca' },
    { key: 'done',    label: 'Done',    color: '#86efac' },
  ],
  sizes: [
    { key: 'XS', label: 'Extra Small' },
    { key: 'S',  label: 'Small' },
    { key: 'M',  label: 'Medium' },
    { key: 'L',  label: 'Large' },
    { key: 'XL', label: 'Extra Large' },
  ],
  capacities: { art: 240, design: 80, math: 320, dev: 480, sound: 160, qa: 200 },
};
let CONFIG = JSON.parse(JSON.stringify(DEFAULT_CONFIG));
try {
  const saved = localStorage.getItem(LS + 'config');
  if (saved) CONFIG = { ...CONFIG, ...JSON.parse(saved) };
} catch (e) {}

// ============================================================
//  USER MUTATIONS (persisted, namespaced per project)
// ============================================================
let USER_ORDER = [];
let USER_STATUS = {};
let USER_SIZES = {};      // game name -> {art,math,dev,sound}
let HIDDEN = new Set();
try { USER_ORDER  = JSON.parse(localStorage.getItem(LS + 'order')  || '[]'); } catch (e) {}
try { USER_STATUS = JSON.parse(localStorage.getItem(LS + 'status') || '{}'); } catch (e) {}
try { USER_SIZES  = JSON.parse(localStorage.getItem(LS + 'sizes')  || '{}'); } catch (e) {}
try { HIDDEN = new Set(JSON.parse(localStorage.getItem(LS + 'hidden') || '[]')); } catch (e) {}

// Apply saved order
if (USER_ORDER.length > 0) {
  const byName = Object.fromEntries(RAW_GAMES.map(g => [g.name, g]));
  const ordered = USER_ORDER.map(n => byName[n]).filter(Boolean);
  const missing = RAW_GAMES.filter(g => !USER_ORDER.includes(g.name));
  RAW_GAMES.length = 0;
  RAW_GAMES.push(...ordered, ...missing);
}
// Apply saved status overrides (default already 'Not Started' from builder)
RAW_GAMES.forEach(g => { if (USER_STATUS[g.name]) g.workflow_status = USER_STATUS[g.name]; });

function saveOrder()  { try { localStorage.setItem(LS + 'order',  JSON.stringify(RAW_GAMES.map(g => g.name))); } catch (e) {} }
function saveStatus() { try { localStorage.setItem(LS + 'status', JSON.stringify(USER_STATUS)); } catch (e) {} }
function saveSizes()  { try { localStorage.setItem(LS + 'sizes',  JSON.stringify(USER_SIZES)); } catch (e) {} }
function saveConfig() { try { localStorage.setItem(LS + 'config', JSON.stringify(CONFIG)); } catch (e) {} }

// ============================================================
//  STATE
// ============================================================
let activeFilters = { status: 'ALL', stage: 'ALL', search: '' };
let currentView = 'roadmap';
let planMode = false;
let openPanel = null;
let dragSrcIdx = null;

// ============================================================
//  HELPERS
// ============================================================
function statusCls(s) {
  const cfg = CONFIG.statuses.find(x => x.key === s);
  return cfg ? cfg.cls : 's-notstart';
}
function stageCls(s) { return 'stage-bg-' + s; }
function stageLabel(k) {
  const cfg = CONFIG.stages.find(x => x.key === k);
  return cfg ? cfg.label : k;
}
function sizeCls(v) { return v && ['XS', 'S', 'M', 'L', 'XL'].includes(v) ? 'sz-' + v : 'sz-NA'; }
function gameColor(g) {
  const stage = CONFIG.stages.find(s => s.key === g.current_stage);
  return stage ? stage.color : '#94a3b8';
}
// resolve a discipline's active sprint objects (>= S1), chronological
function discSprints(disc) {
  const ids = (disc && disc.sprints) ? disc.sprints : [];
  return ids.map(id => SPRINT_BY_ID[String(id)]).filter(Boolean)
            .sort((a, b) => new Date(a.start) - new Date(b.start));
}
function gameSizes(g) { return USER_SIZES[g.name] || {}; }
function hasAnySize(g) {
  const s = gameSizes(g);
  return ['art', 'math', 'dev', 'sound'].some(k => s[k]);
}

const LANE_ORDER = ['art', 'design', 'math', 'dev', 'sound', 'qa'];

// ============================================================
//  SKELETON (thin shell -> engine injects all markup)
// ============================================================
function buildSkeleton() {
  document.body.innerHTML = `
  <div class="hdr">
    <div>
      <h1>${PROJECT.title}</h1>
      <p>${PROJECT.subtitle || ''} · ${SPRINT_LIST.length ? 'Sprint axis from ' + SPRINT_LIST[0].label : ''}</p>
      <div class="refresh-meta"><div class="refresh-dot"></div>Phase 1 · Jira-sourced · <span id="hdrCount">0</span> game epics${REFRESHED ? ' · refreshed ' + REFRESHED : ''}</div>
    </div>
    <div class="hdr-actions">
      <button class="btn" id="planToggle">✎ Plan Mode</button>
    </div>
  </div>

  <div class="plan-banner" id="planBanner">
    <div class="plan-banner-l">PLAN MODE — drag rows to reprioritize · use dropdowns to change workflow status · set sizes & config below</div>
    <button class="btn" id="planSave">✓ Save plan</button>
  </div>

  <div class="plan-config" id="planConfig">
    <div class="pc-title">⚙ DASHBOARD CONFIGURATION — what shows up in the dropdowns</div>
    <div class="pc-grid">
      <div class="pc-col">
        <h4>WORKFLOW STATUSES</h4>
        <p>Manual · plain pill on row</p>
        <div class="pc-list" id="pcStatuses"></div>
        <div class="pc-add" id="pcAddStatus">+ add status</div>
      </div>
      <div class="pc-col">
        <h4>LIFECYCLE STAGES</h4>
        <p>Auto-derived from latest active sprint</p>
        <div class="pc-list" id="pcStages"></div>
      </div>
      <div class="pc-col">
        <h4>SIZE SCALE</h4>
        <p>Used by the per-game size overrides below</p>
        <div class="pc-list" id="pcSizes"></div>
      </div>
    </div>
    <div class="pc-sizes-wrap">
      <h4 style="font-size:10px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.4px;margin-bottom:8px">PER-GAME SIZE OVERRIDES <span style="font-weight:400;font-style:italic;text-transform:none;color:var(--sub)">— manual; not from Jira (Decision #25)</span></h4>
      <div class="pc-sizes-head"><span class="nm">Game</span><span>Art</span><span>Math</span><span>Dev</span><span>Sound</span></div>
      <div id="pcSizeGames"></div>
    </div>
  </div>

  <div class="kpi-strip" id="kpiStrip"></div>

  <div class="filter-bar" id="filterBar">
    <div class="fb-group" id="fbStatusGroup"><span class="fb-label">STATUS</span></div>
    <div class="fb-group" id="fbStageGroup"><span class="fb-label">STAGE</span></div>
    <div class="fb-spacer"></div>
    <input class="fb-search" id="fbSearch" placeholder="🔍 Search games…">
    <div class="view-toggle" id="viewToggle">
      <button class="on" data-view="roadmap">Roadmap</button>
      <button data-view="heatmap">Heatmap</button>
      <button data-view="list">List</button>
    </div>
  </div>

  <div id="emptyState" class="empty-state" style="display:none">
    <h2>No data yet</h2>
    <p>This dashboard has no games to show. Run the Jira builder to populate it:<br>
    <code>python build_jira_data.py --project ${PROJECT.key}</code><br>
    then reload this page.</p>
  </div>

  <div id="roadmapView">
    <div class="axis" id="axis"></div>
    <div id="rows"></div>
  </div>

  <div id="heatmapView" style="display:none">
    <div class="heatmap-wrap">
      <h3 style="font-size:13px;font-weight:600;color:var(--text);margin:0 0 4px">Discipline Hour-Load Heatmap</h3>
      <p style="font-size:11px;color:var(--muted);margin:0">Estimated remaining hours demanded per discipline per month, allocated across each discipline's active sprints. Cells turn red when load exceeds the editable capacity ceiling.</p>
      <div class="heatmap-grid" id="heatmapGrid"></div>
    </div>
  </div>

  <div id="listView" style="display:none">
    <div class="list-view" id="listBody"></div>
  </div>

  <div class="toast" id="toast"></div>

  <div class="footer">
    Pong Game Studios PMO · ${PROJECT.title} · shared engine (dashboard.js / dashboard.css) · data via <code>build_jira_data.py</code> · localStorage keys: <code>${LS}*</code>
  </div>`;
}

// ============================================================
//  FILTER BAR (built from CONFIG)
// ============================================================
function buildFilterBar() {
  const statusGroup = document.getElementById('fbStatusGroup');
  const stageGroup = document.getElementById('fbStageGroup');
  const statusKeys = ['ALL', 'In Production', 'In QA', 'Not Started', 'Signed Off', 'On Hold'];
  statusKeys.forEach(k => {
    const c = document.createElement('span');
    c.className = 'fb-chip' + (k === 'ALL' ? ' on' : '');
    c.dataset.filterStatus = k;
    c.textContent = k === 'ALL' ? 'All' : k;
    statusGroup.appendChild(c);
  });
  const stageKeys = ['ALL', 'art', 'design', 'math', 'dev', 'sound', 'qa'];
  stageKeys.forEach(k => {
    const c = document.createElement('span');
    c.className = 'fb-chip discipline' + (k === 'ALL' ? ' on' : '');
    c.dataset.filterStage = k;
    c.textContent = k === 'ALL' ? 'All' : stageLabel(k);
    stageGroup.appendChild(c);
  });
  document.querySelectorAll('[data-filter-status]').forEach(chip => {
    chip.addEventListener('click', () => {
      document.querySelectorAll('[data-filter-status]').forEach(c => c.classList.remove('on'));
      chip.classList.add('on');
      activeFilters.status = chip.dataset.filterStatus;
      renderRows();
    });
  });
  document.querySelectorAll('[data-filter-stage]').forEach(chip => {
    chip.addEventListener('click', () => {
      document.querySelectorAll('[data-filter-stage]').forEach(c => c.classList.remove('on'));
      chip.classList.add('on');
      activeFilters.stage = chip.dataset.filterStage;
      renderRows();
    });
  });
  document.getElementById('fbSearch').addEventListener('input', e => {
    activeFilters.search = e.target.value;
    renderRows();
  });
}

// ============================================================
//  KPI
// ============================================================
function renderKPI() {
  const total = RAW_GAMES.length;
  const signed = RAW_GAMES.filter(g => g.workflow_status === 'Signed Off').length;
  const inflight = RAW_GAMES.filter(g => ['In Production', 'In QA', 'In Pre-Prod', 'Bug Fixing'].includes(g.workflow_status)).length;
  const notstart = RAW_GAMES.filter(g => g.workflow_status === 'Not Started').length;
  const over = RAW_GAMES.filter(g => g.spent > g.est && g.est > 0).length;
  const overGames = RAW_GAMES.filter(g => g.spent > g.est && g.est > 0).slice(0, 2)
    .map(g => `${g.name.split(' ')[0]} +${Math.round(g.spent - g.est)}h`).join(' · ');

  document.getElementById('kpiStrip').innerHTML = `
    <div class="kpi" style="--rc:#2563eb"><div class="kpi-v">${total}</div><div class="kpi-l">TOTAL GAMES</div><div class="kpi-d">${PROJECT.jira_project} epics</div></div>
    <div class="kpi" style="--rc:#16a34a"><div class="kpi-v">${signed}</div><div class="kpi-l">SIGNED OFF</div><div class="kpi-d">Completed releases</div></div>
    <div class="kpi" style="--rc:#d97706"><div class="kpi-v">${inflight}</div><div class="kpi-l">IN FLIGHT</div><div class="kpi-d">Pre-prod / production / QA</div></div>
    <div class="kpi" style="--rc:#7c3aed"><div class="kpi-v">${notstart}</div><div class="kpi-l">NOT STARTED</div><div class="kpi-d">Future pipeline</div></div>
    <div class="kpi" style="--rc:#dc2626"><div class="kpi-v">${over}</div><div class="kpi-l">OVER ESTIMATE</div><div class="kpi-d">${overGames || '—'}</div></div>
  `;
}

// ============================================================
//  SPRINT AXIS
// ============================================================
function renderAxis() {
  const axisEl = document.getElementById('axis');
  axisEl.innerHTML = '';
  if (!SPRINT_LIST.length) return;

  // stride so chips don't overcrowd
  const stride = Math.max(1, Math.ceil(SPRINT_LIST.length / 16));
  SPRINT_LIST.forEach((s, i) => {
    const end = sprintEnd(s);
    const mid = new Date((new Date(s.start).getTime() + end.getTime()) / 2);
    if (i % stride === 0) {
      const chip = document.createElement('div');
      chip.className = 'sp-chip';
      chip.style.left = pct(mid) + '%';
      chip.innerHTML = `${s.label}<small>${fmtRange(s.start, end)}</small>`;
      axisEl.appendChild(chip);
    }
  });

  // TODAY chip
  if (TODAY >= CHART_START && TODAY <= CHART_END) {
    const t = document.createElement('div');
    t.className = 'ax-today';
    t.style.left = pct(TODAY) + '%';
    t.textContent = 'TODAY';
    axisEl.appendChild(t);
  }
}

// ============================================================
//  ROW RENDERING
// ============================================================
function renderRow(g, idx) {
  const item = document.createElement('div');
  item.className = 'fv-item';
  item.dataset.idx = idx;
  item.dataset.name = g.name;
  if (HIDDEN.has(g.name)) item.style.display = 'none';

  const row = document.createElement('div');
  row.className = 'epic-row';
  if (openPanel === g.name) row.classList.add('open');
  row.style.setProperty('--rc', gameColor(g));

  const dh = document.createElement('div');
  dh.className = 'drag-handle';
  dh.innerHTML = '⠿';

  const label = document.createElement('div');
  label.className = 'epic-label';
  const depIcon = (g.dependencies && g.dependencies.length)
    ? `<span class="dep-icon" title="Depends on ${g.dependencies.join(', ')}">🔗</span>` : '';
  const statusOptions = CONFIG.statuses.map(s =>
    `<option value="${s.key}"${s.key === g.workflow_status ? ' selected' : ''}>${s.key}</option>`).join('');

  // Size chips ONLY when a manual override exists (Decision #25)
  let sizeRow = '';
  if (hasAnySize(g)) {
    const sz = gameSizes(g);
    sizeRow = `<div class="size-row">
      <span class="size-chip"><span class="size-chip-name">A</span><span class="size-chip-val ${sizeCls(sz.art)}">${sz.art || '—'}</span></span>
      <span class="size-chip"><span class="size-chip-name">M</span><span class="size-chip-val ${sizeCls(sz.math)}">${sz.math || '—'}</span></span>
      <span class="size-chip"><span class="size-chip-name">D</span><span class="size-chip-val ${sizeCls(sz.dev)}">${sz.dev || '—'}</span></span>
      <span class="size-chip"><span class="size-chip-name">S</span><span class="size-chip-val ${sizeCls(sz.sound)}">${sz.sound || '—'}</span></span>
    </div>`;
  }

  label.innerHTML = `
    <div class="epic-name">${g.name}${depIcon}</div>
    <div class="chip-row">
      ${g.jira ? `<a class="epic-jira" href="${BASE}${g.jira}" target="_blank" onclick="event.stopPropagation()">${g.jira}</a>` : ''}
      <span class="epic-tag">#${idx + 1}</span>
      <span class="epic-stage ${stageCls(g.current_stage)}">${stageLabel(g.current_stage)}</span>
      <span class="epic-status ${statusCls(g.workflow_status)}">${g.workflow_status}</span>
      <span class="plan-status-edit"><select data-game="${g.name}">${statusOptions}</select></span>
    </div>
    ${sizeRow}
  `;

  // Timeline track with sprint markers
  const track = document.createElement('div');
  track.className = 'epic-track';

  // faint sprint boundary lines
  SPRINT_LIST.forEach(s => {
    const line = document.createElement('div');
    line.className = 'sp-line';
    line.style.left = pct(s.start) + '%';
    track.appendChild(line);
  });
  // today line
  if (TODAY >= CHART_START && TODAY <= CHART_END) {
    const todayLine = document.createElement('div');
    todayLine.className = 'today-line-row';
    todayLine.style.left = pct(TODAY) + '%';
    track.appendChild(todayLine);
  }

  // per-discipline sprint markers
  let laneTop = 8;
  LANE_ORDER.forEach(dKey => {
    const disc = g.disciplines ? g.disciplines.find(d => d.key === dKey) : null;
    const sprs = discSprints(disc);
    if (!sprs.length) return;
    sprs.forEach(s => {
      const end = sprintEnd(s);
      const l = pct(s.start);
      const w = Math.max(pct(end) - l, 0.8);
      const chip = document.createElement('div');
      chip.className = 'lane-spr lane-' + dKey;
      chip.style.left = l + '%';
      chip.style.width = w + '%';
      chip.style.top = laneTop + 'px';
      chip.title = `${dKey.toUpperCase()} · ${s.label} (${fmtRange(s.start, end)})`;
      chip.textContent = w > 3 ? s.label : '';
      track.appendChild(chip);
    });
    laneTop += 11;
  });
  if (laneTop === 8) {
    const none = document.createElement('div');
    none.style.cssText = 'font-size:9px;color:var(--sub);font-style:italic;padding-top:6px';
    none.textContent = 'No scheduled sprints yet';
    track.appendChild(none);
  }

  // Hours column
  const hrs = document.createElement('div');
  hrs.className = 'epic-hrs';
  const over = g.spent > g.est && g.est > 0;
  const progressPct = g.est > 0 ? Math.min(100, Math.round(g.spent / g.est * 100)) : 0;
  const progressColor = over ? '#dc2626' : (progressPct >= 70 ? gameColor(g) : '#60a5fa');
  hrs.innerHTML = `
    <div class="epic-hrs-v ${over ? 'over' : ''}">${Math.round(g.spent)}h</div>
    <div class="epic-hrs-l">SPENT / ${Math.round(g.est)}h est</div>
    <div class="epic-prog"><div class="epic-prog-fill" style="width:${progressPct}%;background:${progressColor}"></div></div>
    <div class="epic-hrs-l" style="color:${over ? '#dc2626' : 'var(--sub)'};margin-top:4px">${over ? `⚠ +${Math.round(g.spent - g.est)}h over` : `${progressPct}% spent`}</div>
  `;

  const chev = document.createElement('div');
  chev.className = 'chev';
  chev.textContent = '⌄';

  row.appendChild(dh);
  row.appendChild(label);
  row.appendChild(track);
  row.appendChild(hrs);
  row.appendChild(chev);

  row.addEventListener('click', e => {
    if (e.target.closest('.drag-handle') || e.target.closest('select') || e.target.closest('a')) return;
    openPanel = (openPanel === g.name) ? null : g.name;
    renderRows();
  });

  label.querySelectorAll('select[data-game]').forEach(sel => {
    sel.addEventListener('change', e => {
      e.stopPropagation();
      const name = sel.dataset.game;
      const game = RAW_GAMES.find(x => x.name === name);
      if (game) {
        game.workflow_status = sel.value;
        USER_STATUS[name] = sel.value;
        saveStatus();
        showToast(`✓ ${name} → ${sel.value}`);
        renderRows();
        renderKPI();
      }
    });
    sel.addEventListener('click', e => e.stopPropagation());
  });

  // Drag and drop
  item.draggable = true;
  item.addEventListener('dragstart', e => {
    if (!planMode) { e.preventDefault(); return; }
    dragSrcIdx = idx;
    e.dataTransfer.effectAllowed = 'move';
    row.classList.add('dragging');
  });
  item.addEventListener('dragend', () => {
    row.classList.remove('dragging');
    document.querySelectorAll('.fv-item').forEach(el => el.classList.remove('drag-over-above', 'drag-over-below'));
  });
  item.addEventListener('dragover', e => {
    if (!planMode || dragSrcIdx === null || dragSrcIdx === idx) return;
    e.preventDefault();
    const rect = item.getBoundingClientRect();
    const mid = rect.top + rect.height / 2;
    item.classList.toggle('drag-over-above', e.clientY < mid);
    item.classList.toggle('drag-over-below', e.clientY >= mid);
  });
  item.addEventListener('dragleave', () => item.classList.remove('drag-over-above', 'drag-over-below'));
  item.addEventListener('drop', e => {
    e.preventDefault();
    if (!planMode || dragSrcIdx === null || dragSrcIdx === idx) return;
    const rect = item.getBoundingClientRect();
    const insertBefore = e.clientY < rect.top + rect.height / 2;
    const [moved] = RAW_GAMES.splice(dragSrcIdx, 1);
    let ti = RAW_GAMES.indexOf(g);
    if (!insertBefore) ti++;
    if (dragSrcIdx < idx) ti--;
    RAW_GAMES.splice(ti < 0 ? 0 : ti, 0, moved);
    dragSrcIdx = null;
    saveOrder();
    showToast(`✓ ${moved.name} reprioritized`);
    renderRows();
  });

  item.appendChild(row);
  if (openPanel === g.name) item.appendChild(renderDetail(g));
  return item;
}

function renderDetail(g) {
  const panel = document.createElement('div');
  panel.className = 'detail open';
  panel.style.setProperty('--rc', gameColor(g));

  const head = document.createElement('div');
  head.className = 'detail-head';
  head.innerHTML = `
    <h3>${g.name} — Lifecycle Detail</h3>
    <div class="meta"><strong>Stage</strong>: ${stageLabel(g.current_stage)} · <strong>Status</strong>: ${g.workflow_status} · <strong>Lead Dev</strong>: ${g.dev_name || '—'}</div>
  `;
  panel.appendChild(head);

  const tabs = document.createElement('div');
  tabs.className = 'detail-tabs';
  tabs.innerHTML = `
    <div class="detail-tab active" data-tab="timeline">TIMELINE</div>
    <div class="detail-tab" data-tab="milestones">SPRINTS</div>
    <div class="detail-tab" data-tab="hours">HOURS</div>
  `;
  panel.appendChild(tabs);

  const body = document.createElement('div');
  body.className = 'detail-body';

  // Timeline tab: per-discipline span (first->last active sprint)
  const tlPane = document.createElement('div');
  LANE_ORDER.forEach(dKey => {
    const disc = g.disciplines ? g.disciplines.find(d => d.key === dKey) : null;
    if (!disc) return;
    const sprs = discSprints(disc);
    const row = document.createElement('div');
    row.className = 'disc-row';
    let bar = '';
    if (sprs.length) {
      const start = new Date(sprs[0].start);
      const end = sprintEnd(sprs[sprs.length - 1]);
      const l = pct(start);
      const w = Math.max(pct(end) - l, 0.6);
      bar = `<div class="disc-bar lane-${dKey}" style="left:${l}%;width:${w}%;color:rgba(0,0,0,.6)">${fmtD(start)} → ${fmtD(end)}</div>`;
    }
    const hrsOver = disc.spent > disc.est && disc.est > 0;
    row.innerHTML = `
      <div class="disc-label">${dKey}</div>
      <div class="disc-track">${bar}</div>
      <div class="disc-hrs">${Math.round(disc.spent)} / ${Math.round(disc.est)}h ${hrsOver ? '<span class="over">⚠</span>' : ''}</div>
    `;
    tlPane.appendChild(row);
  });
  body.appendChild(tlPane);

  // Sprints tab: each active sprint per discipline
  const msPane = document.createElement('div');
  msPane.style.display = 'none';
  msPane.dataset.pane = 'milestones';
  const allSprints = [];
  (g.disciplines || []).forEach(d => discSprints(d).forEach(s =>
    allSprints.push({ stage: d.key, disc: d.name, label: s.label, start: s.start, end: sprintEnd(s) })));
  allSprints.sort((a, b) => new Date(a.start) - new Date(b.start));
  if (!allSprints.length) {
    msPane.innerHTML = '<div style="font-size:11px;color:var(--sub);font-style:italic;padding:10px">No scheduled sprints yet.</div>';
  } else {
    const list = document.createElement('div');
    list.className = 'markers-list';
    allSprints.forEach(m => {
      const it = document.createElement('div');
      it.className = 'marker-item';
      it.innerHTML = `
        <div class="marker-date">${fmtRange(m.start, m.end)}</div>
        <div class="marker-stage stage-bg-${m.stage}">${m.label}</div>
        <div style="flex:1">${m.disc}</div>
      `;
      list.appendChild(it);
    });
    msPane.appendChild(list);
  }
  body.appendChild(msPane);

  // Hours tab
  const hrsPane = document.createElement('div');
  hrsPane.style.display = 'none';
  hrsPane.dataset.pane = 'hours';
  const hoursRows = (g.disciplines || []).map(d => {
    const ratio = d.est > 0 ? d.spent / d.est : 0;
    const over = ratio > 1;
    const pctNum = Math.round(ratio * 100);
    const barColor = over ? '#dc2626' : '#2563eb';
    return `<div class="disc-row">
      <div class="disc-label">${d.key}</div>
      <div class="disc-track"><div class="disc-bar" style="left:0;width:${Math.min(100, pctNum)}%;background:${barColor};color:#fff">${pctNum}%</div></div>
      <div class="disc-hrs">${Math.round(d.spent)} / ${Math.round(d.est)}h ${over ? '<span class="over">⚠</span>' : ''}</div>
    </div>`;
  }).join('');
  hrsPane.innerHTML = hoursRows || '<div style="font-size:11px;color:var(--sub);font-style:italic;padding:10px">No hours data.</div>';
  body.appendChild(hrsPane);

  panel.appendChild(body);

  tabs.querySelectorAll('.detail-tab').forEach(t => {
    t.addEventListener('click', e => {
      e.stopPropagation();
      tabs.querySelectorAll('.detail-tab').forEach(x => x.classList.remove('active'));
      t.classList.add('active');
      tlPane.style.display = t.dataset.tab === 'timeline' ? 'block' : 'none';
      msPane.style.display = t.dataset.tab === 'milestones' ? 'block' : 'none';
      hrsPane.style.display = t.dataset.tab === 'hours' ? 'block' : 'none';
    });
  });
  return panel;
}

function renderRows() {
  const rowsEl = document.getElementById('rows');
  rowsEl.innerHTML = '';
  const filtered = RAW_GAMES.filter(g => {
    if (activeFilters.status !== 'ALL' && g.workflow_status !== activeFilters.status) return false;
    if (activeFilters.stage !== 'ALL' && g.current_stage !== activeFilters.stage) return false;
    if (activeFilters.search && !g.name.toLowerCase().includes(activeFilters.search.toLowerCase())) return false;
    return true;
  });
  filtered.forEach(g => rowsEl.appendChild(renderRow(g, RAW_GAMES.indexOf(g))));
  if (filtered.length === 0) {
    rowsEl.innerHTML = '<div style="text-align:center;padding:30px;color:var(--sub);font-size:12px;font-style:italic">No games match your filters.</div>';
  }
}

// ============================================================
//  HEATMAP  (Decision #26: sprints-per-month allocation)
// ============================================================
function monthsForHeatmap() {
  const months = [];
  const start = new Date(CHART_START.getFullYear(), CHART_START.getMonth(), 1);
  for (let i = 0; i < 12; i++) months.push(new Date(start.getFullYear(), start.getMonth() + i, 1));
  return months;
}
function renderHeatmap() {
  const grid = document.getElementById('heatmapGrid');
  const months = monthsForHeatmap();
  grid.style.gridTemplateColumns = `280px repeat(${months.length}, 1fr)`;

  let html = `<div class="hm-row-label" style="background:var(--surf3)">DISCIPLINE \\ MONTH</div>`;
  months.forEach(m => { html += `<div class="hm-header">${m.toLocaleString('en-CA', { month: 'short', year: '2-digit' })}</div>`; });

  const disciplines = [
    { key: 'art', icon: '🎨', name: 'Art / Creative' },
    { key: 'design', icon: '📐', name: 'Design' },
    { key: 'math', icon: '🧮', name: 'Math' },
    { key: 'dev', icon: '💻', name: 'Development' },
    { key: 'sound', icon: '🎵', name: 'Sound' },
    { key: 'qa', icon: '🧪', name: 'QA' },
  ];

  disciplines.forEach(d => {
    const cap = CONFIG.capacities[d.key] || 200;
    html += `<div class="hm-row-label">${d.icon} ${d.name} <small>cap ${cap}h/mo</small></div>`;
    months.forEach(m => {
      const monthStart = m;
      const monthEnd = new Date(m.getFullYear(), m.getMonth() + 1, 0);
      let totalHours = 0, gameCount = 0;
      RAW_GAMES.forEach(g => {
        const disc = g.disciplines ? g.disciplines.find(x => x.key === d.key) : null;
        const sprs = discSprints(disc);
        if (!sprs.length) return;
        const total = sprs.length;
        let activeInMonth = 0;
        sprs.forEach(s => {
          const ss = new Date(s.start), se = sprintEnd(s);
          if (se >= monthStart && ss <= monthEnd) activeInMonth++;
        });
        if (!activeInMonth) return;
        const remaining = Math.max(0, disc.est - disc.spent);
        totalHours += remaining * (activeInMonth / total);
        gameCount++;
      });
      totalHours = Math.round(totalHours);
      let loadCls = 'hm-load-0';
      if (totalHours > cap) loadCls = 'hm-load-4';
      else if (totalHours > cap * 0.75) loadCls = 'hm-load-3';
      else if (totalHours > cap * 0.4) loadCls = 'hm-load-2';
      else if (totalHours > 0) loadCls = 'hm-load-1';
      html += `<div class="hm-cell ${loadCls}"><div class="hm-cell-h">${totalHours}h${totalHours > cap ? ' ⚠' : ''}</div><div class="hm-cell-g">${gameCount} game${gameCount !== 1 ? 's' : ''}</div></div>`;
    });
  });
  grid.innerHTML = html;
}

// ============================================================
//  LIST VIEW
// ============================================================
function renderList() {
  const listBody = document.getElementById('listBody');
  let html = `<div class="list-row head">
    <div>#</div><div>GAME</div><div>JIRA</div><div>STAGE</div><div>STATUS</div><div>LEAD</div><div>HOURS</div><div>PROGRESS</div>
  </div>`;
  RAW_GAMES.forEach((g, i) => {
    if (HIDDEN.has(g.name)) return;
    const over = g.spent > g.est && g.est > 0;
    const pctNum = g.est > 0 ? Math.round(g.spent / g.est * 100) : 0;
    html += `<div class="list-row">
      <div class="num">${i + 1}</div>
      <div><strong>${g.name}</strong></div>
      <div>${g.jira ? `<a href="${BASE}${g.jira}" target="_blank">${g.jira}</a>` : '—'}</div>
      <div><span class="epic-stage ${stageCls(g.current_stage)}">${stageLabel(g.current_stage)}</span></div>
      <div><span class="epic-status ${statusCls(g.workflow_status)}">${g.workflow_status}</span></div>
      <div style="font-family:'IBM Plex Mono';font-size:10px;color:var(--muted)">${g.dev_name || '—'}</div>
      <div style="font-family:'IBM Plex Mono';font-size:10px;${over ? 'color:#dc2626;font-weight:600' : ''}">${Math.round(g.spent)} / ${Math.round(g.est)}h</div>
      <div style="font-family:'IBM Plex Mono';font-size:10px">${pctNum}%</div>
    </div>`;
  });
  listBody.innerHTML = html;
}

// ============================================================
//  PLAN CONFIG
// ============================================================
function renderPlanConfig() {
  const statusList = document.getElementById('pcStatuses');
  statusList.innerHTML = CONFIG.statuses.map((s, i) => `
    <div class="pc-item"><input value="${s.key}" data-i="${i}" data-type="status"><span class="pc-item-x" data-i="${i}" data-type="status">×</span></div>`).join('');
  const stageList = document.getElementById('pcStages');
  stageList.innerHTML = CONFIG.stages.map((s, i) => `
    <div class="pc-item"><div class="pc-item-color" style="background:${s.color}"></div><input value="${s.label}" data-i="${i}" data-type="stage"><span class="pc-item-x" data-i="${i}" data-type="stage">×</span></div>`).join('');
  const sizeList = document.getElementById('pcSizes');
  sizeList.innerHTML = CONFIG.sizes.map((s, i) => `
    <div class="pc-item"><div class="size-chip-val sz-${s.key}" style="margin:0 4px 0 0">${s.key}</div><input value="${s.label}" data-i="${i}" data-type="size"><span class="pc-item-x" data-i="${i}" data-type="size">×</span></div>`).join('');

  // per-game size overrides
  const sizeOpts = ['—', ...CONFIG.sizes.map(s => s.key)];
  const sizeGames = document.getElementById('pcSizeGames');
  sizeGames.innerHTML = RAW_GAMES.map(g => {
    const sz = gameSizes(g);
    const sel = k => `<select data-game="${g.name}" data-disc="${k}">` +
      sizeOpts.map(o => `<option value="${o === '—' ? '' : o}"${(sz[k] || '') === (o === '—' ? '' : o) ? ' selected' : ''}>${o}</option>`).join('') + `</select>`;
    return `<div class="pc-sizes-game"><span class="nm">${g.name}</span>${sel('art')}${sel('math')}${sel('dev')}${sel('sound')}</div>`;
  }).join('');

  // wire enum edits
  document.querySelectorAll('.pc-item input').forEach(inp => {
    inp.addEventListener('change', () => {
      const i = +inp.dataset.i, type = inp.dataset.type;
      if (type === 'status') CONFIG.statuses[i].key = inp.value;
      if (type === 'stage') CONFIG.stages[i].label = inp.value;
      if (type === 'size') CONFIG.sizes[i].label = inp.value;
      saveConfig(); renderRows();
    });
  });
  document.querySelectorAll('.pc-item-x').forEach(x => {
    x.addEventListener('click', () => {
      const i = +x.dataset.i, type = x.dataset.type;
      if (type === 'status') CONFIG.statuses.splice(i, 1);
      if (type === 'stage') CONFIG.stages.splice(i, 1);
      if (type === 'size') CONFIG.sizes.splice(i, 1);
      saveConfig(); renderPlanConfig(); renderRows();
    });
  });
  // wire size overrides
  document.querySelectorAll('#pcSizeGames select').forEach(sel => {
    sel.addEventListener('change', () => {
      const name = sel.dataset.game, disc = sel.dataset.disc;
      USER_SIZES[name] = USER_SIZES[name] || {};
      if (sel.value) USER_SIZES[name][disc] = sel.value;
      else delete USER_SIZES[name][disc];
      saveSizes();
      renderRows();
    });
  });
  const addStatus = document.getElementById('pcAddStatus');
  if (addStatus && !addStatus._wired) {
    addStatus._wired = true;
    addStatus.addEventListener('click', () => {
      CONFIG.statuses.push({ key: 'New Status', cls: 's-notstart' });
      saveConfig(); renderPlanConfig(); renderRows();
    });
  }
}

// ============================================================
//  VIEW TOGGLE / PLAN MODE / TOAST
// ============================================================
function wireControls() {
  document.getElementById('planToggle').addEventListener('click', () => {
    planMode = !planMode;
    const btn = document.getElementById('planToggle');
    btn.classList.toggle('plan-mode-on', planMode);
    btn.textContent = planMode ? '✎ Plan Mode ON' : '✎ Plan Mode';
    document.getElementById('planBanner').classList.toggle('show', planMode);
    document.getElementById('planConfig').classList.toggle('show', planMode);
    document.body.classList.toggle('plan-mode-on-body', planMode);
    if (planMode) renderPlanConfig();
  });
  document.getElementById('planSave').addEventListener('click', () => {
    saveOrder(); saveStatus(); saveSizes(); saveConfig();
    showToast('✓ Plan saved to localStorage (' + LS + '*)');
  });
  document.querySelectorAll('#viewToggle button').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('#viewToggle button').forEach(b => b.classList.remove('on'));
      btn.classList.add('on');
      currentView = btn.dataset.view;
      document.getElementById('roadmapView').style.display = currentView === 'roadmap' ? 'block' : 'none';
      document.getElementById('heatmapView').style.display = currentView === 'heatmap' ? 'block' : 'none';
      document.getElementById('listView').style.display = currentView === 'list' ? 'block' : 'none';
      if (currentView === 'heatmap') renderHeatmap();
      if (currentView === 'list') renderList();
    });
  });
}

let toastTimer;
function showToast(msg) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove('show'), 2800);
}

// ============================================================
//  INIT
// ============================================================
function init() {
  buildSkeleton();
  if (!RAW_GAMES.length) {
    document.getElementById('emptyState').style.display = 'block';
    document.getElementById('roadmapView').style.display = 'none';
    document.getElementById('filterBar').style.display = 'none';
    renderKPI();
    document.getElementById('hdrCount').textContent = '0';
    wireControls();
    return;
  }
  document.getElementById('hdrCount').textContent = RAW_GAMES.length;
  buildFilterBar();
  wireControls();
  renderKPI();
  renderAxis();
  renderRows();
}

if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
else init();

})();
