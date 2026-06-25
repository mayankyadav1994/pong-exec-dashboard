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

function subRow(s) {
  var bk = esc(s.bucket || "todo");
  var rem = (s.remaining > 0) ? s.remaining + "h" : "—";
  return '<div class="sub-item">' +
    '<a class="sub-key" href="' + esc(s.url) + '" target="_blank" rel="noopener">' + esc(s.id) + "</a>" +
    '<span class="sub-sum" title="' + esc(s.summary) + '">' + esc(s.summary) + "</span>" +
    '<span class="stat st-' + bk + '"><span class="dot b-' + bk + '"></span>' + esc(s.status || "—") + "</span>" +
    '<span class="sub-rem">' + rem + "</span></div>";
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
    p.addEventListener("dragstart", function (e) {
      if (e.target.closest("input, button, a")) { e.preventDefault(); return; }  // don't drag from controls/links
      curDrag = p.dataset.id; p.classList.add("dragging"); e.dataTransfer.effectAllowed = "move";
    });
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
      t.due = (newDay == null) ? null : isoForDay(newDay);
      render();
      persistDay(t, newDay, prevDay);
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
function dayForIso(iso) {
  if (!iso) return null;
  var d = DAYS.find(function (x) { return isoForDay(x.idx) === iso; });
  return d ? d.idx : null;     // outside the sprint window → backlog (date still set in Jira)
}

// Edit a pill's due date directly from the date picker. Writes to Jira; the
// pill jumps to that day (or backlog if the date is outside the sprint).
function editDueDate(id, iso) {
  var t = tickets().find(function (x) { return x.id === id; });
  if (!t) return;
  if (iso === t.due) return;
  var prevDue = t.due, prevDay = t.day;
  t.due = iso || null;
  t.day = dayForIso(iso);
  render();
  if (window.TBWrite && window.TBWrite.setDueDate) {
    window.TBWrite.setDueDate(t, iso || null, function (ok, msg) {
      if (!ok) { t.due = prevDue; t.day = prevDay; render(); toast("⚠️ " + (msg || "Jira write failed — reverted")); }
      else toast("✓ " + t.id + (iso ? " due " + iso : " due cleared"));
    });
  } else {
    toast("Set " + t.id + (iso ? " due " + iso : " cleared") + " (local only)");
  }
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
