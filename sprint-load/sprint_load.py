#!/usr/bin/env python3
"""
sprint_load.py - sprint capacity dashboard generator
====================================================

Pulls open-sprint tickets for four boards from Jira Cloud, aggregates hours per
person, and writes a single self-contained ``dashboard.html`` beside this script
(``template.html`` with the ``__SNAPSHOTS__`` placeholder replaced by inlined
JSON). The page makes no network calls at render time except Google Fonts.

    python sprint_load.py            # normal run
    python sprint_load.py --seed     # one-off: seed bias.json from each board's
                                     # last closed sprint, then do a normal run

Credentials: JIRA_EMAIL + JIRA_TOKEN from the environment (JIRA_API_TOKEN is
accepted as a fallback because the other Pong dashboards use that name).

Runtime deps: Python 3.11 + requests. Nothing else.
See README.md for what every number means.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import requests

# ════════════════════════════════════════════════════════════════════════════
#  HAND-MAINTAINED CONFIGURATION - edit these, nothing below needs to change
# ════════════════════════════════════════════════════════════════════════════

SITE = "https://ponggamestudios.atlassian.net"
SPRINT_FIELD = "customfield_10103"   # array of sprint objects; NOT customfield_10020

# ── DAMS roster ─────────────────────────────────────────────────────────────
# The 12 people whose work is counted on the DAMS board (and excluded from every
# project board). Copied from the assignee clause of board 349's saved filter
# on 2026-11-10. If someone is missing from the dashboard, check this first.
ROSTER: dict[str, str] = {
    "712020:dbae655b-7d4f-4a01-a4e2-8d21d54b534e": "John Di Mauro",
    "62b98f18228c59d8da18ba1e":                    "Sonali Mehra",
    "557058:b6a5af78-ca25-479e-8409-4867599d1974": "Amanda Langford",
    "62deb0e92fe585febb3b738b":                    "Seyeon Oh",
    "712020:8bc52dab-f9c9-4044-898b-c7d758b00b9c": "Roopesh Nair Perayil",
    "712020:aec68cc3-5c35-4bb9-96cf-59a41347e4e9": "Christopher Coey",
    "712020:2eeacc30-2c87-41c2-b778-1999a985649d": "Joel Kazmi",
    "557058:303029d5-4773-433d-97c5-527194b72a41": "George Donciu",
    "712020:cf48926d-5b86-4e89-b72c-a06762358e79": "James Zhang",
    "712020:6a098f57-08e4-4320-b109-27b2228a54df": "Leonid Romanov",
    "712020:876395be-907b-41aa-a83a-057f8bfb9047": "Christian DiCarlo",
    "712020:d2ccfe31-a230-49f6-8adb-c8010c3e0740": "Jen Johnson",
}

# ── Boards ──────────────────────────────────────────────────────────────────
BOARDS: list[dict] = [
    {"slug": "dams", "label": "DAMS board",           "board_id": 349, "project": None},
    {"slug": "ig",   "label": "iGaming board",        "board_id": 250, "project": "IG"},
    {"slug": "v2",   "label": "V2 board",             "board_id": 316, "project": "V2"},
    {"slug": "css",  "label": "Cloud Services board", "board_id": 217, "project": "CSS"},
]
DAMS_PROJECTS = ("IG", "V2", "CSS")

# DAMS issue types. Matches board 349's own filter, which includes Live Issue.
# Roster tickets of any other type are dropped AND reported by the tripwire.
DAMS_ISSUE_TYPES: tuple[str, ...] = (
    "Creative Task", "Creative Subtask", "Math Task", "Math Subtask",
    "Sound Subtask", "Sound Task", "Design Task", "Design Sub-Task",
    "Bug", "Story", "Live Issue",
)

# ── Per-board exclusions ────────────────────────────────────────────────────
# Scoped to the named board ONLY. These people belong to other teams; their own
# boards are unaffected. Never make this global.
BOARD_EXCLUSIONS: dict[str, dict[str, str]] = {
    "ig": {
        "61d344270586a2006940c9eb": "Ehsan Mamakani",
        "5e6540a338b8bd0cf890e9f4": "Yves Griber",
        "5b96f67963c47c2c7d3e5f46": "Boris Itskov",
    },
}

# ── Capacity & thresholds ───────────────────────────────────────────────────
CAPACITY_H = 70.0             # planned hours per person per sprint
UNPLANNED_H = 10.0            # separate unplanned allowance - sits OUTSIDE the 70, never deducted
AT_CAPACITY_BAND_H = 3.5      # |load - capacity| <= this  -> "at capacity"
NEAR_CAPACITY_PCT = 0.85      # load bar turns amber from this share of capacity

BIAS_MIN_CLOSED_EST_H = 40.0  # closed original estimate needed to measure bias
BIAS_SANE_RANGE = (0.25, 4.0) # a measured factor outside this is rejected and flagged
BIAS_UNESTIMATED_SPENT_FLAG = 0.10  # flag if >10% of closed spent sits on unestimated tickets
OUTLIER_REMAINING_MULT = 3.0  # flag anyone whose remaining exceeds this multiple of capacity
CLOSED_STATUS = "Closed"      # the only status that counts as closed for bias

# ── Discipline mapping ──────────────────────────────────────────────────────
DAMS_DISCIPLINE_PREFIXES = ("Math", "Sound", "Creative", "Design")
PROJECT_DISCIPLINE_TYPES: dict[str, tuple[str, ...]] = {
    "Dev":     ("Dev Task", "Dev Subtask", "Story", "Bug", "Enhancement", "CR", "Live Issue"),
    "QA":      ("QA Task", "QA Subtask"),
    "Release": ("Release", "Release Subtask"),
}
DISCIPLINE_ORDER = ["Math", "Sound", "Creative", "Design", "Dev", "QA", "Release", "Unclassified"]

# ── Paging guard ────────────────────────────────────────────────────────────
PAGE_SIZE = 100       # Jira's cap for /search/jql
MAX_PAGES = 60        # 6,000 issues per query - beyond this something is wrong
HTTP_TIMEOUT_S = 60

# ════════════════════════════════════════════════════════════════════════════

HERE = Path(__file__).resolve().parent
TEMPLATE_PATH = HERE / "template.html"
OUTPUT_PATH = HERE / "dashboard.html"
BIAS_PATH = HERE / "bias.json"
PLACEHOLDER = "__SNAPSHOTS__"

FIELDS = ["issuetype", "status", "assignee", SPRINT_FIELD,
          "timeoriginalestimate", "timespent", "timeestimate", "resolutiondate"]

LINK_PREDICATES = {
    "assigned":    "originalEstimate > 0",
    "spent":       "timeSpent > 0",
    # Closed tickets' remaining is counted as 0, so the link leaves them out too.
    "remaining":   f'remainingEstimate > 0 AND status != "{CLOSED_STATUS}"',
    "no_estimate": "originalEstimate IS EMPTY",
    "closed_rem":  f'status = "{CLOSED_STATUS}" AND remainingEstimate > 0',
    "closed":      f'status = "{CLOSED_STATUS}"',
}

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


class BuildError(Exception):
    """Anything that must stop the run before a file is written."""


# ── small helpers ───────────────────────────────────────────────────────────

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_ts(s: str | None) -> datetime | None:
    """Jira timestamps: '2026-11-09T05:00:00.000Z' or '...000+1100'."""
    if not s:
        return None
    s = s.replace("Z", "+00:00")
    if len(s) >= 5 and s[-5] in "+-" and s[-4:].isdigit():      # +HHMM -> +HH:MM
        s = s[:-2] + ":" + s[-2:]
    try:
        d = datetime.fromisoformat(s)
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def hours(seconds) -> float:
    return (seconds or 0) / 3600.0


def r1(x: float) -> float:
    return round(x + 0.0, 1)


def jql_ids(ids) -> str:
    """Account IDs contain colons - every one must be quoted inside JQL."""
    return ", ".join(f'"{i}"' for i in ids)


def jql_strs(items) -> str:
    return ", ".join(f'"{s}"' for s in items)


def nav(jql: str) -> str:
    return f"{SITE}/issues/?jql={quote(jql, safe='')}"


def _env(name: str) -> str | None:
    """Process env first; on Windows fall back to the user environment block
    (a terminal started before the variable was created has a stale env)."""
    v = os.environ.get(name)
    if v:
        return v
    if sys.platform == "win32":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
                return winreg.QueryValueEx(k, name)[0] or None
        except OSError:
            return None
    return None


def credentials() -> tuple[str, str]:
    email = _env("JIRA_EMAIL")
    token, src = _env("JIRA_TOKEN"), "JIRA_TOKEN"
    if not token:
        token, src = _env("JIRA_API_TOKEN"), "JIRA_API_TOKEN"
    missing = [n for n, v in (("JIRA_EMAIL", email), ("JIRA_TOKEN", token)) if not v]
    if missing:
        raise BuildError(
            "Missing Jira credentials: " + ", ".join(missing) + " not set.\n"
            "  Set JIRA_EMAIL and JIRA_TOKEN (an Atlassian API token) in the environment.")
    if src != "JIRA_TOKEN":
        print(f"  note: JIRA_TOKEN not set - using {src}")
    return email, token


# ── Jira client ─────────────────────────────────────────────────────────────

class Jira:
    def __init__(self, email: str, token: str):
        self.s = requests.Session()
        self.s.auth = (email, token)
        self.s.headers.update({"Accept": "application/json"})

    def _req(self, method: str, url: str, **kw) -> dict:
        for attempt in range(4):
            try:
                r = self.s.request(method, url, timeout=HTTP_TIMEOUT_S, **kw)
            except requests.RequestException as e:
                raise BuildError(f"Network error calling Jira ({method} {url}): {e}") from e
            if r.status_code == 429 and attempt < 3:          # rate limit: wait, retry
                wait = min(float(r.headers.get("Retry-After") or 5), 60)
                print(f"  rate-limited by Jira, waiting {wait:.0f}s")
                time.sleep(wait)
                continue
            if r.status_code != 200:
                hint = {401: " (bad email/token)", 403: " (token lacks permission)",
                        400: " (JQL rejected - check quoting / issue type names)"}.get(r.status_code, "")
                raise BuildError(f"Jira returned HTTP {r.status_code}{hint} for {method} {url}\n"
                                 f"  body: {r.text[:600]}")
            return r.json()
        raise BuildError("Jira kept rate-limiting; giving up.")

    def search(self, jql: str, fields: list[str]) -> list[dict]:
        """POST /rest/api/3/search/jql, paging on nextPageToken / isLast."""
        out, token, seen = [], None, set()
        for _ in range(MAX_PAGES):
            body = {"jql": jql, "maxResults": PAGE_SIZE, "fields": fields}
            if token:
                body["nextPageToken"] = token
            d = self._req("POST", f"{SITE}/rest/api/3/search/jql", json=body)
            out.extend(d.get("issues") or [])
            if d.get("isLast", True):
                return out
            token = d.get("nextPageToken")
            if not token:
                raise BuildError("Jira said isLast=false but returned no nextPageToken.")
            if token in seen:
                raise BuildError("Jira returned a repeated nextPageToken - aborting runaway paging.")
            seen.add(token)
        raise BuildError(f"Paging exceeded {MAX_PAGES} pages ({len(out)} issues) for:\n  {jql}")

    def user(self, account_id: str) -> dict:
        return self._req("GET", f"{SITE}/rest/api/3/user", params={"accountId": account_id})

    def closed_sprints(self, board_id: int) -> list[dict]:
        out, start = [], 0
        for _ in range(MAX_PAGES):
            d = self._req("GET", f"{SITE}/rest/agile/1.0/board/{board_id}/sprint",
                          params={"state": "closed", "startAt": start, "maxResults": 50})
            vals = d.get("values") or []
            out.extend(vals)
            if d.get("isLast", True) or not vals:
                return out
            start += len(vals)
        raise BuildError(f"Sprint listing for board {board_id} exceeded {MAX_PAGES} pages.")


# ── JQL ─────────────────────────────────────────────────────────────────────

def board_jql(board: dict, sprint_clause: str = "sprint IN openSprints()") -> str:
    """The pull query. DAMS is NOT type-filtered here - the filter runs in
    Python so the tripwire can see what it drops."""
    if board["slug"] == "dams":
        return (f"project IN ({', '.join(DAMS_PROJECTS)}) AND {sprint_clause} "
                f"AND assignee IN ({jql_ids(ROSTER)})")
    return (f"project = {board['project']} AND {sprint_clause} "
            f"AND assignee IS NOT EMPTY AND assignee NOT IN ({jql_ids(ROSTER)})")


def scope_jql(board: dict) -> str:
    """What the board counts, as one JQL (for the board-level link)."""
    if board["slug"] == "dams":
        return board_jql(board) + f" AND issuetype IN ({jql_strs(DAMS_ISSUE_TYPES)})"
    excl = BOARD_EXCLUSIONS.get(board["slug"])
    base = board_jql(board)
    if excl:
        base = base.replace(f"NOT IN ({jql_ids(ROSTER)})", f"NOT IN ({jql_ids(list(ROSTER) + list(excl))})")
    return base


def person_jql(board: dict, acct: str, predicate: str | None = None) -> str:
    if board["slug"] == "dams":
        parts = [f"project IN ({', '.join(DAMS_PROJECTS)})", "sprint IN openSprints()",
                 f"issuetype IN ({jql_strs(DAMS_ISSUE_TYPES)})"]
    else:
        parts = [f"project = {board['project']}", "sprint IN openSprints()"]
    parts.append(f'assignee = "{acct}"')
    if predicate:
        parts.append(predicate)
    return " AND ".join(parts)


def person_links(board: dict, acct: str) -> dict:
    links = {"person": nav(person_jql(board, acct))}
    for k, pred in LINK_PREDICATES.items():
        links[k] = nav(person_jql(board, acct, pred))
    return links


# ── normalise & classify ────────────────────────────────────────────────────

def norm(issue: dict) -> dict:
    f = issue.get("fields") or {}
    a = f.get("assignee") or {}
    st = f.get("status") or {}
    active = [s for s in (f.get(SPRINT_FIELD) or [])
              if isinstance(s, dict) and s.get("state") == "active"]
    return {
        "key": issue["key"],
        "type": (f.get("issuetype") or {}).get("name") or "?",
        "status": st.get("name") or "?",
        "status_cat": (st.get("statusCategory") or {}).get("key"),
        "account": a.get("accountId"),
        "name": a.get("displayName") or a.get("accountId") or "Unassigned",
        "orig_s": f.get("timeoriginalestimate"),
        "spent_s": f.get("timespent"),
        "rem_s": f.get("timeestimate"),
        "resolved": f.get("resolutiondate"),
        "active_sprints": active,
    }


def discipline_of(slug: str, itype: str) -> str | None:
    if slug != "dams":
        for d, types in PROJECT_DISCIPLINE_TYPES.items():
            if itype in types:
                return d
    for p in DAMS_DISCIPLINE_PREFIXES:
        if itype.startswith(p):
            return p
    return None


def pick_discipline(votes: Counter) -> tuple[str, bool]:
    if not votes:
        return "Unclassified", False
    top = max(votes.values())
    winners = [d for d in DISCIPLINE_ORDER if votes.get(d) == top]
    return winners[0], len(winners) > 1


# ── per-board aggregation ───────────────────────────────────────────────────

def aggregate(board: dict, issues: list[dict]) -> dict[str, dict]:
    people: dict[str, dict] = {}
    for i in issues:
        acct = i["account"]
        p = people.get(acct)
        if p is None:
            p = people[acct] = {
                "account": acct,
                "name": ROSTER.get(acct) if board["slug"] == "dams" else i["name"],
                "tickets": 0, "no_estimate": 0,
                "assigned_h": 0.0, "spent_h": 0.0, "remaining_h": 0.0,
                "closed_tickets": 0, "closed_est_h": 0.0, "closed_spent_h": 0.0,
                "closed_spent_unestimated_h": 0.0,
                "closed_rem": {"count": 0, "hours": 0.0, "keys": []},
                "_votes": Counter(), "_types": Counter(),
            }
            p["name"] = p["name"] or i["name"]
        o, s, r = hours(i["orig_s"]), hours(i["spent_s"]), hours(i["rem_s"])
        p["tickets"] += 1
        if i["orig_s"] is None:
            p["no_estimate"] += 1
        p["assigned_h"] += o
        p["spent_h"] += s
        if i["status"] == CLOSED_STATUS:
            p["closed_tickets"] += 1
            p["closed_est_h"] += o
            p["closed_spent_h"] += s
            if not i["orig_s"]:
                p["closed_spent_unestimated_h"] += s
            if r > 0:                         # closed with remaining: counted as 0, flagged
                p["closed_rem"]["count"] += 1
                p["closed_rem"]["hours"] += r
                p["closed_rem"]["keys"].append(i["key"])
        else:
            p["remaining_h"] += r
        p["_types"][i["type"]] += 1
        d = discipline_of(board["slug"], i["type"])
        if d:
            p["_votes"][d] += 1

    for acct, p in people.items():
        disc, tie = pick_discipline(p["_votes"])
        p["discipline"] = disc
        p["discipline_votes"] = dict(p["_votes"].most_common())
        p["discipline_tie"] = tie
        p["issue_types"] = dict(p.pop("_types").most_common())
        p.pop("_votes")
        for k in ("assigned_h", "spent_h", "remaining_h", "closed_est_h",
                  "closed_spent_h", "closed_spent_unestimated_h"):
            p[k] = r1(p[k])
        p["closed_rem"]["hours"] = r1(p["closed_rem"]["hours"])
        p["links"] = person_links(board, acct)
    return people


def sprint_summary(issues: list[dict]) -> list[dict]:
    seen: dict[int, dict] = {}
    for i in issues:
        for s in i["active_sprints"]:
            e = seen.setdefault(s.get("id"), {
                "id": s.get("id"), "name": s.get("name"), "board_id": s.get("boardId"),
                "start": s.get("startDate"), "end": s.get("endDate"), "tickets": 0})
            e["tickets"] += 1
    return sorted(seen.values(), key=lambda e: (-e["tickets"], e["name"] or ""))


def warn(level: str, title: str, detail: str = "", keys=None, url: str | None = None) -> dict:
    return {"level": level, "title": title, "detail": detail,
            "keys": list(keys or [])[:40], "key_count": len(keys or []), "url": url}


def build_board(jira: Jira, board: dict) -> dict:
    slug = board["slug"]
    t0 = time.time()
    pulled_start = now_iso()
    raw = jira.search(board_jql(board), FIELDS)
    pulled_end = now_iso()
    secs = time.time() - t0

    issues, dupes, seen = [], [], set()
    for x in raw:
        if x["key"] in seen:
            dupes.append(x["key"])
            continue
        seen.add(x["key"])
        issues.append(norm(x))
    pulled = len(issues)
    warnings: list[dict] = []

    if dupes:
        warnings.append(warn("warn", f"{len(dupes)} duplicate issue(s) across pages",
                             "Jira returned the same key on more than one page; duplicates were dropped.", dupes))

    # ── DAMS type filter + tripwire ─────────────────────────────────────────
    tripwire = None
    if slug == "dams":
        outside = [i for i in issues if i["type"] not in DAMS_ISSUE_TYPES]
        issues = [i for i in issues if i["type"] in DAMS_ISSUE_TYPES]
        tripwire = {"count": len(outside),
                    "types": dict(Counter(i["type"] for i in outside).most_common()),
                    "remaining_h": r1(sum(hours(i["rem_s"]) for i in outside if i["status"] != CLOSED_STATUS)),
                    "keys": [i["key"] for i in outside]}
        if outside:
            tjql = board_jql(board) + f" AND issuetype NOT IN ({jql_strs(DAMS_ISSUE_TYPES)})"
            warnings.append(warn(
                "alert", f"Tripwire: {len(outside)} roster ticket(s) dropped by the issue-type filter",
                "These are in the open sprint, assigned to the DAMS roster, but their type is outside the "
                f"DAMS set, so they are NOT counted anywhere: "
                + ", ".join(f"{t} ×{n}" for t, n in tripwire["types"].items())
                + f" ({tripwire['remaining_h']}h remaining).", tripwire["keys"], nav(tjql)))

    # ── per-board exclusions ────────────────────────────────────────────────
    excl = BOARD_EXCLUSIONS.get(slug, {})
    excluded_info = []
    if excl:
        for acct, nm in excl.items():
            mine = [i for i in issues if i["account"] == acct]
            excluded_info.append({"name": nm, "tickets": len(mine),
                                  "remaining_h": r1(sum(hours(i["rem_s"]) for i in mine if i["status"] != CLOSED_STATUS))})
        issues = [i for i in issues if i["account"] not in excl]
        warnings.append(warn(
            "info", f"Board rule: {len(excl)} people excluded from this board only",
            "; ".join(f"{e['name']} ({e['tickets']} tickets, {e['remaining_h']}h remaining)" for e in excluded_info)
            + ". They belong to other teams and still appear on their own boards."))

    # ── data-quality checks ─────────────────────────────────────────────────
    cr = [i for i in issues if i["status"] == CLOSED_STATUS and (i["rem_s"] or 0) > 0]
    if cr:
        warnings.append(warn(
            "warn", f"{len(cr)} Closed ticket(s) still carry {r1(sum(hours(i['rem_s']) for i in cr))}h remaining",
            "Counted as 0 remaining (no work is left on a closed ticket). Zero the Remaining Estimate in Jira to clear this.",
            [i["key"] for i in cr],
            nav(scope_jql(board) + " AND " + LINK_PREDICATES["closed_rem"])))

    done_other = [i for i in issues if i["status_cat"] == "done" and i["status"] != CLOSED_STATUS]
    if done_other:
        sts = Counter(i["status"] for i in done_other)
        warnings.append(warn(
            "warn", f"{len(done_other)} done-category ticket(s) not in status \"{CLOSED_STATUS}\"",
            "Statuses: " + ", ".join(f"{s} ×{n}" for s, n in sts.items())
            + f". Only \"{CLOSED_STATUS}\" counts as closed for estimate bias, so these are left out of it; "
              "their remaining still counts toward load.", [i["key"] for i in done_other]))

    def closed_before_start(i: dict) -> bool:
        starts = [parse_ts(s.get("startDate")) for s in i["active_sprints"]]
        starts = [s for s in starts if s]
        res = parse_ts(i["resolved"])
        return i["status"] == CLOSED_STATUS and bool(res) and bool(starts) and res < min(starts)
    stale = [i for i in issues if closed_before_start(i)]
    if stale:
        warnings.append(warn(
            "warn", f"{len(stale)} ticket(s) were already Closed before this sprint started",
            f"{r1(sum(hours(i['orig_s']) for i in stale))}h estimate / {r1(sum(hours(i['spent_s']) for i in stale))}h spent. "
            "They are still attached to the open sprint, so they inflate Tickets, Assigned and Spent and they feed "
            "estimate bias. Remaining, verdicts and intake are unaffected (closed remaining counts as 0). "
            "Likely cause: closed tickets are carried from sprint to sprint instead of staying in the sprint they closed in.",
            [i["key"] for i in stale]))

    no_sprint = [i["key"] for i in issues if not i["active_sprints"]]
    multi = [i["key"] for i in issues if len(i["active_sprints"]) > 1]
    if no_sprint:
        warnings.append(warn("warn", f"{len(no_sprint)} ticket(s) with no active sprint in {SPRINT_FIELD}",
                             "openSprints() matched them but the sprint field shows none active.", no_sprint))
    if multi:
        warnings.append(warn("warn", f"{len(multi)} ticket(s) in more than one active sprint",
                             "Hours are counted once, but the sprint window is ambiguous.", multi))

    sprints = sprint_summary(issues)
    if slug != "dams":
        off = [s for s in sprints if s["board_id"] != board["board_id"]]
        if off:
            warnings.append(warn("warn", "Tickets sit in another board's sprint",
                                 "; ".join(f"{s['name']} (board {s['board_id']}, {s['tickets']} tickets)" for s in off)))
    ends = sorted({(s["end"] or "")[:10] for s in sprints if s.get("end")})
    if len(ends) > 1:
        d = (datetime.fromisoformat(ends[-1]) - datetime.fromisoformat(ends[0])).days
        if d > 2:
            warnings.append(warn("warn", "Sprints on this board end on different dates",
                                 f"Ends: {', '.join(ends)}. The 70h capacity assumes one sprint window."))

    people = aggregate(board, issues)

    if slug == "dams":
        idle = [nm for acct, nm in ROSTER.items() if acct not in people]
        if idle:
            warnings.append(warn("warn", f"{len(idle)} roster member(s) with no DAMS tickets in the open sprint",
                                 ", ".join(idle) + ". Either genuinely unallocated or the work sits under an issue "
                                 "type / project outside the DAMS scope. Check before reading this as spare capacity."))

    if not issues:
        warnings.append(warn("alert", "No tickets counted on this board",
                             "Either the sprint has not started or the query/roster is wrong. Check before trusting the page."))

    outliers = [p for p in people.values() if p["remaining_h"] > OUTLIER_REMAINING_MULT * CAPACITY_H]
    if outliers:
        warnings.append(warn(
            "warn", f"{len(outliers)} person(s) with remaining above {OUTLIER_REMAINING_MULT:g}× capacity",
            "; ".join(f"{p['name']}: {p['remaining_h']}h remaining on {p['tickets']} tickets "
                      f"({p['remaining_h'] / CAPACITY_H:.1f}× capacity)" for p in outliers)
            + ". At this size it is usually umbrella/parent tickets or a lead's whole queue rather than one "
              "person's sprint. Check before trusting this discipline's totals."))

    unclassified = [p for p in people.values() if p["discipline"] == "Unclassified"]
    if unclassified:
        warnings.append(warn("warn", f"{len(unclassified)} person(s) with no classifiable issue type",
                             "; ".join(f"{p['name']} ({', '.join(p['issue_types'])})" for p in unclassified)
                             + ". Grouped as Unclassified; bias falls back to 1.0."))
    ties = [p for p in people.values() if p["discipline_tie"]]
    if ties:
        warnings.append(warn("info", f"{len(ties)} discipline tie(s) broken by fixed order",
                             "; ".join(f"{p['name']} → {p['discipline']} ({p['discipline_votes']})" for p in ties)))
    if slug != "dams":
        unvoting = Counter()
        for i in issues:
            if discipline_of(slug, i["type"]) is None:
                unvoting[i["type"]] += 1
        if unvoting:
            warnings.append(warn("warn", "Issue types with no discipline mapping",
                                 ", ".join(f"{t} ×{n}" for t, n in unvoting.most_common())
                                 + ". Their hours count; they just don't vote on discipline."))

    return {
        "slug": slug, "label": board["label"], "board_id": board["board_id"],
        "project": board["project"],
        "scope": ("project IN (IG, V2, CSS), DAMS roster of 12, DAMS issue types" if slug == "dams"
                  else f"project = {board['project']}, everyone except the DAMS roster"
                       + (f" and {len(excl)} board-specific exclusions" if excl else "")),
        "scope_url": nav(scope_jql(board)),
        "pulled": {"start": pulled_start, "end": pulled_end, "seconds": round(secs, 1),
                   "raw_tickets": pulled, "counted_tickets": len(issues)},
        "sprints": sprints,
        "tripwire": tripwire,
        "excluded": excluded_info,
        "_people": people,
        "warnings": warnings,
    }


# ── bias ────────────────────────────────────────────────────────────────────

def load_bias() -> dict:
    if not BIAS_PATH.exists():
        return {"people": {}}
    try:
        data = json.loads(BIAS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise BuildError(f"bias.json exists but cannot be read ({e}). Fix or move it; "
                         "refusing to run and overwrite carried factors.") from e
    data.setdefault("people", {})
    return data


def measure(closed_est_h: float, closed_spent_h: float) -> tuple[float | None, str | None]:
    """Returns (factor, rejection_reason). factor None = not measurable."""
    if closed_est_h < BIAS_MIN_CLOSED_EST_H:
        return None, None
    f = closed_spent_h / closed_est_h
    lo, hi = BIAS_SANE_RANGE
    if not lo <= f <= hi:
        return None, f"measured {f:.2f}× outside sane range {lo}–{hi}"
    return f, None


def resolve_bias(boards: list[dict], store: dict) -> tuple[list[dict], int]:
    """measured → carried → inferred (discipline median). Mutates rows; returns
    entries to persist and the count of measured rows."""
    measured_at = now_iso()
    persist = []
    rows = [(b, p) for b in boards for p in b["_people"].values()]

    for b, p in rows:
        f, why = measure(p["closed_est_h"], p["closed_spent_h"])
        p["bias"] = None
        if why:
            b["warnings"].append(warn("warn", f"Bias rejected for {p['name']}",
                                      f"{why} from {p['closed_est_h']}h closed estimate / {p['closed_spent_h']}h spent. "
                                      "Not used, not persisted. Check the closed tickets' logged time.",
                                      url=p["links"]["closed"]))
        if f is not None:
            p["bias"] = {"factor": round(f, 3), "source": "measured",
                         "detail": f"{p['closed_spent_h']}h spent ÷ {p['closed_est_h']}h estimate on "
                                   f"{p['closed_tickets']} Closed tickets attached to the open sprint"}
            persist.append({"account": p["account"], "name": p["name"], "board": b["slug"], "factor": round(f, 4),
                            "closed_est_h": p["closed_est_h"], "closed_spent_h": p["closed_spent_h"],
                            "sprint": " + ".join(s["name"] for s in b["sprints"]),
                            "measured_at": measured_at, "origin": "run"})
        if p["closed_spent_h"] and p["closed_spent_unestimated_h"] / p["closed_spent_h"] > BIAS_UNESTIMATED_SPENT_FLAG:
            p["bias_note"] = (f"{p['closed_spent_unestimated_h']}h of {p['closed_spent_h']}h closed spent is on "
                              "tickets with no original estimate - this inflates the measured factor.")

    people = store.get("people", {})
    for b, p in rows:
        if p["bias"]:
            continue
        entry = people.get(p["account"], {}).get("boards", {})
        pick, frm = entry.get(b["slug"]), b["slug"]
        if not pick and entry:
            frm, pick = max(entry.items(), key=lambda kv: kv[1].get("closed_est_h", 0))
        if pick and pick.get("factor"):
            other = "" if frm == b["slug"] else f" (from the {frm} board)"
            p["bias"] = {"factor": round(pick["factor"], 3), "source": "carried",
                         "detail": f"{pick.get('closed_spent_h')}h ÷ {pick.get('closed_est_h')}h, "
                                   f"{pick.get('sprint', '?')}, {pick.get('origin', 'run')} "
                                   f"{(pick.get('measured_at') or '')[:10]}{other}"}

    pool: dict[str, list[float]] = {}
    for _, p in rows:
        if p["bias"]:
            pool.setdefault(p["discipline"], []).append(p["bias"]["factor"])
    for _, p in rows:
        if p["bias"]:
            continue
        vals = pool.get(p["discipline"], []) if p["discipline"] != "Unclassified" else []
        if vals:
            p["bias"] = {"factor": round(statistics.median(vals), 3), "source": "inferred",
                         "detail": f"median of {len(vals)} measured/carried {p['discipline']} factor(s) "
                                   f"across all boards; only {p['closed_est_h']}h closed here"}
        else:
            p["bias"] = {"factor": 1.0, "source": "inferred",
                         "detail": f"no measured or carried factor for {p['discipline']} on any board - "
                                   "1.0 is a placeholder, not a measurement"}
    return persist, len(persist)


def write_bias(store: dict, persist: list[dict]) -> None:
    people = store.setdefault("people", {})
    for e in persist:
        rec = people.setdefault(e["account"], {"name": e["name"], "boards": {}})
        rec["name"] = e["name"]
        rec.setdefault("boards", {})[e["board"]] = {k: e[k] for k in
            ("factor", "closed_est_h", "closed_spent_h", "sprint", "measured_at", "origin")}
    store["updated"] = now_iso()
    store["_note"] = ("Estimate-bias factors (closed spent / closed estimate) per person per board. "
                      "Written by sprint_load.py; carried forward when a person has < "
                      f"{BIAS_MIN_CLOSED_EST_H:g}h closed this sprint.")
    atomic_write(BIAS_PATH, json.dumps(store, indent=2, ensure_ascii=False) + "\n")


def seed_bias(jira: Jira, store: dict) -> None:
    """One-off: measure factors from each board's most recently closed sprint and
    add them to bias.json where no entry exists yet. Never overwrites."""
    print("\nSeeding bias.json from the last closed sprint per board")
    last: dict[str, dict] = {}
    for b in BOARDS:
        if b["slug"] == "dams":
            continue
        cs = [s for s in jira.closed_sprints(b["board_id"]) if s.get("originBoardId") in (None, b["board_id"])]
        if not cs:
            print(f"  {b['label']}: no closed sprint found - skipped")
            continue
        last[b["slug"]] = max(cs, key=lambda s: s.get("completeDate") or s.get("endDate") or "")
    if not last:
        raise BuildError("Seed found no closed sprints on any project board.")
    added = skipped = 0
    for b in BOARDS:
        if b["slug"] == "dams":
            ids = [s["id"] for s in last.values()]
            clause, label = f"sprint IN ({', '.join(map(str, ids))})", " + ".join(s["name"] for s in last.values())
        elif b["slug"] in last:
            s = last[b["slug"]]
            clause, label = f"sprint = {s['id']}", s["name"]
        else:
            continue
        issues = [norm(x) for x in jira.search(board_jql(b, clause), FIELDS)]
        if b["slug"] == "dams":
            issues = [i for i in issues if i["type"] in DAMS_ISSUE_TYPES]
        issues = [i for i in issues if i["account"] not in BOARD_EXCLUSIONS.get(b["slug"], {})]
        ppl = aggregate(b, issues)
        n_meas = 0
        for p in ppl.values():
            f, why = measure(p["closed_est_h"], p["closed_spent_h"])
            if f is None:
                if why:
                    print(f"    ! {p['name']}: {why} - not seeded")
                continue
            n_meas += 1
            rec = store["people"].setdefault(p["account"], {"name": p["name"], "boards": {}})
            if b["slug"] in rec.setdefault("boards", {}):
                skipped += 1
                continue
            rec["boards"][b["slug"]] = {"factor": round(f, 4), "closed_est_h": p["closed_est_h"],
                                        "closed_spent_h": p["closed_spent_h"], "sprint": label,
                                        "measured_at": now_iso(), "origin": "seed"}
            added += 1
        print(f"  {b['label']:<22} {label:<32} {len(issues):>4} tickets  {len(ppl):>3} people  {n_meas:>3} measurable")
    print(f"  seeded {added} factor(s); {skipped} already present and left alone")


# ── cross-board ─────────────────────────────────────────────────────────────

def mark_cross_board(boards: list[dict]) -> int:
    where: dict[str, list[tuple[dict, dict]]] = {}
    for b in boards:
        for p in b["_people"].values():
            where.setdefault(p["account"], []).append((b, p))
    n = 0
    for acct, hits in where.items():
        for b, p in hits:
            others = [{"slug": ob["slug"], "label": ob["label"], "remaining_h": op["remaining_h"],
                       "assigned_h": op["assigned_h"], "tickets": op["tickets"], "url": op["links"]["person"]}
                      for ob, op in hits if ob is not b]
            p["other_boards"] = others
            p["combined_remaining_h"] = r1(sum(op["remaining_h"] for _, op in hits))
        if len(hits) > 1:
            n += 1
    return n


# ── next-sprint arithmetic (mirrored in template.html) ──────────────────────

def intake_row(remaining_h: float, bias: float, capacity: float = CAPACITY_H) -> dict:
    true_load = remaining_h * bias
    room = capacity - true_load
    return {"true_load": true_load, "room": room,
            "intake": room / bias if room > 0 else 0.0,
            "overflow": -room if room < 0 else 0.0}


# ── output ──────────────────────────────────────────────────────────────────

def atomic_write(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def render(snapshots: dict) -> str:
    if not TEMPLATE_PATH.exists():
        raise BuildError(f"Template not found: {TEMPLATE_PATH}")
    tpl = TEMPLATE_PATH.read_text(encoding="utf-8")
    n = tpl.count(PLACEHOLDER)
    if n != 1:
        raise BuildError(f"template.html must contain {PLACEHOLDER} exactly once (found {n}).")
    payload = json.dumps(snapshots, ensure_ascii=True, separators=(",", ":"))
    payload = payload.replace("<", "\\u003c")          # no '</script>' can escape the JSON
    return tpl.replace(PLACEHOLDER, payload)


def main() -> int:
    ap = argparse.ArgumentParser(description="Build the sprint capacity dashboard.")
    ap.add_argument("--seed", action="store_true",
                    help="seed bias.json from each board's last closed sprint before building")
    ap.add_argument("--allow-empty", action="store_true",
                    help="write the dashboard even if a board has zero tickets (e.g. between sprints)")
    args = ap.parse_args()

    if not TEMPLATE_PATH.exists():          # fail before spending minutes on Jira
        raise BuildError(f"Template not found: {TEMPLATE_PATH}")
    email, token = credentials()
    jira = Jira(email, token)
    store = load_bias()

    # Jira answers a JQL with an unknown accountId with 200 + zero issues, so a
    # typo in the roster would silently drop a person. Verify every ID up front.
    bad = []
    for acct, nm in [*ROSTER.items(), *((a, n) for ex in BOARD_EXCLUSIONS.values() for a, n in ex.items())]:
        try:
            u = jira.user(acct)
        except BuildError:
            bad.append(f"{nm} ({acct}): not found in Jira")
            continue
        if not u.get("active", True):
            print(f"  note: {nm} is a deactivated Jira account")
    if bad:
        raise BuildError("Unknown account ID(s) in ROSTER / BOARD_EXCLUSIONS:\n  " + "\n  ".join(bad))
    print(f"  {len(ROSTER)} roster + {sum(map(len, BOARD_EXCLUSIONS.values()))} exclusion account IDs verified")

    if args.seed:
        seed_bias(jira, store)

    generated = now_iso()
    print("\nTickets pulled")
    boards = []
    for b in BOARDS:
        snap = build_board(jira, b)
        boards.append(snap)
        p = snap["pulled"]
        extra = f"  ({p['raw_tickets'] - p['counted_tickets']} not counted)" if p["raw_tickets"] != p["counted_tickets"] else ""
        print(f"  {b['label']:<22} {p['raw_tickets']:>5} pulled  {p['counted_tickets']:>5} counted  {p['seconds']:>5.1f}s{extra}")

    # Jira also answers an unknown project / issue type with 200 + zero issues.
    empty = [b["label"] for b in boards if b["pulled"]["counted_tickets"] == 0]
    if empty and not args.allow_empty:
        raise BuildError(f"No tickets counted on: {', '.join(empty)}.\n"
                         "  Jira returns an empty 200 for an unknown project key or issue type, so this is treated "
                         "as an error.\n  If the board is genuinely between sprints, re-run with --allow-empty.")

    persist, n_measured = resolve_bias(boards, store)
    n_cross = mark_cross_board(boards)

    # ── tripwire, loudly ────────────────────────────────────────────────────
    tw = boards[0]["tripwire"]
    if tw and tw["count"]:
        bar = "!" * 72
        print(f"\n{bar}\n!! TRIPWIRE: {tw['count']} DAMS-roster ticket(s) are OUTSIDE the issue-type filter and are"
              f"\n!! NOT COUNTED anywhere: {tw['types']}  ({tw['remaining_h']}h remaining)"
              f"\n!! {', '.join(tw['keys'][:15])}{' ...' if tw['count'] > 15 else ''}\n{bar}")
    else:
        print("\nTripwire: 0 DAMS-roster tickets outside the issue-type filter")

    # ── per-board summary ───────────────────────────────────────────────────
    print(f"\n{'Board':<22} {'people':>6} {'tickets':>7} {'remaining':>10} {'intake':>8} {'overflow':>9}   bias m/c/i")
    snapshots = {}
    for b in boards:
        people = list(b.pop("_people").values())
        people.sort(key=lambda p: (DISCIPLINE_ORDER.index(p["discipline"]), -p["remaining_h"], p["name"]))
        rem = sum(p["remaining_h"] for p in people)
        calc = [intake_row(p["remaining_h"], p["bias"]["factor"]) for p in people]
        src = Counter(p["bias"]["source"] for p in people)
        print(f"  {b['label']:<20} {len(people):>6} {sum(p['tickets'] for p in people):>7} "
              f"{rem:>9.1f}h {sum(c['intake'] for c in calc):>7.1f}h {sum(c['overflow'] for c in calc):>8.1f}h"
              f"   {src['measured']}/{src['carried']}/{src['inferred']}")
        b["people"] = people
        b["generated_at"] = generated
        b["site"] = SITE
        b["config"] = {"capacity_h": CAPACITY_H, "unplanned_h": UNPLANNED_H,
                       "at_capacity_band_h": AT_CAPACITY_BAND_H, "near_capacity_pct": NEAR_CAPACITY_PCT,
                       "bias_min_closed_est_h": BIAS_MIN_CLOSED_EST_H, "closed_status": CLOSED_STATUS,
                       "discipline_order": DISCIPLINE_ORDER}
        snapshots[b["slug"]] = b
    print("  overflow = true-load hours (remaining × bias) above 70h; bias m/c/i = measured/carried/inferred")
    print(f"\nCross-board people: {n_cross}")

    # ── warnings ────────────────────────────────────────────────────────────
    nw = sum(1 for b in boards for w in b["warnings"] if w["level"] != "info")
    print(f"\nChecks: {nw} warning(s)")
    for b in boards:
        for w in b["warnings"]:
            if w["level"] == "info":
                continue
            tag = "ALERT" if w["level"] == "alert" else "warn "
            print(f"  [{tag}] {b['slug']:<4} {w['title']}")

    # ── all-or-nothing write ────────────────────────────────────────────────
    if set(snapshots) != {b["slug"] for b in BOARDS}:
        raise BuildError("Not every board produced a snapshot - refusing to write.")
    html = render(snapshots)
    atomic_write(OUTPUT_PATH, html)
    write_bias(store, persist)
    print(f"\nWrote {OUTPUT_PATH.name} ({len(html) / 1024:.0f} KB) and {BIAS_PATH.name} "
          f"({n_measured} measured factor(s) persisted)")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BuildError as e:
        print(f"\nERROR: {e}\nNothing was written.", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nInterrupted. Nothing was written.", file=sys.stderr)
        sys.exit(130)
