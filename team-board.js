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

// --- date helpers ------------------------------------------------------------
function parseISO(s) { var p = (s || "").split("-"); return p.length === 3 ? new Date(+p[0], +p[1] - 1, +p[2]) : null; }
function fmtShort(d) { return d.toLocaleDateString("en-US", { month: "short", day: "numeric" }); }
function sameDay(a, b) { return a && b && a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate(); }
function isWeekend(d) { var g = d.getDay(); return g === 0 || g === 6; }

// Build the sprint day list: [{idx, date, dow, weekend, today}]
function buildDays() {
  var s = DATA.sprint;
  if (!s || !s.start || !s.end) return [];
  var start = parseISO(s.start), end = parseISO(s.end), today = new Date();
  var out = [], i = 0, d = new Date(start);
  while (d <= end && i < 60) {
    out.push({ idx: i, date: new Date(d), dow: DOW[d.getDay()], weekend: isWeekend(d), today: sameDay(d, today) });
    d.setDate(d.getDate() + 1); i++;
  }
  return out;
}
var DAYS = buildDays();

function tickets() { return (DATA.depts[curDept] && DATA.depts[curDept].tickets) || []; }

// --- rendering ---------------------------------------------------------------
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
  var locked = t.is_subtask ? "" : " locked";              // only subtasks are drag-persisted (guardrail)
  var rel = t.release || t.game || "";
  var bk = esc(t.bucket || "todo");
  var rem = t.remaining;
  var remHtml = (rem > 0)
    ? '<span class="rem has" title="Remaining estimate">' + rem + "h left</span>"
    : '<span class="rem none" title="No remaining estimate">' + (t.est ? "0h left" : "no est") + "</span>";
  var statHtml = '<span class="stat st-' + bk + '" title="' + esc(t.status || "") + '">' +
    '<span class="dot b-' + bk + '"></span>' + esc(t.status || "—") + "</span>";
  return '<div class="pill ' + curDept + locked + '" draggable="' + (t.is_subtask ? "true" : "false") + '" data-id="' + esc(t.id) + '"' +
    (t.is_subtask ? "" : ' title="Tasks aren\'t day-planned — only their subtasks (protects target dates)"') + '>' +
    '<div class="pill-top"><a class="pill-key" href="' + esc(t.url) + '" target="_blank" rel="noopener">' + esc(t.id) + "</a>" +
      remHtml + "</div>" +
    '<div class="pill-sum">' + esc(t.summary) + "</div>" +
    '<div class="pill-bot">' + statHtml + flagHtml(t) + "</div>" +
    '<div class="pill-bot"><span class="pill-meta">' + who + "</span>" +
      '<span class="game">' + esc(rel) + "</span></div></div>";
}

function render() {
  // calendar grid: align weeks Mon–Sun
  var cal = document.getElementById("cal");
  if (!DAYS.length) { cal.innerHTML = '<p class="footer">No active sprint window found.</p>'; }
  else {
    var lead = (DAYS[0].date.getDay() + 6) % 7;   // Mon=0
    var cells = [];
    for (var l = 0; l < lead; l++) cells.push(null);
    DAYS.forEach(function (d) { cells.push(d); });
    while (cells.length % 7 !== 0) cells.push(null);
    var html = "", wk = 0;
    for (var i = 0; i < cells.length; i += 7) {
      wk++;
      html += '<div class="week-lbl">Week ' + wk + "</div><div class=\"week\">";
      for (var j = i; j < i + 7; j++) {
        var c = cells[j];
        if (!c) { html += '<div class="day empty"></div>'; continue; }
        html += '<div class="day' + (c.today ? " today" : "") + (c.weekend ? " weekend" : "") + '">' +
          '<div class="day-head"><span><span class="day-date">' + fmtShort(c.date) + '</span> ' +
          '<span class="day-dow">' + c.dow + '</span></span><span class="cap" data-cap="' + c.idx + '"></span></div>' +
          '<div class="day-body" data-day="' + c.idx + '"></div></div>';
      }
      html += "</div>";
    }
    cal.innerHTML = html;
  }

  // place pills
  var bl = document.getElementById("backlog");
  bl.innerHTML = "";
  var list = tickets(), blCount = 0;
  list.forEach(function (t) {
    var zone = (t.day == null) ? bl : cal.querySelector('.day-body[data-day="' + t.day + '"]');
    if (!zone) { zone = bl; }                     // day fell outside window → backlog
    if (zone === bl) blCount++;
    zone.insertAdjacentHTML("beforeend", pillHtml(t));
  });
  document.getElementById("blCount").textContent = blCount;

  // capacity per day = sum of REMAINING hours (outstanding work on that day)
  DAYS.forEach(function (d) {
    var sum = list.filter(function (t) { return t.day === d.idx; }).reduce(function (a, t) { return a + (t.remaining || 0); }, 0);
    var el = cal.querySelector('.cap[data-cap="' + d.idx + '"]');
    if (!el) return;
    sum = Math.round(sum);
    el.textContent = sum + "h";
    el.className = "cap " + (sum > DAILY_CAP ? "over" : (sum >= DAILY_CAP * 0.75 ? "warn" : "ok"));
  });

  wireDrag();
}

// --- drag & drop -------------------------------------------------------------
function wireDrag() {
  document.querySelectorAll(".pill[draggable='true']").forEach(function (p) {
    p.addEventListener("dragstart", function (e) { curDrag = p.dataset.id; p.classList.add("dragging"); e.dataTransfer.effectAllowed = "move"; });
    p.addEventListener("dragend", function () { p.classList.remove("dragging"); });
  });
  document.querySelectorAll(".day-body, #backlog").forEach(function (z) {
    z.addEventListener("dragover", function (e) { e.preventDefault(); z.classList.add("drop-hot"); });
    z.addEventListener("dragleave", function () { z.classList.remove("drop-hot"); });
    z.addEventListener("drop", function (e) {
      e.preventDefault(); z.classList.remove("drop-hot");
      var t = tickets().find(function (x) { return x.id === curDrag; });
      if (!t) return;
      var raw = z.dataset.day;
      var newDay = (raw === "backlog") ? null : parseInt(raw, 10);
      if (newDay === t.day) return;
      var prevDay = t.day;
      t.day = newDay;                       // optimistic
      render();
      persistDay(t, newDay, prevDay);
    });
  });
}

// Persist a day move to Jira (Due Date on the subtask). Filled in by the relay
// (team-board-write.js sets window.TBWrite). Falls back to a local-only toast.
function persistDay(ticket, newDay, prevDay) {
  var dateStr = (newDay == null) ? null : isoForDay(newDay);
  if (window.TBWrite && window.TBWrite.setDueDate) {
    window.TBWrite.setDueDate(ticket, dateStr, function (ok, msg) {
      if (!ok) { ticket.day = prevDay; render(); toast("⚠️ " + (msg || "Jira write failed — reverted")); }
      else toast("✓ " + ticket.id + (dateStr ? " → " + dateStr : " → unscheduled"));
    });
  } else {
    toast("Moved " + ticket.id + (dateStr ? " → " + dateStr : " → unscheduled") + " (local only — connect Jira write in Edit Plan)");
  }
}
function isoForDay(idx) {
  var d = DAYS.find(function (x) { return x.idx === idx; });
  if (!d) return null;
  var dt = d.date, m = ("0" + (dt.getMonth() + 1)).slice(-2), day = ("0" + dt.getDate()).slice(-2);
  return dt.getFullYear() + "-" + m + "-" + day;
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
    "DAMS open sprint · drag subtasks onto days · Edit Plan to add/remove work · IG + V2";
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
function start() {
  if (!curDept) { document.getElementById("cal").innerHTML = '<p class="footer">No data — run team_board.py.</p>'; return; }
  chrome(); renderTabs(); render();
  document.getElementById("editPlanBtn").addEventListener("click", openDrawer);
  document.getElementById("epClose").addEventListener("click", closeDrawer);
  document.getElementById("scrim").addEventListener("click", closeDrawer);
}
if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start); else start();
})();
