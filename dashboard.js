/* ============================================================
   Game Pipeline dashboard - shared engine (V2 + iGaming)

   Two ways to use it:
   1) Standalone page: set window.PROJECT then load this script — it mounts
      that one project into <body>.
   2) Combined page (game-pipeline.html): load both data files + this script,
      then call window.GamePipeline.mount('v2'|'ig', containerEl) to render a
      project into a container; call again to switch (in-place, no reload).

   Data contract (from dashboard-data-*.js):
       window.GP_DATA = window.GP_DATA || {};
       window.GP_DATA.v2 = { games:[...], sprints:[{id,label,start,end}], refreshed_at:'...' };
   ============================================================ */
(function () {
"use strict";

const BASE = 'https://ponggamestudios.atlassian.net/browse/';
const LANE_ORDER = ['art', 'design', 'math', 'dev', 'sound', 'qa'];

// --- Project metadata --------------------------------------------------------
const PROJECT_META = {
  v2: { key: 'v2', title: 'V2 Game Pipeline',
        subtitle: 'Pong Game Studios · V2 game-epic lifecycle dashboard',
        jira_project: 'V2', ls_prefix: 'gp_v2_' },
  ig: { key: 'ig', title: 'iGaming Game Pipeline',
        subtitle: 'Pong Game Studios · iGaming game-epic lifecycle dashboard',
        jira_project: 'IG', ls_prefix: 'gp_ig_' },
};

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
    { key: 'XS', label: 'Extra Small' }, { key: 'S', label: 'Small' },
    { key: 'M', label: 'Medium' }, { key: 'L', label: 'Large' }, { key: 'XL', label: 'Extra Large' },
  ],
  capacities: { art: 240, design: 80, math: 320, dev: 480, sound: 160, qa: 200 },
  velocities: {},   // per-dept h/sprint OVERRIDES (empty → use studio-average; Decision #38)
};
// Fallback velocity (h/sprint per dept) when a dept has no studio history.
const DEFAULT_VELOCITY = { art: 50, design: 30, math: 40, dev: 140, sound: 20, qa: 25 };

function getData(key) {
  const d = (window.GP_DATA && window.GP_DATA[key]) || {};
  return { games: d.games || [], sprints: d.sprints || [], refreshed: d.refreshed_at || null };
}

// ============================================================
//  Per-mount state (reassigned by mount(); functions below close over it)
// ============================================================
let PROJECT, LS, APP;
let RAW_GAMES, RAW_SPRINTS, REFRESHED, SPRINT_LIST, SPRINT_BY_ID, CHART_START, CHART_END, TODAY;
let ALL_SPRINTS, showForecast, studioVel;   // forecast (Decision #38)
let CONFIG, USER_ORDER, USER_STATUS, USER_SIZES, HIDDEN;
let activeFilters, currentView, planMode, openPanel, dragSrcIdx, toastTimer;
let drawerTab, drawerOpenRows, drawerDragIdx;

// ============================================================
//  TIME / SPRINT helpers
// ============================================================
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
  const d = new Date(s.start); d.setDate(d.getDate() + 13); return d;
}
// Compact label for narrow lane chips: "IG Sprint 7" -> "S7" (full name on hover).
function shortSprint(label) {
  const m = String(label || '').match(/(\d+)\s*$/);
  return m ? 'S' + m[1] : String(label || '');
}

// ============================================================
//  FORECAST — hypothetical timeline from remaining hours + velocity (Decision #38)
// ============================================================
function pastSprintCount(d) {
  return (d.sprints || []).filter(id => { const s = SPRINT_BY_ID[String(id)]; return s && new Date(s.start) <= TODAY; }).length;
}
// Studio-average velocity: Σ spent ÷ Σ active past discipline-sprints, per dept.
function computeStudioVel() {
  const acc = {}; LANE_ORDER.forEach(k => acc[k] = { sp: 0, spr: 0 });
  RAW_GAMES.forEach(g => (g.disciplines || []).forEach(d => {
    if (acc[d.key]) { acc[d.key].sp += d.spent || 0; acc[d.key].spr += pastSprintCount(d); }
  }));
  const v = {}; LANE_ORDER.forEach(k => { v[k] = acc[k].spr > 0 ? acc[k].sp / acc[k].spr : null; });
  return v;
}
function baseRate(k) {
  const ovr = CONFIG.velocities && CONFIG.velocities[k];
  if (ovr) return ovr;
  if (studioVel && studioVel[k]) return studioVel[k];
  return DEFAULT_VELOCITY[k] || 30;
}
// Per-game rate: own pace when it has real history, clamped to 0.5×–2× the base.
function effRate(d) {
  const base = baseRate(d.key);
  const past = pastSprintCount(d);
  if (past >= 2 && (d.spent || 0) >= 8) { const own = d.spent / past; return Math.min(Math.max(own, 0.5 * base), 2 * base); }
  return base;
}
function applyForecast() {
  studioVel = computeStudioVel();
  RAW_GAMES.forEach(g => { g._proj = null; });
  CHART_START = SPRINT_LIST.length ? new Date(SPRINT_LIST[0].start) : new Date('2026-05-11');
  const realEnd = SPRINT_LIST.length ? new Date(SPRINT_LIST[SPRINT_LIST.length - 1].end || SPRINT_LIST[SPRINT_LIST.length - 1].start) : new Date('2027-12-07');
  ALL_SPRINTS = SPRINT_LIST.slice();
  if (!showForecast || !SPRINT_LIST.length) { CHART_END = realEnd; return; }

  let firstFuture = SPRINT_LIST.findIndex(s => new Date(s.start) > TODAY);
  if (firstFuture < 0) firstFuture = SPRINT_LIST.length;

  let maxNeed = 0;
  RAW_GAMES.forEach(g => {
    if (g.delivered) return;
    const disc = {}; let ship = 0, any = false;
    LANE_ORDER.forEach(k => {
      const d = (g.disciplines || []).find(x => x.key === k); if (!d) return;
      const rem = Math.max(0, (d.est || 0) - (d.spent || 0)); if (rem <= 0) return;
      const need = Math.max(1, Math.ceil(rem / effRate(d)));
      disc[k] = need; any = true; ship = Math.max(ship, need); maxNeed = Math.max(maxNeed, need);
    });
    if (any) g._proj = { disc, shipOffset: ship };
  });

  const slotsAfter = SPRINT_LIST.length - firstFuture;
  const synth = Math.max(0, maxNeed - slotsAfter);
  if (synth > 0) {
    const last = SPRINT_LIST[SPRINT_LIST.length - 1];
    const m = String(last.label).match(/^(.*?)(\d+)\s*$/);
    const pre = m ? m[1] : (PROJECT.jira_project + ' Sprint ');
    const num = m ? +m[2] : SPRINT_LIST.length;
    let start = new Date(last.start);
    for (let i = 1; i <= synth; i++) {
      start = new Date(start); start.setDate(start.getDate() + 14);
      const e = new Date(start); e.setDate(e.getDate() + 12);
      const sp = { id: 'proj' + i, label: pre + (num + i), start: start.toISOString().slice(0, 10), end: e.toISOString().slice(0, 10), projected: true };
      SPRINT_BY_ID[sp.id] = sp; ALL_SPRINTS.push(sp);
    }
  }
  RAW_GAMES.forEach(g => {
    if (!g._proj) return;
    const slots = {};
    Object.keys(g._proj.disc).forEach(k => {
      const need = g._proj.disc[k], arr = [];
      for (let j = 0; j < need; j++) { const s = ALL_SPRINTS[firstFuture + j]; if (s) arr.push(s); }
      slots[k] = arr;
    });
    g._proj.slots = slots;
    g._proj.ship = ALL_SPRINTS[firstFuture + g._proj.shipOffset - 1] || ALL_SPRINTS[ALL_SPRINTS.length - 1];
  });
  CHART_END = new Date(ALL_SPRINTS[ALL_SPRINTS.length - 1].end || ALL_SPRINTS[ALL_SPRINTS.length - 1].start);
}

// ============================================================
//  HELPERS
// ============================================================
function statusCls(s) { const c = CONFIG.statuses.find(x => x.key === s); return c ? c.cls : 's-notstart'; }
function stageCls(s) { return 'stage-bg-' + s; }
function stageLabel(k) { const c = CONFIG.stages.find(x => x.key === k); return c ? c.label : k; }
function sizeCls(v) { return v && ['XS', 'S', 'M', 'L', 'XL'].includes(v) ? 'sz-' + v : 'sz-NA'; }
function gameColor(g) { const s = CONFIG.stages.find(x => x.key === g.current_stage); return s ? s.color : '#94a3b8'; }
function discSprints(disc) {
  const ids = (disc && disc.sprints) ? disc.sprints : [];
  return ids.map(id => SPRINT_BY_ID[String(id)]).filter(Boolean)
            .sort((a, b) => new Date(a.start) - new Date(b.start));
}
function gameSizes(g) { return USER_SIZES[g.name] || {}; }
function hasAnySize(g) { const s = gameSizes(g); return ['art', 'math', 'dev', 'sound'].some(k => s[k]); }
function fvRow(g) {
  if (g.delivered) {
    return `<div class="fv-row"><span class="fv-chip delivered" title="Delivered in ${g.delivered.fv} on ${g.delivered.date}">✓ Delivered · ${g.delivered.fv} · ${fmtD(g.delivered.date)}</span></div>`;
  }
  const fvs = g.fixVersions || [];
  if (!fvs.length) return '';
  return '<div class="fv-row">' + fvs.map(v =>
    `<span class="fv-chip${v.released ? ' delivered' : ''}" title="${v.name}${v.released && v.releaseDate ? ' · released ' + v.releaseDate : ''}">${v.released ? '✓ ' : ''}${v.name}</span>`
  ).join('') + '</div>';
}

function saveOrder()  { try { localStorage.setItem(LS + 'order',  JSON.stringify(RAW_GAMES.map(g => g.name))); } catch (e) {} }
function saveStatus() { try { localStorage.setItem(LS + 'status', JSON.stringify(USER_STATUS)); } catch (e) {} }
function saveSizes()  { try { localStorage.setItem(LS + 'sizes',  JSON.stringify(USER_SIZES)); } catch (e) {} }
function saveConfig() { try { localStorage.setItem(LS + 'config', JSON.stringify(CONFIG)); } catch (e) {} }
function saveHidden() { try { localStorage.setItem(LS + 'hidden', JSON.stringify([...HIDDEN])); } catch (e) {} }
function visibleGames() { return RAW_GAMES.filter(g => !HIDDEN.has(g.name)); }

// ============================================================
//  SKELETON
// ============================================================
function buildSkeleton() {
  APP.innerHTML = `
  <div class="hdr">
    <div>
      <h1>${PROJECT.title}</h1>
      <p>${PROJECT.subtitle || ''}${SPRINT_LIST.length ? ' · sprint axis from ' + SPRINT_LIST[0].label : ''}</p>
      <div class="refresh-meta"><div class="refresh-dot"></div>Phase 1 · Jira-sourced · <span id="hdrCount">0</span> game epics${REFRESHED ? ' · refreshed ' + REFRESHED : ''}</div>
    </div>
    <div class="hdr-actions"><button class="btn" id="planToggle">✎ Plan Mode</button></div>
  </div>
  <div class="kpi-strip" id="kpiStrip"></div>
  <div class="filter-bar" id="filterBar">
    <div class="fb-group" id="fbStatusGroup"><span class="fb-label">STATUS</span></div>
    <div class="fb-group" id="fbStageGroup"><span class="fb-label">STAGE</span></div>
    <div class="fb-spacer"></div>
    <input class="fb-search" id="fbSearch" placeholder="🔍 Search games…">
    <button class="fb-chip fc-toggle" id="fcToggle" title="Project a hypothetical timeline from remaining hours ÷ velocity">🔮 Forecast</button>
    <div class="view-toggle" id="viewToggle">
      <button class="on" data-view="roadmap">Roadmap</button>
      <button data-view="heatmap">Heatmap</button>
      <button data-view="list">List</button>
    </div>
  </div>
  <div id="emptyState" class="empty-state" style="display:none">
    <h2>No data yet</h2>
    <p>No games to show for ${PROJECT.jira_project}. Run the Jira builder:<br>
    <code>python build_jira_data.py --project ${PROJECT.key}</code><br>then reload.</p>
  </div>
  <div id="roadmapView"><div class="axis" id="axis"></div><div id="rows"></div></div>
  <div id="heatmapView" style="display:none">
    <div class="heatmap-wrap">
      <h3 style="font-size:13px;font-weight:600;color:var(--text);margin:0 0 4px">Discipline Hour-Load Heatmap</h3>
      <p style="font-size:11px;color:var(--muted);margin:0">Estimated remaining hours per discipline per month, allocated across each discipline's active sprints. Cells turn red when load exceeds the editable capacity ceiling.</p>
      <div class="heatmap-grid" id="heatmapGrid"></div>
    </div>
  </div>
  <div id="listView" style="display:none"><div class="list-view" id="listBody"></div></div>

  <div class="gp-overlay" id="gpOverlay"></div>
  <div class="gp-drawer" id="gpDrawer" aria-hidden="true">
    <div class="gp-drawer-head"><h2>⚙ Edit Plan</h2><button class="gp-drawer-close" id="gpDrawerClose" title="Close">✕</button></div>
    <div class="gp-tabs" id="gpTabs">
      <button class="gp-tab active" data-tab="games">Games</button>
      <button class="gp-tab" data-tab="settings">Settings</button>
    </div>
    <div class="gp-drawer-note">Toggle to show/hide · drag the ⠿ handle to reorder · click ▾ to set status &amp; sizes. Changes autosave to this browser.</div>
    <div class="gp-drawer-body" id="gpDrawerBody"></div>
    <div class="gp-drawer-foot" id="gpDrawerFoot"></div>
  </div>

  <div class="toast" id="toast"></div>
  <div class="footer">Pong Game Studios PMO · ${PROJECT.title} · shared engine · data via <code>build_jira_data.py</code> · localStorage keys: <code>${LS}*</code></div>`;
}

// ============================================================
//  FILTER BAR
// ============================================================
function buildFilterBar() {
  const statusGroup = document.getElementById('fbStatusGroup');
  const stageGroup = document.getElementById('fbStageGroup');
  ['ALL', 'Not Started', 'In Pre-Prod', 'In Production', 'In QA', 'On Hold', 'Signed Off'].forEach(k => {
    const c = document.createElement('span');
    c.className = 'fb-chip' + (k === 'ALL' ? ' on' : '');
    c.dataset.filterStatus = k; c.textContent = k === 'ALL' ? 'All' : k;
    statusGroup.appendChild(c);
  });
  ['ALL', 'art', 'design', 'math', 'dev', 'sound', 'qa'].forEach(k => {
    const c = document.createElement('span');
    c.className = 'fb-chip discipline' + (k === 'ALL' ? ' on' : '');
    c.dataset.filterStage = k; c.textContent = k === 'ALL' ? 'All' : stageLabel(k);
    stageGroup.appendChild(c);
  });
  document.querySelectorAll('[data-filter-status]').forEach(chip => chip.addEventListener('click', () => {
    document.querySelectorAll('[data-filter-status]').forEach(c => c.classList.remove('on'));
    chip.classList.add('on'); activeFilters.status = chip.dataset.filterStatus; renderRows();
  }));
  document.querySelectorAll('[data-filter-stage]').forEach(chip => chip.addEventListener('click', () => {
    document.querySelectorAll('[data-filter-stage]').forEach(c => c.classList.remove('on'));
    chip.classList.add('on'); activeFilters.stage = chip.dataset.filterStage; renderRows();
  }));
  document.getElementById('fbSearch').addEventListener('input', e => { activeFilters.search = e.target.value; renderRows(); });
}

// ============================================================
//  KPI
// ============================================================
function renderKPI() {
  const VG = visibleGames();
  const total = VG.length;
  const signed = VG.filter(g => g.workflow_status === 'Signed Off').length;
  const inflight = VG.filter(g => ['In Production', 'In QA', 'In Pre-Prod', 'Bug Fixing'].includes(g.workflow_status)).length;
  const notstart = VG.filter(g => g.workflow_status === 'Not Started').length;
  const over = VG.filter(g => g.spent > g.est && g.est > 0).length;
  const overGames = VG.filter(g => g.spent > g.est && g.est > 0).slice(0, 2)
    .map(g => `${g.name.split(' ')[0]} +${Math.round(g.spent - g.est)}h`).join(' · ');
  document.getElementById('kpiStrip').innerHTML = `
    <div class="kpi" style="--rc:#2563eb"><div class="kpi-v">${total}</div><div class="kpi-l">TOTAL GAMES</div><div class="kpi-d">${PROJECT.jira_project} epics</div></div>
    <div class="kpi" style="--rc:#16a34a"><div class="kpi-v">${signed}</div><div class="kpi-l">SIGNED OFF</div><div class="kpi-d">Completed releases</div></div>
    <div class="kpi" style="--rc:#d97706"><div class="kpi-v">${inflight}</div><div class="kpi-l">IN FLIGHT</div><div class="kpi-d">Pre-prod / production / QA</div></div>
    <div class="kpi" style="--rc:#7c3aed"><div class="kpi-v">${notstart}</div><div class="kpi-l">NOT STARTED</div><div class="kpi-d">Future pipeline</div></div>
    <div class="kpi" style="--rc:#dc2626"><div class="kpi-v">${over}</div><div class="kpi-l">OVER ESTIMATE</div><div class="kpi-d">${overGames || '—'}</div></div>`;
}

// ============================================================
//  SPRINT AXIS
// ============================================================
function renderAxis() {
  const axisEl = document.getElementById('axis');
  axisEl.innerHTML = '';
  const list = ALL_SPRINTS && ALL_SPRINTS.length ? ALL_SPRINTS : SPRINT_LIST;
  if (!list.length) return;
  const stride = Math.max(1, Math.ceil(list.length / 18));
  list.forEach((s, i) => {
    if (i % stride !== 0) return;
    const end = sprintEnd(s);
    const mid = new Date((new Date(s.start).getTime() + end.getTime()) / 2);
    const chip = document.createElement('div');
    chip.className = 'sp-chip' + (s.projected ? ' proj' : '');
    chip.style.left = pct(mid) + '%';
    chip.innerHTML = `${s.label}<small>${fmtRange(s.start, end)}</small>`;
    axisEl.appendChild(chip);
  });
  if (TODAY >= CHART_START && TODAY <= CHART_END) {
    const t = document.createElement('div');
    t.className = 'ax-today'; t.style.left = pct(TODAY) + '%'; t.textContent = 'TODAY';
    axisEl.appendChild(t);
  }
}

// ============================================================
//  ROW RENDERING
// ============================================================
function renderRow(g, idx) {
  const item = document.createElement('div');
  item.className = 'fv-item'; item.dataset.idx = idx; item.dataset.name = g.name;
  if (HIDDEN.has(g.name)) item.style.display = 'none';

  const row = document.createElement('div');
  row.className = 'epic-row';
  if (openPanel === g.name) row.classList.add('open');
  row.style.setProperty('--rc', gameColor(g));

  const dh = document.createElement('div'); dh.className = 'drag-handle'; dh.innerHTML = '⠿';

  const label = document.createElement('div'); label.className = 'epic-label';
  const depIcon = (g.dependencies && g.dependencies.length) ? `<span class="dep-icon" title="Depends on ${g.dependencies.join(', ')}">🔗</span>` : '';
  const isOverride = USER_STATUS[g.name] != null;
  const drift = isOverride && USER_STATUS[g.name] !== g._auto_status;
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
      <span class="epic-status ${statusCls(g.workflow_status)}">${g.workflow_status}${isOverride ? ' <span class="ovr-mark" title="Manually set — auto-derived from Jira: ' + g._auto_status + '">✎</span>' : ''}</span>
      ${drift ? `<span class="status-drift" title="Jira-derived status is now '${g._auto_status}', but a manual override is in effect">auto: ${g._auto_status}</span>` : ''}
    </div>${fvRow(g)}${sizeRow}`;

  const track = document.createElement('div'); track.className = 'epic-track';
  (ALL_SPRINTS || SPRINT_LIST).forEach(s => { const l = document.createElement('div'); l.className = 'sp-line' + (s.projected ? ' proj' : ''); l.style.left = pct(s.start) + '%'; track.appendChild(l); });
  if (TODAY >= CHART_START && TODAY <= CHART_END) { const tl = document.createElement('div'); tl.className = 'today-line-row'; tl.style.left = pct(TODAY) + '%'; track.appendChild(tl); }
  const proj = (showForecast && g._proj) ? g._proj : null;
  let laneTop = 8;
  LANE_ORDER.forEach(dKey => {
    const disc = g.disciplines ? g.disciplines.find(d => d.key === dKey) : null;
    const sprs = discSprints(disc);
    const pslots = (proj && proj.slots && proj.slots[dKey]) ? proj.slots[dKey] : [];
    if (!sprs.length && !pslots.length) return;
    const top = laneTop;
    const lane = (s, dashed) => {
      const end = sprintEnd(s), l = pct(s.start), w = Math.max(pct(end) - l, 0.8);
      const chip = document.createElement('div');
      chip.className = 'lane-spr lane-' + dKey + (dashed ? ' proj' : '');
      chip.style.left = l + '%'; chip.style.width = w + '%'; chip.style.top = top + 'px';
      chip.title = `${dKey.toUpperCase()} · ${s.label} (${fmtRange(s.start, end)})${dashed ? ' · projected' : ''}`;
      chip.textContent = w > 3 ? shortSprint(s.label) : '';
      track.appendChild(chip);
    };
    sprs.forEach(s => lane(s, false));
    pslots.forEach(s => lane(s, true));
    laneTop += 11;
  });
  if (proj && proj.ship) {
    const sm = document.createElement('div'); sm.className = 'ship-line';
    sm.style.left = pct(proj.ship.start) + '%';
    sm.title = `Projected ship · ${proj.ship.label} (${fmtD(proj.ship.start)})`;
    track.appendChild(sm);
  }
  if (laneTop === 8) { const n = document.createElement('div'); n.style.cssText = 'font-size:9px;color:var(--sub);font-style:italic;padding-top:6px'; n.textContent = 'No scheduled sprints yet'; track.appendChild(n); }

  const hrs = document.createElement('div'); hrs.className = 'epic-hrs';
  const over = g.spent > g.est && g.est > 0;
  const progressPct = g.est > 0 ? Math.min(100, Math.round(g.spent / g.est * 100)) : 0;
  const progressColor = over ? '#dc2626' : (progressPct >= 70 ? gameColor(g) : '#60a5fa');
  hrs.innerHTML = `
    <div class="epic-hrs-v ${over ? 'over' : ''}">${Math.round(g.spent)}h</div>
    <div class="epic-hrs-l">SPENT / ${Math.round(g.est)}h est</div>
    <div class="epic-prog"><div class="epic-prog-fill" style="width:${progressPct}%;background:${progressColor}"></div></div>
    <div class="epic-hrs-l" style="color:${over ? '#dc2626' : 'var(--sub)'};margin-top:4px">${over ? `⚠ +${Math.round(g.spent - g.est)}h over` : `${progressPct}% spent`}</div>
    ${proj && proj.ship ? `<div class="epic-hrs-l proj-ship" title="Forecast: remaining hours ÷ velocity (parallel)">≈ ship ${shortSprint(proj.ship.label)} · ${fmtD(proj.ship.start)}</div>` : ''}`;

  const chev = document.createElement('div'); chev.className = 'chev'; chev.textContent = '⌄';
  row.appendChild(dh); row.appendChild(label); row.appendChild(track); row.appendChild(hrs); row.appendChild(chev);

  row.addEventListener('click', e => {
    if (e.target.closest('.drag-handle') || e.target.closest('select') || e.target.closest('a')) return;
    openPanel = (openPanel === g.name) ? null : g.name; renderRows();
  });

  item.draggable = true;
  item.addEventListener('dragstart', e => { if (!planMode) { e.preventDefault(); return; } dragSrcIdx = idx; e.dataTransfer.effectAllowed = 'move'; row.classList.add('dragging'); });
  item.addEventListener('dragend', () => { row.classList.remove('dragging'); document.querySelectorAll('.fv-item').forEach(el => el.classList.remove('drag-over-above', 'drag-over-below')); });
  item.addEventListener('dragover', e => {
    if (!planMode || dragSrcIdx === null || dragSrcIdx === idx) return;
    e.preventDefault();
    const rect = item.getBoundingClientRect(), mid = rect.top + rect.height / 2;
    item.classList.toggle('drag-over-above', e.clientY < mid);
    item.classList.toggle('drag-over-below', e.clientY >= mid);
  });
  item.addEventListener('dragleave', () => item.classList.remove('drag-over-above', 'drag-over-below'));
  item.addEventListener('drop', e => {
    e.preventDefault();
    if (!planMode || dragSrcIdx === null || dragSrcIdx === idx) return;
    const rect = item.getBoundingClientRect(), insertBefore = e.clientY < rect.top + rect.height / 2;
    const [moved] = RAW_GAMES.splice(dragSrcIdx, 1);
    let ti = RAW_GAMES.indexOf(g);
    if (!insertBefore) ti++;
    if (dragSrcIdx < idx) ti--;
    RAW_GAMES.splice(ti < 0 ? 0 : ti, 0, moved);
    dragSrcIdx = null; saveOrder(); showToast(`✓ ${moved.name} reprioritized`); renderRows();
  });

  item.appendChild(row);
  if (openPanel === g.name) item.appendChild(renderDetail(g));
  return item;
}

function renderDetail(g) {
  const panel = document.createElement('div'); panel.className = 'detail open'; panel.style.setProperty('--rc', gameColor(g));
  const head = document.createElement('div'); head.className = 'detail-head';
  const ovr = (USER_STATUS[g.name] != null);
  const ovrNote = ovr ? ` · <span style="color:#d97706">✎ manual (auto: ${g._auto_status})</span>` : '';
  head.innerHTML = `<h3>${g.name} — Lifecycle Detail</h3><div class="meta"><strong>Stage</strong>: ${stageLabel(g.current_stage)} · <strong>Status</strong>: ${g.workflow_status}${ovrNote} · <strong>Jira epic</strong>: ${g.epic_status || '—'} · <strong>Lead Dev</strong>: ${g.dev_name || '—'}</div>`;
  panel.appendChild(head);
  const tabs = document.createElement('div'); tabs.className = 'detail-tabs';
  tabs.innerHTML = `<div class="detail-tab active" data-tab="timeline">TIMELINE</div><div class="detail-tab" data-tab="milestones">SPRINTS</div><div class="detail-tab" data-tab="hours">HOURS</div>`;
  panel.appendChild(tabs);
  const body = document.createElement('div'); body.className = 'detail-body';

  const tlPane = document.createElement('div');
  LANE_ORDER.forEach(dKey => {
    const disc = g.disciplines ? g.disciplines.find(d => d.key === dKey) : null;
    if (!disc) return;
    const sprs = discSprints(disc);
    const r = document.createElement('div'); r.className = 'disc-row';
    let bar = '';
    if (sprs.length) {
      const start = new Date(sprs[0].start), end = sprintEnd(sprs[sprs.length - 1]);
      const l = pct(start), w = Math.max(pct(end) - l, 0.6);
      bar = `<div class="disc-bar lane-${dKey}" style="left:${l}%;width:${w}%;color:rgba(0,0,0,.6)">${fmtD(start)} → ${fmtD(end)}</div>`;
    }
    const hrsOver = disc.spent > disc.est && disc.est > 0;
    r.innerHTML = `<div class="disc-label">${dKey}</div><div class="disc-track">${bar}</div><div class="disc-hrs">${Math.round(disc.spent)} / ${Math.round(disc.est)}h ${hrsOver ? '<span class="over">⚠</span>' : ''}</div>`;
    tlPane.appendChild(r);
  });
  body.appendChild(tlPane);

  const msPane = document.createElement('div'); msPane.style.display = 'none';
  const allSprints = [];
  (g.disciplines || []).forEach(d => discSprints(d).forEach(s => allSprints.push({ stage: d.key, disc: d.name, label: s.label, start: s.start, end: sprintEnd(s) })));
  allSprints.sort((a, b) => new Date(a.start) - new Date(b.start));
  if (!allSprints.length) {
    msPane.innerHTML = '<div style="font-size:11px;color:var(--sub);font-style:italic;padding:10px">No scheduled sprints yet.</div>';
  } else {
    const list = document.createElement('div'); list.className = 'markers-list';
    allSprints.forEach(m => {
      const it = document.createElement('div'); it.className = 'marker-item';
      it.innerHTML = `<div class="marker-date">${fmtRange(m.start, m.end)}</div><div class="marker-stage stage-bg-${m.stage}">${m.label}</div><div style="flex:1">${m.disc}</div>`;
      list.appendChild(it);
    });
    msPane.appendChild(list);
  }
  body.appendChild(msPane);

  const hrsPane = document.createElement('div'); hrsPane.style.display = 'none';
  const hoursRows = (g.disciplines || []).map(d => {
    const ratio = d.est > 0 ? d.spent / d.est : 0, over = ratio > 1, pctNum = Math.round(ratio * 100);
    return `<div class="disc-row"><div class="disc-label">${d.key}</div><div class="disc-track"><div class="disc-bar" style="left:0;width:${Math.min(100, pctNum)}%;background:${over ? '#dc2626' : '#2563eb'};color:#fff">${pctNum}%</div></div><div class="disc-hrs">${Math.round(d.spent)} / ${Math.round(d.est)}h ${over ? '<span class="over">⚠</span>' : ''}</div></div>`;
  }).join('');
  hrsPane.innerHTML = hoursRows || '<div style="font-size:11px;color:var(--sub);font-style:italic;padding:10px">No hours data.</div>';
  body.appendChild(hrsPane);
  panel.appendChild(body);

  tabs.querySelectorAll('.detail-tab').forEach(t => t.addEventListener('click', e => {
    e.stopPropagation();
    tabs.querySelectorAll('.detail-tab').forEach(x => x.classList.remove('active'));
    t.classList.add('active');
    tlPane.style.display = t.dataset.tab === 'timeline' ? 'block' : 'none';
    msPane.style.display = t.dataset.tab === 'milestones' ? 'block' : 'none';
    hrsPane.style.display = t.dataset.tab === 'hours' ? 'block' : 'none';
  }));
  return panel;
}

function renderRows() {
  const rowsEl = document.getElementById('rows');
  rowsEl.innerHTML = '';
  const filtered = visibleGames().filter(g => {
    if (activeFilters.status !== 'ALL' && g.workflow_status !== activeFilters.status) return false;
    if (activeFilters.stage !== 'ALL' && g.current_stage !== activeFilters.stage) return false;
    if (activeFilters.search && !g.name.toLowerCase().includes(activeFilters.search.toLowerCase())) return false;
    return true;
  });
  filtered.forEach(g => rowsEl.appendChild(renderRow(g, RAW_GAMES.indexOf(g))));
  if (filtered.length === 0) rowsEl.innerHTML = '<div style="text-align:center;padding:30px;color:var(--sub);font-size:12px;font-style:italic">No games match your filters.</div>';
}

// ============================================================
//  HEATMAP
// ============================================================
function renderHeatmap() {
  const grid = document.getElementById('heatmapGrid');
  const months = [];
  const start = new Date(CHART_START.getFullYear(), CHART_START.getMonth(), 1);
  for (let i = 0; i < 12; i++) months.push(new Date(start.getFullYear(), start.getMonth() + i, 1));
  grid.style.gridTemplateColumns = `280px repeat(${months.length}, 1fr)`;
  let html = `<div class="hm-row-label" style="background:var(--surf3)">DISCIPLINE \\ MONTH</div>`;
  months.forEach(m => { html += `<div class="hm-header">${m.toLocaleString('en-CA', { month: 'short', year: '2-digit' })}</div>`; });
  const disciplines = [
    { key: 'art', icon: '🎨', name: 'Art / Creative' }, { key: 'design', icon: '📐', name: 'Design' },
    { key: 'math', icon: '🧮', name: 'Math' }, { key: 'dev', icon: '💻', name: 'Development' },
    { key: 'sound', icon: '🎵', name: 'Sound' }, { key: 'qa', icon: '🧪', name: 'QA' },
  ];
  disciplines.forEach(d => {
    const cap = CONFIG.capacities[d.key] || 200;
    html += `<div class="hm-row-label">${d.icon} ${d.name} <small>cap ${cap}h/mo</small></div>`;
    months.forEach(m => {
      const monthStart = m, monthEnd = new Date(m.getFullYear(), m.getMonth() + 1, 0);
      let totalHours = 0, gameCount = 0;
      visibleGames().forEach(g => {
        const disc = g.disciplines ? g.disciplines.find(x => x.key === d.key) : null;
        const sprs = discSprints(disc);
        if (!sprs.length) return;
        let activeInMonth = 0;
        sprs.forEach(s => { const ss = new Date(s.start), se = sprintEnd(s); if (se >= monthStart && ss <= monthEnd) activeInMonth++; });
        if (!activeInMonth) return;
        totalHours += Math.max(0, disc.est - disc.spent) * (activeInMonth / sprs.length);
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
  let html = `<div class="list-row head"><div>#</div><div>GAME</div><div>JIRA</div><div>STAGE</div><div>STATUS</div><div>LEAD</div><div>HOURS</div><div>PROGRESS</div></div>`;
  visibleGames().forEach((g, i) => {
    const over = g.spent > g.est && g.est > 0, pctNum = g.est > 0 ? Math.round(g.spent / g.est * 100) : 0;
    html += `<div class="list-row">
      <div class="num">${i + 1}</div><div><strong>${g.name}</strong></div>
      <div>${g.jira ? `<a href="${BASE}${g.jira}" target="_blank">${g.jira}</a>` : '—'}</div>
      <div><span class="epic-stage ${stageCls(g.current_stage)}">${stageLabel(g.current_stage)}</span></div>
      <div><span class="epic-status ${statusCls(g.workflow_status)}">${g.workflow_status}</span></div>
      <div style="font-family:'IBM Plex Mono';font-size:10px;color:var(--muted)">${g.dev_name || '—'}</div>
      <div style="font-family:'IBM Plex Mono';font-size:10px;${over ? 'color:#dc2626;font-weight:600' : ''}">${Math.round(g.spent)} / ${Math.round(g.est)}h</div>
      <div style="font-family:'IBM Plex Mono';font-size:10px">${pctNum}%</div></div>`;
  });
  document.getElementById('listBody').innerHTML = html;
}

// ============================================================
//  EDIT PLAN DRAWER (Decision #35)
// ============================================================
function openDrawer() {
  planMode = true;
  const pt = document.getElementById('planToggle');
  pt.classList.add('plan-mode-on'); pt.textContent = '✎ Editing…';
  document.body.classList.add('plan-mode-on-body');
  document.getElementById('gpOverlay').classList.add('open');
  const dr = document.getElementById('gpDrawer'); dr.classList.add('open'); dr.setAttribute('aria-hidden', 'false');
  renderDrawer();
}
function closeDrawer() {
  planMode = false;
  const pt = document.getElementById('planToggle');
  pt.classList.remove('plan-mode-on'); pt.textContent = '✎ Plan Mode';
  document.body.classList.remove('plan-mode-on-body');
  document.getElementById('gpOverlay').classList.remove('open');
  const dr = document.getElementById('gpDrawer'); dr.classList.remove('open'); dr.setAttribute('aria-hidden', 'true');
}

function renderDrawer() {
  document.querySelectorAll('#gpTabs .gp-tab').forEach(b => b.classList.toggle('active', b.dataset.tab === drawerTab));
  const body = document.getElementById('gpDrawerBody');
  if (drawerTab === 'settings') renderDrawerSettings(body); else renderDrawerGames(body);
  const hidden = RAW_GAMES.length - visibleGames().length;
  document.getElementById('gpDrawerFoot').innerHTML =
    `<button class="gp-foot-btn" id="gpReset">↺ Reset local edits</button><span style="flex:1"></span>` +
    `<span style="font-size:10px;color:var(--sub);align-self:center">${visibleGames().length} shown${hidden ? ' · ' + hidden + ' hidden' : ''}</span>`;
  document.getElementById('gpReset').onclick = resetLocalEdits;
}

function renderDrawerGames(body) {
  const statusOptions = g => CONFIG.statuses.map(s => `<option value="${s.key}"${s.key === g.workflow_status ? ' selected' : ''}>${s.key}</option>`).join('');
  const sizeOpts = ['—', ...CONFIG.sizes.map(s => s.key)];
  const sizeSel = (g, k) => `<label>${k.toUpperCase()}<select data-size-game="${g.name}" data-disc="${k}">` +
    sizeOpts.map(o => `<option value="${o === '—' ? '' : o}"${(gameSizes(g)[k] || '') === (o === '—' ? '' : o) ? ' selected' : ''}>${o}</option>`).join('') + `</select></label>`;
  body.innerHTML = RAW_GAMES.map((g, idx) => {
    const on = !HIDDEN.has(g.name), open = drawerOpenRows.has(g.name), isOvr = USER_STATUS[g.name] != null;
    return `<div class="gpg${on ? '' : ' off'}${open ? ' open' : ''}" data-idx="${idx}" draggable="true">
      <div class="gpg-head">
        <span class="gpg-handle" title="Drag to reorder">⠿</span>
        <button class="gpg-toggle${on ? ' on' : ''}" data-hide="${g.name}" title="${on ? 'Visible — click to hide' : 'Hidden — click to show'}"></button>
        <span class="gpg-name">${g.name}${g.jira ? `<span class="k">${g.jira}</span>` : ''}</span>
        <span class="gpg-status"><select data-status-game="${g.name}">${statusOptions(g)}</select>${isOvr ? ' <span class="ovr-mark" title="manual override (auto: ' + g._auto_status + ')">✎</span>' : ''}</span>
        <span class="gpg-chev" data-expand="${g.name}">▾</span>
      </div>
      <div class="gpg-expand">
        <div class="gpg-sizes">${sizeSel(g, 'art')}${sizeSel(g, 'math')}${sizeSel(g, 'dev')}${sizeSel(g, 'sound')}</div>
        <div style="margin-top:8px;font-size:10px;color:var(--sub)">Stage: <strong>${stageLabel(g.current_stage)}</strong> · Jira epic: ${g.epic_status || '—'}${isOvr ? ` · <button class="revert-auto" data-revert="${g.name}">↺ revert status to auto (${g._auto_status})</button>` : ''}</div>
      </div>
    </div>`;
  }).join('');

  body.querySelectorAll('.gpg-toggle').forEach(b => b.addEventListener('click', e => {
    e.stopPropagation(); const n = b.dataset.hide;
    if (HIDDEN.has(n)) HIDDEN.delete(n); else HIDDEN.add(n);
    saveHidden(); renderRows(); renderKPI(); renderDrawer();
    const hdr = document.getElementById('hdrCount'); if (hdr) hdr.textContent = visibleGames().length;
  }));
  body.querySelectorAll('.gpg-chev').forEach(c => c.addEventListener('click', e => {
    e.stopPropagation(); const n = c.dataset.expand;
    if (drawerOpenRows.has(n)) drawerOpenRows.delete(n); else drawerOpenRows.add(n);
    c.closest('.gpg').classList.toggle('open');
  }));
  body.querySelectorAll('select[data-status-game]').forEach(sel => sel.addEventListener('change', () => {
    const g = RAW_GAMES.find(x => x.name === sel.dataset.statusGame); if (!g) return;
    if (sel.value === g._auto_status) { delete USER_STATUS[g.name]; g.workflow_status = g._auto_status; }
    else { USER_STATUS[g.name] = sel.value; g.workflow_status = sel.value; }
    saveStatus(); renderRows(); renderKPI(); renderDrawer();
  }));
  body.querySelectorAll('select[data-size-game]').forEach(sel => sel.addEventListener('change', () => {
    const n = sel.dataset.sizeGame, d = sel.dataset.disc;
    USER_SIZES[n] = USER_SIZES[n] || {};
    if (sel.value) USER_SIZES[n][d] = sel.value; else delete USER_SIZES[n][d];
    saveSizes(); renderRows();
  }));
  body.querySelectorAll('.revert-auto').forEach(b => b.addEventListener('click', () => {
    const g = RAW_GAMES.find(x => x.name === b.dataset.revert); if (!g) return;
    delete USER_STATUS[g.name]; g.workflow_status = g._auto_status;
    saveStatus(); renderRows(); renderKPI(); renderDrawer();
  }));
  // drag-reorder within the drawer
  body.querySelectorAll('.gpg').forEach(rowEl => {
    rowEl.addEventListener('dragstart', e => { drawerDragIdx = +rowEl.dataset.idx; e.dataTransfer.effectAllowed = 'move'; rowEl.style.opacity = '.4'; });
    rowEl.addEventListener('dragend', () => { rowEl.style.opacity = ''; });
    rowEl.addEventListener('dragover', e => e.preventDefault());
    rowEl.addEventListener('drop', e => {
      e.preventDefault();
      const ti = +rowEl.dataset.idx;
      if (drawerDragIdx === null || drawerDragIdx === ti) return;
      const [moved] = RAW_GAMES.splice(drawerDragIdx, 1);
      let t = ti; if (drawerDragIdx < ti) t--;
      RAW_GAMES.splice(t < 0 ? 0 : t, 0, moved);
      drawerDragIdx = null; saveOrder(); renderRows(); renderDrawer();
    });
  });
}

function renderDrawerSettings(body) {
  const enumCol = (title, items, type, withColor) => `<div class="pc-col"><h4>${title}</h4><div class="pc-list">` +
    items.map((s, i) => `<div class="pc-item">${withColor ? `<div class="pc-item-color" style="background:${s.color}"></div>` : `<div class="size-chip-val sz-${s.key}" style="margin:0 4px 0 0">${s.key}</div>`}<input value="${s.label || s.key}" data-i="${i}" data-type="${type}"><span class="pc-item-x" data-i="${i}" data-type="${type}">×</span></div>`).join('') +
    (type === 'status' ? `<div class="pc-add" id="pcAddStatus">+ add status</div>` : '') + `</div></div>`;
  const numField = (label, k, val, attr, step) =>
    `<label style="font-size:9px;color:var(--sub);font-weight:700;display:flex;flex-direction:column;gap:3px">${label}<input type="number" min="0" step="${step}" value="${val}" ${attr}="${k}" style="border:1px solid var(--border2);border-radius:3px;padding:4px 6px;font-family:'IBM Plex Mono';font-size:11px"></label>`;
  const caps = Object.keys(CONFIG.capacities).map(k => numField(k.toUpperCase(), k, CONFIG.capacities[k], 'data-cap', 20)).join('');
  const vels = LANE_ORDER.map(k => {
    const v = Math.round(baseRate(k));
    const src = (CONFIG.velocities && CONFIG.velocities[k]) ? 'override' : (studioVel && studioVel[k] ? 'studio avg' : 'default');
    return numField(`${k.toUpperCase()} <span style="font-weight:400;color:var(--sub)">(${src})</span>`, k, v, 'data-vel', 5);
  }).join('');
  body.innerHTML = `<div class="pc-grid">
      ${enumCol('WORKFLOW STATUSES', CONFIG.statuses, 'status', false)}
      ${enumCol('LIFECYCLE STAGES', CONFIG.stages, 'stage', true)}
      ${enumCol('SIZE SCALE', CONFIG.sizes, 'size', false)}
    </div>
    <div style="margin-top:16px"><h4 style="font-size:10px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.4px;margin-bottom:8px">FORECAST VELOCITY (h/sprint per dept) — drives projected timeline <span style="font-weight:400;font-style:italic;text-transform:none;color:var(--sub)">· pre-filled with studio average; edit to override</span></h4>
    <div style="display:grid;grid-template-columns:repeat(6,1fr);gap:8px">${vels}</div></div>
    <div style="margin-top:16px"><h4 style="font-size:10px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.4px;margin-bottom:8px">DISCIPLINE CAPACITY (h/mo) — heatmap ceilings</h4>
    <div style="display:grid;grid-template-columns:repeat(6,1fr);gap:8px">${caps}</div></div>`;
  body.querySelectorAll('.pc-item input').forEach(inp => inp.addEventListener('change', () => {
    const i = +inp.dataset.i, type = inp.dataset.type;
    if (type === 'status') CONFIG.statuses[i].key = inp.value;
    if (type === 'stage') CONFIG.stages[i].label = inp.value;
    if (type === 'size') CONFIG.sizes[i].label = inp.value;
    saveConfig(); renderRows();
  }));
  body.querySelectorAll('.pc-item-x').forEach(x => x.addEventListener('click', () => {
    const i = +x.dataset.i, type = x.dataset.type;
    if (type === 'status') CONFIG.statuses.splice(i, 1);
    if (type === 'stage') CONFIG.stages.splice(i, 1);
    if (type === 'size') CONFIG.sizes.splice(i, 1);
    saveConfig(); renderDrawer(); renderRows();
  }));
  const add = document.getElementById('pcAddStatus');
  if (add) add.onclick = () => { CONFIG.statuses.push({ key: 'New Status', cls: 's-notstart' }); saveConfig(); renderDrawer(); renderRows(); };
  body.querySelectorAll('input[data-cap]').forEach(inp => inp.addEventListener('change', () => {
    const v = parseInt(inp.value, 10); if (!isNaN(v)) CONFIG.capacities[inp.dataset.cap] = v;
    saveConfig(); if (currentView === 'heatmap') renderHeatmap();
  }));
  body.querySelectorAll('input[data-vel]').forEach(inp => inp.addEventListener('change', () => {
    const v = parseFloat(inp.value);
    CONFIG.velocities = CONFIG.velocities || {};
    if (!isNaN(v) && v > 0) CONFIG.velocities[inp.dataset.vel] = v; else delete CONFIG.velocities[inp.dataset.vel];
    saveConfig(); applyForecast(); renderAxis(); renderRows();
  }));
}

function resetLocalEdits() {
  ['order', 'status', 'sizes', 'hidden', 'config'].forEach(k => { try { localStorage.removeItem(LS + k); } catch (e) {} });
  showToast('↺ Local edits reset to Jira defaults');
  mount(PROJECT.key, APP);   // rebuild from defaults
  drawerTab = 'games'; drawerOpenRows = new Set();
  openDrawer();
}

// ============================================================
//  CONTROLS / TOAST
// ============================================================
function wireControls() {
  document.getElementById('planToggle').addEventListener('click', () => { planMode ? closeDrawer() : openDrawer(); });
  document.getElementById('gpDrawerClose').addEventListener('click', closeDrawer);
  document.getElementById('gpOverlay').addEventListener('click', closeDrawer);
  document.querySelectorAll('#gpTabs .gp-tab').forEach(b => b.addEventListener('click', () => { drawerTab = b.dataset.tab; renderDrawer(); }));
  const fc = document.getElementById('fcToggle');
  if (fc) {
    fc.classList.toggle('on', showForecast);
    fc.addEventListener('click', () => {
      showForecast = !showForecast;
      try { localStorage.setItem(LS + 'forecast', JSON.stringify(showForecast)); } catch (e) {}
      fc.classList.toggle('on', showForecast);
      applyForecast(); renderAxis(); renderRows();
    });
  }
  document.querySelectorAll('#viewToggle button').forEach(btn => btn.addEventListener('click', () => {
    document.querySelectorAll('#viewToggle button').forEach(b => b.classList.remove('on'));
    btn.classList.add('on'); currentView = btn.dataset.view;
    document.getElementById('roadmapView').style.display = currentView === 'roadmap' ? 'block' : 'none';
    document.getElementById('heatmapView').style.display = currentView === 'heatmap' ? 'block' : 'none';
    document.getElementById('listView').style.display = currentView === 'list' ? 'block' : 'none';
    if (currentView === 'heatmap') renderHeatmap();
    if (currentView === 'list') renderList();
  }));
}

function showToast(msg) {
  const t = document.getElementById('toast');
  t.textContent = msg; t.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove('show'), 2800);
}

// ============================================================
//  MOUNT (sets up one project into a container; call again to switch)
// ============================================================
function mount(projectKey, container) {
  PROJECT = PROJECT_META[projectKey] || window.PROJECT || PROJECT_META.v2;
  APP = container || document.body;
  LS = PROJECT.ls_prefix;

  const data = getData(PROJECT.key);
  RAW_SPRINTS = data.sprints; REFRESHED = data.refreshed;
  RAW_GAMES = (data.games || []).slice();

  SPRINT_LIST = RAW_SPRINTS.slice().sort((a, b) => new Date(a.start) - new Date(b.start));
  SPRINT_BY_ID = {};
  SPRINT_LIST.forEach(s => { SPRINT_BY_ID[String(s.id)] = s; });
  CHART_START = SPRINT_LIST.length ? new Date(SPRINT_LIST[0].start) : new Date('2026-05-11');
  CHART_END = SPRINT_LIST.length ? new Date(SPRINT_LIST[SPRINT_LIST.length - 1].end || SPRINT_LIST[SPRINT_LIST.length - 1].start) : new Date('2027-12-07');
  TODAY = REFRESHED ? new Date(REFRESHED.slice(0, 10) + 'T00:00:00') : new Date();

  CONFIG = JSON.parse(JSON.stringify(DEFAULT_CONFIG));
  try { const s = localStorage.getItem(LS + 'config'); if (s) CONFIG = { ...CONFIG, ...JSON.parse(s) }; } catch (e) {}
  USER_ORDER = []; USER_STATUS = {}; USER_SIZES = {}; HIDDEN = new Set();
  try { USER_ORDER = JSON.parse(localStorage.getItem(LS + 'order') || '[]'); } catch (e) {}
  try { USER_STATUS = JSON.parse(localStorage.getItem(LS + 'status') || '{}'); } catch (e) {}
  try { USER_SIZES = JSON.parse(localStorage.getItem(LS + 'sizes') || '{}'); } catch (e) {}
  try { HIDDEN = new Set(JSON.parse(localStorage.getItem(LS + 'hidden') || '[]')); } catch (e) {}

  if (USER_ORDER.length) {
    const byName = Object.fromEntries(RAW_GAMES.map(g => [g.name, g]));
    const ordered = USER_ORDER.map(n => byName[n]).filter(Boolean);
    const missing = RAW_GAMES.filter(g => !USER_ORDER.includes(g.name));
    RAW_GAMES = [...ordered, ...missing];
  }
  // Preserve the Jira-derived value, then apply any manual override on top.
  RAW_GAMES.forEach(g => {
    g._auto_status = g.workflow_status;            // derived from Jira (Decision #32)
    if (USER_STATUS[g.name]) g.workflow_status = USER_STATUS[g.name];
  });

  try { showForecast = JSON.parse(localStorage.getItem(LS + 'forecast')); } catch (e) {}
  if (showForecast === null || showForecast === undefined) showForecast = true;
  applyForecast();

  activeFilters = { status: 'ALL', stage: 'ALL', search: '' };
  currentView = 'roadmap'; planMode = false; openPanel = null; dragSrcIdx = null;
  drawerTab = 'games'; drawerOpenRows = new Set(); drawerDragIdx = null;
  document.body.classList.remove('plan-mode-on-body');

  buildSkeleton();
  if (!RAW_GAMES.length) {
    document.getElementById('emptyState').style.display = 'block';
    document.getElementById('roadmapView').style.display = 'none';
    document.getElementById('filterBar').style.display = 'none';
    document.getElementById('hdrCount').textContent = '0';
    renderKPI(); wireControls();
    return;
  }
  document.getElementById('hdrCount').textContent = visibleGames().length;
  buildFilterBar(); wireControls(); renderKPI(); renderAxis(); renderRows();
}

// ============================================================
//  BOOT
// ============================================================
window.GamePipeline = { mount: mount, meta: PROJECT_META };

function boot() {
  if (window.PROJECT && window.PROJECT.key) mount(window.PROJECT.key, document.body);
}
if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
else boot();

})();
