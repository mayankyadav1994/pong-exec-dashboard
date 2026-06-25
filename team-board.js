/* ============================================================
   Team Board — sprint planning calendar engine.
   Reads window.TB_DATA (from team-board-data.js); renders a draggable
   day calendar per department. Drag/drop + Edit Plan persist to Jira via
   the GitHub Actions relay in team-board-write.js (loaded last, optional).
   ============================================================ */
(function () {
"use strict";

var DATA = window.TB_DATA || { depts: {}, sprint: null, refreshed_at: "" };
var DAILY_CAP = 16;                       // hours/day before a column goes red
var DOW = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

var deptKeys = Object.keys(DATA.depts || {});
var curDept = deptKeys[0] || null;
var curDrag = null;
var expanded = {};        // pill id -> subtask dropdown open?

// --- date helpers ------------------------------------------------------------
function parseISO(s) { var p = (s || "").split("-"); return p.length === 3 ? new Date(+p[0], +p[1] - 1, +p[2]) : null; }
function isoOf(d) { var m = ("0" + (d.getMonth() + 1)).slice(-2), day = ("0" + d.getDate()).slice(-2); return d.getFullYear() + "-" + m + "-" + day; }
function fmtShort(d) { return d.toLocaleDateString("en-US", { month: "short", day: "numeric" }); }
function sameDay(a, b) { return a && b && isoOf(a) === isoOf(b); }
function isWeekend(d) { var g = d.getDay(); return g === 0 || g === 6; }
function addDays(d, n) { var x = new Date(d); x.setDate(x.getDate() + n); return x; }
function startOfWeek(d) { return addDays(d, -((d.getDay() + 6) % 7)); }   // Monday

var TODAY = new Date();
var SPRINT_START = (DATA.sprint && DATA.sprint.start) ? parseISO(DATA.sprint.start) : null;
var SPRINT_END = (DATA.sprint && DATA.sprint.end) ? parseISO(DATA.sprint.end) : null;
var viewMode = "month";                 // 'day' | 'week' | 'month'
var anchor = new Date(TODAY);           // focal date for the current view
function inSprint(d) { return SPRINT_START && SPRINT_END && d >= SPRINT_START && d <= SPRINT_END; }

function tickets() { return (DATA.depts[curDept] && DATA.depts[curDept].tickets) || []; }
function subtasksFlat() {
  var out = [];
  tickets().forEach(function (t) { (t.subtasks || []).forEach(function (s) { s._parent = t.id; out.push(s); }); });
  return out;
}
function findItem(id) {
  return tickets().find(function (x) { return x.id === id; })
      || subtasksFlat().find(function (s) { return s.id === id; }) || null;
}

// --- cards -------------------------------------------------------------------
function flagHtml(t) {
  var f = t.flags || {};
  if (f.blocked) return '<span class="flag f-blocked">⛔ blocked</span>';
  if (f.unassigned) return '<span class="flag f-unassigned">no owner</span>';
  if (f.unestimated) return '<span class="flag f-unestimated">no est</span>';
  return "";
}
function esc(s) { return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) { return ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]; }); }

function pillHtml(t) {
  var who = t.assignee
    ? '<span class="avatar">' + esc(t.initials || "?") + '</span><span class="who">' + esc(t.assignee) + "</span>"
    : '<span class="avatar none">—</span><span class="who">Unassigned</span>';
  var rel = t.release || t.game || "";
  var bk = esc(t.bucket || "todo");
  var rem = t.remaining;
  var remHtml = (rem > 0)
    ? '<span class="rem has" title="Remaining (rolled up incl. subtasks)">' + rem + "h left</span>"
    : '<span class="rem none">' + (t.est ? "0h left" : "no est") + "</span>";
  var statHtml = '<span class="stat st-' + bk + '" title="' + esc(t.status || "") + '">' +
    '<span class="dot b-' + bk + '"></span>' + esc(t.status || "—") + "</span>";
  var isOpen = !!expanded[t.id];
  var nSub = t.sub_count || 0;
  var subToggle = nSub > 0
    ? '<button class="sub-toggle" data-id="' + esc(t.id) + '">' + (isOpen ? "▾ " : "▸ ") + nSub + " subtask" + (nSub > 1 ? "s" : "") + "</button>"
    : '<span class="sub-none">no subtasks</span>';
  var dueVal = t.due ? esc(t.due) : "";
  var foot = '<div class="pill-foot">' + subToggle +
    '<label class="due-edit-wrap" title="Set due date — writes to Jira">📅<input type="date" class="due-edit" data-id="' + esc(t.id) + '" value="' + dueVal + '"></label></div>';
  var panel = nSub > 0
    ? '<div class="sub-panel" data-panel="' + esc(t.id) + '"' + (isOpen ? "" : " hidden") + ">" + (t.subtasks || []).map(subRow).join("") + "</div>"
    : "";
  return '<div class="pill ' + curDept + '" draggable="true" data-id="' + esc(t.id) + '">' +
    '<div class="pill-top"><a class="pill-key" href="' + esc(t.url) + '" target="_blank" rel="noopener">' + esc(t.id) + "</a>" + remHtml + "</div>" +
    '<div class="pill-sum">' + esc(t.summary) + "</div>" +
    '<div class="pill-bot">' + statHtml + flagHtml(t) + "</div>" +
    '<div class="pill-bot"><span class="pill-meta">' + who + '</span><span class="game">' + esc(rel) + "</span></div>" +
    foot + panel + "</div>";
}

// Subtask row inside a parent's dropdown — also draggable onto a day.
function subRow(s) {
  var bk = esc(s.bucket || "todo");
  var rem = (s.remaining > 0) ? s.remaining + "h" : "—";
  return '<div class="sub-row" draggable="true" data-id="' + esc(s.id) + '" title="Drag onto a day to schedule">' +
    '<a class="sub-key" href="' + esc(s.url) + '" target="_blank" rel="noopener">' + esc(s.id) + "</a>" +
    '<span class="sub-sum" title="' + esc(s.summary) + '">' + esc(s.summary) + "</span>" +
    '<span class="stat st-' + bk + '"><span class="dot b-' + bk + '"></span>' + esc(s.status || "—") + "</span>" +
    '<span class="sub-rem">' + rem + "</span></div>";
}

// Compact subtask chip shown on a calendar day / backlog.
function subChipHtml(s) {
  var bk = esc(s.bucket || "todo");
  var rem = (s.remaining > 0) ? s.remaining + "h" : "";
  return '<div class="sub-chip ' + curDept + '" draggable="true" data-id="' + esc(s.id) + '" title="' + esc(s.summary) + '">' +
    '<span class="dot b-' + bk + '"></span>' +
    '<a class="chip-key" href="' + esc(s.url) + '" target="_blank" rel="noopener">' + esc(s.id) + "</a>" +
    '<span class="chip-sum">' + esc(s.summary) + "</span>" +
    (rem ? '<span class="chip-rem">' + rem + "</span>" : "") + "</div>";
}

// --- calendar views ----------------------------------------------------------
function mkCell(d) {
  return {
    date: new Date(d), iso: isoOf(d), dow: DOW[d.getDay()], weekend: isWeekend(d),
    today: sameDay(d, TODAY), inSprint: inSprint(d),
    sprintStart: SPRINT_START && sameDay(d, SPRINT_START),
    sprintEnd: SPRINT_END && sameDay(d, SPRINT_END),
  };
}
function buildCells() {
  if (viewMode === "day") return { single: true, rows: [[mkCell(anchor)]] };
  if (viewMode === "week") {
    var st = startOfWeek(anchor), row = [];
    for (var i = 0; i < 7; i++) row.push(mkCell(addDays(st, i)));
    return { single: false, rows: [row] };
  }
  // month: 6 weeks; days outside the month are blank
  var first = new Date(anchor.getFullYear(), anchor.getMonth(), 1);
  var d = startOfWeek(first), rows = [];
  for (var w = 0; w < 6; w++) {
    var row = [];
    for (var j = 0; j < 7; j++) { row.push(d.getMonth() === anchor.getMonth() ? mkCell(d) : null); d = addDays(d, 1); }
    rows.push(row);
  }
  return { single: false, rows: rows.filter(function (r) { return r.some(Boolean); }) };
}
function dayCellHtml(c, big) {
  if (!c) return '<div class="day empty"></div>';
  var cls = "day" + (c.today ? " today" : "") + (c.weekend ? " weekend" : "") +
            (c.inSprint ? " in-sprint" : "") + (big ? " day-big" : "");
  var mark = c.sprintStart ? '<span class="sp-marker start">▸ Sprint start</span>'
           : (c.sprintEnd ? '<span class="sp-marker end">Sprint end ◂</span>' : "");
  return '<div class="' + cls + '">' +
    '<div class="day-head"><span><span class="day-date">' + fmtShort(c.date) + '</span> <span class="day-dow">' + c.dow + '</span></span>' +
    '<span class="cap" data-iso="' + c.iso + '"></span></div>' +
    (mark ? '<div class="sp-marker-row">' + mark + "</div>" : "") +
    '<div class="day-body" data-iso="' + c.iso + '"></div></div>';
}
function renderGrid(view) {
  if (view.single) return '<div class="cal-day-single">' + dayCellHtml(view.rows[0][0], true) + "</div>";
  var head = '<div class="week dow-head">' +
    ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].map(function (x) { return '<div class="dowh">' + x + "</div>"; }).join("") + "</div>";
  var body = view.rows.map(function (row) {
    return '<div class="week">' + row.map(function (c) { return dayCellHtml(c, false); }).join("") + "</div>";
  }).join("");
  return head + body;
}

function render() {
  var cal = document.getElementById("cal");
  if (!SPRINT_START) { cal.innerHTML = '<p class="footer">No active sprint window found.</p>'; return; }
  var view = buildCells();
  cal.innerHTML = renderGrid(view);

  var tasks = tickets(), subs = subtasksFlat();

  // backlog: anything with no due date (tasks as pills, subtasks as chips)
  var bl = document.getElementById("backlog"), blCount = 0;
  bl.innerHTML = "";
  tasks.forEach(function (t) { if (t.due == null) { bl.insertAdjacentHTML("beforeend", pillHtml(t)); blCount++; } });
  subs.forEach(function (s) { if (s.due == null) { bl.insertAdjacentHTML("beforeend", subChipHtml(s)); blCount++; } });
  document.getElementById("blCount").textContent = blCount;

  // place items + capacity on each visible day
  view.rows.forEach(function (row) {
    row.forEach(function (c) {
      if (!c) return;
      var body = cal.querySelector('.day-body[data-iso="' + c.iso + '"]');
      if (!body) return;
      tasks.forEach(function (t) { if (t.due === c.iso) body.insertAdjacentHTML("beforeend", pillHtml(t)); });
      subs.forEach(function (s) { if (s.due === c.iso) body.insertAdjacentHTML("beforeend", subChipHtml(s)); });
      // capacity = leaf work: subtasks + tasks-without-subtasks (avoid double count)
      var cap = 0;
      subs.forEach(function (s) { if (s.due === c.iso) cap += s.remaining || 0; });
      tasks.forEach(function (t) { if (t.due === c.iso && !(t.sub_count > 0)) cap += t.remaining || 0; });
      var capEl = cal.querySelector('.cap[data-iso="' + c.iso + '"]');
      if (capEl) { cap = Math.round(cap); capEl.textContent = cap + "h"; capEl.className = "cap " + (cap > DAILY_CAP ? "over" : (cap >= DAILY_CAP * 0.75 ? "warn" : "ok")); }
    });
  });

  updateControls();
  wireDrag();
}

// --- drag & drop -------------------------------------------------------------
function wireDrag() {
  document.querySelectorAll(".pill, .sub-chip, .sub-row").forEach(function (p) {
    p.addEventListener("dragstart", function (e) {
      if (e.target.closest("input, button, a")) { e.preventDefault(); return; }   // not from controls/links
      curDrag = p.dataset.id; e.stopPropagation(); p.classList.add("dragging"); e.dataTransfer.effectAllowed = "move";
    });
    p.addEventListener("dragend", function () { p.classList.remove("dragging"); });
  });
  document.querySelectorAll(".day-body, #backlog").forEach(function (z) {
    z.addEventListener("dragover", function (e) { e.preventDefault(); z.classList.add("drop-hot"); });
    z.addEventListener("dragleave", function () { z.classList.remove("drop-hot"); });
    z.addEventListener("drop", function (e) {
      e.preventDefault(); z.classList.remove("drop-hot");
      var item = findItem(curDrag);
      if (!item) return;
      var iso = z.dataset.iso || null;                  // backlog has no data-iso → unschedule
      if ((iso || null) === (item.due || null)) return;
      var prev = item.due;
      item.due = iso || null;                            // optimistic
      render();
      persistDue(item, iso || null, prev);
    });
  });
  // expand / collapse subtasks
  document.querySelectorAll(".sub-toggle").forEach(function (b) {
    b.addEventListener("click", function (e) {
      e.stopPropagation();
      var id = b.dataset.id;
      expanded[id] = !expanded[id];
      var panel = document.querySelector('.sub-panel[data-panel="' + id + '"]');
      if (panel) panel.hidden = !expanded[id];
      b.textContent = (expanded[id] ? "▾ " : "▸ ") + b.textContent.replace(/^[▸▾]\s/, "");
    });
  });
  // edit due date directly (writes to Jira)
  document.querySelectorAll(".due-edit").forEach(function (inp) {
    inp.addEventListener("click", function (e) { e.stopPropagation(); });
    inp.addEventListener("change", function () { editDueDate(inp.dataset.id, inp.value || null); });
  });
}

// Persist a due-date change to Jira via the relay (optimistic; revert on fail).
function persistDue(item, iso, prev) {
  if (window.TBWrite && window.TBWrite.setDueDate) {
    window.TBWrite.setDueDate(item, iso, function (ok, msg) {
      if (!ok) { item.due = prev; render(); toast("⚠️ " + (msg || "Jira write failed — reverted")); }
      else toast("✓ " + item.id + (iso ? " → " + iso : " → unscheduled"));
    });
  } else {
    toast("Moved " + item.id + (iso ? " → " + iso : " → unscheduled") + " (local only — connect Jira write in Edit Plan)");
  }
}

// Edit a due date from the date picker (tasks/stories).
function editDueDate(id, iso) {
  var t = findItem(id);
  if (!t || iso === t.due) return;
  var prev = t.due;
  t.due = iso || null;
  render();
  persistDue(t, iso || null, prev);
}

// --- view + navigation controls ---------------------------------------------
function setView(v) { viewMode = v; render(); }
function shiftView(dir) {
  if (viewMode === "day") anchor = addDays(anchor, dir);
  else if (viewMode === "week") anchor = addDays(anchor, 7 * dir);
  else anchor = new Date(anchor.getFullYear(), anchor.getMonth() + dir, 1);
  render();
}
function rangeLabel() {
  if (viewMode === "day") return anchor.toLocaleDateString("en-US", { weekday: "short", month: "long", day: "numeric", year: "numeric" });
  if (viewMode === "week") { var st = startOfWeek(anchor); return fmtShort(st) + " – " + fmtShort(addDays(st, 6)) + ", " + addDays(st, 6).getFullYear(); }
  return anchor.toLocaleDateString("en-US", { month: "long", year: "numeric" });
}
function updateControls() {
  var lab = document.getElementById("rangeLabel"); if (lab) lab.textContent = rangeLabel();
  document.querySelectorAll(".viewsw button").forEach(function (b) { b.classList.toggle("active", b.dataset.view === viewMode); });
}

// --- dept tabs + chrome ------------------------------------------------------
function renderTabs() {
  var box = document.getElementById("depttabs");
  var icon = { math: "📐", art: "🎨" };
  box.innerHTML = deptKeys.map(function (k) {
    return '<button class="dtab' + (k === curDept ? " active" : "") + '" data-dept="' + k + '">' +
      (icon[k] || "") + " " + esc(DATA.depts[k].label) + "</button>";
  }).join("");
  box.querySelectorAll(".dtab").forEach(function (b) {
    b.addEventListener("click", function () { curDept = b.dataset.dept; renderTabs(); render(); });
  });
}

function chrome() {
  var s = DATA.sprint || {};
  document.getElementById("subhead").textContent =
    "DAMS open sprint · drag tasks & subtasks onto days · Day/Week/Month views · Edit Plan to add/remove · IG + V2";
  document.getElementById("spinfo").textContent =
    (s.name || "Open sprint") + (s.start ? " · " + s.start + " – " + s.end : "") +
    (s.activeCount > 1 ? " · " + s.activeCount + " active sprints" : "");
  document.getElementById("footer").textContent =
    "Source: DAMS Jira · project in (IG, V2) AND sprint in openSprints() ORDER BY Rank · day = Due Date · refreshed " + (DATA.refreshed_at || "?");
}

// --- toast -------------------------------------------------------------------
var toastTimer;
function toast(msg) {
  var el = document.getElementById("toast");
  el.textContent = msg; el.classList.add("show");
  clearTimeout(toastTimer); toastTimer = setTimeout(function () { el.classList.remove("show"); }, 3200);
}
window.TBToast = toast;

// --- Edit Plan drawer --------------------------------------------------------
function renderEP() {
  var body = document.getElementById("epBody");
  var inS = tickets();
  function row(t) {
    return '<div class="ep-row in"><span class="k">' + esc(t.id) + '</span>' +
      '<span class="s">' + esc(t.summary) + '<br><span class="game">' + esc(t.release || t.game || "") +
      ' · ' + (t.est ? t.est + "h" : "no est") + '</span></span>' +
      '<button class="toggle rm" data-id="' + esc(t.id) + '">Remove</button></div>';
  }
  body.innerHTML =
    '<div class="ep-sec">Add a ticket to the open sprint</div>' +
    '<input class="ep-search" id="epAdd" placeholder="Type a Jira key (e.g. IG-1234) and press Enter">' +
    '<div class="ep-sec">In sprint — ' + esc(DATA.depts[curDept].label) + ' (' + inS.length + ')</div>' +
    inS.map(row).join("");
  body.querySelectorAll(".toggle.rm").forEach(function (btn) {
    btn.addEventListener("click", function () { removeFromSprint(btn.dataset.id); });
  });
  var add = document.getElementById("epAdd");
  add.addEventListener("keydown", function (e) { if (e.key === "Enter" && add.value.trim()) addToSprint(add.value.trim().toUpperCase()); });
}
function removeFromSprint(id) {
  if (window.TBWrite && window.TBWrite.removeFromSprint) window.TBWrite.removeFromSprint(id, afterPlanChange);
  else toast("Remove " + id + " (connect Jira write to apply)");
}
function addToSprint(id) {
  if (window.TBWrite && window.TBWrite.addToSprint) window.TBWrite.addToSprint(id, afterPlanChange);
  else toast("Add " + id + " (connect Jira write to apply)");
}
function afterPlanChange(ok, msg) { toast((ok ? "✓ " : "⚠️ ") + (msg || "")); }

function openDrawer() { document.getElementById("scrim").classList.add("open"); document.getElementById("drawer").classList.add("open"); renderEP(); }
function closeDrawer() { document.getElementById("scrim").classList.remove("open"); document.getElementById("drawer").classList.remove("open"); }

// --- boot --------------------------------------------------------------------
// Open the calendar where the work actually is: the due date carrying the most
// items (else today). Avoids landing on an empty month when everything is
// parked on the sprint-end date.
function busiestAnchor() {
  var tally = {};
  tickets().forEach(function (t) { if (t.due) tally[t.due] = (tally[t.due] || 0) + 1; });
  subtasksFlat().forEach(function (s) { if (s.due) tally[s.due] = (tally[s.due] || 0) + 1; });
  var best = null, n = 0;
  Object.keys(tally).forEach(function (iso) { if (tally[iso] > n) { n = tally[iso]; best = iso; } });
  return best ? parseISO(best) : new Date(TODAY);
}

function start() {
  if (!curDept) { document.getElementById("cal").innerHTML = '<p class="footer">No data — run team_board.py.</p>'; return; }
  chrome(); renderTabs();
  anchor = busiestAnchor();
  // calendar view + navigation controls
  document.querySelectorAll(".viewsw button").forEach(function (b) {
    b.addEventListener("click", function () { setView(b.dataset.view); });
  });
  document.getElementById("navPrev").addEventListener("click", function () { shiftView(-1); });
  document.getElementById("navNext").addEventListener("click", function () { shiftView(1); });
  document.getElementById("navToday").addEventListener("click", function () { anchor = new Date(TODAY); render(); });
  document.getElementById("navSprint").addEventListener("click", function () { if (SPRINT_START) { anchor = new Date(SPRINT_START); render(); } });
  render();
  document.getElementById("editPlanBtn").addEventListener("click", openDrawer);
  document.getElementById("epClose").addEventListener("click", closeDrawer);
  document.getElementById("scrim").addEventListener("click", closeDrawer);
}
if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start); else start();
})();
