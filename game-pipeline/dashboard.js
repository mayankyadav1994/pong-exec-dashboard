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
const LANE_ORDER = ['art', 'design', 'math', 'dev', 'sound', 'review', 'qa'];  // review before QA (#59)

// --- Shared "Save as default for everyone" via GitHub (Decision #39) ---------
const GH_OWNER = 'mayankyadav1994', GH_REPO = 'pong-exec-dashboard';
const GH_API = `https://api.github.com/repos/${GH_OWNER}/${GH_REPO}`;
const GH_PAT_KEY = 'gp_github_pat', GH_USER_KEY = 'gp_github_user', GH_ED_KEY = 'gp_github_editor';  // sessionStorage
const SHARED_CACHE = {};   // project key -> committed shared plan object
const PLAN_VERSION = {};   // key -> shared-plan version captured at load (stale-publish guard, #54)
let _lastSharedRefresh = 0; // throttle timestamp for the viewer soft auto-refresh (#54)
let SHARED = {};           // current project's shared plan
let EDITORS = [];          // allowed GitHub logins (UX gate; real gate = repo perms)
let SHARED_STATUS = {}, SHARED_SIZES = {};
// Same override pattern as status: shared plan holds team-wide overrides,
// USER_STAGE holds per-browser edits until the next Save-as-default. Auto
// value (from Jira classifier) is captured once as g._auto_stage so we can
// detect drift and offer revert.
let SHARED_STAGE = {};
// Notes per game: { "<game name>": [{ts, author, body}, ...] }. Shared via
// plan-{key}.json. New notes append to a working copy in NOTES, persisted to
// SHARED_NOTES on Save-as-default. Append-only via the UI — older entries
// stay readable as the history of decisions / context for stakeholders.
let SHARED_NOTES = {};
let NOTES = {};
let SHARED_REASONS = {};   // published date-move "why" reasons (#66)
let REASONS = {};          // working copy = shared + this browser's overlay
let openPanelTab = null;   // when set, the next opened detail panel activates this tab (badge → HISTORY)

// --- Project metadata --------------------------------------------------------
const PROJECT_META = {
  v2: { key: 'v2', title: 'V2 Game Pipeline',
        subtitle: 'Pong Game Studios · V2 game-epic lifecycle dashboard',
        jira_project: 'V2', ls_prefix: 'gp_v2_' },
  ig: { key: 'ig', title: 'iGaming Game Pipeline',
        subtitle: 'Pong Game Studios · iGaming game-epic lifecycle dashboard',
        jira_project: 'IG', ls_prefix: 'gp_ig_' },
  igf: { key: 'igf', title: 'iGaming Features',
        subtitle: 'Pong Game Studios · iGaming feature pipeline · add features from the Edit ▸ search',
        jira_project: 'IG', ls_prefix: 'gp_igf_' },
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
    // Terminal state — game intentionally shelved / discontinued. Not
    // auto-derived (Jira has no equivalent status the classifier maps to);
    // only reachable via a manual override in Plan Mode.
    { key: 'Cancelled',     cls: 's-cancel' },
  ],
  stages: [
    { key: 'concept', label: 'Concept', color: '#fde68a' },
    { key: 'art',     label: 'Art',     color: '#fed7aa' },
    { key: 'design',  label: 'Design',  color: '#bfdbfe' },
    { key: 'math',    label: 'Math',    color: '#bbf7d0' },
    { key: 'dev',     label: 'Dev',     color: '#93c5fd' },
    { key: 'sound',   label: 'Sound',   color: '#f5d0e0' },
    { key: 'review',  label: 'Review',  color: '#ddd6fe' },
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
let CONFIG, USER_ORDER, USER_STATUS, USER_STAGE, USER_SIZES, HIDDEN, ALL_GAMES, USER_ADDED;
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
// Interpret a value as a calendar date for DISPLAY (#56). A date-only string
// ('YYYY-MM-DD') is built at LOCAL midnight so a due/sprint/delivery date shows
// the SAME calendar day in every viewer timezone. `new Date("2026-08-31")` would
// parse as UTC midnight and render a day early for any viewer west of UTC (e.g.
// a Jira duedate of Aug 31 showing as Aug 30). Date objects pass through as-is.
function asDate(d) {
  if (d instanceof Date) return d;
  const m = String(d).match(/^(\d{4})-(\d{2})-(\d{2})/);
  return m ? new Date(+m[1], +m[2] - 1, +m[3]) : new Date(d);
}
function fmtD(d) {
  return asDate(d).toLocaleDateString('en-CA', { month: 'short', day: 'numeric', year: '2-digit' });
}
function fmtRange(s, e) {
  return asDate(s).toLocaleDateString('en-CA', { month: 'short', day: 'numeric' }) +
    ' – ' + asDate(e || s).toLocaleDateString('en-CA', { month: 'short', day: 'numeric' });
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
// Week-start (Monday) dates spanning the chart window, for the weekly calendar
// ticks + gridlines (#67). Sprint starts are Mondays, so these align with them
// and add the in-between weeks.
function chartWeeks() {
  const out = [];
  const d = new Date(CHART_START);
  const dow = d.getDay();                 // 0=Sun..6=Sat
  d.setDate(d.getDate() + (dow === 1 ? 0 : (8 - dow) % 7));   // advance to first Monday
  while (d <= CHART_END) { out.push(new Date(d)); d.setDate(d.getDate() + 7); }
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
  // Statuses that mean "no further work projected" (Option B: manual override
  // is the source of truth — if the dashboard's effective status says the
  // game is past dev, we trust it instead of redoing hours math).
  const STATUS_TERMINAL = new Set(['Signed Off', 'Delivered', 'On Hold']);
  const STATUS_QA_ONLY  = new Set(['In QA']);
  RAW_GAMES.forEach(g => {
    if (g.delivered) return;
    const ws = g.workflow_status || '';
    if (STATUS_TERMINAL.has(ws)) return;  // no forecast for done/held games
    // "In QA" alone does NOT mean production is finished. derive_status
    // (build_jira_data.py) flips a game to In QA on a single reopened bug or an
    // active QA sub-task, even while dev/art still have substantial open work.
    // Only collapse to a QA-only forecast when production is genuinely done —
    // otherwise forecast the unfinished production lanes too, so a game that is
    // a third of the way through dev can't show an "≈ Est … 72d early" that
    // silently discounts the remaining production hours in front of
    // stakeholders (#52). A production lane counts as open if the build did not
    // flag it done/hold and it still has estimated hours left.
    const PROD_LANES = ['art', 'design', 'math', 'dev', 'sound'];
    const prodOpen = PROD_LANES.some(k => {
      const d = (g.disciplines || []).find(x => x.key === k);
      // "remaining work exists" — use Jira's Remaining Estimate directly
      // when the builder emitted it, else fall back to est-minus-spent.
      const rem = (d && d.remaining != null) ? d.remaining : Math.max(0, (d.est || 0) - (d.spent || 0));
      return d && d.phase !== 'done' && d.phase !== 'hold' && rem > 0;
    });
    const qaOnly = STATUS_QA_ONLY.has(ws) && !prodOpen;
    const disc = {}; let ship = 0, any = false;
    // The lane the game is actively sitting in (its current stage; QA for a
    // genuinely-QA-only game). Even when this lane has overrun its estimate
    // (remaining hours <= 0) the game is provably not finished, so it must
    // still forecast to at least the current/next sprint rather than drop out
    // of the forecast entirely. Without this, an over-budget-but-still-active
    // QA game shows no ≈ Est line and no projected bar at all.
    const activeKey = qaOnly ? 'qa' : g.current_stage;
    LANE_ORDER.forEach(k => {
      const d = (g.disciplines || []).find(x => x.key === k); if (!d) return;
      // Option A: if the discipline was flagged done by the Jira-side
      // classifier (build_jira_data.py phase=='done'), don't second-guess it.
      if (d.phase === 'done') return;
      // QA-only mode: game is officially In QA, every non-QA lane is treated
      // as done regardless of its est-vs-spent gap. Stops the dashboard from
      // projecting more art / sound / dev when the game has moved past those.
      if (qaOnly && k !== 'qa') return;
      // Prefer Jira's Remaining Estimate directly; fall back to est-minus-spent
      // for old data payloads that haven't been rebuilt with the new field.
      const rem = (d.remaining != null) ? Math.max(0, d.remaining) : Math.max(0, (d.est || 0) - (d.spent || 0));
      // No remaining hours: skip unless this is the active lane, which floors
      // to one sprint of remaining work so the game still forecasts to its
      // current/next sprint instead of disappearing.
      if (rem <= 0 && k !== activeKey) return;
      const need = rem > 0 ? Math.max(1, Math.ceil(rem / effRate(d))) : 1;
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
// Per-department people breakdown (#51). Names + logged hours come from the
// build (discipline.people, assignee-attributed, sorted by hours desc).
const DEPT_COLORS = { art: '#f59e0b', design: '#3b82f6', math: '#22c55e', dev: '#6366f1', sound: '#ec4899', review: '#8b5cf6', qa: '#eab308' };
function initialsOf(n) { return String(n || '').trim().split(/\s+/).slice(0, 2).map(w => w[0] || '').join('').toUpperCase(); }
function fmtHrs(h) { return (Math.round(h * 10) / 10).toString(); }
// Chip line for the HOURS tab — one avatar+name+hours chip per person; the
// top contributor is outlined and zero-hour (assigned, not started) dimmed.
function peopleChips(disc) {
  const ppl = (disc && disc.people) ? disc.people : [];
  if (!ppl.length) return '';
  const col = DEPT_COLORS[disc.key] || 'var(--muted)';
  return '<div class="people">' + ppl.map((p, i) => {
    const zero = !(p.hours > 0);
    return `<span class="person${i === 0 && !zero ? ' lead' : ''}${zero ? ' zero' : ''}" data-tip="<b>${p.name}</b><div class='t-sub'>${fmtHrs(p.hours)}h on ${disc.key.toUpperCase()}</div>">`
      + `<span class="av" style="background:${col}">${initialsOf(p.name)}</span>`
      + `<span class="nm">${p.name}</span><span class="hh">${fmtHrs(p.hours)}h</span></span>`;
  }).join('') + '</div>';
}
// Compact people list appended to a burndown lane tooltip (department total).
function peopleTip(disc) {
  const ppl = (disc && disc.people || []).filter(p => p.hours > 0);
  if (!ppl.length) return '';
  return '<div class="t-people">' + ppl.map(p =>
    `<div class="t-person"><span>${p.name}</span><span>${fmtHrs(p.hours)}h</span></div>`).join('') + '</div>';
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
function saveStage()  { try { localStorage.setItem(LS + 'stage',  JSON.stringify(USER_STAGE)); } catch (e) {} }
function saveSizes()  { try { localStorage.setItem(LS + 'sizes',  JSON.stringify(USER_SIZES)); } catch (e) {} }
function saveConfig() { try { localStorage.setItem(LS + 'config', JSON.stringify(CONFIG)); } catch (e) {} }
function saveHidden() { try { localStorage.setItem(LS + 'hidden', JSON.stringify([...HIDDEN])); } catch (e) {} }
function saveAdded()  { try { localStorage.setItem(LS + 'added',  JSON.stringify(USER_ADDED)); } catch (e) {} }
function visibleGames() { return RAW_GAMES.filter(g => !HIDDEN.has(g.name)); }

// Effective workflow status for a game: local override > shared plan > Jira auto.
// Status progression rank — used for auto-promotion: when Jira has advanced
// the auto status past a stale shared override (e.g. shared=In Progress but
// Jira tickets now show In QA), we ignore the override and surface the live
// auto value. statusIdx returns -1 for anything not in this chain (e.g. a
// custom status the dashboard hasn't seen) — those skip auto-promotion.
const STATUS_PROGRESSION = [
  'Not Started', 'On Hold', 'In Pre-Prod', 'In Progress', 'In QA',
  'Signed Off', 'Delivered',
];
function statusIdx(s) { return STATUS_PROGRESSION.indexOf(s); }

function resolveGameStatus(g) {
  if (g._auto_status == null) g._auto_status = g.workflow_status;   // Jira-derived (#32), captured once
  g._shared_status = SHARED_STATUS[g.name] || null;
  g._auto_promoted_from = null;
  // Option B (#53): if Jira's auto status sits strictly downstream of the
  // shared override, the override is stale — drop it for display, remember
  // the original so the banner can list "from X → to Y" and offer to
  // permanently clear it via Save-as-default.
  if (g._shared_status && g._shared_status !== g._auto_status) {
    const ai = statusIdx(g._auto_status), si = statusIdx(g._shared_status);
    if (ai >= 0 && si >= 0 && ai > si) {
      g._auto_promoted_from = g._shared_status;
      g._shared_status = null;
    }
  }
  if (USER_STATUS[g.name] != null) { g.workflow_status = USER_STATUS[g.name]; g._status_source = 'local'; }
  else if (g._shared_status != null) { g.workflow_status = g._shared_status; g._status_source = 'shared'; }
  else { g.workflow_status = g._auto_status; g._status_source = 'auto'; }
}

// Stage progression — mirrors the discipline-pipeline order so auto-promote
// can detect when Jira's derived stage has advanced past a stale shared
// override (e.g. shared=dev but Jira tickets now show qa work active).
const STAGE_PROGRESSION = [
  'concept', 'design', 'art', 'math', 'dev', 'sound', 'qa', 'bugfix', 'done',
];
function stageIdx(s) { return STAGE_PROGRESSION.indexOf(s); }

function resolveGameStage(g) {
  // Capture the Jira-derived stage exactly once so subsequent renders don't
  // treat an overridden value as the auto baseline.
  if (g._auto_stage == null) g._auto_stage = g.current_stage;
  g._shared_stage = SHARED_STAGE[g.name] || null;
  g._auto_stage_promoted_from = null;
  // Same auto-promote rule as status: shared override strictly upstream of
  // the current auto value → drop the override, remember it for the banner.
  if (g._shared_stage && g._shared_stage !== g._auto_stage) {
    const ai = stageIdx(g._auto_stage), si = stageIdx(g._shared_stage);
    if (ai >= 0 && si >= 0 && ai > si) {
      g._auto_stage_promoted_from = g._shared_stage;
      g._shared_stage = null;
    }
  }
  if (USER_STAGE[g.name] != null) { g.current_stage = USER_STAGE[g.name]; g._stage_source = 'local'; }
  else if (g._shared_stage != null) { g.current_stage = g._shared_stage; g._stage_source = 'shared'; }
  else { g.current_stage = g._auto_stage; g._stage_source = 'auto'; }
}

// "+ Add game" (#47): pull an off-roster Jira epic onto the board, editable like any game.
function addGameToRoster(jira) {
  if (RAW_GAMES.some(g => g.jira === jira)) return;
  const g = ALL_GAMES.find(x => x.jira === jira); if (!g) return;
  const wasEmpty = RAW_GAMES.length === 0;   // e.g. the Features board starts empty (#70)
  if (!USER_ADDED.includes(jira)) USER_ADDED.push(jira);
  saveAdded(); resolveGameStatus(g); resolveGameStage(g); RAW_GAMES.push(g); saveOrder();
  if (wasEmpty) {
    // Board just came alive — mount()'s empty branch had shown the empty state,
    // hidden the chart, and skipped buildFilterBar/renderAxis. Reveal + build it.
    document.getElementById('emptyState').style.display = 'none';
    const fb = document.getElementById('filterBar'); if (fb) fb.style.display = '';
    document.getElementById('roadmapView').style.display = (currentView === 'roadmap' || currentView === 'gantt') ? 'block' : 'none';
    buildFilterBar();
  }
  applyForecast(); renderAxis(); renderRows(); renderKPI(); renderDrawer();
  const hdr = document.getElementById('hdrCount'); if (hdr) hdr.textContent = visibleGames().length;
  if (wasEmpty) requestAnimationFrame(centerToday);
  showToast('✓ Added ' + g.name);
}
function removeGameFromRoster(jira) {
  const i = USER_ADDED.indexOf(jira); if (i >= 0) { USER_ADDED.splice(i, 1); saveAdded(); }
  const g = RAW_GAMES.find(x => x.jira === jira);
  const stillRoster = g && (g.in_roster !== false || (SHARED.added || []).includes(jira));
  if (g && !stillRoster) RAW_GAMES = RAW_GAMES.filter(x => x.jira !== jira);
  saveOrder(); applyForecast(); renderAxis(); renderRows(); renderKPI(); renderDrawer();
  const hdr = document.getElementById('hdrCount'); if (hdr) hdr.textContent = visibleGames().length;
  if (!RAW_GAMES.length) {   // last item removed — restore the empty state (#70)
    document.getElementById('emptyState').style.display = 'block';
    document.getElementById('roadmapView').style.display = 'none';
    const fb = document.getElementById('filterBar'); if (fb) fb.style.display = 'none';
  }
}

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
  <div class="gp-share-banner" id="gpShareBanner" style="display:none"></div>
  <div class="gp-promote-banner" id="gpPromoteBanner" style="display:none"></div>
  <div class="kpi-strip" id="kpiStrip"></div>
  <div class="filter-bar" id="filterBar">
    <div class="fb-group" id="fbStatusGroup"><span class="fb-label">STATUS</span></div>
    <div class="fb-group" id="fbStageGroup"><span class="fb-label">STAGE</span></div>
    <div class="fb-spacer"></div>
    <input class="fb-search" id="fbSearch" placeholder="🔍 Search games…">
    <button class="fb-chip fc-toggle" id="fcToggle" title="Project a hypothetical timeline from remaining hours ÷ velocity">🔮 Forecast</button>
    <div class="view-toggle" id="viewToggle">
      <button class="on" data-view="gantt">Gantt</button>
      <button data-view="roadmap">Roadmap</button>
      <button data-view="heatmap">Heatmap</button>
      <button data-view="list">List</button>
    </div>
    <button class="fb-chip" id="exportBtn" title="Export the games table (respects current filters)">⬇ Export</button>
  </div>
  <div id="emptyState" class="empty-state" style="display:none">
    ${PROJECT.key === 'igf'
      ? `<h2>No features on the board yet</h2>
         <p>Open <b>✎ Edit Plan</b> and use the <b>search box</b> to add iGaming features to this board.<br>
         <span style="color:var(--sub)">Sign in first to save the board for everyone.</span></p>`
      : `<h2>No data yet</h2>
         <p>No games to show for ${PROJECT.jira_project}. Run the Jira builder:<br>
         <code>python build_jira_data.py --project ${PROJECT.key}</code><br>then reload.</p>`}
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
  // Re-entrant: clear existing chips (keep the .fb-label) so this can be re-run
  // live after a status edit in Plan Mode (#51).
  statusGroup.querySelectorAll('.fb-chip').forEach(c => c.remove());
  stageGroup.querySelectorAll('.fb-chip').forEach(c => c.remove());
  let curStatus = (activeFilters && activeFilters.status) || 'ALL';
  const curStage = (activeFilters && activeFilters.stage) || 'ALL';
  // If the active status was renamed/removed, fall back to ALL.
  if (curStatus !== 'ALL' && !CONFIG.statuses.some(s => s.key === curStatus)) {
    curStatus = 'ALL'; if (activeFilters) activeFilters.status = 'ALL';
  }
  // STATUS chips mirror the Plan Mode "Workflow Statuses" config, not a hard-coded
  // list, so the top filter always reflects the configured statuses (#51).
  ['ALL', ...CONFIG.statuses.map(s => s.key)].forEach(k => {
    const c = document.createElement('span');
    c.className = 'fb-chip' + (k === curStatus ? ' on' : '');
    c.dataset.filterStatus = k; c.textContent = k === 'ALL' ? 'All' : k;
    statusGroup.appendChild(c);
  });
  ['ALL', 'art', 'design', 'math', 'dev', 'sound', 'review', 'qa'].forEach(k => {
    const c = document.createElement('span');
    c.className = 'fb-chip discipline' + (k === curStage ? ' on' : '');
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
  const eb = document.getElementById('exportBtn'); if (eb) eb.onclick = openExportModal;
}

// ============================================================
//  CSV EXPORT (#62) — Default (all columns) or a custom column pick.
//  Respects the current Status / Stage / search filters. Pure client-side.
// ============================================================
function filteredGames() {
  return visibleGames().filter(g => {
    if (activeFilters.status !== 'ALL' && g.workflow_status !== activeFilters.status) return false;
    if (activeFilters.stage !== 'ALL' && g.current_stage !== activeFilters.stage) return false;
    if (activeFilters.search && !g.name.toLowerCase().includes(activeFilters.search.toLowerCase())) return false;
    return true;
  });
}
function exportColumns() {
  const disc = (g, k) => (g.disciplines || []).find(x => x.key === k) || null;
  const cap = s => s.charAt(0).toUpperCase() + s.slice(1);
  const cols = [
    { key: 'idx',       label: '#',            get: (g, i) => i + 1 },
    { key: 'name',      label: 'Game',         get: g => g.name },
    { key: 'jira',      label: 'Jira',         get: g => g.jira || '' },
    { key: 'status',    label: 'Status',       get: g => g.workflow_status || '' },
    { key: 'stage',     label: 'Stage',        get: g => stageLabel(g.current_stage) },
    { key: 'lead',      label: 'Lead Dev',     get: g => g.dev_name || '' },
    { key: 'priority',  label: 'Priority',     get: g => g.priority || '' },
    { key: 'fv',        label: 'Fix Version',  get: g => (g.fixVersions || []).map(v => v.name).join('; ') },
    { key: 'target',    label: 'Target',       get: g => g.target_date || '' },
    { key: 'spent',     label: 'Spent (h)',    get: g => Math.round(g.spent) },
    { key: 'scope',     label: 'Scope (h)',    get: g => Math.round(g.scope != null ? g.scope : (g.spent + (g.remaining || 0))) },
    { key: 'remaining', label: 'Remaining (h)',get: g => Math.round(g.remaining || 0) },
    { key: 'pctdone',   label: '% Done',       get: g => { const sc = g.scope || (g.spent + (g.remaining || 0)); return sc > 0 ? Math.round(g.spent / sc * 100) : 0; } },
  ];
  LANE_ORDER.forEach(k => {
    cols.push({ key: k + '_spent', label: `${cap(k)} spent (h)`, get: g => { const d = disc(g, k); return d ? Math.round(d.spent) : 0; } });
    cols.push({ key: k + '_scope', label: `${cap(k)} scope (h)`, get: g => { const d = disc(g, k); return d ? Math.round(d.scope) : 0; } });
  });
  return cols;
}
function csvCell(v) { v = v == null ? '' : String(v); return /[",\n\r]/.test(v) ? '"' + v.replace(/"/g, '""') + '"' : v; }
function downloadCSV(name, text) {
  const blob = new Blob(['﻿' + text], { type: 'text/csv;charset=utf-8' });   // BOM so Excel reads UTF-8
  const a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = name;
  document.body.appendChild(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(a.href), 1500);
}
function runExport(colKeys) {
  const all = exportColumns();
  const cols = colKeys && colKeys.length ? all.filter(c => colKeys.includes(c.key)) : all;
  const games = filteredGames();
  const head = cols.map(c => csvCell(c.label)).join(',');
  const body = games.map((g, i) => cols.map(c => csvCell(c.get(g, i))).join(',')).join('\r\n');
  const stamp = (REFRESHED || '').slice(0, 10) || new Date().toISOString().slice(0, 10);
  downloadCSV(`game-pipeline-${PROJECT.key}-${stamp}.csv`, head + '\r\n' + body);
  showToast(`Exported ${games.length} game${games.length === 1 ? '' : 's'} to CSV`);
}
function openExportModal() {
  const n = filteredGames().length;
  const checks = exportColumns().map(c =>
    `<label class="exp-col"><input type="checkbox" data-col="${c.key}" checked> ${c.label}</label>`).join('');
  openModal(`<h3>⬇ Export games</h3>
    <p class="gp-modal-note">Exports the <b>${n}</b> game${n === 1 ? '' : 's'} currently shown — your Status / Stage / search filters apply. CSV opens in Excel &amp; Google Sheets.</p>
    <div class="gp-modal-foot"><button class="gp-foot-btn primary" id="expDefault">Default — all columns</button><button class="gp-foot-btn" id="expChoose">Choose columns…</button><button class="gp-foot-btn" id="expCancel">Cancel</button></div>
    <div id="expCustom" style="display:none;margin-top:14px">
      <div class="exp-cols">${checks}</div>
      <div class="gp-modal-foot"><button class="gp-foot-btn" id="expNone">Clear all</button><button class="gp-foot-btn primary" id="expGo">Export selected</button></div>
    </div>`);
  document.getElementById('expCancel').onclick = closeModal;
  document.getElementById('expDefault').onclick = () => { runExport(null); closeModal(); };
  document.getElementById('expChoose').onclick = () => { document.getElementById('expCustom').style.display = 'block'; };
  document.getElementById('expNone').onclick = () => document.querySelectorAll('#expCustom input[data-col]').forEach(c => { c.checked = false; });
  document.getElementById('expGo').onclick = () => {
    const keys = [...document.querySelectorAll('#expCustom input[data-col]:checked')].map(c => c.dataset.col);
    if (!keys.length) { showToast('Pick at least one column'); return; }
    runExport(keys); closeModal();
  };
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
// On landing, scroll the synced tracks so TODAY sits mid-viewport (#69) — the
// pipeline is "now"-centric, so don't make people scroll to find today. Runs
// once per mount (after layout); user scrolling afterwards is preserved.
function centerToday() {
  if (!(TODAY >= CHART_START && TODAY <= CHART_END)) return;
  requestAnimationFrame(() => {
    const sc = document.getElementById('axisTrack') || document.querySelector('.tl-scroll');
    if (!sc || !sc.clientWidth) return;
    const todayPx = pct(TODAY) / 100 * trackPxWidth();
    const target = Math.max(0, Math.min(todayPx - sc.clientWidth / 2, sc.scrollWidth - sc.clientWidth));
    tlScrollLeft = target;
    document.querySelectorAll('.tl-scroll').forEach(o => { o.scrollLeft = target; });
  });
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
  // Weekly calendar ticks (#67): a small day-of-month marker at each week start,
  // under the month band, so the axis reads as a calendar down to the week.
  chartWeeks().forEach(w => {
    const wk = document.createElement('div');
    wk.className = 'ax-week'; wk.style.left = pct(w) + '%'; wk.textContent = w.getDate();
    track.appendChild(wk);
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
      ${(g.history && g.history.target && g.history.target.length) ? `<span class="hist-badge" title="Completion date moved ${g.history.target.length}× — click for the history &amp; why">📅 ${g.history.target.length}×</span>` : ''}
    </div>${fvRow(g)}${sizeRow}`;

  const track = document.createElement('div'); track.className = 'epic-track tl-scroll';
  const trackInner = document.createElement('div'); trackInner.className = 'tl-inner'; trackInner.style.width = trackPxWidth() + 'px';
  track.appendChild(trackInner);
  // Weekly gridlines (#67) — drawn first so the darker sprint lines sit on top.
  chartWeeks().forEach(w => { const l = document.createElement('div'); l.className = 'wk-line'; l.style.left = pct(w) + '%'; trackInner.appendChild(l); });
  (ALL_SPRINTS || SPRINT_LIST).forEach(s => { const l = document.createElement('div'); l.className = 'sp-line' + (s.projected ? ' proj' : ''); l.style.left = pct(s.start) + '%'; trackInner.appendChild(l); });
  if (TODAY >= CHART_START && TODAY <= CHART_END) { const tl = document.createElement('div'); tl.className = 'today-line-row'; tl.style.left = pct(TODAY) + '%'; tl.dataset.tip = `<b>📍 Today</b><div class="t-sub">${fmtD(TODAY)}</div>`; trackInner.appendChild(tl); }
  const proj = (showForecast && g._proj) ? g._proj : null;
  let laneTop = 8;
  if (currentView === 'gantt') {
    // Concise Gantt (#65): ONE consolidated bar per game + a milestone diamond per
    // team (filled = done ✓, hollow = open). Detail lives in the TIMELINE tab.
    row.classList.add('gantt-row');
    const starts = [], ends = [];
    LANE_ORDER.forEach(k => { const d = g.disciplines ? g.disciplines.find(x => x.key === k) : null; discSprints(d).forEach(s => { starts.push(+new Date(s.start)); ends.push(+sprintEnd(s)); }); });
    const barStart = starts.length ? new Date(Math.min(...starts)) : CHART_START;
    const cands = []; if (ends.length) cands.push(Math.max(...ends));
    if (proj && proj.ship) cands.push(+new Date(proj.ship.start));
    if (g.target_date) cands.push(+asDate(g.target_date));
    const barEnd = cands.length ? new Date(Math.max(...cands)) : barStart;
    const gl = pct(barStart), gw = Math.max(pct(barEnd) - gl, 0.8);
    const gsc = (g.scope != null) ? g.scope : g.est;
    const fillPct = gsc > 0 ? Math.min(100, Math.round(g.spent / gsc * 100)) : 0;
    const bar = document.createElement('div'); bar.className = 'gantt-bar';
    bar.style.left = gl + '%'; bar.style.width = gw + '%';   // beige fill via --gantt-fill (stage-agnostic)
    bar.innerHTML = `<div class="gantt-fill" style="width:${fillPct}%"></div>`;
    bar.dataset.tip = `<b>${g.name}</b><div class="t-sub">${fmtD(barStart)} → ${fmtD(barEnd)} · ${fillPct}% done</div>`;
    trackInner.appendChild(bar);
    LANE_ORDER.forEach(k => {
      const d = g.disciplines ? g.disciplines.find(x => x.key === k) : null; if (!d) return;
      const sps = discSprints(d);
      const md = d.target_date ? asDate(d.target_date) : (sps.length ? sprintEnd(sps[sps.length - 1]) : null);
      if (!md) return;
      const doneD = d.phase === 'done';
      const ms = document.createElement('div'); ms.className = 'gantt-ms' + (doneD ? ' done' : '');
      ms.style.left = pct(md) + '%'; ms.style.setProperty('--mc', DEPT_COLORS[k] || 'var(--muted)');
      ms.dataset.tip = `<b>${k.toUpperCase()}${doneD ? ' ✓ done' : ''}</b><div class="t-sub">${Math.round(d.spent || 0)} / ${Math.round(d.scope || 0)}h · due ${fmtD(md)}</div>`;
      trackInner.appendChild(ms);
    });
  } else
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
      chip.dataset.tip = `<b>${dKey.toUpperCase()} · ${s.label}</b><div class="t-sub">${fmtRange(s.start, end)}${dashed ? ' · projected' : ''}</div>${dashed ? '' : peopleTip(disc)}`;
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
  if (currentView !== 'gantt' && laneTop === 8) { const n = document.createElement('div'); n.style.cssText = 'font-size:9px;color:var(--sub);font-style:italic;padding-top:6px'; n.textContent = 'No scheduled sprints yet'; trackInner.appendChild(n); }
  registerScroller(track);

  const hrs = document.createElement('div'); hrs.className = 'epic-hrs';
  // Scope = spent + remaining (Jira Remaining Estimate). Falls back to est
  // for pre-fix data payloads. "Over" is only meaningful vs the original
  // planning estimate — kept for the ⚠ over-budget indicator only.
  const gScope = (g.scope != null) ? g.scope : g.est;
  const gRem   = (g.remaining != null) ? g.remaining : Math.max(0, g.est - g.spent);
  const over   = g.spent > g.est && g.est > 0;
  const progressPct  = gScope > 0 ? Math.min(100, Math.round(g.spent / gScope * 100)) : 0;
  const progressColor = over ? '#dc2626' : (progressPct >= 70 ? gameColor(g) : '#60a5fa');
  const dl = (g.target_date && proj && proj.ship) ? targetDelta(g.target_date, proj.ship.start) : null;
  hrs.innerHTML = `
    <div class="epic-hrs-v ${over ? 'over' : ''}">${Math.round(g.spent)}h</div>
    <div class="epic-hrs-l" title="Scope = spent + remaining (from Jira Remaining Estimate). Original est: ${Math.round(g.est)}h">SPENT / ${Math.round(gScope)}h scope</div>
    <div class="epic-prog"><div class="epic-prog-fill" style="width:${progressPct}%;background:${progressColor}"></div></div>
    <div class="epic-hrs-l" style="color:${over ? '#dc2626' : 'var(--sub)'};margin-top:4px">${over ? `⚠ +${Math.round(g.spent - g.est)}h over est` : `${progressPct}% done · ${Math.round(gRem)}h to go`}</div>
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
    // A HISTORY badge click always opens the panel on the HISTORY tab (never toggles it shut).
    if (e.target.closest('.hist-badge')) { openPanel = g.name; openPanelTab = 'history'; renderRows(); return; }
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
  // Tab order: NOTES first (default active on expand) — gives editors a
  // place to keep stakeholder-facing context that doesn't fit in Jira fields.
  tabs.innerHTML =
    `<div class="detail-tab active" data-tab="notes">📝 NOTES</div>` +
    `<div class="detail-tab" data-tab="timeline">TIMELINE</div>` +
    `<div class="detail-tab" data-tab="milestones">SPRINTS</div>` +
    `<div class="detail-tab" data-tab="hours">HOURS</div>` +
    `<div class="detail-tab" data-tab="history">📅 HISTORY</div>`;
  panel.appendChild(tabs);
  const body = document.createElement('div'); body.className = 'detail-body';

  // ── NOTES pane ──
  // Default-visible. Most-recent-first list of timestamped notes + an
  // append-only entry box. Each entry stamps `getGhUser()` (anon if no PAT).
  // Ctrl/Cmd+Enter on the textarea OR the "+ Add note" button commits.
  const notesPane = document.createElement('div'); notesPane.className = 'notes-pane';
  const renderNotesList = () => {
    const list = notesFor(g).filter(n => !n.deleted);   // hide tombstoned notes (#57)
    const canDelete = getGhEditor();                    // only signed-in editors can delete
    const items = list.map(n => {
      const ts = n.ts ? new Date(n.ts) : null;
      const stamp = ts ? `${ts.toISOString().slice(0,10)} ${ts.toISOString().slice(11,16)} UTC` : '';
      const author = n.author || 'anon';
      const body = String(n.body || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/\n/g,'<br>');
      const del = canDelete ? `<button class="note-del" data-id="${encodeURIComponent(noteId(n))}" title="Delete this note">🗑</button>` : '';
      return `<div class="note-card"><div class="note-meta"><span class="note-date">${stamp}</span> · <span class="note-author">${author}</span></div><div class="note-body">${body}</div>${del}</div>`;
    }).join('') || `<div class="note-empty">No notes yet for this game. Add one below to log context for stakeholders.</div>`;
    notesList.innerHTML = items;
    notesList.querySelectorAll('.note-del').forEach(btn => btn.addEventListener('click', e => {
      e.stopPropagation();
      if (!confirm('Delete this note? It will be removed for everyone once you Save as default.')) return;
      deleteNote(g, decodeURIComponent(btn.dataset.id));
      renderNotesList();
      showToast('Note removed — Save as default to publish');
    }));
  };
  const notesEditor = document.createElement('div'); notesEditor.className = 'note-editor';
  notesEditor.innerHTML =
    `<textarea class="note-input" placeholder="Add a note for ${g.name}…  (Ctrl/⌘+Enter to save)"></textarea>` +
    `<div class="note-editor-row">` +
    `  <span class="note-hint">${getGhUser() ? '✓ ' + getGhUser() : 'not signed in — note will be stamped "anon"'}</span>` +
    `  <button class="gsb-btn primary note-add-btn">+ Add note</button>` +
    `</div>`;
  const notesList = document.createElement('div'); notesList.className = 'notes-list';
  notesPane.appendChild(notesEditor);
  notesPane.appendChild(notesList);
  renderNotesList();
  const inputEl = notesEditor.querySelector('.note-input');
  const commitNote = () => {
    if (addNote(g, inputEl.value)) {
      inputEl.value = '';
      renderNotesList();
      showToast('Note added — Save as default to publish');
    }
  };
  notesEditor.querySelector('.note-add-btn').addEventListener('click', e => { e.stopPropagation(); commitNote(); });
  inputEl.addEventListener('keydown', e => {
    if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') { e.preventDefault(); commitNote(); }
    e.stopPropagation();
  });
  inputEl.addEventListener('click', e => e.stopPropagation());
  body.appendChild(notesPane);

  // ── HISTORY pane (#66) ── completion-date moves + scope changes, each with a
  // "why" (producer reason > linked note > inferred). ✎ edits persist via the
  // shared-plan write-back like notes.
  const histPane = document.createElement('div'); histPane.className = 'hist-pane'; histPane.style.display = 'none';
  const renderHist = () => {
    histPane.innerHTML = historyHtml(g);
    histPane.querySelectorAll('.why-edit').forEach(btn => btn.addEventListener('click', e => {
      e.stopPropagation();
      const moveDate = btn.dataset.move;
      const cur = reasonFor(g.jira, moveDate);
      const row = btn.closest('.hist-row');
      if (row.querySelector('.why-editor')) return;
      const editor = document.createElement('div'); editor.className = 'why-editor';
      editor.innerHTML = `<input class="why-input" type="text" maxlength="240" placeholder="Why did the date move?" value="${cur ? esc(cur.text).replace(/"/g, '&quot;') : ''}"><button class="gsb-btn primary why-save">Save</button><button class="gsb-btn why-cancel">Cancel</button>`;
      row.appendChild(editor);
      const inp = editor.querySelector('.why-input'); inp.focus();
      inp.addEventListener('click', e2 => e2.stopPropagation());
      inp.addEventListener('keydown', e2 => {
        e2.stopPropagation();
        if (e2.key === 'Enter') editor.querySelector('.why-save').click();
        if (e2.key === 'Escape') editor.querySelector('.why-cancel').click();
      });
      editor.querySelector('.why-save').addEventListener('click', e2 => {
        e2.stopPropagation(); setMoveReason(g.jira, moveDate, inp.value); renderHist();
        showToast('Reason saved — Save as default to publish');
      });
      editor.querySelector('.why-cancel').addEventListener('click', e2 => { e2.stopPropagation(); renderHist(); });
    }));
  };
  renderHist();
  body.appendChild(histPane);

  const tlPane = document.createElement('div'); tlPane.style.display = 'none';
  // Activity "pulse" (#60/#61): each lane is a fixed-scale, horizontally-scrollable
  // rail that shares the main chart's axis (pct/trackPxWidth) and scrolls in SYNC
  // with the burndown above (tl-scroll), so months + today line up and long spans
  // scroll instead of squishing. Rounded pills mark the weeks work was logged (gaps
  // = paused); per-department colour on pill AND name; work logged before the chart
  // window is summed into a "◀ earlier" note (option A). Targets hidden on done lanes.
  const MSWK = 7 * 864e5;
  const laneCol = k => DEPT_COLORS[k] || 'var(--muted)';
  const innerW = trackPxWidth();
  const sprLines = (ALL_SPRINTS || SPRINT_LIST).map(s => `<div class="sp-line${s.projected ? ' proj' : ''}" style="left:${pct(s.start)}%"></div>`).join('');
  const todayLine = (TODAY >= CHART_START && TODAY <= CHART_END) ? `<div class="today-line-row" style="left:${pct(TODAY)}%"></div>` : '';

  // Month band header — carries the visible scrollbar for the pulse.
  const mh = document.createElement('div'); mh.className = 'disc-row disc-month-head tl-lane';
  let mhTrack = '';
  chartMonths().forEach(m => { const l = pct(m); mhTrack += `<div class="dmh-div" style="left:${l}%"></div><div class="dmh-lab" style="left:${l}%">${monthLabel(m)}</div>`; });
  mh.innerHTML = `<div class="disc-label"></div><div class="disc-track tl-scroll"><div class="tl-in" style="width:${innerW}px">${mhTrack}</div></div><div class="disc-hrs"></div>`;
  tlPane.appendChild(mh);
  registerScroller(mh.querySelector('.tl-scroll'));

  // Pills for weeks inside the chart window; hours before it are summed as "earlier".
  const pulseOf = (disc) => {
    const weeks = disc.weeks || {}, keys = Object.keys(weeks).sort();
    let earlier = 0; const ir = [];
    keys.forEach(k => { if (asDate(k) < CHART_START) earlier += weeks[k]; else ir.push(k); });
    const col = laneCol(disc.key); let html = '';
    if (ir.length) {
      const runs = []; let a = ir[0], p = ir[0];
      for (let i = 1; i < ir.length; i++) { if (Math.round((asDate(ir[i]) - asDate(p)) / 864e5) === 7) p = ir[i]; else { runs.push([a, p]); a = ir[i]; p = ir[i]; } }
      runs.push([a, p]);
      html = runs.map(([x1, x2]) => {
        const l = pct(x1), end = new Date(+asDate(x2) + MSWK), w = Math.max(pct(end) - l, 0.6);
        const hrs = ir.filter(k => k >= x1 && k <= x2).reduce((s, k) => s + weeks[k], 0);
        return `<div class="tl-pill" style="left:${l}%;width:${w}%;background:${col}" data-tip="<b>${disc.key.toUpperCase()}</b><div class='t-sub'>${fmtD(x1)} → ${fmtD(end)} · ${Math.round(hrs)}h logged</div>"></div>`;
      }).join('');
    }
    return { html, earlier: Math.round(earlier), any: keys.length > 0 };
  };

  LANE_ORDER.forEach(dKey => {
    const disc = g.disciplines ? g.disciplines.find(d => d.key === dKey) : null;
    if (!disc) return;
    const r = document.createElement('div'); r.className = 'disc-row tl-lane';
    const p = pulseOf(disc), done = disc.phase === 'done';
    const tgtTick = (disc.target_date && !done) ? `<div class="disc-target" style="left:${pct(disc.target_date)}%" data-tip="<b>🎯 ${dKey.toUpperCase()} target</b><div class='t-sub'>${fmtD(disc.target_date)}</div>"></div>` : '';
    // Remaining-work ghost (#63): for an unfinished lane with hours left, a dashed
    // segment projects the leftover (scope − spent) from the last activity (or today)
    // to the team's due date — "~Xh left". Done lanes get none.
    const rem = Math.round((disc.scope || 0) - (disc.spent || 0));
    let ghost = '';
    if (!done && rem > 0 && disc.target_date) {
      const wk = Object.keys(disc.weeks || {}).sort();
      const lastEnd = wk.length ? new Date(+asDate(wk[wk.length - 1]) + MSWK) : TODAY;
      const gStart = lastEnd > TODAY ? lastEnd : TODAY;
      const l = pct(gStart), rr = pct(disc.target_date);
      if (rr > l) ghost = `<div class="tl-ghost" style="left:${l}%;width:${Math.max(rr - l, 0.6)}%;--gc:${laneCol(dKey)}" data-tip="<b>~${rem}h remaining</b><div class='t-sub'>${dKey.toUpperCase()} · to ${fmtD(disc.target_date)}</div>"></div>`;
    }
    const empty = p.any ? '' : `<span class="tl-empty">no work logged yet</span>`;
    const inner = `<div class="tl-in tl-lane-in" style="width:${innerW}px"><div class="tl-rail"></div>${sprLines}${todayLine}${ghost}${p.html}${empty}${tgtTick}</div>`;
    const over = disc.spent > disc.scope && disc.scope > 0;
    // Completed signifier (#63): a green ✓ on the numbers when every ticket is closed.
    const hrsMark = done ? '<span class="disc-done" title="Completed — all tickets closed">✓</span>' : (over ? '<span class="over">⚠</span>' : '');
    // Consolidated to ≤3 short lines so they don't overlap in the row (#66):
    //   spent/scope ✓   |   "~Xh left · 🎯 date" (or "✓ done")   |   "◀ Nh earlier"
    const sub = done
      ? `<div class="disc-tgt done">done</div>`
      : `<div class="disc-tgt">${rem > 0 ? `~${rem}h left` : ''}${(rem > 0 && disc.target_date) ? ' · ' : ''}${disc.target_date ? `🎯 ${fmtD(disc.target_date)}` : ''}</div>`;
    const earlierTxt = p.earlier > 0 ? `<div class="disc-earlier" title="Logged before the chart window">◀ ${p.earlier}h earlier</div>` : '';
    r.innerHTML = `<div class="disc-label" style="color:${laneCol(dKey)}">${dKey}</div>`
      + `<div class="disc-track tl-scroll tl-nobar">${inner}</div>`
      + `<div class="disc-hrs"><div>${Math.round(disc.spent)} / ${Math.round(disc.scope)}h ${hrsMark}</div>${sub}${earlierTxt}</div>`;
    tlPane.appendChild(r);
    registerScroller(r.querySelector('.tl-scroll'));
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
  // HOURS panel uses SCOPE (spent + remaining) as the denominator now, so
  // "100%" = discipline done. "Over" indicator still fires vs the ORIGINAL
  // planning estimate — that's the meaningful "budget exceeded" signal.
  const hoursRows = (g.disciplines || []).map(d => {
    const scope = (d.scope != null) ? d.scope : d.est;
    const remaining = (d.remaining != null) ? d.remaining : Math.max(0, d.est - d.spent);
    const ratio = scope > 0 ? d.spent / scope : 0;
    const pctNum = Math.min(100, Math.round(ratio * 100));
    const done = d.phase === 'done';
    const overOrig = d.est > 0 && d.spent > d.est;
    const overWarn = overOrig ? `<span class="over" title="Spent ${Math.round(d.spent - d.est)}h more than the original ${Math.round(d.est)}h estimate">⚠</span>` : '';
    const remChip = (!done && remaining > 0) ? ` · ${Math.round(remaining)}h left` : '';
    // Completed signifier (#63): green ✓ when all tickets are closed.
    const mark = done ? '<span class="disc-done" title="Completed — all tickets closed">✓</span>' : overWarn;
    const row = `<div class="disc-row"><div class="disc-label">${d.key}</div><div class="disc-track"><div class="disc-bar" style="left:0;width:${pctNum}%;background:${done ? '#16a34a' : (overOrig ? '#dc2626' : '#2563eb')};color:#fff">${pctNum}%</div></div><div class="disc-hrs" title="Scope = spent + Jira Remaining Estimate. Original est: ${Math.round(d.est)}h">${Math.round(d.spent)} / ${Math.round(scope)}h${remChip} ${mark}</div></div>`;
    return `<div class="disc-block">${row}${peopleChips(d)}</div>`;
  }).join('');
  hrsPane.innerHTML = hoursRows || '<div style="font-size:11px;color:var(--sub);font-style:italic;padding:10px">No hours data.</div>';
  body.appendChild(hrsPane);
  panel.appendChild(body);

  tabs.querySelectorAll('.detail-tab').forEach(t => t.addEventListener('click', e => {
    e.stopPropagation();
    tabs.querySelectorAll('.detail-tab').forEach(x => x.classList.remove('active'));
    t.classList.add('active');
    notesPane.style.display = t.dataset.tab === 'notes' ? 'block' : 'none';
    tlPane.style.display = t.dataset.tab === 'timeline' ? 'block' : 'none';
    msPane.style.display = t.dataset.tab === 'milestones' ? 'block' : 'none';
    hrsPane.style.display = t.dataset.tab === 'hours' ? 'block' : 'none';
    histPane.style.display = t.dataset.tab === 'history' ? 'block' : 'none';
    if (t.dataset.tab === 'history') renderHist();
    // The pulse tracks can't take a scroll position while hidden; on reveal, snap
    // them to the shared scroll offset so they line up with the chart above (#61).
    if (t.dataset.tab === 'timeline') requestAnimationFrame(() => tlPane.querySelectorAll('.tl-scroll').forEach(o => { o.scrollLeft = tlScrollLeft; }));
  }));
  // A HISTORY-badge click asked to open straight to that tab (#66).
  if (openPanelTab) {
    const tb = tabs.querySelector(`.detail-tab[data-tab="${openPanelTab}"]`);
    openPanelTab = null;
    if (tb) tb.click();
  }
  return panel;
}

function renderRows() {
  const rowsEl = document.getElementById('rows');
  rowsEl.innerHTML = '';
  const filtered = filteredGames();
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
    { key: 'sound', icon: '🎵', name: 'Sound' }, { key: 'review', icon: '🔎', name: 'Review' }, { key: 'qa', icon: '🧪', name: 'QA' },
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
  const so = document.getElementById('gpSignOut'); if (so) so.onclick = () => { setPat(''); showToast('Signed out'); renderDrawer(); renderPromoteBanner(); };
}

function renderDrawerGames(body) {
  const statusOptions = g => CONFIG.statuses.map(s => `<option value="${s.key}"${s.key === g.workflow_status ? ' selected' : ''}>${s.key}</option>`).join('');
  const sizeOpts = ['—', ...CONFIG.sizes.map(s => s.key)];
  const sizeSel = (g, k) => `<label>${k.toUpperCase()}<select data-size-game="${g.name}" data-disc="${k}">` +
    sizeOpts.map(o => `<option value="${o === '—' ? '' : o}"${(gameSizes(g)[k] || '') === (o === '—' ? '' : o) ? ' selected' : ''}>${o}</option>`).join('') + `</select></label>`;
  const stageOptions = g => CONFIG.stages.map(s => `<option value="${s.key}"${s.key === g.current_stage ? ' selected' : ''}>${s.label}</option>`).join('');
  const list = RAW_GAMES.map((g, idx) => {
    const on = !HIDDEN.has(g.name), open = drawerOpenRows.has(g.name);
    const src   = g._status_source;
    const srcSt = g._stage_source;
    const added = USER_ADDED.includes(g.jira);
    // Mark next to each dropdown: 📌 shared default, ✎ local override. Kept
    // separate for status and stage so an editor sees which one they've
    // touched independently.
    const statusMark = src === 'shared' ? ' <span class="pin-mark" title="shared default (auto: ' + g._auto_status + ')">📌</span>'
      : (src === 'local' ? ' <span class="ovr-mark" title="local override (auto: ' + g._auto_status + ')">✎</span>' : '');
    const stageMark  = srcSt === 'shared' ? ' <span class="pin-mark" title="shared default (auto: ' + stageLabel(g._auto_stage) + ')">📌</span>'
      : (srcSt === 'local' ? ' <span class="ovr-mark" title="local override (auto: ' + stageLabel(g._auto_stage) + ')">✎</span>' : '');
    const revertNeeded = src !== 'auto' || srcSt !== 'auto';
    return `<div class="gpg${on ? '' : ' off'}${open ? ' open' : ''}" data-idx="${idx}" draggable="true">
      <div class="gpg-head">
        <span class="gpg-handle" title="Drag to reorder">⠿</span>
        <button class="gpg-toggle${on ? ' on' : ''}" data-hide="${g.name}" title="${on ? 'Visible — click to hide' : 'Hidden — click to show'}"></button>
        <span class="gpg-name">${g.name}${g.jira ? `<span class="k">${g.jira}</span>` : ''}${added ? '<span class="gpg-added" title="Added on the board">+added</span>' : ''}</span>
        <span class="gpg-stage" title="Lifecycle stage — the discipline the game is most-downstream in"><select data-stage-game="${g.name}">${stageOptions(g)}</select>${stageMark}</span>
        <span class="gpg-status"><select data-status-game="${g.name}">${statusOptions(g)}</select>${statusMark}</span>
        ${added ? `<button class="gpg-remove" data-remove="${g.jira}" title="Remove from board">✕</button>` : ''}
        <span class="gpg-chev" data-expand="${g.name}">▾</span>
      </div>
      <div class="gpg-expand">
        <div class="gpg-sizes">${sizeSel(g, 'art')}${sizeSel(g, 'math')}${sizeSel(g, 'dev')}${sizeSel(g, 'sound')}</div>
        <div style="margin-top:8px;font-size:10px;color:var(--sub)">Jira epic: ${g.epic_status || '—'}${revertNeeded ? ` · <button class="revert-auto" data-revert="${g.name}" title="Clear local + shared overrides on BOTH status and stage; restore Jira-derived values">↺ revert to auto (status: ${g._auto_status} · stage: ${stageLabel(g._auto_stage)})</button>` : ''}</div>
      </div>
    </div>`;
  }).join('');
  body.innerHTML = `<div class="gpg-add">
      <input type="text" id="gpgAddSearch" placeholder="➕ Add game — search Jira epics by name or key…" autocomplete="off" spellcheck="false">
      <div class="gpg-add-results" id="gpgAddResults"></div>
    </div>${list}`;

  // "+ Add game" — type-to-search over off-roster epics by name or ticket (#49).
  // No pre-populated dropdown: results appear only once you type.
  const addInput = document.getElementById('gpgAddSearch'), addRes = document.getElementById('gpgAddResults');
  const ADD_CAP = 20;
  function renderAddResults() {
    const q = (addInput.value || '').trim().toLowerCase();
    if (!q) { addRes.innerHTML = '<div class="gpg-add-empty">Type a game name or ticket number (e.g. IG-1513) to search.</div>'; return; }
    const inRoster = new Set(RAW_GAMES.map(g => g.jira));
    const all = ALL_GAMES.filter(g => !inRoster.has(g.jira) && (g.name + ' ' + g.jira).toLowerCase().includes(q));
    if (!all.length) { addRes.innerHTML = '<div class="gpg-add-empty">No matching epic (it may already be on the board).</div>'; return; }
    const cands = all.slice(0, ADD_CAP);
    addRes.innerHTML = cands.map(g => `<div class="gpg-add-item" data-add="${g.jira}"><span class="gpg-add-name">${g.name}</span><span class="k">${g.jira}</span><span class="gpg-add-st">${g.workflow_status || ''}</span></div>`).join('')
      + (all.length > cands.length ? `<div class="gpg-add-empty">+${all.length - cands.length} more — keep typing to narrow.</div>` : '');
    addRes.querySelectorAll('[data-add]').forEach(el => el.addEventListener('click', () => addGameToRoster(el.dataset.add)));
  }
  addInput.addEventListener('input', renderAddResults);
  addInput.addEventListener('focus', renderAddResults);
  body.querySelectorAll('.gpg-remove').forEach(b => b.addEventListener('click', e => { e.stopPropagation(); removeGameFromRoster(b.dataset.remove); }));

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
    if (sel.value === g._auto_status) { delete USER_STATUS[g.name]; g.workflow_status = g._auto_status; g._status_source = 'auto'; }
    else { USER_STATUS[g.name] = sel.value; g.workflow_status = sel.value; g._status_source = 'local'; }
    saveStatus(); renderRows(); renderKPI(); renderDrawer();
  }));
  // Stage dropdown mirrors the status handler: match auto → clear the local
  // override so it re-syncs with Jira; anything else → pin as a local edit
  // that will ship into the shared plan when the user saves.
  body.querySelectorAll('select[data-stage-game]').forEach(sel => sel.addEventListener('change', () => {
    const g = RAW_GAMES.find(x => x.name === sel.dataset.stageGame); if (!g) return;
    if (sel.value === g._auto_stage) { delete USER_STAGE[g.name]; g.current_stage = g._auto_stage; g._stage_source = 'auto'; }
    else { USER_STAGE[g.name] = sel.value; g.current_stage = sel.value; g._stage_source = 'local'; }
    saveStage(); renderRows(); renderKPI(); renderDrawer();
  }));
  body.querySelectorAll('select[data-size-game]').forEach(sel => sel.addEventListener('change', () => {
    const n = sel.dataset.sizeGame, d = sel.dataset.disc;
    USER_SIZES[n] = USER_SIZES[n] || {};
    if (sel.value) USER_SIZES[n][d] = sel.value; else delete USER_SIZES[n][d];
    saveSizes(); renderRows();
  }));
  body.querySelectorAll('.revert-auto').forEach(b => b.addEventListener('click', () => {
    const g = RAW_GAMES.find(x => x.name === b.dataset.revert); if (!g) return;
    delete USER_STATUS[g.name]; g.workflow_status = g._auto_status; g._status_source = 'auto';
    delete USER_STAGE[g.name];  g.current_stage  = g._auto_stage;  g._stage_source  = 'auto';
    saveStatus(); saveStage(); renderRows(); renderKPI(); renderDrawer();
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
    saveConfig(); if (type === 'status') buildFilterBar(); renderRows();
  }));
  body.querySelectorAll('.pc-item-x').forEach(x => x.addEventListener('click', () => {
    const i = +x.dataset.i, type = x.dataset.type;
    if (type === 'status') CONFIG.statuses.splice(i, 1);
    if (type === 'stage') CONFIG.stages.splice(i, 1);
    if (type === 'size') CONFIG.sizes.splice(i, 1);
    saveConfig(); if (type === 'status') buildFilterBar(); renderDrawer(); renderRows();
  }));
  const add = document.getElementById('pcAddStatus');
  if (add) add.onclick = () => { CONFIG.statuses.push({ key: 'New Status', cls: 's-notstart' }); saveConfig(); buildFilterBar(); renderDrawer(); renderRows(); };
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
  ['order', 'status', 'sizes', 'hidden', 'config', 'added', 'notes', 'reasons'].forEach(k => { try { localStorage.removeItem(LS + k); } catch (e) {} });
  showToast('↺ Local edits reset (shared defaults still apply)');
  mount(PROJECT.key, APP);   // rebuild from defaults
  drawerTab = 'games'; drawerOpenRows = new Set();
  openDrawer();
}

// --- New-shared-plan banner (#52): local edits mask the shared plan, so when a
// NEWER shared plan is published, offer viewers an opt-in "Apply".
function getSharedSeen() { try { return localStorage.getItem(LS + 'shared_seen') || ''; } catch (e) { return ''; } }
function setSharedSeen(v) { try { localStorage.setItem(LS + 'shared_seen', v || ''); } catch (e) {} }
function hasLocalEdits() {
  try {
    const o = localStorage.getItem(LS + 'order');
    const st = localStorage.getItem(LS + 'status');
    const sz = localStorage.getItem(LS + 'sizes');
    const ad = localStorage.getItem(LS + 'added');
    const nt = localStorage.getItem(LS + 'notes');
    return !!(o || (st && st !== '{}') || (sz && sz !== '{}') || (ad && ad !== '[]')
      || (nt && nt !== '{}')
      || localStorage.getItem(LS + 'hidden') || localStorage.getItem(LS + 'config'));
  } catch (e) { return false; }
}

// Notes persistence — staged in localStorage like other plan edits, then
// pushed to plan-<key>.json via publishPlan. Append-only from the UI; older
// entries stay readable as the history of decisions for stakeholders.
function saveNotes() {
  try { localStorage.setItem(LS + 'notes', JSON.stringify(NOTES || {})); } catch (e) {}
}
function loadNotesOverlay() {
  try {
    const raw = localStorage.getItem(LS + 'notes');
    if (!raw) return;
    const local = JSON.parse(raw);
    if (!local || typeof local !== 'object') return;
    // Merge the local overlay OVER shared per game (union) — NEVER replace (#55).
    // A stale overlay keyed by a game's old (pre-rename) name must not wipe the
    // shared notes for that game or any other; it only adds this browser's own
    // unpublished notes on top.
    NOTES = NOTES || {};
    Object.keys(local).forEach(k => { NOTES[k] = mergeNoteLists(NOTES[k], local[k]); });
  } catch (e) {}
}
// Notes are keyed by the game's STABLE Jira epic key (#53). They used to be
// keyed by the display name, so a Jira rename silently orphaned a game's notes
// (the dashboard looked them up under the new name and found nothing).
function noteKey(game) { return (game && game.jira) || (game && game.name) || game; }
// Old game display names that were renamed in Jira → their stable jira key. Jira
// data doesn't retain old names, so notes filed pre-rename can't be auto-mapped;
// this folds them onto the game instead of leaving them orphaned (#55).
const NOTE_KEY_ALIASES = { 'Gen2 Game: Steampunk Fortune': 'IG-1511' };
// Stable identity for a note (timestamp + author + body).
function noteId(n) { return (n && n.ts || '') + '|' + (n && n.author || '') + '|' + (n && n.body || ''); }
// Union two note lists, de-duped by identity, newest first (the order addNote
// maintains via unshift). Deletion is a tombstone: if the same note appears both
// deleted and not-deleted (a stale tab or main still carrying the live copy),
// the delete WINS, so a removed note can't be resurrected by the union (#57).
// Returns clones so a display-time merge never mutates the underlying NOTES.
function mergeNoteLists(a, b) {
  const byId = new Map();
  [...(a || []), ...(b || [])].forEach(n => {
    if (!n || !n.body) return;
    const id = noteId(n);
    const prev = byId.get(id);
    if (prev) { if (n.deleted) prev.deleted = true; }
    else byId.set(id, { ...n });
  });
  const out = [...byId.values()];
  out.sort((x, y) => String(y.ts || '').localeCompare(String(x.ts || '')));
  return out;
}
// Notes for a game: its jira-keyed list, plus any legacy list still filed under
// the current display name (belt-and-braces during the name→jira transition).
function notesFor(g) {
  const byJira = (g && g.jira && NOTES) ? NOTES[g.jira] : null;
  const byName = (g && g.name && NOTES) ? NOTES[g.name] : null;
  return mergeNoteLists(byJira, byName);
}
// Fold any legacy name-keyed note lists onto their stable jira key, so a past
// or future Jira rename can't hide a game's notes. Unknown keys are left as-is.
function migrateNotesToJira() {
  const byName = {}, jiraSet = new Set();
  (ALL_GAMES || []).forEach(g => { if (g.jira) { jiraSet.add(g.jira); if (g.name) byName[g.name] = g.jira; } });
  const out = {};
  Object.keys(NOTES || {}).forEach(k => {
    const key = jiraSet.has(k) ? k : (byName[k] || NOTE_KEY_ALIASES[k] || k);
    out[key] = mergeNoteLists(out[key], NOTES[k]);
  });
  NOTES = out;
}
function addNote(game, body) {
  body = String(body || '').trim();
  if (!body) return false;
  const key = noteKey(game);
  NOTES = NOTES || {};
  NOTES[key] = NOTES[key] || [];
  NOTES[key].unshift({
    ts: new Date().toISOString(),
    author: getGhUser() || 'anon',
    body,
  });
  saveNotes();
  return true;
}
// Soft-delete a note (#57): tombstone it (deleted:true) rather than removing it,
// so the append-only union-merge on publish/load can't resurrect it. Marks it
// under both the jira key and any legacy name key it might still live under.
function deleteNote(game, id) {
  NOTES = NOTES || {};
  [noteKey(game), game && game.name].forEach(k => {
    if (!k || !NOTES[k]) return;
    NOTES[k].forEach(n => { if (noteId(n) === id) n.deleted = true; });
  });
  saveNotes();
  return true;
}

// ── Date-move "why" reasons (#66) ──────────────────────────────────────────
// A producer can annotate WHY a completion date moved. Stored exactly like
// notes: keyed jira -> { <moveDate>: {text, author, ts} }, staged in
// localStorage, published into plan-<key>.json under `move_reasons`, and
// union-merged (newest ts wins) so a stale tab never clobbers another's reason.
function saveReasons() { try { localStorage.setItem(LS + 'reasons', JSON.stringify(REASONS || {})); } catch (e) {} }
function loadReasonsOverlay() {
  try {
    const raw = localStorage.getItem(LS + 'reasons'); if (!raw) return;
    const local = JSON.parse(raw); if (!local || typeof local !== 'object') return;
    REASONS = REASONS || {};
    Object.keys(local).forEach(k => { REASONS[k] = { ...(REASONS[k] || {}), ...local[k] }; });
  } catch (e) {}
}
function reasonFor(jira, moveDate) { const m = REASONS && REASONS[jira]; return (m && m[moveDate]) || null; }
function setMoveReason(jira, moveDate, text) {
  text = String(text || '').trim();
  REASONS = REASONS || {}; REASONS[jira] = REASONS[jira] || {};
  if (!text) delete REASONS[jira][moveDate];
  else REASONS[jira][moveDate] = { text, author: getGhUser() || 'anon', ts: new Date().toISOString() };
  saveReasons();
}
function mergeReasons(a, b) {
  const out = {};
  new Set([...Object.keys(a || {}), ...Object.keys(b || {})]).forEach(jira => {
    const am = (a || {})[jira] || {}, bm = (b || {})[jira] || {}, m = {};
    new Set([...Object.keys(am), ...Object.keys(bm)]).forEach(d => {
      const x = am[d], y = bm[d];
      m[d] = !x ? y : !y ? x : (String(y.ts || '') > String(x.ts || '') ? y : x);
    });
    out[jira] = m;
  });
  return out;
}
// Correlate a date-move to the nearest existing note within a window around when
// the move was detected (notes are often logged just before/after a re-plan).
function noteNearDate(g, moveDate) {
  const D = asDate(moveDate); if (!D) return null;
  const lo = +D - 30 * 864e5, hi = +D + 7 * 864e5;
  let best = null, bestDist = Infinity;
  notesFor(g).filter(n => !n.deleted && n.ts).forEach(n => {
    const t = +new Date(n.ts); if (t < lo || t > hi) return;
    const dist = Math.abs(t - +D);
    if (dist < bestDist) { bestDist = dist; best = n; }
  });
  return best;
}
// Resolve the "why" for a target move. Precedence (#66):
// producer pencil reason > correlated note > auto-derived (scope) > none.
function whyForMove(g, ev) {
  const r = reasonFor(g.jira, ev.date);
  if (r && r.text) return { kind: 'producer', text: r.text, author: r.author };
  const n = noteNearDate(g, ev.date);
  if (n && n.body) return { kind: 'note', text: n.body, author: n.author, ts: n.ts };
  const sc = ((g.history && g.history.scope) || []).find(
    s => s.delta > 0 && Math.abs(+asDate(s.date) - +asDate(ev.date)) <= 12 * 864e5);
  if (sc) return { kind: 'auto', text: `+${sc.delta}h scope added around this time` };
  return { kind: 'none', text: '' };
}
const esc = s => String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
// Build the HISTORY tab body for a game (date-move log + scope-change log).
function historyHtml(g) {
  const tEvents = ((g.history && g.history.target) || []).slice().reverse();  // newest first
  const sEvents = ((g.history && g.history.scope) || []).slice().reverse();
  if (!tEvents.length && !sEvents.length)
    return `<div class="hist-empty">No completion-date moves or scope changes recorded yet.<div class="hist-sub">History builds from the daily snapshots — this game's target date and scope have held steady across every tracked day.</div></div>`;
  let html = `<div class="hist-summary">📅 <b>Completion date moved ${tEvents.length}×</b>${sEvents.length ? ` · scope changed ${sEvents.length}×` : ''}<div class="hist-sub">Auto-detected by diffing daily snapshots. The “why” pulls from linked notes &amp; scope; ✎ to set a producer reason.</div></div>`;
  if (tEvents.length) {
    html += `<div class="hist-section">🎯 Planned target date</div>`;
    html += tEvents.map(ev => {
      const d = ev.days;
      const cls = d == null ? '' : d > 0 ? 'h-late' : 'h-early';
      const dtxt = d == null ? '' : d > 0 ? `▲ slipped +${d} wd` : d < 0 ? `▼ pulled in ${-d} wd` : 'no shift';
      const why = whyForMove(g, ev);
      const srcLabel = { producer: '✎ producer', note: '📝 linked note', auto: '~ inferred', none: '' }[why.kind] || '';
      const bodyHtml = why.text ? esc(why.text) : `<span class="why-none">no reason recorded</span>`;
      const auth = why.author ? ` <span class="why-auth">— ${esc(why.author)}</span>` : '';
      const pencil = getGhEditor() ? `<button class="why-edit" data-move="${esc(ev.date)}" title="Set / edit the reason">✎</button>` : '';
      return `<div class="hist-row"><div class="hist-line"><span class="hist-date">${esc(ev.date)}</span><span class="hist-move">${fmtD(ev.from)} → ${fmtD(ev.to)}</span><span class="hist-delta ${cls}">${dtxt}</span></div>` +
        `<div class="hist-why ${why.kind}">${srcLabel ? `<span class="why-src">${srcLabel}</span> ` : ''}<span class="why-body">${bodyHtml}</span>${auth}${pencil}</div></div>`;
    }).join('');
  }
  if (sEvents.length) {
    html += `<div class="hist-section">📈 Scope</div>`;
    html += sEvents.map(ev =>
      `<div class="hist-row scope"><div class="hist-line"><span class="hist-date">${esc(ev.date)}</span><span class="hist-move">${ev.from}h → ${ev.to}h</span><span class="hist-delta ${ev.delta > 0 ? 'h-late' : 'h-early'}">${ev.delta > 0 ? '+' : ''}${ev.delta}h</span></div></div>`).join('');
  }
  return html;
}
function renderShareBanner() {
  const el = document.getElementById('gpShareBanner');
  if (!el) return;
  const sv = SHARED && SHARED.updated_at;
  // No shared plan, or this browser already shows shared (no local edits): nothing to offer.
  if (!sv || !hasLocalEdits()) { el.style.display = 'none'; if (sv) setSharedSeen(sv); return; }
  // Local edits are masking shared; only prompt if the shared plan is newer than acknowledged.
  if (getSharedSeen() === sv) { el.style.display = 'none'; return; }
  const by = SHARED.updated_by ? ' by ' + SHARED.updated_by : '';
  el.innerHTML = `<span class="gsb-txt">📌 A newer shared plan${by} was published (${sv}). Your local edits are hiding it.</span>`
    + `<button class="gsb-btn primary" id="gsbApply">Apply shared plan</button>`
    + `<button class="gsb-btn" id="gsbDismiss">Keep my edits</button>`;
  el.style.display = 'flex';
  document.getElementById('gsbApply').onclick = () => { setSharedSeen(sv); resetLocalEdits(); };
  document.getElementById('gsbDismiss').onclick = () => { setSharedSeen(sv); el.style.display = 'none'; };
}

// Auto-promote banner: lists every game whose shared override was passed by
// the live auto status this load. Offers two actions:
//   • Save to plan  → publishes a plan with those stale entries removed
//   • Keep manual   → re-pins the overrides locally so they survive the auto
//                     promotion (writes to USER_STATUS); editor can later
//                     Save-as-default to push them back to shared.
let PENDING_RELEASES = new Set();   // games ✓-released this session, awaiting publish (#71)
let PROMOTE_RESOLVED = new Set();   // games the editor has ✓/✗-resolved this session (hide their row)
function renderPromoteBanner() {
  const el = document.getElementById('gpPromoteBanner');
  if (!el) return;
  // Editor-only (#58/#71): a normal viewer already sees the auto-promoted
  // (daily-Jira) state — stale overrides are dropped for display in
  // resolveGameStatus/Stage — so they get NO banner at all, just the dashboard.
  if (!getGhEditor()) { el.style.display = 'none'; return; }
  const drifted = RAW_GAMES.filter(g => g._auto_promoted_from || g._auto_stage_promoted_from);
  const shown = drifted.filter(g => !PROMOTE_RESOLVED.has(g.jira));
  if (!shown.length && !PENDING_RELEASES.size) { el.style.display = 'none'; return; }
  // Per-item review (#71): a ✓ (release → accept Jira) or ✗ (keep the override)
  // in front of each line, instead of the old bulk Save/Keep buttons.
  const rows = shown.map(g => {
    const parts = [];
    if (g._auto_promoted_from) parts.push(`status <span class="ap-from">${g._auto_promoted_from}</span> → <span class="ap-to">${g._auto_status}</span>`);
    if (g._auto_stage_promoted_from) parts.push(`stage <span class="ap-from">${stageLabel(g._auto_stage_promoted_from)}</span> → <span class="ap-to">${stageLabel(g._auto_stage)}</span>`);
    return `<li class="apb-row">`
      + `<button class="apb-acc" data-jira="${g.jira}" title="Release — accept Jira and drop the saved override">✓</button>`
      + `<button class="apb-rej" data-jira="${g.jira}" title="Keep — re-pin your manual override">✗</button>`
      + `<span class="apb-txt"><b>${g.name}</b> — ${parts.join(' · ')}</span></li>`;
  }).join('');
  const pend = PENDING_RELEASES.size;
  el.innerHTML = `<div class="apb-head">📢 ${shown.length ? shown.length + ' game' + (shown.length !== 1 ? 's' : '') + ' moved on in Jira past a saved override — ✓ release or ✗ keep each' : 'All reviewed'}.</div>`
    + (rows ? `<ul class="apb-list">${rows}</ul>` : '')
    + (pend ? `<div class="apb-actions"><button class="gsb-btn primary" id="apbSave">💾 Save ${pend} release${pend !== 1 ? 's' : ''} to plan</button><span class="apb-note">Publishes the cleared overrides for everyone · keeps stay on your browser until you Save as default.</span></div>` : '');
  el.style.display = 'block';
  el.querySelectorAll('.apb-acc').forEach(b => b.onclick = () => {
    const g = RAW_GAMES.find(x => x.jira === b.dataset.jira); if (!g) return;
    // Release → follow Jira: drop any local override so the game matches auto
    // (buildPlanPayload then omits it, clearing the stale shared entry on save).
    delete USER_STATUS[g.name]; delete USER_STAGE[g.name];
    g.workflow_status = g._auto_status; g._status_source = 'auto';
    g.current_stage = g._auto_stage; g._stage_source = 'auto';
    PENDING_RELEASES.add(g.jira); PROMOTE_RESOLVED.add(g.jira);
    saveStatus(); saveStage(); renderPromoteBanner(); renderRows();
  });
  el.querySelectorAll('.apb-rej').forEach(b => b.onclick = () => {
    const g = RAW_GAMES.find(x => x.jira === b.dataset.jira); if (!g) return;
    // Keep → re-pin the manual override locally (yours only until Save as default).
    if (g._auto_promoted_from) { USER_STATUS[g.name] = g._auto_promoted_from; g.workflow_status = g._auto_promoted_from; g._status_source = 'local'; }
    if (g._auto_stage_promoted_from) { USER_STAGE[g.name] = g._auto_stage_promoted_from; g.current_stage = g._auto_stage_promoted_from; g._stage_source = 'local'; }
    PENDING_RELEASES.delete(g.jira); PROMOTE_RESOLVED.add(g.jira);
    saveStatus(); saveStage(); renderPromoteBanner(); renderRows();
  });
  const sb = document.getElementById('apbSave');
  if (sb) sb.onclick = async () => {
    if (!getPat()) { openSignInModal(); return; }
    sb.disabled = true; sb.textContent = 'Saving…';
    try {
      await publishPlan();
      const n = PENDING_RELEASES.size; PENDING_RELEASES.clear();
      showToast(`✓ Released ${n} override${n !== 1 ? 's' : ''} to the shared plan`);
      SHARED_STATUS = (SHARED_CACHE[PROJECT.key] || {}).status || {};
      SHARED_STAGE  = (SHARED_CACHE[PROJECT.key] || {}).stage  || {};
      RAW_GAMES.forEach(g => { resolveGameStatus(g); resolveGameStage(g); });
      renderPromoteBanner(); renderRows();
    } catch (e) {
      sb.disabled = false; sb.textContent = '💾 Save releases to plan';
      // The old bug was a silent no-op when the shared plan was stale — now we
      // surface it with a reconcile path instead of leaving the banner stuck.
      if (e && e.stale) { openStaleModal(e); return; }
      showToast('Save failed: ' + e.message);
    }
  };
}

// ============================================================
//  SHARED PLAN — load + publish via GitHub (Decision #39)
// ============================================================
// A cheap version stamp for the shared plan (#54). Two loads with the same
// stamp are the same published state; a change means someone published since.
function planVersion(p) { return (p && p.updated_at) ? (p.updated_at + '|' + (p.updated_by || '')) : null; }
async function loadSharedData(key) {
  try { const r = await fetch(`plan-${key}.json?ts=` + new Date().getTime()); if (r.ok) SHARED_CACHE[key] = await r.json(); } catch (e) {}
  if (SHARED_CACHE[key] === undefined) SHARED_CACHE[key] = {};
  PLAN_VERSION[key] = planVersion(SHARED_CACHE[key]);   // baseline for the stale-publish guard
  try { const r = await fetch('editors.json?ts=' + new Date().getTime()); if (r.ok) { const j = await r.json(); EDITORS = j.editors || []; } } catch (e) {}
}
// True when this browser has unpublished edits we must not silently discard on a
// soft refresh (#54): plan mode, local status/stage/size/order/hidden overrides,
// or local notes not yet saved as default.
function hasLocalEdits() {
  if (planMode) return true;
  const some = o => o && Object.keys(o).length > 0;
  if (some(USER_STATUS) || some(USER_STAGE) || some(USER_SIZES) || (USER_ORDER && USER_ORDER.length)) return true;
  try { if (localStorage.getItem(LS + 'notes') || localStorage.getItem(LS + 'reasons') || localStorage.getItem(LS + 'hidden') != null) return true; } catch (e) {}
  return false;
}
// Soft auto-refresh for viewers (#54): when a tab regains focus (throttled), pull
// the latest shared plan and re-render in place if it changed — so everyone sees
// the current default without a disruptive full reload. Never runs while this
// browser has unpublished edits or is typing, so it can't yank work in progress.
async function maybeRefreshShared(opts) {
  opts = opts || {};
  const key = PROJECT && PROJECT.key; if (!key) return;
  if (hasLocalEdits()) return;
  const ae = document.activeElement;
  if (ae && (ae.tagName === 'TEXTAREA' || ae.tagName === 'INPUT')) return;   // don't interrupt typing
  const now = Date.now();
  if (!opts.force && now - _lastSharedRefresh < 60000) return;               // throttle to once/min
  _lastSharedRefresh = now;
  let latest = null;
  try { const r = await fetch(`plan-${key}.json?ts=` + now); if (r.ok) latest = await r.json(); } catch (e) { return; }
  if (!latest || planVersion(latest) === PLAN_VERSION[key]) return;          // unchanged → nothing to do
  SHARED_CACHE[key] = latest; PLAN_VERSION[key] = planVersion(latest);
  mount(key, APP);
  showToast('↻ Updated to the latest shared view');
}
// Team token persists in localStorage so it's entered ONCE per browser (#46),
// not every session. Treat it like a password (see TEAM_ACCESS.md).
function getPat() { try { return localStorage.getItem(GH_PAT_KEY) || ''; } catch (e) { return ''; } }
function getGhUser() { try { return localStorage.getItem(GH_USER_KEY) || ''; } catch (e) { return ''; } }
function getGhEditor() { try { return localStorage.getItem(GH_ED_KEY) === '1'; } catch (e) { return false; } }
function setPat(t, user, isEd) {
  try {
    if (t) { localStorage.setItem(GH_PAT_KEY, t); if (user) localStorage.setItem(GH_USER_KEY, user); localStorage.setItem(GH_ED_KEY, isEd ? '1' : '0'); }
    else { localStorage.removeItem(GH_PAT_KEY); localStorage.removeItem(GH_USER_KEY); localStorage.removeItem(GH_ED_KEY); }
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
  const status = {}, stage = {}, sizes = {};
  RAW_GAMES.forEach(g => {
    if (g.workflow_status !== g._auto_status) status[g.name] = g.workflow_status;
    // Only ship stage entries that actually diverge from what Jira derives; matches
    // the status pattern so auto-promotion naturally purges them on next save.
    if (g.current_stage && g._auto_stage && g.current_stage !== g._auto_stage) stage[g.name] = g.current_stage;
    const sz = gameSizes(g), keep = {};
    ['art', 'math', 'dev', 'sound'].forEach(k => { if (sz[k]) keep[k] = sz[k]; });
    if (Object.keys(keep).length) sizes[g.name] = keep;
  });
  // Notes — strip empty arrays so the JSON stays clean. Anything that's been
  // appended via addNote() shows up here.
  const notes = {};
  Object.keys(NOTES || {}).forEach(k => { const arr = NOTES[k] || []; if (arr.length) notes[k] = arr; });
  // Date-move "why" reasons — strip empty per-game maps so the JSON stays clean (#66).
  const move_reasons = {};
  Object.keys(REASONS || {}).forEach(k => { const m = REASONS[k] || {}; if (Object.keys(m).length) move_reasons[k] = m; });
  return {
    order: RAW_GAMES.map(g => g.name), status, stage, sizes, hidden: [...HIDDEN], notes, move_reasons,
    // Games on the board that aren't default roster — by the explicit added-set
    // OR a non-roster flag, so `added` can't drift out of sync with `order` (#47/#52).
    added: (function () {
      const s = new Set([...(SHARED.added || []), ...(USER_ADDED || [])]);
      return RAW_GAMES.filter(g => g.in_roster === false || s.has(g.jira)).map(g => g.jira);
    })(),
    config: { statuses: CONFIG.statuses, stages: CONFIG.stages, sizes: CONFIG.sizes, capacities: CONFIG.capacities, velocities: CONFIG.velocities || {} },
    updated_by: getGhUser() || null, updated_at: nowStamp(),
  };
}
async function publishPlan(force) {
  const path = `plan-${PROJECT.key}.json`;
  const payload = buildPlanPayload();
  let sha = null, remote = null;
  try {
    const cur = await ghFetch(`${GH_API}/contents/${path}`);
    sha = cur.sha;
    try { remote = JSON.parse(decodeURIComponent(escape(atob((cur.content || '').replace(/\s/g, ''))))); } catch (e) { remote = null; }
  } catch (e) { /* first time: no file */ }
  // Stale-publish guard (#54): if the shared plan changed since this tab loaded
  // it (someone else published), don't silently overwrite their status/stage/
  // order/hidden edits. Notes still union-merge below and are never lost; for
  // everything else the editor must reload & re-apply (or explicitly force).
  if (!force && remote) {
    const loaded = PLAN_VERSION[PROJECT.key], current = planVersion(remote);
    if (loaded && current && current !== loaded) {
      const err = new Error('The shared view changed since you loaded it.');
      err.stale = true; err.by = remote.updated_by || 'someone'; err.at = remote.updated_at || '';
      throw err;
    }
  }
  // Never lose a note to a stale tab (#53): notes are append-only, so union
  // this session's notes with whatever is currently on main rather than
  // overwriting the file wholesale. (Other maps still overwrite — see #53
  // follow-up; notes are the append-only case that must never regress.)
  if (remote && remote.notes) {
    const merged = {};
    new Set([...Object.keys(remote.notes), ...Object.keys(payload.notes || {})])
      .forEach(k => { merged[k] = mergeNoteLists(remote.notes[k], (payload.notes || {})[k]); });
    payload.notes = merged;
  }
  // Same never-lose rule for date-move reasons: union with main, newest ts wins (#66).
  if (remote && remote.move_reasons) {
    payload.move_reasons = mergeReasons(remote.move_reasons, payload.move_reasons || {});
  }
  const content = btoa(unescape(encodeURIComponent(JSON.stringify(payload, null, 2))));
  const body = { message: `plan(${PROJECT.key}): save as default by ${getGhUser() || 'editor'}`, content, branch: 'main' };
  if (sha) body.sha = sha;
  await ghFetch(`${GH_API}/contents/${path}`, { method: 'PUT', body: JSON.stringify(body) });
  SHARED_CACHE[PROJECT.key] = payload;   // reflect immediately for this browser
  PLAN_VERSION[PROJECT.key] = planVersion(payload);   // our publish is the new baseline (#54)
}

// --- modal helpers ---
function openModal(html) {
  const ov = document.getElementById('gpModalOverlay'), m = document.getElementById('gpModal');
  m.innerHTML = html; ov.classList.add('open');
}
function closeModal() { document.getElementById('gpModalOverlay').classList.remove('open'); }

function openSignInModal() {
  openModal(`<h3>🔐 Sign in to publish</h3>
    <p class="gp-modal-note">Paste your <b>team token</b> (a GitHub fine-grained token scoped to <code>${GH_OWNER}/${GH_REPO}</code>, <b>Contents: Read &amp; write</b>). It's stored on <b>this browser only</b> so you only enter it <b>once</b> — not committed, not shared. Viewers without it stay read-only. <span style="color:var(--sub)">(Ask your admin for it — see TEAM_ACCESS.md.)</span></p>
    <input type="password" id="gpPat" placeholder="github_pat_…" autocomplete="off" spellcheck="false">
    <div class="gp-modal-msg" id="gpPatMsg"></div>
    <div class="gp-modal-foot"><button class="gp-foot-btn" id="gpPatCancel">Cancel</button><button class="gp-foot-btn primary" id="gpPatVerify">Verify &amp; sign in</button></div>`);
  document.getElementById('gpPatCancel').onclick = closeModal;
  document.getElementById('gpPatVerify').onclick = async () => {
    const tok = document.getElementById('gpPat').value.trim();
    const msg = document.getElementById('gpPatMsg');
    if (!tok) { msg.textContent = 'Enter a token.'; return; }
    try { localStorage.setItem(GH_PAT_KEY, tok); } catch (e) {}
    msg.textContent = 'Verifying…';
    try {
      const { login, emails } = await verifyPat();   // throws unless the token has repo WRITE access
      // Real gate = GitHub write access (verifyPat already confirmed it). A
      // fine-grained repo-scoped token can't read account emails, so the email
      // allowlist can't be relied on — write access is the enforcement (#48).
      const ed = true;
      const allow = EDITORS.map(x => String(x).toLowerCase());
      const display = emails.find(e => allow.includes(e)) || login;
      setPat(tok, display, ed);
      closeModal(); showToast('✓ Signed in as ' + display + ' — you can publish'); renderDrawer(); renderPromoteBanner();
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
    catch (e) { if (e && e.stale) { openStaleModal(e); return; } msg.textContent = '✗ ' + e.message; }
  };
}
// Shown when "Save as default" is blocked because someone published since this
// tab loaded (#54). Reload re-applies this browser's edits on top of the latest;
// "Save anyway" force-publishes (notes still merge, never lost).
function openStaleModal(info) {
  openModal(`<h3>⚠ Shared view changed since you loaded</h3>
    <p class="gp-modal-note"><b>${info.by || 'Someone'}</b> saved a new default${info.at ? ' at <b>' + info.at + '</b>' : ''} while your tab was open. Saving now would roll back their changes. Reload the latest — your own edits re-apply on top — then Save as default again. <span style="color:var(--sub)">(Notes are always merged, never lost.)</span></p>
    <div class="gp-modal-msg" id="gpStaleMsg"></div>
    <div class="gp-modal-foot"><button class="gp-foot-btn primary" id="gpStaleReload">↻ Reload latest &amp; re-apply</button><button class="gp-foot-btn" id="gpStaleForce">Save anyway</button></div>`);
  document.getElementById('gpStaleReload').onclick = async () => {
    closeModal();
    await loadSharedData(PROJECT.key);   // refresh SHARED_CACHE + PLAN_VERSION to latest
    mount(PROJECT.key, APP);             // re-mount; this browser's local edits re-layer on top
    showToast('↻ Loaded the latest shared view — review, then Save as default again');
  };
  document.getElementById('gpStaleForce').onclick = async () => {
    const msg = document.getElementById('gpStaleMsg'); msg.textContent = 'Committing…';
    try { await publishPlan(true); closeModal(); showToast('✓ Saved (overwrote newer changes; notes merged)'); renderDrawer(); }
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
    // Gantt + Roadmap share the timeline container (#65); renderRows() picks the
    // row style (milestone bar vs stacked lanes) from currentView.
    document.getElementById('roadmapView').style.display = (currentView === 'roadmap' || currentView === 'gantt') ? 'block' : 'none';
    document.getElementById('heatmapView').style.display = currentView === 'heatmap' ? 'block' : 'none';
    document.getElementById('listView').style.display = currentView === 'list' ? 'block' : 'none';
    if (currentView === 'heatmap') renderHeatmap();
    else if (currentView === 'list') renderList();
    else renderRows();
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
  ALL_GAMES = (data.games || []).slice();   // every epic incl. add-game candidates (#47)
  RAW_GAMES = ALL_GAMES.slice();            // narrowed to the roster below

  SPRINT_LIST = RAW_SPRINTS.slice().sort((a, b) => new Date(a.start) - new Date(b.start));
  SPRINT_BY_ID = {};
  SPRINT_LIST.forEach(s => { SPRINT_BY_ID[String(s.id)] = s; });
  CHART_START = SPRINT_LIST.length ? new Date(SPRINT_LIST[0].start) : new Date('2026-05-11');
  CHART_END = SPRINT_LIST.length ? new Date(SPRINT_LIST[SPRINT_LIST.length - 1].end || SPRINT_LIST[SPRINT_LIST.length - 1].start) : new Date('2027-12-07');
  TODAY = REFRESHED ? new Date(REFRESHED.slice(0, 10) + 'T00:00:00') : new Date();

  // Precedence: Jira auto  <  shared committed plan  <  this browser's local edits.
  SHARED = SHARED_CACHE[PROJECT.key] || {};
  SHARED_STATUS = SHARED.status || {};
  SHARED_STAGE  = SHARED.stage  || {};
  SHARED_SIZES = SHARED.sizes || {};
  SHARED_NOTES = SHARED.notes || {};
  // Working copy of notes — starts identical to shared, mutated by addNote()
  // and persisted via publishPlan. Deep-clone so adding a note doesn't
  // retroactively mutate the SHARED_CACHE entry.
  NOTES = JSON.parse(JSON.stringify(SHARED_NOTES));
  loadNotesOverlay();   // local unpublished notes win for this browser
  migrateNotesToJira(); // fold legacy name-keyed notes onto stable jira keys (#53)
  saveNotes();          // persist the merged, jira-keyed set so a stale local overlay self-heals (#55)

  // Date-move "why" reasons — same shared-baseline + local-overlay model (#66).
  SHARED_REASONS = SHARED.move_reasons || {};
  REASONS = JSON.parse(JSON.stringify(SHARED_REASONS));
  loadReasonsOverlay();
  saveReasons();

  CONFIG = JSON.parse(JSON.stringify(DEFAULT_CONFIG));
  if (SHARED.config) CONFIG = { ...CONFIG, ...SHARED.config };
  try { const s = localStorage.getItem(LS + 'config'); if (s) CONFIG = { ...CONFIG, ...JSON.parse(s) }; } catch (e) {}

  USER_ORDER = []; USER_STATUS = {}; USER_STAGE = {}; USER_SIZES = {};
  try { USER_ORDER = JSON.parse(localStorage.getItem(LS + 'order') || '[]'); } catch (e) {}
  try { USER_STATUS = JSON.parse(localStorage.getItem(LS + 'status') || '{}'); } catch (e) {}
  try { USER_STAGE  = JSON.parse(localStorage.getItem(LS + 'stage')  || '{}'); } catch (e) {}
  try { USER_SIZES = JSON.parse(localStorage.getItem(LS + 'sizes') || '{}'); } catch (e) {}
  // hidden: local set if this browser has touched it, else the shared set
  const lhid = localStorage.getItem(LS + 'hidden');
  HIDDEN = new Set(lhid != null ? JSON.parse(lhid || '[]') : (SHARED.hidden || []));

  // Roster = default in_roster epics + any games added on the dashboard (#47).
  USER_ADDED = [];
  try { USER_ADDED = JSON.parse(localStorage.getItem(LS + 'added') || '[]'); } catch (e) {}
  const addedSet = new Set([...(SHARED.added || []), ...USER_ADDED]);
  RAW_GAMES = ALL_GAMES.filter(g => g.in_roster !== false || addedSet.has(g.jira));

  const order = USER_ORDER.length ? USER_ORDER : (SHARED.order || []);
  if (order.length) {
    const byName = Object.fromEntries(RAW_GAMES.map(g => [g.name, g]));
    const ordered = order.map(n => byName[n]).filter(Boolean);
    const missing = RAW_GAMES.filter(g => !order.includes(g.name));
    RAW_GAMES = [...ordered, ...missing];
  }
  // Resolve effective status + stage per game: local > shared > auto.
  RAW_GAMES.forEach(resolveGameStatus);
  RAW_GAMES.forEach(resolveGameStage);

  try { showForecast = JSON.parse(localStorage.getItem(LS + 'forecast')); } catch (e) {}
  if (showForecast === null || showForecast === undefined) showForecast = true;
  applyForecast();

  activeFilters = { status: 'ALL', stage: 'ALL', search: '' };
  currentView = 'gantt'; planMode = false; openPanel = null; dragSrcIdx = null;   // Gantt is the landing view (#65)
  drawerTab = 'games'; drawerOpenRows = new Set(); drawerDragIdx = null;
  document.body.classList.remove('plan-mode-on-body');

  buildSkeleton();
  setupTips();
  // Viewer soft auto-refresh (#54): pick up newly-published shared views on tab
  // focus + a slow backstop timer. Registered once; gated inside maybeRefreshShared.
  if (!window._gpRefreshInit) {
    window._gpRefreshInit = true;
    document.addEventListener('visibilitychange', () => { if (!document.hidden) maybeRefreshShared(); });
    window.addEventListener('focus', () => maybeRefreshShared());
    setInterval(() => { if (!document.hidden) maybeRefreshShared(); }, 5 * 60 * 1000);
  }
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
  buildFilterBar(); wireControls(); renderKPI(); renderAxis(); renderRows(); renderShareBanner(); renderPromoteBanner();
  centerToday();   // land with today centred (#69)
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
