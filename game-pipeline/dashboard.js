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

// --- Shared "Save as default for everyone" via GitHub (Decision #39) ---------
const GH_OWNER = 'mayankyadav1994', GH_REPO = 'pong-exec-dashboard';
const GH_API = `https://api.github.com/repos/${GH_OWNER}/${GH_REPO}`;
const GH_PAT_KEY = 'gp_github_pat', GH_USER_KEY = 'gp_github_user', GH_ED_KEY = 'gp_github_editor';  // sessionStorage
const SHARED_CACHE = {};   // project key -> committed shared plan object
let SHARED = {};           // current project's shared plan
let EDITORS = [];          // allowed GitHub logins (UX gate; real gate = repo perms)
let SHARED_STATUS = {}, SHARED_SIZES = {};

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
    { key: 'In Progress',   cls: 's-prod' },
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
// Whole-day delta b - a (positive => b is later than a). Used for early/late.
function dayDelta(a, b) {
  const da = (a instanceof Date) ? a : new Date(a);
  const db = (b instanceof Date) ? b : new Date(b);
  return Math.round((db - da) / 86400000);
}
// First-of-month dates spanning the current chart window (for the month band).
function chartMonths() {
  const out = [];
  let m = new Date(CHART_START.getFullYear(), CHART_START.getMonth(), 1);
  while (m <= CHART_END) { out.push(new Date(m)); m = new Date(m.getFullYear(), m.getMonth() + 1, 1); }
  return out;
}
// "Jun" or "Jan '27" (year shown only on January, for orientation).
function monthLabel(m) {
  const base = m.toLocaleDateString('en-CA', { month: 'short' });
  return m.getMonth() === 0 ? `${base} '${String(m.getFullYear()).slice(2)}` : base;
}
// Early/late vs a target date, given a forecast/end date. Returns {cls, txt} or null.
function targetDelta(targetISO, shipDate) {
  if (!targetISO || !shipDate) return null;
  const d = dayDelta(targetISO, shipDate);          // ship later than target => late
  if (d > 0) return { cls: 'late',   txt: `▼ ${d}d late` };
  if (d < 0) return { cls: 'early',  txt: `▲ ${-d}d early` };
  return { cls: 'ontime', txt: '● on target' };
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
// Furthest target date across visible games + their departments (#40), so the
// chart can be extended to keep far-out targets on-screen instead of clamped.
function maxTargetDate() {
  let mx = null;
  visibleGames().forEach(g => {
    [g.target_date, ...((g.disciplines || []).map(d => d.target_date))]
      .filter(Boolean).forEach(t => { const dt = new Date(t); if (!mx || dt > mx) mx = dt; });
  });
  return mx;
}
// Push CHART_END out past the furthest target (+2wk margin) so its 🎯 flag lands
// inside the visible timeline rather than pinned to the right edge (#42).
function extendChartForTargets() {
  const mt = maxTargetDate();
  if (!mt) return;
  const pad = new Date(mt); pad.setDate(pad.getDate() + 14);
  if (pad > CHART_END) CHART_END = pad;
}
function applyForecast() {
  studioVel = computeStudioVel();
  RAW_GAMES.forEach(g => { g._proj = null; });
  CHART_START = SPRINT_LIST.length ? new Date(SPRINT_LIST[0].start) : new Date('2026-05-11');
  const realEnd = SPRINT_LIST.length ? new Date(SPRINT_LIST[SPRINT_LIST.length - 1].end || SPRINT_LIST[SPRINT_LIST.length - 1].start) : new Date('2027-12-07');
  ALL_SPRINTS = SPRINT_LIST.slice();
  if (!showForecast || !SPRINT_LIST.length) { CHART_END = realEnd; extendChartForTargets(); return; }

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
  extendChartForTargets();
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
function gameSizes(g) { return { ...(SHARED_SIZES[g.name] || {}), ...(USER_SIZES[g.name] || {}) }; }
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
      <div class="refresh-meta"><div class="refresh-dot"></div>Phase 1 · Jira-sourced · <span id="hdrCount">0</span> game epics${REFRESHED ? ' · refreshed ' + REFRESHED : ''}<span id="sharedBadge"></span></div>
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

  <div class="gp-modal-overlay" id="gpModalOverlay"><div class="gp-modal" id="gpModal"></div></div>

  <div class="toast" id="toast"></div>
  <div class="footer">Pong Game Studios PMO · ${PROJECT.title} · shared engine · data via <code>build_jira_data.py</code> · localStorage keys: <code>${LS}*</code></div>`;
}

// ============================================================
//  FILTER BAR
// ============================================================
function buildFilterBar() {
  const statusGroup = document.getElementById('fbStatusGroup');
  const stageGroup = document.getElementById('fbStageGroup');
  ['ALL', 'Not Started', 'In Pre-Prod', 'In Progress', 'In QA', 'On Hold', 'Signed Off'].forEach(k => {
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
  const inflight = VG.filter(g => ['In Progress', 'In QA', 'In Pre-Prod', 'Bug Fixing'].includes(g.workflow_status)).length;
  const notstart = VG.filter(g => g.workflow_status === 'Not Started').length;
  const over = VG.filter(g => g.spent > g.est && g.est > 0).length;
  const overGames = VG.filter(g => g.spent > g.est && g.est > 0).slice(0, 2)
    .map(g => `${g.name.split(' ')[0]} +${Math.round(g.spent - g.est)}h`).join(' · ');
  document.getElementById('kpiStrip').innerHTML = `
    <div class="kpi" style="--rc:#2563eb"><div class="kpi-v">${total}</div><div class="kpi-l">TOTAL GAMES</div><div class="kpi-d">${PROJECT.jira_project} epics</div></div>
    <div class="kpi" style="--rc:#16a34a"><div class="kpi-v">${signed}</div><div class="kpi-l">SIGNED OFF</div><div class="kpi-d">Completed releases</div></div>
    <div class="kpi" style="--rc:#d97706"><div class="kpi-v">${inflight}</div><div class="kpi-l">IN FLIGHT</div><div class="kpi-d">Pre-prod / progress / QA</div></div>
    <div class="kpi" style="--rc:#7c3aed"><div class="kpi-v">${notstart}</div><div class="kpi-l">NOT STARTED</div><div class="kpi-d">Future pipeline</div></div>
    <div class="kpi" style="--rc:#dc2626"><div class="kpi-v">${over}</div><div class="kpi-l">OVER ESTIMATE</div><div class="kpi-d">${overGames || '—'}</div></div>`;
}

// ============================================================
//  SPRINT AXIS
// ============================================================
// ============================================================
//  TIMELINE SCROLL — per-row scrollers kept in sync (#44)
// ============================================================
// Pixel width of the scrollable track (~110px per 2-week sprint).
function trackPxWidth() {
  const days = Math.max(1, (CHART_END - CHART_START) / 86400000);
  return Math.round(days / 14 * 110);
}
// Every row track + the axis share one scroll position, so you can pan from any
// row (no single bottom scrollbar) and the month/sprint header always matches.
let tlScrollLeft = 0, tlSyncing = false;
function registerScroller(el) {
  el.addEventListener('scroll', () => {
    if (tlSyncing) return;
    tlSyncing = true;
    tlScrollLeft = el.scrollLeft;
    document.querySelectorAll('.tl-scroll').forEach(o => { if (o !== el) o.scrollLeft = tlScrollLeft; });
    tlSyncing = false;
  });
  requestAnimationFrame(() => { el.scrollLeft = tlScrollLeft; });
}

// ============================================================
//  MARKER TOOLTIP — one floating card for every timeline marker (#43)
// ============================================================
function setupTips() {
  if (window._gpTipsInit) return;
  window._gpTipsInit = true;
  const tip = document.createElement('div'); tip.className = 'gp-tip'; tip.id = 'gpTip';
  document.body.appendChild(tip);
  let cur = null;
  const place = (e) => {
    const pad = 16, w = tip.offsetWidth, h = tip.offsetHeight;
    let x = e.clientX + pad, y = e.clientY + pad;
    if (x + w > window.innerWidth - 8) x = e.clientX - w - pad;
    if (y + h > window.innerHeight - 8) y = e.clientY - h - pad;
    tip.style.left = Math.max(8, x) + 'px'; tip.style.top = Math.max(8, y) + 'px';
  };
  document.addEventListener('mouseover', e => {
    const el = e.target.closest('[data-tip]'); if (!el) return;
    cur = el; tip.innerHTML = el.dataset.tip; tip.classList.add('on'); place(e);
  });
  document.addEventListener('mousemove', e => { if (cur && tip.classList.contains('on')) place(e); });
  document.addEventListener('mouseout', e => {
    if (!cur) return;
    if (e.relatedTarget && cur.contains(e.relatedTarget)) return;
    tip.classList.remove('on'); cur = null;
  });
  document.addEventListener('scroll', () => { if (cur) { tip.classList.remove('on'); cur = null; } }, true);
}

function renderAxis() {
  const axisEl = document.getElementById('axis');
  // The axis is a synced scroller (#44): a fixed inner width (~110px/sprint) that
  // scrolls in lock-step with every row track.
  axisEl.innerHTML = '<div class="axis-left"></div><div class="axis-track tl-scroll" id="axisTrack"><div class="tl-inner" id="axisInner"></div></div><div class="axis-right"></div>';
  const scroller = document.getElementById('axisTrack');
  const track = document.getElementById('axisInner');
  track.style.width = trackPxWidth() + 'px';
  registerScroller(scroller);
  const list = ALL_SPRINTS && ALL_SPRINTS.length ? ALL_SPRINTS : SPRINT_LIST;
  if (!list.length) return;
  // Month band: a divider at each month boundary + a left-aligned month label,
  // so the timeline reads as labelled month columns above the sprints (#41).
  chartMonths().forEach(m => {
    const l = pct(m);
    const div = document.createElement('div');
    div.className = 'ax-month-div'; div.style.left = l + '%';
    track.appendChild(div);
    const lab = document.createElement('div');
    lab.className = 'ax-month'; lab.style.left = l + '%'; lab.textContent = monthLabel(m);
    track.appendChild(lab);
  });
  const stride = Math.max(1, Math.ceil(list.length / 40));
  list.forEach((s, i) => {
    if (i % stride !== 0) return;
    const end = sprintEnd(s);
    const mid = new Date((new Date(s.start).getTime() + end.getTime()) / 2);
    const chip = document.createElement('div');
    chip.className = 'sp-chip' + (s.projected ? ' proj' : '');
    chip.style.left = pct(mid) + '%';
    chip.innerHTML = `${s.label}<small>${fmtRange(s.start, end)}</small>`;
    chip.dataset.tip = `<b>${s.label}</b><div class="t-sub">${fmtRange(s.start, end)}${s.projected ? ' · projected sprint' : ''}</div>`;
    track.appendChild(chip);
  });
  if (TODAY >= CHART_START && TODAY <= CHART_END) {
    const t = document.createElement('div');
    t.className = 'ax-today'; t.style.left = pct(TODAY) + '%'; t.textContent = 'TODAY';
    t.dataset.tip = `<b>📍 Today</b><div class="t-sub">${fmtD(TODAY)}</div>`;
    track.appendChild(t);
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
  const src = g._status_source;
  const isOverride = src !== 'auto';
  const drift = src === 'local' && USER_STATUS[g.name] !== g._auto_status;
  const statusMark = src === 'shared'
    ? ` <span class="pin-mark" title="Shared default${SHARED.updated_by ? ' · pinned by ' + SHARED.updated_by : ''} — auto-derived: ${g._auto_status}">📌</span>`
    : (src === 'local' ? ` <span class="ovr-mark" title="Manually set (this browser) — auto-derived: ${g._auto_status}">✎</span>` : '');
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
      <span class="epic-status ${statusCls(g.workflow_status)}">${g.workflow_status}${statusMark}</span>
      ${drift ? `<span class="status-drift" title="Jira-derived status is now '${g._auto_status}', but a manual override is in effect">auto: ${g._auto_status}</span>` : ''}
    </div>${fvRow(g)}${sizeRow}`;

  const track = document.createElement('div'); track.className = 'epic-track tl-scroll';
  const trackInner = document.createElement('div'); trackInner.className = 'tl-inner'; trackInner.style.width = trackPxWidth() + 'px';
  track.appendChild(trackInner);
  (ALL_SPRINTS || SPRINT_LIST).forEach(s => { const l = document.createElement('div'); l.className = 'sp-line' + (s.projected ? ' proj' : ''); l.style.left = pct(s.start) + '%'; trackInner.appendChild(l); });
  if (TODAY >= CHART_START && TODAY <= CHART_END) { const tl = document.createElement('div'); tl.className = 'today-line-row'; tl.style.left = pct(TODAY) + '%'; tl.dataset.tip = `<b>📍 Today</b><div class="t-sub">${fmtD(TODAY)}</div>`; trackInner.appendChild(tl); }
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
      chip.dataset.tip = `<b>${dKey.toUpperCase()} · ${s.label}</b><div class="t-sub">${fmtRange(s.start, end)}${dashed ? ' · projected' : ''}</div>`;
      chip.textContent = w > 3 ? shortSprint(s.label) : '';
      trackInner.appendChild(chip);
    };
    sprs.forEach(s => lane(s, false));
    pslots.forEach(s => lane(s, true));
    laneTop += 11;
  });
  if (proj && proj.ship) {
    const sm = document.createElement('div'); sm.className = 'ship-line';
    sm.style.left = pct(proj.ship.start) + '%';
    sm.dataset.tip = `<b>⚑ Estimated completion</b><div class="t-sub">${proj.ship.label} · ${fmtD(proj.ship.start)}</div><div class="t-note">remaining hours ÷ velocity</div>`;
    trackInner.appendChild(sm);
  }
  // Targeted due date (Jira epic due date): a solid flag, tinted early/late vs
  // the forecast ship when forecast is on (#40).
  if (g.target_date) {
    const tl2 = document.createElement('div'); tl2.className = 'target-line';
    tl2.style.left = pct(g.target_date) + '%';
    const dl = (proj && proj.ship) ? targetDelta(g.target_date, proj.ship.start) : null;
    if (dl) tl2.classList.add(dl.cls);
    tl2.dataset.tip = `<b>🎯 Target completion</b><div class="t-sub">${fmtD(g.target_date)}</div>${dl ? `<div class="t-chip ${dl.cls}">${dl.txt} vs estimate</div>` : ''}`;
    trackInner.appendChild(tl2);
  }
  if (laneTop === 8) { const n = document.createElement('div'); n.style.cssText = 'font-size:9px;color:var(--sub);font-style:italic;padding-top:6px'; n.textContent = 'No scheduled sprints yet'; trackInner.appendChild(n); }
  registerScroller(track);

  const hrs = document.createElement('div'); hrs.className = 'epic-hrs';
  const over = g.spent > g.est && g.est > 0;
  const progressPct = g.est > 0 ? Math.min(100, Math.round(g.spent / g.est * 100)) : 0;
  const progressColor = over ? '#dc2626' : (progressPct >= 70 ? gameColor(g) : '#60a5fa');
  const dl = (g.target_date && proj && proj.ship) ? targetDelta(g.target_date, proj.ship.start) : null;
  hrs.innerHTML = `
    <div class="epic-hrs-v ${over ? 'over' : ''}">${Math.round(g.spent)}h</div>
    <div class="epic-hrs-l">SPENT / ${Math.round(g.est)}h est</div>
    <div class="epic-prog"><div class="epic-prog-fill" style="width:${progressPct}%;background:${progressColor}"></div></div>
    <div class="epic-hrs-l" style="color:${over ? '#dc2626' : 'var(--sub)'};margin-top:4px">${over ? `⚠ +${Math.round(g.spent - g.est)}h over` : `${progressPct}% spent`}</div>
    ${g.target_date ? `<div class="epic-hrs-l tgt-line" title="Targeted due date (Jira epic due date)">🎯 Target ${fmtD(g.target_date)}</div>` : ''}
    ${proj && proj.ship ? `<div class="epic-hrs-l proj-ship" title="Forecast: remaining hours ÷ velocity (parallel)">≈ Est ${shortSprint(proj.ship.label)} · ${fmtD(proj.ship.start)}</div>` : ''}
    ${dl ? `<div class="dl-chip ${dl.cls}" title="Forecast ship vs target">${dl.txt}</div>` : ''}`;

  const chev = document.createElement('div'); chev.className = 'chev'; chev.textContent = '⌄';
  // Freeze the name column (left) and hours column (right); only the track scrolls (#43).
  const leftWrap = document.createElement('div'); leftWrap.className = 'epic-left';
  leftWrap.appendChild(dh); leftWrap.appendChild(label);
  const rightWrap = document.createElement('div'); rightWrap.className = 'epic-right';
  rightWrap.appendChild(hrs); rightWrap.appendChild(chev);
  row.appendChild(leftWrap); row.appendChild(track); row.appendChild(rightWrap);

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
  // Month band header so the department bars read under labelled month columns (#41).
  const mh = document.createElement('div'); mh.className = 'disc-row disc-month-head';
  let mhTrack = '';
  chartMonths().forEach(m => {
    const l = pct(m);
    mhTrack += `<div class="dmh-div" style="left:${l}%"></div><div class="dmh-lab" style="left:${l}%">${monthLabel(m)}</div>`;
  });
  mh.innerHTML = `<div class="disc-label"></div><div class="disc-track dmh-track">${mhTrack}</div><div class="disc-hrs"></div>`;
  tlPane.appendChild(mh);
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
    // Department target (latest due date among this discipline's tasks; #40):
    // a tick on the track, tinted late if the bar runs past it, plus a dated line.
    let tgtTick = '', tgtTxt = '';
    if (disc.target_date) {
      const cls = sprs.length ? (dayDelta(disc.target_date, sprintEnd(sprs[sprs.length - 1])) > 0 ? 'late' : 'early') : '';
      tgtTick = `<div class="disc-target ${cls}" style="left:${pct(disc.target_date)}%" data-tip="<b>🎯 ${dKey.toUpperCase()} target</b><div class='t-sub'>${fmtD(disc.target_date)}</div>"></div>`;
      tgtTxt = `<div class="disc-tgt">🎯 ${fmtD(disc.target_date)}</div>`;
    }
    const hrsOver = disc.spent > disc.est && disc.est > 0;
    r.innerHTML = `<div class="disc-label">${dKey}</div><div class="disc-track">${bar}${tgtTick}</div><div class="disc-hrs"><div>${Math.round(disc.spent)} / ${Math.round(disc.est)}h ${hrsOver ? '<span class="over">⚠</span>' : ''}</div>${tgtTxt}</div>`;
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
  const login = getGhUser();
  let authHtml;
  if (!login) authHtml = `<button class="gp-foot-btn" id="gpSignIn">🔐 Sign in to publish</button>`;
  else if (getGhEditor()) authHtml = `<span class="gp-foot-user" title="signed in">✓ ${login}</span><button class="gp-foot-btn primary" id="gpPublish">💾 Save as default for everyone</button><button class="gp-foot-btn" id="gpSignOut">sign out</button>`;
  else authHtml = `<span class="gp-foot-user">✓ ${login} · view-only</span><button class="gp-foot-btn" id="gpSignOut">sign out</button>`;
  document.getElementById('gpDrawerFoot').innerHTML =
    `<button class="gp-foot-btn" id="gpReset">↺ Reset local edits</button>${authHtml}<span style="flex:1"></span>` +
    `<span style="font-size:10px;color:var(--sub);align-self:center">${visibleGames().length} shown${hidden ? ' · ' + hidden + ' hidden' : ''}</span>`;
  document.getElementById('gpReset').onclick = resetLocalEdits;
  const si = document.getElementById('gpSignIn'); if (si) si.onclick = openSignInModal;
  const pub = document.getElementById('gpPublish'); if (pub) pub.onclick = confirmPublish;
  const so = document.getElementById('gpSignOut'); if (so) so.onclick = () => { setPat(''); showToast('Signed out'); renderDrawer(); };
}

function renderDrawerGames(body) {
  const statusOptions = g => CONFIG.statuses.map(s => `<option value="${s.key}"${s.key === g.workflow_status ? ' selected' : ''}>${s.key}</option>`).join('');
  const sizeOpts = ['—', ...CONFIG.sizes.map(s => s.key)];
  const sizeSel = (g, k) => `<label>${k.toUpperCase()}<select data-size-game="${g.name}" data-disc="${k}">` +
    sizeOpts.map(o => `<option value="${o === '—' ? '' : o}"${(gameSizes(g)[k] || '') === (o === '—' ? '' : o) ? ' selected' : ''}>${o}</option>`).join('') + `</select></label>`;
  body.innerHTML = RAW_GAMES.map((g, idx) => {
    const on = !HIDDEN.has(g.name), open = drawerOpenRows.has(g.name), src = g._status_source;
    const mark = src === 'shared' ? ' <span class="pin-mark" title="shared default (auto: ' + g._auto_status + ')">📌</span>'
      : (src === 'local' ? ' <span class="ovr-mark" title="local override (auto: ' + g._auto_status + ')">✎</span>' : '');
    return `<div class="gpg${on ? '' : ' off'}${open ? ' open' : ''}" data-idx="${idx}" draggable="true">
      <div class="gpg-head">
        <span class="gpg-handle" title="Drag to reorder">⠿</span>
        <button class="gpg-toggle${on ? ' on' : ''}" data-hide="${g.name}" title="${on ? 'Visible — click to hide' : 'Hidden — click to show'}"></button>
        <span class="gpg-name">${g.name}${g.jira ? `<span class="k">${g.jira}</span>` : ''}</span>
        <span class="gpg-status"><select data-status-game="${g.name}">${statusOptions(g)}</select>${mark}</span>
        <span class="gpg-chev" data-expand="${g.name}">▾</span>
      </div>
      <div class="gpg-expand">
        <div class="gpg-sizes">${sizeSel(g, 'art')}${sizeSel(g, 'math')}${sizeSel(g, 'dev')}${sizeSel(g, 'sound')}</div>
        <div style="margin-top:8px;font-size:10px;color:var(--sub)">Stage: <strong>${stageLabel(g.current_stage)}</strong> · Jira epic: ${g.epic_status || '—'}${src !== 'auto' ? ` · <button class="revert-auto" data-revert="${g.name}">↺ revert to auto (${g._auto_status})</button>` : ''}</div>
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
  showToast('↺ Local edits reset (shared defaults still apply)');
  mount(PROJECT.key, APP);   // rebuild from defaults
  drawerTab = 'games'; drawerOpenRows = new Set();
  openDrawer();
}

// ============================================================
//  SHARED PLAN — load + publish via GitHub (Decision #39)
// ============================================================
async function loadSharedData(key) {
  try { const r = await fetch(`plan-${key}.json?ts=` + new Date().getTime()); if (r.ok) SHARED_CACHE[key] = await r.json(); } catch (e) {}
  if (SHARED_CACHE[key] === undefined) SHARED_CACHE[key] = {};
  try { const r = await fetch('editors.json?ts=' + new Date().getTime()); if (r.ok) { const j = await r.json(); EDITORS = j.editors || []; } } catch (e) {}
}
function getPat() { try { return sessionStorage.getItem(GH_PAT_KEY) || ''; } catch (e) { return ''; } }
function getGhUser() { try { return sessionStorage.getItem(GH_USER_KEY) || ''; } catch (e) { return ''; } }
function getGhEditor() { try { return sessionStorage.getItem(GH_ED_KEY) === '1'; } catch (e) { return false; } }
function setPat(t, user, isEd) {
  try {
    if (t) { sessionStorage.setItem(GH_PAT_KEY, t); if (user) sessionStorage.setItem(GH_USER_KEY, user); sessionStorage.setItem(GH_ED_KEY, isEd ? '1' : '0'); }
    else { sessionStorage.removeItem(GH_PAT_KEY); sessionStorage.removeItem(GH_USER_KEY); sessionStorage.removeItem(GH_ED_KEY); }
  } catch (e) {}
}
// Editor allowlist is by EMAIL (with GitHub login as a fallback match). Empty
// list = any collaborator with push access. Real gate is repo write permission.
function matchEditor(login, emails) {
  if (!EDITORS.length) return true;
  const allow = EDITORS.map(x => String(x).toLowerCase());
  return [String(login).toLowerCase(), ...emails].some(i => allow.includes(i));
}

async function ghFetch(url, opts) {
  opts = opts || {};
  const headers = Object.assign({ 'Authorization': 'token ' + getPat(), 'Accept': 'application/vnd.github+json', 'Content-Type': 'application/json' }, opts.headers || {});
  const r = await fetch(url, Object.assign({}, opts, { headers }));
  if (!r.ok) { let m = 'HTTP ' + r.status; try { const j = await r.json(); m += ': ' + (j.message || ''); } catch (e) {} throw new Error(m); }
  return r.status === 204 ? {} : r.json();
}
async function verifyPat() {
  const u = await ghFetch('https://api.github.com/user');
  const repo = await ghFetch(GH_API);
  if (!repo.permissions || !repo.permissions.push) throw new Error(`${u.login} can't push to ${GH_OWNER}/${GH_REPO} — ask an admin for write access.`);
  let emails = [];
  try { const e = await ghFetch('https://api.github.com/user/emails'); emails = (e || []).filter(x => x.verified).map(x => String(x.email).toLowerCase()); } catch (err) { /* token lacks email scope */ }
  if (u.email) emails.push(String(u.email).toLowerCase());
  return { login: u.login, emails: [...new Set(emails)] };
}
function nowStamp() { const d = new Date(); return d.toISOString().slice(0, 16).replace('T', ' '); }
function buildPlanPayload() {
  const status = {}, sizes = {};
  RAW_GAMES.forEach(g => {
    if (g.workflow_status !== g._auto_status) status[g.name] = g.workflow_status;
    const sz = gameSizes(g), keep = {};
    ['art', 'math', 'dev', 'sound'].forEach(k => { if (sz[k]) keep[k] = sz[k]; });
    if (Object.keys(keep).length) sizes[g.name] = keep;
  });
  return {
    order: RAW_GAMES.map(g => g.name), status, sizes, hidden: [...HIDDEN],
    config: { statuses: CONFIG.statuses, stages: CONFIG.stages, sizes: CONFIG.sizes, capacities: CONFIG.capacities, velocities: CONFIG.velocities || {} },
    updated_by: getGhUser() || null, updated_at: nowStamp(),
  };
}
async function publishPlan() {
  const path = `plan-${PROJECT.key}.json`;
  const payload = buildPlanPayload();
  const content = btoa(unescape(encodeURIComponent(JSON.stringify(payload, null, 2))));
  let sha = null;
  try { const cur = await ghFetch(`${GH_API}/contents/${path}`); sha = cur.sha; } catch (e) { /* first time: no file */ }
  const body = { message: `plan(${PROJECT.key}): save as default by ${getGhUser() || 'editor'}`, content, branch: 'main' };
  if (sha) body.sha = sha;
  await ghFetch(`${GH_API}/contents/${path}`, { method: 'PUT', body: JSON.stringify(body) });
  SHARED_CACHE[PROJECT.key] = payload;   // reflect immediately for this browser
}

// --- modal helpers ---
function openModal(html) {
  const ov = document.getElementById('gpModalOverlay'), m = document.getElementById('gpModal');
  m.innerHTML = html; ov.classList.add('open');
}
function closeModal() { document.getElementById('gpModalOverlay').classList.remove('open'); }

function openSignInModal() {
  openModal(`<h3>🔐 Sign in to publish</h3>
    <p class="gp-modal-note">Paste a GitHub <b>fine-grained token</b> scoped to <code>${GH_OWNER}/${GH_REPO}</code> with <b>Contents: Read &amp; write</b>. It's kept only in this tab's memory (sessionStorage), never committed.</p>
    <input type="password" id="gpPat" placeholder="github_pat_…" autocomplete="off" spellcheck="false">
    <div class="gp-modal-msg" id="gpPatMsg"></div>
    <div class="gp-modal-foot"><button class="gp-foot-btn" id="gpPatCancel">Cancel</button><button class="gp-foot-btn primary" id="gpPatVerify">Verify &amp; sign in</button></div>`);
  document.getElementById('gpPatCancel').onclick = closeModal;
  document.getElementById('gpPatVerify').onclick = async () => {
    const tok = document.getElementById('gpPat').value.trim();
    const msg = document.getElementById('gpPatMsg');
    if (!tok) { msg.textContent = 'Enter a token.'; return; }
    try { sessionStorage.setItem(GH_PAT_KEY, tok); } catch (e) {}
    msg.textContent = 'Verifying…';
    try {
      const { login, emails } = await verifyPat();
      const ed = matchEditor(login, emails);
      const allow = EDITORS.map(x => String(x).toLowerCase());
      const display = emails.find(e => allow.includes(e)) || login;
      setPat(tok, display, ed);
      closeModal(); showToast('✓ Signed in as ' + display + (ed ? '' : ' (view-only — not an editor)')); renderDrawer();
    } catch (e) { setPat(''); msg.textContent = '✗ ' + e.message; }
  };
}
function confirmPublish() {
  const login = getGhUser();
  openModal(`<h3>💾 Save as default for everyone</h3>
    <p class="gp-modal-note">This commits the current plan to <code>plan-${PROJECT.key}.json</code> on <code>main</code> as <b>${login}</b>. It becomes the shared baseline for <b>all viewers</b> after the redeploy (~1–3 min), overriding the Jira auto-pull until changed.</p>
    <div class="gp-modal-msg" id="gpPubMsg"></div>
    <div class="gp-modal-foot"><button class="gp-foot-btn" id="gpPubCancel">Cancel</button><button class="gp-foot-btn primary" id="gpPubGo">Commit to main</button></div>`);
  document.getElementById('gpPubCancel').onclick = closeModal;
  document.getElementById('gpPubGo').onclick = async () => {
    const msg = document.getElementById('gpPubMsg'); msg.textContent = 'Committing…';
    try { await publishPlan(); closeModal(); showToast('✓ Saved as default — live after redeploy'); renderDrawer(); }
    catch (e) { msg.textContent = '✗ ' + e.message; }
  };
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

  // Precedence: Jira auto  <  shared committed plan  <  this browser's local edits.
  SHARED = SHARED_CACHE[PROJECT.key] || {};
  SHARED_STATUS = SHARED.status || {};
  SHARED_SIZES = SHARED.sizes || {};

  CONFIG = JSON.parse(JSON.stringify(DEFAULT_CONFIG));
  if (SHARED.config) CONFIG = { ...CONFIG, ...SHARED.config };
  try { const s = localStorage.getItem(LS + 'config'); if (s) CONFIG = { ...CONFIG, ...JSON.parse(s) }; } catch (e) {}

  USER_ORDER = []; USER_STATUS = {}; USER_SIZES = {};
  try { USER_ORDER = JSON.parse(localStorage.getItem(LS + 'order') || '[]'); } catch (e) {}
  try { USER_STATUS = JSON.parse(localStorage.getItem(LS + 'status') || '{}'); } catch (e) {}
  try { USER_SIZES = JSON.parse(localStorage.getItem(LS + 'sizes') || '{}'); } catch (e) {}
  // hidden: local set if this browser has touched it, else the shared set
  const lhid = localStorage.getItem(LS + 'hidden');
  HIDDEN = new Set(lhid != null ? JSON.parse(lhid || '[]') : (SHARED.hidden || []));

  const order = USER_ORDER.length ? USER_ORDER : (SHARED.order || []);
  if (order.length) {
    const byName = Object.fromEntries(RAW_GAMES.map(g => [g.name, g]));
    const ordered = order.map(n => byName[n]).filter(Boolean);
    const missing = RAW_GAMES.filter(g => !order.includes(g.name));
    RAW_GAMES = [...ordered, ...missing];
  }
  // Resolve effective status per game: local > shared > auto.
  RAW_GAMES.forEach(g => {
    g._auto_status = g.workflow_status;                 // Jira-derived (Decision #32)
    g._shared_status = SHARED_STATUS[g.name] || null;
    if (USER_STATUS[g.name] != null) { g.workflow_status = USER_STATUS[g.name]; g._status_source = 'local'; }
    else if (g._shared_status != null) { g.workflow_status = g._shared_status; g._status_source = 'shared'; }
    else { g._status_source = 'auto'; }
  });

  try { showForecast = JSON.parse(localStorage.getItem(LS + 'forecast')); } catch (e) {}
  if (showForecast === null || showForecast === undefined) showForecast = true;
  applyForecast();

  activeFilters = { status: 'ALL', stage: 'ALL', search: '' };
  currentView = 'roadmap'; planMode = false; openPanel = null; dragSrcIdx = null;
  drawerTab = 'games'; drawerOpenRows = new Set(); drawerDragIdx = null;
  document.body.classList.remove('plan-mode-on-body');

  buildSkeleton();
  setupTips();
  // First visit to a project: fetch its shared plan + editor list, then re-mount.
  if (SHARED_CACHE[PROJECT.key] === undefined) {
    loadSharedData(PROJECT.key).then(() => mount(projectKey, container));
  }
  const sb = document.getElementById('sharedBadge');
  if (sb && SHARED && SHARED.updated_by) sb.innerHTML = ` · <span class="shared-badge" title="Shared defaults committed by ${SHARED.updated_by} on ${SHARED.updated_at || ''} — override the Jira auto-pull">📌 shared plan · ${SHARED.updated_by}</span>`;
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
