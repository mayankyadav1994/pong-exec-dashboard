"""
v2_timeline.py
Pulls V2 fix-version delivery data from Jira and renders v2-timeline.html
from v2-timeline.template.html.

See docs/V2_RELEASE_TIMELINE_KNOWLEDGE.md for the spec. The mechanical model
implemented here is the pure-Jira version; producer-tuned overrides described
in docs/V2_TIMELINE_EDGE_CASES.md are intentionally not implemented yet.
"""

import json
import sys
from datetime import date
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

# Per-FV config in priority order; queuedHours flows top → bottom.
# Source: docs/V2_RELEASE_TIMELINE_KNOWLEDGE.md §2, §6, §7 (Lab), §8 (Sales),
# §9 (Sprints), §11 (Colours).
#
# Regulated releases (P2P 16.00, PT 14.00) carry the lab pipeline config and
# Sales Trip pin. "indev_style" lets each release's "In Development" badge
# pick up the FV's accent colour.
#
# These are *defaults*. Live values come from config/v2.json if present
# (see load_config below). Fallback values here keep working if the config
# file is missing or unparseable.
DEFAULT_FV_CONFIG = [
    {"key": "V2 SW 15.00",  "color": "#60a5fa",
     "sub": "Sweepstakes · Games",
     "qaWeeks": 2},

    {"key": "V2 SW 16.00",  "color": "#93c5fd",
     "sub": "Offline Upgrader · Pay It Forward",
     "qaWeeks": 2,
     "note": 'Jira fix version "V2 SW 16.00" — mapped from "V2 SW P2P 16.00"',
     "indev_style": "color:#1e3a8a;border-color:rgba(37,99,235,.3);background:rgba(37,99,235,.09)"},

    {"key": "V2 PT 13.30",  "color": "#c4b5fd",
     "sub": "8× Game Ports · Volume Buttons · Server Cleanup",
     "qaWeeks": 2,
     "indev_style": "color:#3b0764;border-color:rgba(124,58,237,.3);background:rgba(124,58,237,.09)"},

    {"key": "V2 P2P 16.00", "color": "#fb923c",
     "sub": "Georgia P2P · Mechanical Meters · Task Handler R7",
     "qaWeeks": 3,
     "indev_style": "color:#7c2d12;border-color:rgba(234,88,12,.3);background:rgba(234,88,12,.09)",
     "isLab": True, "lab1Weeks": 4, "pilotWeeks": 2, "lab2Weeks": 4,
     "salesTrip": {"date": "2026-06-27", "label": "Sales Trip · Georgia"}},

    {"key": "V2 HHR 3.00",  "color": "#4ade80",
     "sub": "User Role Restrictions · Maintenance Menu",
     "qaWeeks": 2,
     "indev_style": "color:#14532d;border-color:rgba(22,163,74,.3);background:rgba(22,163,74,.09)"},

    {"key": "V2 PT 14.00",  "color": "#f87171",
     "sub": "Ohio Central Server · Location Server · Pull Tab Print",
     "qaWeeks": 3,
     "isLab": True, "lab1Weeks": 4, "pilotWeeks": 2, "lab2Weeks": 4,
     "salesTrip": {"tbd": True, "label": "Sales Trip · Ohio"}},

    {"key": "V2 C2 5.10",   "color": "#a78bfa",
     "sub": "C2 Game Ports · Bingo Paytables · 4 Games",
     "qaWeeks": 2,
     "indev_style": "color:#3b0764;border-color:rgba(124,58,237,.25);background:rgba(124,58,237,.07)"},
]
DEFAULT_HIDDEN_FVS: list = []
DEFAULT_HOLIDAYS = {"2026-05-18"}  # Victoria Day


def load_config():
    """Read config/v2.json if present. Returns a dict with fv_order, fv_meta,
    holidays, hidden_fvs keys (any may be missing → fall back to defaults).
    Never raises — bad JSON falls through silently so the workflow can't be
    bricked by a malformed save from the panel."""
    cfg_path = Path(__file__).parent / "config" / "v2.json"
    if not cfg_path.exists():
        print(f"  ℹ no config file at {cfg_path.name}; using hardcoded defaults")
        return {}
    try:
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        print(f"  ✓ loaded config/{cfg_path.name}")
        return cfg
    except (json.JSONDecodeError, OSError) as e:
        print(f"  ⚠ could not parse config/{cfg_path.name} ({e}); using defaults")
        return {}


def _build_fv_config(cfg):
    """Compose the FV_CONFIG list the rest of the script consumes from either
    the loaded JSON config or the hardcoded defaults.

    Each entry merges fv_meta[key] with {"key": key} so the schema matches
    DEFAULT_FV_CONFIG exactly. Order comes from fv_order; FVs missing from
    fv_meta keep their DEFAULT_FV_CONFIG values."""
    if not cfg:
        return list(DEFAULT_FV_CONFIG)
    default_by_key = {c["key"]: c for c in DEFAULT_FV_CONFIG}
    meta = cfg.get("fv_meta") or {}
    order = cfg.get("fv_order") or [c["key"] for c in DEFAULT_FV_CONFIG]
    out = []
    for key in order:
        merged = dict(default_by_key.get(key, {}))
        merged.update(meta.get(key, {}))
        merged["key"] = key
        out.append(merged)
    return out


_CONFIG = load_config()
FV_CONFIG = _build_fv_config(_CONFIG)
HIDDEN_FVS_DEFAULT = _CONFIG.get("hidden_fvs") or DEFAULT_HIDDEN_FVS

DEV_TYPES   = ["Story", "Dev Task", "Dev Subtask"]
OTHER_TYPES = ["Creative Task", "Creative Subtask", "Sound Task", "Sound Subtask",
               "Math Task", "Math Subtask", "Pre-Prod Task"]

# Default styles used when a per-FV indev_style isn't provided (SW 15 in QA,
# PT 14 Scheduled — both fall here).
STATUS_STYLES = {
    "In QA":          "color:#92400e;border-color:rgba(217,119,6,.35);background:rgba(217,119,6,.10)",
    "In Development": "color:#1e3a8a;border-color:rgba(37,99,235,.3);background:rgba(37,99,235,.09)",
    "Scheduled":      "color:#7f1d1d;border-color:rgba(220,38,38,.3);background:rgba(220,38,38,.09)",
}

# Source: docs/V2_RELEASE_TIMELINE_KNOWLEDGE.md §9 — chart now runs S1–S15
# (May 11 → Dec 7) so Lab 2 endpoints for regulated releases fit on screen.
SPRINTS = [
    {"label": "S1",  "start": "2026-05-11"},
    {"label": "S2",  "start": "2026-05-25"},
    {"label": "S3",  "start": "2026-06-08"},
    {"label": "S4",  "start": "2026-06-22"},
    {"label": "S5",  "start": "2026-07-06"},
    {"label": "S6",  "start": "2026-07-20"},
    {"label": "S7",  "start": "2026-08-03"},
    {"label": "S8",  "start": "2026-08-17"},
    {"label": "S9",  "start": "2026-08-31"},
    {"label": "S10", "start": "2026-09-14"},
    {"label": "S11", "start": "2026-09-28"},
    {"label": "S12", "start": "2026-10-12"},
    {"label": "S13", "start": "2026-10-26"},
    {"label": "S14", "start": "2026-11-09"},
    {"label": "S15", "start": "2026-11-23"},
    {"label": "",    "start": "2026-12-07"},
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
    """Auto-derive FV status label from the *majority* of open task statuses.

    Rules (50% threshold, inclusive — see docs/V2_TIMELINE_EDGE_CASES.md §16):
      • ≥50% of open tickets in QA-like status     → In QA      (devStart=null)
      • ≥50% of open tickets in "New" status        → Scheduled (devStart=today)
      • otherwise                                    → In Development

    QA check runs before New, so a hypothetical 50/50 QA/New tie lands on
    "In QA" — defensible because QA is the later phase.
    """
    open_tasks = [t for p in dev_people for t in p["tasks"] if not is_done(t["status"])]
    if not open_tasks:
        return ("In QA", None)

    qa_like = {"In QA", "In QA R1", "In QA R2", "Ready For QA", "QA In Progress"}
    n_total = len(open_tasks)
    n_qa    = sum(1 for t in open_tasks if t["status"] in qa_like)
    n_new   = sum(1 for t in open_tasks if t["status"] == "New")

    if n_qa  / n_total >= 0.5:
        return ("In QA", None)
    if n_new / n_total >= 0.5:
        return ("Scheduled", TODAY.isoformat())
    return ("In Development", TODAY.isoformat())


def mark_bottleneck_for_fv(fv, fvs_before):
    """
    Mark the bottleneck person + their highest-hour open task on a single FV.
    Bottleneck = person whose (open_hours + queued_hours from prior FVs) / hpd is
    the largest. Note: we don't *emit* queuedHours — the template's getDynamicQueued
    computes it at render time so it stays in sync with drag-reorder and scenarios.
    The bottleneck flag is precomputed for visual styling (red ⛓ pill).
    """
    def queued_for(name):
        total = 0
        for prev in fvs_before:
            prev_p = next((x for x in prev["devPeople"] if x["name"] == name), None)
            if prev_p:
                total += sum(t["hours"] for t in prev_p["tasks"] if not is_done(t["status"]))
        return total

    best_days, best_person = -1, None
    for p in fv["devPeople"]:
        open_h = sum(t["hours"] for t in p["tasks"] if not is_done(t["status"]))
        total  = open_h + queued_for(p["name"])
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


# ── Scope (epic grouping) ────────────────────────────────────────────────────

def compute_scope(all_issues):
    """
    Group issues by their direct Jira parent (epic or story) for the Scope tab.

    For each parent, emit one entry with key/name/status/done/total/taskKeys.
    Tasks without a parent are dropped (they have no scope context).

    Known limitation (docs/V2_TIMELINE_EDGE_CASES.md §17): when an issue's
    direct parent is a Story rather than an Epic, the grouping shows the Story
    as the scope item. Grandparent-epic resolution would need a second query.
    """
    by_parent = {}
    for issue in all_issues:
        if parent_is_admin(issue):
            continue
        fields = issue["fields"]
        parent = fields.get("parent")
        if not parent:
            continue
        pkey = parent.get("key")
        if not pkey:
            continue
        pfields = parent.get("fields") or {}
        entry = by_parent.setdefault(pkey, {
            "key":      pkey,
            "name":     pfields.get("summary", pkey),
            "status":   ((pfields.get("status") or {}).get("name") or "New"),
            "done":     0,
            "total":    0,
            "taskKeys": [],
        })
        entry["taskKeys"].append(issue["key"])
        entry["total"] += 1
        status_cat = (fields.get("status") or {}).get("statusCategory", {}).get("key")
        if status_cat == "done":
            entry["done"] += 1
    # Sort: by descending total tasks (most active scope items first)
    return sorted(by_parent.values(), key=lambda x: -x["total"])


# ── Sprint info (current sprint, workdays elapsed) ───────────────────────────

HOLIDAYS = set(_CONFIG.get("holidays") or DEFAULT_HOLIDAYS)  # keep in sync with template

def count_workdays(start, end_exclusive):
    """Mon-Fri days in [start, end_exclusive) excluding holidays."""
    from datetime import timedelta
    n = 0
    d = start
    while d < end_exclusive:
        if d.weekday() < 5 and d.isoformat() not in HOLIDAYS:
            n += 1
        d += timedelta(days=1)
    return n


def _fmt_period(start, end_inclusive):
    """'May 11 – May 24' (cross-platform)."""
    try:
        return f"{start.strftime('%b %-d')} – {end_inclusive.strftime('%b %-d')}"
    except ValueError:
        return f"{start.strftime('%b %#d')} – {end_inclusive.strftime('%b %#d')}"


def current_sprint_info():
    """
    Detect which sprint TODAY falls into and compute work-days elapsed / total.
    Returns the dict the template expects:
      { label, period, workDaysToDate, workDaysTotal }
    """
    from datetime import timedelta
    for i, s in enumerate(SPRINTS[:-1]):
        start = date.fromisoformat(s["start"])
        next_start = date.fromisoformat(SPRINTS[i + 1]["start"])
        if start <= TODAY < next_start:
            end_inclusive = next_start - timedelta(days=1)
            return {
                "label":          s["label"],
                "period":         _fmt_period(start, end_inclusive),
                # workDaysToDate is inclusive of TODAY (we count today as elapsed)
                "workDaysToDate": count_workdays(start, TODAY + timedelta(days=1)),
                "workDaysTotal":  count_workdays(start, next_start),
            }
    return {"label": "—", "period": "—", "workDaysToDate": 0, "workDaysTotal": 0}


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

    # Use per-FV indev_style when available and the FV is in development,
    # otherwise fall back to the global STATUS_STYLES table.
    if status_label == "In Development" and cfg.get("indev_style"):
        status_style = cfg["indev_style"]
    else:
        status_style = STATUS_STYLES[status_label]

    # Scope: group dev + other issues by Jira parent (epic/story)
    scope = compute_scope(dev_issues + other_issues)
    if scope:
        print(f"  scope: {len(scope)} parent groups")

    fv = {
        "key":          cfg["key"],
        "color":        cfg["color"],
        "sub":          cfg["sub"],
        "devStart":     dev_start,
        "qaWeeks":      cfg["qaWeeks"],
        "statusLabel":  status_label,
        "statusStyle":  status_style,
        "devPeople":    dev_people,
        "otherPeople":  other_people,
        "scope":        scope,
    }
    if cfg.get("note"):
        fv["note"] = cfg["note"]
    # Regulated-release fields (lab pipeline + Sales Trip pin)
    if cfg.get("isLab"):
        fv["isLab"]      = True
        fv["lab1Weeks"]  = cfg["lab1Weeks"]
        fv["pilotWeeks"] = cfg["pilotWeeks"]
        fv["lab2Weeks"]  = cfg["lab2Weeks"]
    if cfg.get("salesTrip"):
        fv["salesTrip"] = cfg["salesTrip"]
    # Config-only fields — passed straight through from config/v2.json so
    # the dashboard JS can render scope-tab overrides and milestone pins.
    fv["epic_overrides"] = cfg.get("epic_overrides") or {}
    fv["milestones"]     = cfg.get("milestones") or []
    return fv


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print(f"▶ Building V2 timeline — {TODAY}")
    fvs = [build_fv(cfg) for cfg in FV_CONFIG]
    print()

    # Bottleneck per FV — uses fvs[:i] as the "higher priority" baseline,
    # matching what JS getDynamicQueued sees at default order.
    for i, fv in enumerate(fvs):
        bp, days = mark_bottleneck_for_fv(fv, fvs[:i])
        if bp:
            print(f"  {fv['key']}: critical path = {bp['name']} (~{days:.1f} business days remaining)")
        else:
            print(f"  {fv['key']}: no critical path (no open dev work)")

    # Sprint info (label, period, workdays elapsed / total)
    sprint_info  = current_sprint_info()
    sprint_logs  = {}  # SPRINT_LOGS: live Tempo/worklog fetch deferred — see
                       # docs/V2_TIMELINE_EDGE_CASES.md §18. With {}, the
                       # Sprint Activity panel renders "No hours logged".

    refresh_label  = f"{TODAY.strftime('%B')} {TODAY.day}, {TODAY.year}"
    sprint_header  = f"{sprint_info['label']}: {sprint_info['period']}"
    print(f"\n  sprint: {sprint_header} · {sprint_info['workDaysToDate']}/{sprint_info['workDaysTotal']} work days elapsed")

    template = Path("v2-timeline.template.html").read_text(encoding="utf-8")
    rendered = (template
        .replace("__TODAY__",         TODAY.isoformat())
        .replace("__FV_DATA__",       json.dumps(fvs, ensure_ascii=False))
        .replace("__SPRINTS_DATA__",  json.dumps(SPRINTS, ensure_ascii=False))
        .replace("__SPRINT__",        json.dumps(sprint_info, ensure_ascii=False))
        .replace("__SPRINT_LOGS__",   json.dumps(sprint_logs, ensure_ascii=False))
        .replace("__HOLIDAYS__",      json.dumps(sorted(HOLIDAYS), ensure_ascii=False))
        .replace("__REFRESH_LABEL__", refresh_label)
        .replace("__SPRINT_HEADER__", sprint_header)
    )
    Path("v2-timeline.html").write_text(rendered, encoding="utf-8")
    print("\n✅ v2-timeline.html written")


if __name__ == "__main__":
    main()
