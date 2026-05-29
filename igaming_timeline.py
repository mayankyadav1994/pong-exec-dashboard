"""
igaming_timeline.py
Pulls iGaming fix-version delivery data from Jira and renders
igaming-timeline.html from igaming-timeline.template.html.

See docs/IG_RELEASE_TIMELINE_KNOWLEDGE.md for the spec. Same structure as
v2_timeline.py but with iGaming-specific data-shaping rules (4 department
lanes including Design, no Lab pipeline, customfield_10103 for Sprint).
"""

import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

from dotenv import load_dotenv

from jira_client import jira_jql

# Ensure Unicode glyphs in print() (▶, →, ⛓) render on Windows where the
# default cp1252 stdout encoding can't handle them. No-op on Linux CI.
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

load_dotenv()

TODAY = date.today()

# Sprint custom field — VERIFIED for this Jira instance.
# Do not assume the standard 10020 ID; see docs/IG_RELEASE_TIMELINE_KNOWLEDGE.md §3.4.
SPRINT_FIELD = "customfield_10103"

# ── Issue type groups (§3.1) ──────────────────────────────────────────────────
DEV_TYPES   = {"Dev Task", "Dev Subtask", "Story", "Pre-Prod Task", "Pre-Prod Subtask"}
CRE_TYPES   = {"Creative Task", "Creative Subtask"}
MATH_TYPES  = {"Math Task", "Math Subtask"}
SND_TYPES   = {"Sound Task", "Sound Subtask"}
DES_TYPES   = {"Design Task", "Design Subtask"}   # iGaming-only 4th lane

# ── FV priority order + visual metadata (§2, §7.1) ────────────────────────────
# Real releases first (by ascending release date), then bucket FVs.
FV_ORDER = [
    "ELG 3.30", "ELG 4.00", "PFH2 Games 2.10", "Horse Play 1.00", "ELG 4.10",
    "PFH2 Games 2.00", "PFH2 Games 3.00", "PFH2 Games 4.00", "PFH2 Services 3.00",
    "ELG Game Configs", "ELG Website", "ELG New Games", "ELG Feature Backlog",
    "PFH2 Games Backlog", "New Games - iGaming",
]

FV_META = {
    "ELG 3.30":            {"color": "#60a5fa", "sub": "iGaming · ELG Round 4 + Bug Fixes",  "qaWeeks": 2, "release": "2026-04-30"},
    "ELG 4.00":            {"color": "#3b82f6", "sub": "iGaming · ELG Round 5 + New Games",  "qaWeeks": 2, "release": "2026-05-29"},
    "ELG 4.10":            {"color": "#2563eb", "sub": "iGaming · ELG Round 6",               "qaWeeks": 2, "release": None},
    "PFH2 Games 2.10":     {"color": "#fb923c", "sub": "iGaming · PFH2 Maintenance",          "qaWeeks": 2, "release": "2026-05-19"},
    "PFH2 Games 2.00":     {"color": "#f59e0b", "sub": "iGaming · PFH2 Maintenance (prev)",   "qaWeeks": 2, "release": None},
    "PFH2 Games 3.00":     {"color": "#f97316", "sub": "iGaming · PFH2 Games Round 3",        "qaWeeks": 2, "release": None},
    "PFH2 Games 4.00":     {"color": "#ea580c", "sub": "iGaming · PFH2 Games Round 4",        "qaWeeks": 2, "release": None},
    "PFH2 Services 3.00":  {"color": "#fed7aa", "sub": "iGaming · PFH2 Backend Services",     "qaWeeks": 2, "release": None},
    "Horse Play 1.00":     {"color": "#4ade80", "sub": "iGaming · Horse Play Launch",         "qaWeeks": 2, "release": "2026-05-29"},
    "ELG Game Configs":    {"color": "#a78bfa", "sub": "Bucket · Game configuration tasks",   "qaWeeks": 2, "release": None},
    "ELG Website":         {"color": "#c4b5fd", "sub": "Bucket · ELG website work",           "qaWeeks": 2, "release": None},
    "ELG New Games":       {"color": "#a855f7", "sub": "Bucket · ELG new games pipeline",     "qaWeeks": 2, "release": None},
    "ELG Feature Backlog": {"color": "#8b5cf6", "sub": "Bucket · ELG feature backlog",        "qaWeeks": 2, "release": None},
    "PFH2 Games Backlog":  {"color": "#94a3b8", "sub": "Bucket · PFH2 games backlog",         "qaWeeks": 2, "release": None},
    "New Games - iGaming": {"color": "#64748b", "sub": "Bucket · iGaming new games backlog",  "qaWeeks": 2, "release": None},
}

# Status badge styles (§3.5)
STATUS_STYLES = {
    "In QA":           "color:#92400e;border-color:rgba(217,119,6,.35);background:rgba(217,119,6,.10)",
    "In Dev":          "color:#1e40af;border-color:rgba(37,99,235,.35);background:rgba(37,99,235,.10)",
    "Scheduled":       "color:#475569;border-color:rgba(100,116,139,.35);background:rgba(100,116,139,.10)",
    "No Active Work":  "color:#475569;border-color:rgba(100,116,139,.35);background:rgba(100,116,139,.10)",
}

# Sprint chips on the axis (§7.3 — iGaming uses the same 14-day cadence as V2
# starting May 11; chips labelled S6–S20 to match active sprint = IG Sprint 7).
SPRINTS = [
    {"label": "S6",  "start": "2026-05-11"}, {"label": "S7",  "start": "2026-05-25"},
    {"label": "S8",  "start": "2026-06-08"}, {"label": "S9",  "start": "2026-06-22"},
    {"label": "S10", "start": "2026-07-06"}, {"label": "S11", "start": "2026-07-20"},
    {"label": "S12", "start": "2026-08-03"}, {"label": "S13", "start": "2026-08-17"},
    {"label": "S14", "start": "2026-08-31"}, {"label": "S15", "start": "2026-09-14"},
    {"label": "S16", "start": "2026-09-28"}, {"label": "S17", "start": "2026-10-12"},
    {"label": "S18", "start": "2026-10-26"}, {"label": "S19", "start": "2026-11-09"},
    {"label": "S20", "start": "2026-11-23"}, {"label": "",    "start": "2026-12-07"},
]

DONE_STATUSES = {"Closed", "Done"}
HOLIDAYS = {"2026-05-18"}  # Victoria Day — keep in sync with template


# ── Helpers ───────────────────────────────────────────────────────────────────

def norm_name(name):
    """Normalize assignee display names (e.g. 'Sonali.Mehra' → 'Sonali Mehra',
    'BinZhang' → 'Bin Zhang'). Falls through unchanged for already-clean names."""
    if not name:
        return "Unassigned"
    n = name.replace(".", " ")
    if n == "BinZhang":
        return "Bin Zhang"
    return n


def group_for(itype):
    """Map a Jira issuetype name to one of: dev, Creative, Math, Sound, Design,
    or None (the issuetype is not included in the dashboard)."""
    if itype in DEV_TYPES:  return "dev"
    if itype in CRE_TYPES:  return "Creative"
    if itype in MATH_TYPES: return "Math"
    if itype in SND_TYPES:  return "Sound"
    if itype in DES_TYPES:  return "Design"
    return None


def parent_is_admin(issue):
    """Knowledge base §3.2: exclude subtasks of Release/Merge parents."""
    parent = issue["fields"].get("parent") or {}
    parent_summary = ((parent.get("fields") or {}).get("summary") or "").lower()
    return "release" in parent_summary or "merge" in parent_summary


def secs_to_hours(secs):
    """Jira returns time in seconds; round to 2dp hours for display."""
    return round((secs or 0) / 3600, 2)


def is_done(status):
    return status in DONE_STATUSES


# ── Jira fetches (§3.3) ──────────────────────────────────────────────────────

FV_TASKS_FIELDS = [
    "summary", "status", "issuetype", "assignee",
    "timeestimate", "timeoriginalestimate", "timespent",
    "fixVersions", "parent",
    SPRINT_FIELD,
]

SPRINT_TASKS_FIELDS = FV_TASKS_FIELDS  # same shape for now


def fetch_fv_tasks():
    """All active FV-eligible tasks across all unreleased fix versions."""
    return jira_jql(
        jql=(
            'project = IG '
            'AND fixVersion in unreleasedVersions() '
            'AND statusCategory != Done '
            'AND issuetype not in ("Bug", "Enhancement", "QA Task") '
            'AND summary !~ "Release" '
            'AND summary !~ "Merge"'
        ),
        fields=FV_TASKS_FIELDS,
    )


def fetch_sprint_tasks():
    """Active sprint dev work only (§3.3)."""
    return jira_jql(
        jql=(
            'project = IG '
            'AND sprint in openSprints() '
            'AND statusCategory != Done '
            'AND issuetype in ("Dev Task", "Dev Subtask", "Story", "Pre-Prod Task", "Pre-Prod Subtask") '
            'AND summary !~ "Release" '
            'AND summary !~ "Merge"'
        ),
        fields=SPRINT_TASKS_FIELDS,
    )


def fetch_epics():
    """All epics in unreleased fix versions (for Scope tab)."""
    return jira_jql(
        jql=(
            'project = IG '
            'AND issuetype = Epic '
            'AND fixVersion in unreleasedVersions()'
        ),
        fields=["summary", "status", "fixVersions"],
    )


# ── Transform: FV structure ──────────────────────────────────────────────────

def extract_sprint_meta(sprint_field_value):
    """Pull the active sprint dict out of a customfield_10103 value.
    The field returns an array of sprint dicts; we want the one in 'active' state."""
    if not sprint_field_value:
        return None
    for s in sprint_field_value:
        if (s or {}).get("state") == "active":
            return s
    # Fallback: first sprint if no active flag found
    return sprint_field_value[0] if sprint_field_value else None


def build_fv_structure(fv_tasks, epic_by_key):
    """
    Returns a dict keyed by fix version name. Each entry has:
      - dev_people: {assignee → [tasks]}
      - other_people: {"name::group" → {name, type, tasks}}
      - epics_taskkeys: {epic_key → [task_keys]}
      - unscoped_taskkeys: [task_keys]
    Tasks include hours, status, parent for later assembly.
    """
    fv_struct = {}

    for issue in fv_tasks:
        if parent_is_admin(issue):
            continue

        f = issue["fields"]
        itype = (f.get("issuetype") or {}).get("name") or ""
        grp = group_for(itype)
        if grp is None:
            continue

        a = f.get("assignee") or {}
        aname = norm_name(a.get("displayName"))
        status = (f.get("status") or {}).get("name") or ""
        if not status:
            continue

        parent_key = ((f.get("parent") or {}).get("key")) if f.get("parent") else None
        fvs = f.get("fixVersions") or []
        if not fvs:
            continue

        task_obj = {
            "key":     issue["key"],
            "summary": f.get("summary", ""),
            "hours":   secs_to_hours(f.get("timeestimate")),
            "status":  status,
            "origH":   secs_to_hours(f.get("timeoriginalestimate")),
            "spentH":  secs_to_hours(f.get("timespent")),
            "parent":  parent_key,
        }

        for fv in fvs:
            fv_name = fv.get("name")
            if not fv_name:
                continue
            slot = fv_struct.setdefault(fv_name, {
                "dev_people":        {},
                "other_people":      {},
                "epics_taskkeys":    {},
                "unscoped_taskkeys": [],
                "epics_status":      {},
            })

            if grp == "dev":
                slot["dev_people"].setdefault(aname, []).append(task_obj)
            else:
                key = f"{aname}::{grp}"
                op = slot["other_people"].setdefault(key, {"name": aname, "type": grp, "tasks": []})
                op["tasks"].append(task_obj)

            # Scope tracking — record the task key against its epic (or unscoped bucket)
            if parent_key and parent_key in epic_by_key:
                slot["epics_taskkeys"].setdefault(parent_key, []).append(issue["key"])
                slot["epics_status"][parent_key] = epic_by_key[parent_key]
            else:
                slot["unscoped_taskkeys"].append(issue["key"])

    return fv_struct


def classify_fv_status(all_tasks):
    """Derive the FV's badge label from the mix of open task statuses.

    Rules:
      - No active work → "No Active Work"
      - Any "In QA*" status (and no "In Progress") → "In QA"
      - Any "In Progress" → "In Dev"
      - Otherwise → "Scheduled"
    """
    open_tasks = [t for t in all_tasks if not is_done(t["status"])]
    if not open_tasks:
        return "No Active Work"

    in_qa = sum(1 for t in open_tasks if t["status"].startswith("In QA"))
    in_prog = sum(1 for t in open_tasks if t["status"] == "In Progress")

    if in_qa > 0 and in_prog == 0:
        return "In QA"
    if in_prog > 0:
        return "In Dev"
    return "Scheduled"


def sort_tasks_by_status(tasks):
    """Status-rank sort — In Progress and live work first, To Do/New last,
    Done well at the bottom. Tie-break by descending remaining hours."""
    rank = {
        "In Progress": 0, "Reopened": 1, "Ready": 2, "Ready For QA": 3, "In QA": 4,
        "Pre-Prod In Progress": 2.5, "To Do": 5, "New": 6, "To Be Closed": 7,
        "Closed": 99, "Done": 99,
    }
    return sorted(tasks, key=lambda t: (rank.get(t["status"], 50), -t["hours"]))


def build_fv_list(fv_struct):
    """Assemble the FV list in priority order with critical-path auto-flagging.
    Returns the list ready to be JSON-dumped into the template."""
    out = []

    for fv_name in FV_ORDER:
        meta = FV_META.get(fv_name, {"color": "#94a3b8", "sub": "", "qaWeeks": 2, "release": None})
        slot = fv_struct.get(fv_name, {
            "dev_people": {}, "other_people": {}, "epics_taskkeys": {},
            "unscoped_taskkeys": [], "epics_status": {},
        })

        # Flatten everything for the FV status decision
        all_tasks = []
        for tasks in slot["dev_people"].values():
            all_tasks.extend(tasks)
        for op in slot["other_people"].values():
            all_tasks.extend(op["tasks"])

        status_label = classify_fv_status(all_tasks)
        has_active = any(not is_done(t["status"]) for t in all_tasks)
        dev_start = TODAY.isoformat() if has_active else None

        # Critical-path = dev person with the most open hours in this FV
        dev_with_hours = []
        for name, tasks in slot["dev_people"].items():
            open_h = sum(t["hours"] for t in tasks if not is_done(t["status"]))
            if open_h > 0:
                dev_with_hours.append((name, open_h))
        dev_with_hours.sort(key=lambda x: -x[1])
        critical_dev = dev_with_hours[0][0] if dev_with_hours else None

        # Build dev people array, flagging critical path + heaviest open task
        dev_people = []
        for name in sorted(slot["dev_people"].keys()):
            tasks = sort_tasks_by_status(slot["dev_people"][name])
            person_obj = {"name": name, "tasks": tasks}
            if name == critical_dev:
                person_obj["bottleneck"] = True
                for t in tasks:
                    if not is_done(t["status"]) and t["hours"] > 0:
                        t["bottleneck"] = True
                        break
            dev_people.append(person_obj)

        # Other people array (Creative / Math / Sound / Design)
        other_people = []
        for key in sorted(slot["other_people"].keys()):
            op = slot["other_people"][key]
            if not op["tasks"]:
                continue
            other_people.append({
                "name":  op["name"],
                "type":  op["type"],
                "tasks": sort_tasks_by_status(op["tasks"]),
            })

        # Scope: epics with task-key references (V2-style — buildScopeBody in JS
        # resolves keys against the per-FV taskLookup)
        scope = []
        for epic_key, task_keys in slot["epics_taskkeys"].items():
            einfo = slot["epics_status"].get(epic_key, {"summary": epic_key, "status": "New"})
            done = sum(1 for k in task_keys
                       if any(t["key"] == k and is_done(t["status"]) for t in all_tasks))
            scope.append({
                "key":      epic_key,
                "name":     einfo["summary"],
                "status":   einfo["status"],
                "done":     done,
                "total":    len(task_keys),
                "taskKeys": task_keys,
            })
        if slot["unscoped_taskkeys"]:
            done_unscoped = sum(1 for k in slot["unscoped_taskkeys"]
                                if any(t["key"] == k and is_done(t["status"]) for t in all_tasks))
            scope.append({
                "key":      None,
                "name":     "Other tasks (no epic in this FV)",
                "status":   "In Progress",
                "done":     done_unscoped,
                "total":    len(slot["unscoped_taskkeys"]),
                "taskKeys": slot["unscoped_taskkeys"],
            })

        out.append({
            "key":         fv_name,
            "color":       meta["color"],
            "sub":         meta["sub"],
            "devStart":    dev_start,
            "qaWeeks":     meta["qaWeeks"],
            "statusLabel": status_label,
            "statusStyle": STATUS_STYLES.get(status_label, STATUS_STYLES["Scheduled"]),
            "release":     meta.get("release"),
            "devPeople":   dev_people,
            "otherPeople": other_people,
            "scope":       scope,
        })

    return out


# ── Transform: Active Sprint Board (§5) ──────────────────────────────────────

def build_sprint_data(sprint_tasks):
    """Group active-sprint dev tasks by assignee for the Sprint Board section.
    Also extracts sprint metadata (name, start/end, board) from the first task's
    customfield_10103 active-sprint entry."""
    if not sprint_tasks:
        return None

    sprint_meta = None
    by_assignee = {}

    for issue in sprint_tasks:
        if parent_is_admin(issue):
            continue
        f = issue["fields"]
        itype = (f.get("issuetype") or {}).get("name") or ""
        if itype not in DEV_TYPES:
            continue  # dev-only

        if sprint_meta is None:
            sprint_meta = extract_sprint_meta(f.get(SPRINT_FIELD))

        a = f.get("assignee") or {}
        aname = norm_name(a.get("displayName"))
        by_assignee.setdefault(aname, []).append({
            "key":     issue["key"],
            "summary": f.get("summary", ""),
            "status":  (f.get("status") or {}).get("name") or "",
            "origH":   secs_to_hours(f.get("timeoriginalestimate")),
            "spentH":  secs_to_hours(f.get("timespent")),
            "hours":   secs_to_hours(f.get("timeestimate")),
            "fvs":     [fv.get("name", "") for fv in (f.get("fixVersions") or [])],
            "itype":   itype,
        })

    if not sprint_meta:
        # No active sprint detected — emit an empty board so the section
        # renders gracefully.
        return {"name": "—", "startDate": TODAY.isoformat(), "endDate": TODAY.isoformat(),
                "boardId": None, "byAssignee": {}}

    return {
        "name":       sprint_meta.get("name", "—"),
        "startDate":  (sprint_meta.get("startDate") or "")[:10],
        "endDate":    (sprint_meta.get("endDate") or "")[:10],
        "boardId":    sprint_meta.get("boardId"),
        "byAssignee": by_assignee,
    }


# ── Sprint header label (mirrors v2_timeline.current_sprint_info) ────────────

def _fmt_period(start_iso, end_iso):
    """'May 25 – Jun 7' (cross-platform)."""
    from datetime import datetime
    s = datetime.fromisoformat(start_iso).date()
    e = datetime.fromisoformat(end_iso).date()
    try:
        return f"{s.strftime('%b %-d')} – {e.strftime('%b %-d')}"
    except ValueError:
        return f"{s.strftime('%b %#d')} – {e.strftime('%b %#d')}"


def make_sprint_header(sprint_data):
    """e.g. 'IG Sprint 7: May 25 – Jun 8'."""
    if not sprint_data or not sprint_data.get("name") or sprint_data["name"] == "—":
        return "—"
    return f"{sprint_data['name']}: {_fmt_period(sprint_data['startDate'], sprint_data['endDate'])}"


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print(f"▶ Building iGaming timeline — {TODAY}")

    print("  Fetching epics …")
    epics_raw = fetch_epics()
    epic_by_key = {
        e["key"]: {
            "summary": e["fields"].get("summary", ""),
            "status":  (e["fields"].get("status") or {}).get("name") or "New",
        }
        for e in epics_raw
    }
    print(f"    {len(epic_by_key)} epics indexed")

    print("  Fetching FV tasks …")
    fv_tasks = fetch_fv_tasks()
    print(f"    {len(fv_tasks)} active FV-eligible tasks")

    print("  Fetching active sprint tasks …")
    sprint_tasks = fetch_sprint_tasks()
    print(f"    {len(sprint_tasks)} sprint items")

    print("  Building FV structure …")
    fv_struct = build_fv_structure(fv_tasks, epic_by_key)
    fv_list = build_fv_list(fv_struct)
    for fv in fv_list:
        dev_open = sum(
            t["hours"] for p in fv["devPeople"]
            for t in p["tasks"] if not is_done(t["status"])
        )
        crit = next((p["name"] for p in fv["devPeople"] if p.get("bottleneck")), None)
        print(f"    {fv['key']:<22} {fv['statusLabel']:<14} {dev_open:>6.1f}h dev open"
              + (f" · ⛓ {crit}" if crit else ""))

    print("  Building active sprint board …")
    sprint_data = build_sprint_data(sprint_tasks)
    sprint_header = make_sprint_header(sprint_data)
    if sprint_data:
        n_devs = len(sprint_data["byAssignee"])
        n_items = sum(len(v) for v in sprint_data["byAssignee"].values())
        print(f"    {sprint_header} · {n_devs} devs · {n_items} items")

    refresh_label = f"{TODAY.strftime('%B')} {TODAY.day}, {TODAY.year}"

    template_path = Path("igaming-timeline.template.html")
    template = template_path.read_text(encoding="utf-8")
    rendered = (template
        .replace("__TODAY__",          TODAY.isoformat())
        .replace("__FV_DATA__",        json.dumps(fv_list, ensure_ascii=False))
        .replace("__SPRINTS_DATA__",   json.dumps(SPRINTS, ensure_ascii=False))
        .replace("__SPRINT_DATA__",    json.dumps(sprint_data, ensure_ascii=False))
        .replace("__REFRESH_LABEL__",  refresh_label)
        .replace("__SPRINT_HEADER__",  sprint_header)
    )
    Path("igaming-timeline.html").write_text(rendered, encoding="utf-8")
    print("\n✅ igaming-timeline.html written")


if __name__ == "__main__":
    main()
