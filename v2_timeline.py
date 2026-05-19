"""
v2_timeline.py
Pulls V2 fix-version delivery data from Jira and renders v2-timeline.html
from v2-timeline.template.html.

See docs/V2_RELEASE_TIMELINE_KNOWLEDGE.md for the spec. The mechanical model
implemented here is the pure-Jira version; producer-tuned overrides described
in docs/V2_TIMELINE_EDGE_CASES.md are intentionally not implemented yet.
"""

import json
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

from jira_client import jira_jql

load_dotenv()

TODAY = date.today()

# Per-FV config in priority order; queuedHours flows top → bottom.
# Source: docs/V2_RELEASE_TIMELINE_KNOWLEDGE.md §2, §6, §9.2
FV_CONFIG = [
    {"key": "V2 SW 15.00",  "color": "#60a5fa", "sub": "Sweepstakes · Games",                       "qaWeeks": 2},
    {"key": "V2 SW 16.00",  "color": "#93c5fd", "sub": "Offline Upgrader · Pay It Forward",          "qaWeeks": 2,
     "note": 'Jira fix version "V2 SW 16.00"'},
    {"key": "V2 PT 13.30",  "color": "#c4b5fd", "sub": "8× Game Ports · Server Cleanup",             "qaWeeks": 2},
    {"key": "V2 P2P 16.00", "color": "#fb923c", "sub": "Georgia P2P · Task Handler R7",              "qaWeeks": 3},
    {"key": "V2 HHR 3.00",  "color": "#4ade80", "sub": "User Role Restrictions · Maintenance Menu",  "qaWeeks": 2},
    {"key": "V2 PT 14.00",  "color": "#f87171", "sub": "Ohio Central Server · Location Server",      "qaWeeks": 3},
]

DEV_TYPES   = ["Story", "Dev Task", "Dev Subtask"]
OTHER_TYPES = ["Creative Task", "Creative Subtask", "Sound Task", "Sound Subtask",
               "Math Task", "Math Subtask", "Pre-Prod Task"]

STATUS_STYLES = {
    "In QA":          "color:#92400e;border-color:rgba(217,119,6,.35);background:rgba(217,119,6,.10)",
    "In Development": "color:#1e3a8a;border-color:rgba(37,99,235,.3);background:rgba(37,99,235,.09)",
    "Scheduled":      "color:#7f1d1d;border-color:rgba(220,38,38,.3);background:rgba(220,38,38,.09)",
}

# Source: docs/V2_RELEASE_TIMELINE_KNOWLEDGE.md §7
SPRINTS = [
    {"label": "S1", "start": "2026-05-11"},
    {"label": "S2", "start": "2026-05-25"},
    {"label": "S3", "start": "2026-06-08"},
    {"label": "S4", "start": "2026-06-22"},
    {"label": "S5", "start": "2026-07-06"},
    {"label": "S6", "start": "2026-07-20"},
    {"label": "",   "start": "2026-08-12"},
]


# ── Helpers ───────────────────────────────────────────────────────────────────

def is_done(status):
    return status in ("Closed", "Done")


def hpd(name):
    """Effective hours per day after availability buffer (§4.1)."""
    return 4 if name == "Rejosh Samuel" else 6


def parent_is_admin(issue):
    """Knowledge base §3.2: exclude subtasks of Release/Merge Code parents."""
    parent = issue["fields"].get("parent") or {}
    parent_summary = ((parent.get("fields") or {}).get("summary") or "").lower()
    return "release" in parent_summary or "merge" in parent_summary


# ── Fetch ─────────────────────────────────────────────────────────────────────

def fetch_fv_tasks(fv_name, types):
    """Knowledge base §3.3 / §12.2 JQL template."""
    types_jql = ", ".join(f'"{t}"' for t in types)
    jql = (
        f'project = V2 AND fixVersion = "{fv_name}" '
        f'AND issuetype in ({types_jql}) '
        f'AND summary !~ "Release" AND summary !~ "Merge" '
        f'ORDER BY assignee ASC'
    )
    fields = ["summary", "status", "issuetype", "assignee",
              "timeestimate", "timeoriginalestimate", "parent"]
    return jira_jql(jql, fields)


# ── Transform ─────────────────────────────────────────────────────────────────

def group_by_assignee(issues, fv_name):
    """Group issues by assignee displayName; emit task records the template expects."""
    by_name = {}
    for issue in issues:
        if parent_is_admin(issue):
            continue
        fields   = issue["fields"]
        assignee = fields.get("assignee") or {}
        name     = assignee.get("displayName") or "Unassigned"
        status   = (fields.get("status") or {}).get("name", "")
        if not status:
            continue
        hours = round((fields.get("timeestimate") or 0) / 3600)
        task  = {
            "key":     issue["key"],
            "summary": fields.get("summary", ""),
            "hours":   hours,
            "status":  status,
        }
        if status == "In Progress" and hours == 0:
            print(f"  ⚠ {fv_name} :: {task['key']} ({name}) — In Progress with 0h estimate")
        entry = by_name.setdefault(name, {"name": name, "tasks": []})
        entry["tasks"].append(task)
        # Classify Others-group people by issuetype first word
        itype_first = ((fields.get("issuetype") or {}).get("name") or "").split()[0]
        if itype_first in ("Creative", "Sound", "Math", "Pre-Prod") and "type" not in entry:
            entry["type"] = itype_first
    return list(by_name.values())


def classify_status_label(dev_people):
    """Auto-derive FV status label from the mix of open task statuses.

    Heuristic (intentionally simple — see V2_TIMELINE_EDGE_CASES.md §9):
      • all open dev work in QA-like status     → In QA       (devStart=null)
      • only "New" statuses (nothing started)   → Scheduled   (devStart=today)
      • everything else                          → In Development (devStart=today)
    """
    open_tasks = [t for p in dev_people for t in p["tasks"] if not is_done(t["status"])]
    if not open_tasks:
        return ("In QA", None)
    statuses = {t["status"] for t in open_tasks}
    qa_like  = {"In QA", "In QA R1", "In QA R2", "Ready For QA", "QA In Progress"}
    if statuses.issubset(qa_like):
        return ("In QA", None)
    if statuses.issubset({"New"}):
        return ("Scheduled", TODAY.isoformat())
    return ("In Development", TODAY.isoformat())


def mark_bottleneck(dev_people):
    """Knowledge base §11: bottleneck = person whose projected done date is latest."""
    best_days, best_person = -1, None
    for p in dev_people:
        open_h = sum(t["hours"] for t in p["tasks"] if not is_done(t["status"]))
        total  = open_h + p.get("queuedHours", 0)
        days   = total / hpd(p["name"]) if total > 0 else 0
        if days > best_days:
            best_days, best_person = days, p
    if best_person and best_days > 0:
        best_person["bottleneck"] = True
        open_tasks = [t for t in best_person["tasks"] if not is_done(t["status"])]
        if open_tasks:
            top = max(open_tasks, key=lambda t: t["hours"])
            if top["hours"] > 0:
                top["bottleneck"] = True
    return best_person, best_days


def compute_queued_hours(fvs):
    """Knowledge base §12.4: per person, sum open hours from higher-priority FVs."""
    for idx, fv in enumerate(fvs):
        for person in fv["devPeople"]:
            queued = 0
            for prev in fvs[:idx]:
                prev_p = next((x for x in prev["devPeople"] if x["name"] == person["name"]), None)
                if prev_p:
                    queued += sum(t["hours"] for t in prev_p["tasks"] if not is_done(t["status"]))
            person["queuedHours"] = round(queued)


# ── Build one FV ──────────────────────────────────────────────────────────────

def build_fv(cfg):
    print(f"\n— {cfg['key']} —")
    dev_issues   = fetch_fv_tasks(cfg["key"], DEV_TYPES)
    other_issues = fetch_fv_tasks(cfg["key"], OTHER_TYPES)
    print(f"  fetched {len(dev_issues)} dev / {len(other_issues)} other issues")

    dev_people   = group_by_assignee(dev_issues, cfg["key"])
    other_people = group_by_assignee(other_issues, cfg["key"])

    if not dev_people and not other_people:
        print(f"  ⚠ {cfg['key']} has no tasks — fix version may be empty in Jira")

    status_label, dev_start = classify_status_label(dev_people)
    fv = {
        "key":          cfg["key"],
        "color":        cfg["color"],
        "sub":          cfg["sub"],
        "devStart":     dev_start,
        "qaWeeks":      cfg["qaWeeks"],
        "statusLabel":  status_label,
        "statusStyle":  STATUS_STYLES[status_label],
        "devPeople":    dev_people,
        "otherPeople":  other_people,
    }
    if cfg.get("note"):
        fv["note"] = cfg["note"]
    return fv


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print(f"▶ Building V2 timeline — {TODAY}")
    fvs = [build_fv(cfg) for cfg in FV_CONFIG]
    compute_queued_hours(fvs)
    print()
    for fv in fvs:
        bp, days = mark_bottleneck(fv["devPeople"])
        if bp:
            print(f"  {fv['key']}: bottleneck = {bp['name']} (~{days:.1f} business days remaining)")
        else:
            print(f"  {fv['key']}: no bottleneck (no open dev work)")

    template = Path("v2-timeline.template.html").read_text(encoding="utf-8")
    rendered = (template
        .replace("__TODAY__",        TODAY.isoformat())
        .replace("__FV_DATA__",      json.dumps(fvs, ensure_ascii=False))
        .replace("__SPRINTS_DATA__", json.dumps(SPRINTS, ensure_ascii=False))
    )
    Path("v2-timeline.html").write_text(rendered, encoding="utf-8")
    print("\n✅ v2-timeline.html written")


if __name__ == "__main__":
    main()
