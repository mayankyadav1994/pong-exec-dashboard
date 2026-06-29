/* ============================================================
   Team Board — Jira write relay (client side).
   Defines window.TBWrite. Each call fires a GitHub repository_dispatch; the
   apply-plan.yml workflow (which holds the Jira token) performs the actual
   Jira write. The token is never in the page, and Jira-Cloud CORS is avoided.

   Writes are QUEUED: a 204 from GitHub means "accepted, applying in ~1 min".
   The page already updated optimistically; the daily refresh reconciles.
   Auth: a GitHub PAT with `repo` scope, entered once per session.
   ============================================================ */
(function () {
"use strict";

var GH_OWNER = "mayankyadav1994", GH_REPO = "pong-exec-dashboard";
var DISPATCH_URL = "https://api.github.com/repos/" + GH_OWNER + "/" + GH_REPO + "/dispatches";
var PAT_KEY = "tb_github_pat";   // sessionStorage

function getPat() {
  var pat = "";
  try { pat = sessionStorage.getItem(PAT_KEY) || ""; } catch (e) {}
  if (!pat) {
    pat = window.prompt(
      "Enter a GitHub Personal Access Token with 'repo' scope to apply changes to Jira.\n" +
      "(Stored only for this browser session.)") || "";
    pat = pat.trim();
    if (pat) { try { sessionStorage.setItem(PAT_KEY, pat); } catch (e) {} }
  }
  return pat;
}

function dispatch(payload, cb) {
  var pat = getPat();
  if (!pat) { cb && cb(false, "no GitHub token — change not applied"); return; }
  fetch(DISPATCH_URL, {
    method: "POST",
    headers: {
      "Authorization": "Bearer " + pat,
      "Accept": "application/vnd.github+json",
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ event_type: "team-board-apply", client_payload: payload }),
  }).then(function (r) {
    if (r.status === 204) { cb && cb(true, "queued — applies to Jira in ~1 min"); return; }
    if (r.status === 401 || r.status === 403) {
      try { sessionStorage.removeItem(PAT_KEY); } catch (e) {}
      cb && cb(false, "GitHub auth failed (token needs 'repo' scope) — re-enter on next change");
      return;
    }
    r.text().then(function (t) { cb && cb(false, "GitHub error " + r.status + ": " + t.slice(0, 120)); });
  }).catch(function (e) { cb && cb(false, "network error: " + e.message); });
}

window.TBWrite = {
  setDueDate: function (ticket, dateStr, cb) {
    dispatch({ action: "setDueDate", key: ticket.id, date: dateStr }, cb);
  },
  setDates: function (ticket, startStr, dueStr, cb) {
    dispatch({ action: "setDates", key: ticket.id, start: startStr, due: dueStr }, cb);
  },
  addToSprint: function (key, cb) {
    dispatch({ action: "addToSprint", key: key }, cb);
  },
  removeFromSprint: function (key, cb) {
    dispatch({ action: "removeFromSprint", key: key }, cb);
  },
};
})();
