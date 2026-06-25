"""
team_board.py
=============
Builds team-board-data.js for the Team Board sprint-planning calendar page.

Pulls every Math + Art ticket in the open sprint(s) across IG and V2:

    project in (IG, V2) AND sprint in openSprints()
        AND issuetype in (<dept types>) ORDER BY Rank

and writes ``team-board-data.js``:

    window.TB_DATA = {
      refreshed_at: 'YYYY-MM-DD HH:MM',
      sprint: { name, start, end, activeCount },
      depts: { math: {label, issuetypes, tickets:[...]}, art: {...} }
    };

Each ticket carries everything a pill needs (key, summary, assignee, hours,
status, release, flags) plus ``day`` — the 0-13 index within the sprint derived
from the Jira Due Date (the field we reuse for day-placement), or null = backlog.

Read-only. Writes back to Jira live from the page via the Actions relay; this
builder only reads. Credentials: JIRA_EMAIL / JIRA_API_TOKEN (env or
game-pipeline/.env locally). Same secrets as the other dashboards.

Usage:  python team_board.py            # build all depts
        python team_board.py --verbose
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv

from jira_client import jira_jql

# Windows console UTF-8 (✓ ⚠ ✗ glyphs) — no-op on Linux CI.
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

OK, WARN, ERR = "[ok]", "[warn]", "[err]"
ROOT = Path(__file__).resolve().parent

# Credentials: prefer a root .env, fall back to game-pipeline/.env (where they
# currently live), then rely on real env vars (CI).
load_dotenv(ROOT / ".env")
load_dotenv(ROOT / "game-pipeline" / ".env")

JIRA_BROWSE = "https://ponggamestudios.atlassian.net/browse/"

# Sprint custom field differs per project (from build_jira_data.py).
SPRINT_FIELDS = ["customfield_10020", "customfield_10103"]
EPIC_NAME_FIELD = "customfield_10014"

# Department -> issue types. Mirrors DISCIPLINE_BY_ISSUETYPE in
# game-pipeline/build_jira_data.py. Add more depts here later (dev/qa/sound/design).
DEPTS = {
    "math": {
        "label": "Math",
        "issuetypes": ["Math Task", "Math Subtask"],
    },
    "art": {
        "label": "Art / Creative",
        "issuetypes": ["Creative Task", "Creative Subtask", "Pre-Prod Task"],
    },
}

ISSUE_FIELDS = [
    "summary", "status", "assignee", "issuetype",
    "timeoriginalestimate", "timespent", "timeestimate", "duedate", "parent",
    "fixVersions", "priority", "issuelinks",
] + SPRINT_FIELDS

# Status -> bucket (drives the pill's coloured dot). Subset of build_jira_data.
STATUS_BUCKET = {
    "new": "todo", "to do": "todo", "todo": "todo", "ready": "todo",
    "backlog": "todo", "open": "todo", "reopened from backlog": "todo",
    "in progress": "wip", "in review": "wip", "review": "wip", "reopened": "wip",
    "pre-prod in progress": "wip", "pre-prod in review": "wip",
    "pre-prod reopened": "wip", "in development": "wip",
    "in qa": "qa", "ready for qa": "qa", "qa": "qa", "testing": "qa",
    "on hold": "hold", "blocked": "hold",
    "closed": "done", "signed off": "done", "released": "done",
    "deployed": "done", "to be closed": "done", "known issue": "done", "done": "done",
}
HOLD_STATUSES = {"on hold", "blocked"}


def bucket(status):
    return STATUS_BUCKET.get((status or "").strip().lower(), "todo")


def secs_to_hours(v):
    try:
        return round(float(v) / 3600.0, 1)
    except (TypeError, ValueError):
        return 0.0


def parse_date(value):
    if not value or not isinstance(value, str):
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S.%fZ",
                "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d"):
        try:
            return datetime.strptime(value.strip(), fmt).date()
        except ValueError:
            continue
    return None


def initials(name):
    if not name:
        return None
    parts = [p for p in name.replace(".", " ").split() if p]
    if not parts:
        return None
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[-1][0]).upper()


def active_sprints(fields):
    """Return the list of ACTIVE sprint dicts attached to an issue (any project)."""
    out = []
    for f in SPRINT_FIELDS:
        val = fields.get(f)
        if not val:
            continue
        items = val if isinstance(val, list) else [val]
        for item in items:
            if isinstance(item, dict) and (item.get("state") or "").lower() == "active":
                out.append(item)
    return out


def pick_release(fixversions):
    """Pick a release label from a fixVersions list: prefer unreleased, else any."""
    if not fixversions:
        return None
    names = [v.get("name") for v in fixversions if v.get("name")]
    if not names:
        return None
    unrel = [v.get("name") for v in fixversions
             if v.get("name") and not v.get("released")]
    return (unrel or names)[0]


def is_blocked(fields):
    """Blocked if status says so, or an inward 'is blocked by' link exists."""
    if (fields.get("status", {}) or {}).get("name", "").strip().lower() in HOLD_STATUSES:
        return True
    for link in fields.get("issuelinks", []) or []:
        t = (link.get("type") or {})
        if link.get("inwardIssue") and "block" in (t.get("inward") or "").lower():
            return True
    return False


def build_parent_cache(issues, verbose=False):
    """Resolve releases by walking the parent chain. Batch-fetch parents (and
    grandparents) that we need, keyed by issue key."""
    cache = {}

    def fetch_keys(keys):
        keys = [k for k in keys if k and k not in cache]
        for i in range(0, len(keys), 50):
            chunk = keys[i:i + 50]
            jql = "key in (" + ",".join(chunk) + ")"
            for it in jira_jql(jql, ["summary", "fixVersions", "parent",
                                     EPIC_NAME_FIELD, "issuetype"]):
                cache[it["key"]] = it.get("fields", {}) or {}

    # Level 1: direct parents of tickets that lack their own fixVersion.
    lvl1 = {((it.get("fields", {}) or {}).get("parent") or {}).get("key")
            for it in issues}
    fetch_keys(list(lvl1))
    # Level 2: parents of those parents (Epic above a Task).
    lvl2 = {(f.get("parent") or {}).get("key") for f in cache.values()}
    fetch_keys(list(lvl2))
    if verbose:
        print(f"   {OK} parent cache: {len(cache)} ancestor issue(s)")
    return cache


def resolve_release(fields, cache):
    """Release name + game (epic) name by walking up: self -> parent -> grandparent."""
    rel = pick_release(fields.get("fixVersions"))
    game = None
    node = fields
    seen = 0
    while node and seen < 4:
        if rel is None:
            rel = pick_release(node.get("fixVersions"))
        it = (node.get("issuetype") or {}).get("name", "").lower()
        if it == "epic":
            game = node.get(EPIC_NAME_FIELD) or node.get("summary")
            break
        pk = (node.get("parent") or {}).get("key")
        node = cache.get(pk)
        seen += 1
    return rel, game


def day_index(due, start, end):
    """0-based index within the sprint window from the Due Date, else None.

    Tickets are placed on the calendar by their Jira Due Date. Anything without
    a due date, or due outside the sprint window, lands in the Unscheduled rail.
    """
    if not (due and start and end):
        return None
    if start <= due <= end:
        return (due - start).days
    return None


def build_dept(dept_key, cfg, verbose=False):
    types = ", ".join(f'"{t}"' for t in cfg["issuetypes"])
    jql = (f"project in (IG, V2) AND sprint in openSprints() "
           f"AND issuetype in ({types}) ORDER BY Rank")
    if verbose:
        print(f"   {OK} {dept_key}: {jql}")
    issues = jira_jql(jql, ISSUE_FIELDS)
    print(f"   {OK} {cfg['label']}: {len(issues)} ticket(s) in open sprint")
    cache = build_parent_cache(issues, verbose) if issues else {}

    # Collect active sprint windows (for the calendar) across all returned issues.
    sprints = {}
    for it in issues:
        for s in active_sprints(it.get("fields", {}) or {}):
            sid = s.get("id")
            if sid is not None and sid not in sprints:
                sprints[sid] = {
                    "name": s.get("name"),
                    "start": parse_date(s.get("startDate")),
                    "end": parse_date(s.get("endDate")),
                    "count": 0,
                }
        # tally membership for "most populated" pick
        for s in active_sprints(it.get("fields", {}) or {}):
            if s.get("id") in sprints:
                sprints[s["id"]]["count"] += 1

    tickets = []
    for it in issues:
        f = it.get("fields", {}) or {}
        status_name = (f.get("status") or {}).get("name")
        assignee = f.get("assignee") or {}
        who = assignee.get("displayName")
        est = secs_to_hours(f.get("timeoriginalestimate"))
        due = parse_date(f.get("duedate"))
        rel, game = resolve_release(f, cache)
        itype = (f.get("issuetype") or {}).get("name", "")
        blocked = is_blocked(f)
        tickets.append({
            "id": it["key"],
            "url": JIRA_BROWSE + it["key"],
            "summary": f.get("summary") or "",
            "issuetype": itype,
            "is_subtask": itype.strip().lower().endswith("subtask"),
            "status": status_name,
            "bucket": bucket(status_name),
            "assignee": who,
            "assignee_id": assignee.get("accountId"),
            "initials": initials(who),
            "est": est,
            "spent": secs_to_hours(f.get("timespent")),
            "remaining": secs_to_hours(f.get("timeestimate")),   # Jira remaining estimate
            "due": due.isoformat() if due else None,
            "release": rel,
            "game": game,
            "priority": (f.get("priority") or {}).get("name"),
            "flags": {
                "unassigned": who is None,
                "unestimated": est == 0,
                "blocked": blocked,
            },
        })
    return tickets, sprints


def choose_sprint(all_sprints):
    """Pick the calendar window: the active sprint with the most tickets that has
    real dates; fall back to earliest start + 13 days."""
    dated = {sid: s for sid, s in all_sprints.items() if s.get("start")}
    if not dated:
        return None
    best = max(dated.values(), key=lambda s: s["count"])
    start = best["start"]
    end = best["end"] or (start + timedelta(days=13))
    return {
        "name": best.get("name"),
        "start": start.isoformat(),
        "end": end.isoformat(),
        "activeCount": len(dated),
        "_start": start,
        "_end": end,
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description="Build team-board-data.js from Jira.")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args(argv)

    print(f"{OK} Team Board builder · {datetime.now():%Y-%m-%d %H:%M}")

    dept_tickets = {}
    all_sprints = {}
    for key, cfg in DEPTS.items():
        tickets, sprints = build_dept(key, cfg, args.verbose)
        dept_tickets[key] = (cfg, tickets)
        for sid, s in sprints.items():
            if sid not in all_sprints:
                all_sprints[sid] = s
            else:
                all_sprints[sid]["count"] += s["count"]

    sprint = choose_sprint(all_sprints)
    if not sprint:
        print(f"{WARN} no dated active sprint found — calendar window will be empty")
    start = sprint and sprint.pop("_start")
    end = sprint and sprint.pop("_end")

    depts_out = {}
    for key, (cfg, tickets) in dept_tickets.items():
        for t in tickets:
            t["day"] = day_index(parse_date(t["due"]), start, end)
        depts_out[key] = {
            "label": cfg["label"],
            "issuetypes": cfg["issuetypes"],
            "tickets": tickets,
        }

    payload = {
        "refreshed_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "sprint": sprint,
        "depts": depts_out,
    }
    out = ROOT / "team-board-data.js"
    out.write_text(
        "// Auto-generated by team_board.py — do not edit by hand.\n"
        f"// Refreshed {payload['refreshed_at']}\n"
        "window.TB_DATA = " + json.dumps(payload, ensure_ascii=False) + ";\n",
        encoding="utf-8",
    )
    n = sum(len(d["tickets"]) for d in depts_out.values())
    print(f"{OK} wrote {out.name} · {n} ticket(s) · sprint "
          f"{sprint['start'] if sprint else '?'}–{sprint['end'] if sprint else '?'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
