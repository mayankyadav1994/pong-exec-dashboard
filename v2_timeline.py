"""
v2_timeline.py
Pulls V2 fix-version delivery data from Jira and renders v2-timeline.html
from v2-timeline.template.html.

See docs/V2_RELEASE_TIMELINE_KNOWLEDGE.md for the spec. The mechanical model
implemented here is the pure-Jira version; producer-tuned overrides described
in docs/V2_TIMELINE_EDGE_CASES.md are intentionally not implemented yet.
"""

import json
import math
import re
import sys
from datetime import date, timedelta
from pathlib import Path

from dotenv import load_dotenv

from jira_client import jira_get, jira_jql

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
# FV_CONFIG is the *initial* fallback. main() refreshes it after fetching the
# live list of unreleased fix versions from Jira so the dashboard reflects
# what's actually in Jira right now (not just what the config has memorised).
FV_CONFIG = _build_fv_config(_CONFIG)
HIDDEN_FVS_DEFAULT = _CONFIG.get("hidden_fvs") or DEFAULT_HIDDEN_FVS


def fetch_unreleased_fvs():
    """Fetch all unreleased, non-archived fix versions for the V2 project.
    Returns list of dicts: {name, id, releaseDate, description}."""
    data = jira_get("/project/V2/versions")
    out = []
    for v in data:
        if v.get("released") or v.get("archived"):
            continue
        name = v.get("name") or ""
        if not name:
            continue
        out.append({
            "name":        name,
            "id":          v.get("id"),
            "releaseDate": v.get("releaseDate"),
            "description": v.get("description", "") or "",
        })
    return out


def _merge_fv_order(jira_fvs, config_order):
    """Combine config priority + Jira reality into a single ordered name list.
    FVs in config that exist in Jira keep their config position; FVs in Jira
    but not in config are appended (sorted by releaseDate); FVs in config but
    not in Jira drop out."""
    jira_names = {fv["name"]: fv for fv in jira_fvs}
    out, seen = [], set()
    for name in config_order:
        if name in jira_names and name not in seen:
            out.append(name)
            seen.add(name)
    extras = [fv for fv in jira_fvs if fv["name"] not in seen]
    extras.sort(key=lambda f: (f.get("releaseDate") or "9999-99-99", f["name"]))
    for fv in extras:
        out.append(fv["name"])
    return out


def _build_fv_config_live(jira_fvs, cfg):
    """Like _build_fv_config but Jira's live list drives existence. Config is
    used only for priority order and per-FV metadata overrides. FVs that
    aren't in DEFAULT_FV_CONFIG fall back to grey + 2 weeks QA + description
    from Jira as their subtitle — the manager can customize via Plan Editor."""
    default_by_key = {c["key"]: c for c in DEFAULT_FV_CONFIG}
    meta = (cfg or {}).get("fv_meta") or {}
    cfg_order = (cfg or {}).get("fv_order") or []
    jira_by_name = {f["name"]: f for f in jira_fvs}
    out = []
    for name in _merge_fv_order(jira_fvs, cfg_order):
        if name in default_by_key:
            merged = dict(default_by_key[name])
        else:
            jira_fv = jira_by_name.get(name, {})
            merged = {
                "color":   "#94a3b8",
                "sub":     jira_fv.get("description") or "",
                "qaWeeks": 2,
            }
        # Jira's releaseDate is the default target; config-level target_date
        # (set via Plan Editor → Save-as-default) overrides it via meta.update.
        jira_rel = (jira_by_name.get(name) or {}).get("releaseDate")
        if jira_rel:
            merged.setdefault("jira_release_date", jira_rel)
        merged.update(meta.get(name, {}))
        merged["key"] = name
        out.append(merged)
    return out

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

# ── QA-cycle detection (Release-ticket signal, V2-only) ─────────────────────
# When an FV has a Release-issuetype parent ticket and any of its child
# round-tickets is currently being worked, the build is in a QA cycle —
# even if the regular dev-task-count rule still sees a few `Ready` /
# `In Progress` stragglers that would otherwise classify it as
# "In Development". The Release-ticket signal trumps the count rule.
#
# Child summaries follow the convention "<FV name> - R<n>" (e.g.
# "V2 PT 13.30 - R3"), so the round label on the pill becomes the same
# "R<n>" suffix.

_ROUND_RE = re.compile(r'\bR(\d+)\b', re.IGNORECASE)

ACTIVE_QA_STATUSES = {
    "In Progress", "In-Progress", "In QA", "In QA R1", "In QA R2",
    "Ready For QA", "QA In Progress", "In-Review", "Reopened", "Re-opened",
}

def _extract_round(summary):
    """Pull the 'R<n>' suffix out of a child ticket summary; None if missing."""
    if not summary:
        return None
    m = _ROUND_RE.search(summary)
    return f"R{m.group(1)}" if m else None


def fetch_release_tickets(fv_name):
    """Fetch Release-type parents for an FV plus any tickets parented under
    them (the 'round' sub-tickets). Returns the parent list with each
    parent's children attached as `_children`."""
    parents = jira_jql(
        jql=f'project = V2 AND fixVersion = "{fv_name}" AND issuetype = "Release"',
        fields=["summary", "status", "issuetype"],
    )
    if not parents:
        return []
    keys = ",".join(p["key"] for p in parents)
    children = jira_jql(
        jql=f"parent in ({keys})",
        fields=["summary", "status", "parent"],
    )
    by_parent = {p["key"]: [] for p in parents}
    for c in children:
        pk = ((c.get("fields", {}).get("parent") or {}).get("key"))
        if pk in by_parent:
            by_parent[pk].append(c)
    for p in parents:
        p["_children"] = by_parent.get(p["key"], [])
    return parents


def detect_qa_round(release_tickets):
    """If any release-round child is in an active QA-cycle status, return
    its 'R<n>' tag. With multiple active rounds in flight, picks the
    HIGHEST R-number (most current round). Returns None when nothing
    active matches the pattern."""
    active = []
    for p in release_tickets:
        for c in p.get("_children", []):
            cf = c.get("fields") or {}
            status_name = (cf.get("status") or {}).get("name", "")
            if status_name in ACTIVE_QA_STATUSES:
                r = _extract_round(cf.get("summary", ""))
                if r:
                    active.append(r)
    if not active:
        return None
    active.sort(key=lambda r: int(r[1:]), reverse=True)
    return active[0]


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
              "timeestimate", "timeoriginalestimate", "timespent", "parent"]
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
        hours   = round((fields.get("timeestimate") or 0) / 3600)
        # origH / spentH feed the detail-panel "Total Scope · Done · Progress"
        # strip (used to compute % complete at the FV level). Hours-only;
        # keep estimates rounded the same way `hours` is for visual consistency.
        orig_h  = round((fields.get("timeoriginalestimate") or 0) / 3600, 2)
        spent_h = round((fields.get("timespent") or 0) / 3600, 2)
        task  = {
            "key":     issue["key"],
            "summary": fields.get("summary", ""),
            "hours":   hours,
            "origH":   orig_h,
            "spentH":  spent_h,
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


def classify_status_label(dev_people, qa_round=None):
    """Auto-derive FV status label from the *majority* of open task statuses.

    Rules (50% threshold, inclusive — see docs/V2_TIMELINE_EDGE_CASES.md §16):
      • Release-ticket signal (qa_round set)        → In QA · R<n>   (NEW)
      • ≥50% of open tickets in QA-like status     → In QA      (devStart=null)
      • ≥50% of open tickets in "New" status        → Scheduled (devStart=today)
      • otherwise                                    → In Development

    The Release-ticket signal beats every count-based rule — when a release
    round is in flight, the build is in QA regardless of how many `Ready` or
    `In Progress` stragglers still sit on the dev side.

    QA check runs before New, so a hypothetical 50/50 QA/New tie lands on
    "In QA" — defensible because QA is the later phase.
    """
    if qa_round:
        return (f"In QA · {qa_round}", None)
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
    n = 0
    d = start
    while d < end_exclusive:
        if d.weekday() < 5 and d.isoformat() not in HOLIDAYS:
            n += 1
        d += timedelta(days=1)
    return n


# ── Drift snapshot machinery (history of projected end dates) ───────────────
# Ported from the template's calcDevEnd so daily Python runs produce the same
# projection a viewer would see with default buffers. Snapshots live in
# snapshots/v2/<YYYY-MM-DD>.json and feed the ghost-bar + HISTORY tab UI.

def _add_wd_hours(start, hours, daily_rate):
    """Add ceil(hours/daily_rate) Mon-Fri non-holiday days to `start`."""
    if hours <= 0:
        return start
    days = math.ceil(hours / daily_rate)
    d, added = start, 0
    while added < days:
        d = d + timedelta(days=1)
        if d.weekday() < 5 and d.isoformat() not in HOLIDAYS:
            added += 1
    return d


def _calc_dev_end(fvs, fv_idx, fv):
    """Mirror of the template's calcDevEnd using default per-person buffers."""
    if not fv.get("devStart"):
        return TODAY
    latest = TODAY
    for p in fv.get("devPeople", []):
        name = p.get("name", "")
        open_h = sum(t["hours"] for t in p.get("tasks", []) if not is_done(t["status"]))
        # Queued = this person's open hours on higher-priority (lower-index) FVs
        queued = 0
        for j in range(fv_idx):
            other = fvs[j]
            if other.get("isScenario"):
                continue
            for op in other.get("devPeople", []):
                if op.get("name") == name:
                    queued += sum(t["hours"] for t in op.get("tasks", []) if not is_done(t["status"]))
        total = open_h + queued
        if total <= 0:
            continue
        done = _add_wd_hours(TODAY, total, hpd(name))
        if done > latest:
            latest = done
    return latest


def compute_snapshot(fvs):
    """Build today's drift snapshot for all non-scenario FVs."""
    snap = {"date": TODAY.isoformat(), "fvs": {}}
    for idx, fv in enumerate(fvs):
        if fv.get("isScenario"):
            continue
        dev_end = _calc_dev_end(fvs, idx, fv)
        qa_weeks = fv.get("qaWeeks") or 0
        qa_end = _add_wd_hours(dev_end, qa_weeks * 5 * 8, 8) if qa_weeks > 0 else dev_end
        lab1_end = pilot_end = final_end = None
        if fv.get("isLab"):
            lab1_end = _add_wd_hours(qa_end, (fv.get("lab1Weeks") or 0) * 5 * 8, 8)
            pilot_end = _add_wd_hours(lab1_end, (fv.get("pilotWeeks") or 0) * 5 * 8, 8)
            final_end = _add_wd_hours(pilot_end, (fv.get("lab2Weeks") or 0) * 5 * 8, 8)
        all_tasks = (
            [t for p in fv.get("devPeople", []) for t in p.get("tasks", [])]
            + [t for p in fv.get("otherPeople", []) for t in p.get("tasks", [])]
        )
        snap["fvs"][fv["key"]] = {
            "devEnd":   dev_end.isoformat()   if dev_end   else None,
            "qaEnd":    qa_end.isoformat()    if qa_end    else None,
            "lab1End":  lab1_end.isoformat()  if lab1_end  else None,
            "pilotEnd": pilot_end.isoformat() if pilot_end else None,
            "finalEnd": final_end.isoformat() if final_end else None,
            "totalEstH": round(sum(t.get("origH", 0) for t in all_tasks)),
            "spentH":    round(sum(t.get("spentH", 0) for t in all_tasks)),
        }
    return snap


def write_snapshot(dashboard_key, snap):
    """Persist snap to snapshots/<dashboard>/<date>.json; trim files >90d old."""
    snap_dir = Path("snapshots") / dashboard_key
    snap_dir.mkdir(parents=True, exist_ok=True)
    (snap_dir / f"{snap['date']}.json").write_text(
        json.dumps(snap, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    cutoff = TODAY - timedelta(days=90)
    for f in snap_dir.glob("*.json"):
        try:
            if date.fromisoformat(f.stem) < cutoff:
                f.unlink()
        except (ValueError, OSError):
            continue


def load_snapshots(dashboard_key, n=30):
    """Return last n snapshots (chronological) for injection into the template."""
    snap_dir = Path("snapshots") / dashboard_key
    if not snap_dir.exists():
        return []
    files = sorted(snap_dir.glob("*.json"))[-n:]
    out = []
    for f in files:
        try:
            out.append(json.loads(f.read_text(encoding="utf-8")))
        except Exception:
            continue
    return out


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

    # Release-ticket signal — trumps the dev-task-count rule when the build
    # is in QA cycles. Adds one extra Jira call per FV (parents + children).
    release_tickets = fetch_release_tickets(cfg["key"])
    qa_round = detect_qa_round(release_tickets)
    if qa_round:
        print(f"  release signal: QA round in progress → {qa_round}")
    status_label, dev_start = classify_status_label(dev_people, qa_round=qa_round)

    # Use per-FV indev_style when available and the FV is in development,
    # otherwise fall back to the global STATUS_STYLES table. For composite
    # labels like "In QA · R3", strip the " · …" suffix before the lookup so
    # we reuse the base "In QA" style rather than needing a new dict key per
    # round.
    style_key = status_label.split(" · ", 1)[0]
    if style_key == "In Development" and cfg.get("indev_style"):
        status_style = cfg["indev_style"]
    else:
        status_style = STATUS_STYLES.get(style_key) or STATUS_STYLES["Scheduled"]

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
    # Target date for the right-side summary panel: config override wins over
    # Jira's releaseDate, which wins over None (shown as "TBD" in the UI).
    target = cfg.get("target_date") or cfg.get("jira_release_date")
    if target:
        fv["targetDate"] = target
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

    # Refresh FV_CONFIG against Jira's live unreleased FV list. The config
    # provides priority order + metadata overrides (color, qaWeeks, lab
    # pipeline, sales trip), but Jira decides what actually exists. FVs
    # released or archived in Jira drop out automatically; new ones appear
    # with sensible defaults that the manager can customize via Plan Editor.
    global FV_CONFIG
    print("  Fetching unreleased fix versions from Jira …")
    jira_fvs = fetch_unreleased_fvs()
    print(f"    {len(jira_fvs)} unreleased fix versions: {[f['name'] for f in jira_fvs]}")
    FV_CONFIG = _build_fv_config_live(jira_fvs, _CONFIG)
    cfg_order = set((_CONFIG.get('fv_order') or []))
    new_fvs   = [c['key'] for c in FV_CONFIG if c['key'] not in cfg_order]
    gone_fvs  = [n for n in cfg_order if n not in {f['name'] for f in jira_fvs}]
    if new_fvs:  print(f"    auto-added (not in config): {new_fvs}")
    if gone_fvs: print(f"    dropped (released/archived/renamed in Jira): {gone_fvs}")

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

    # Drift snapshot — compute today's projections, persist, then load the
    # recent history. The template uses these for the ghost bar + HISTORY tab.
    today_snap = compute_snapshot(fvs)
    write_snapshot("v2", today_snap)
    snapshots = load_snapshots("v2", n=30)
    print(f"\n  snapshot: wrote {len(today_snap['fvs'])} FV projections · loaded {len(snapshots)} historical snapshots")

    template = Path("v2-timeline.template.html").read_text(encoding="utf-8")
    rendered = (template
        .replace("__TODAY__",         TODAY.isoformat())
        .replace("__FV_DATA__",       json.dumps(fvs, ensure_ascii=False))
        .replace("__SPRINTS_DATA__",  json.dumps(SPRINTS, ensure_ascii=False))
        .replace("__SPRINT__",        json.dumps(sprint_info, ensure_ascii=False))
        .replace("__SPRINT_LOGS__",   json.dumps(sprint_logs, ensure_ascii=False))
        .replace("__HOLIDAYS__",      json.dumps(sorted(HOLIDAYS), ensure_ascii=False))
        # SERVER_FV_META = the fv_meta dict actually persisted in config/v2.json
        # (NOT the merged-with-auto-discovery version). The Save-as-default flow
        # uses this as the base for the payload so auto-discovered FVs with grey
        # defaults don't appear as fake "changes" in the diff. See template
        # buildConfigPayload() for the consumer.
        .replace("__SERVER_FV_META__",json.dumps(_CONFIG.get("fv_meta") or {}, ensure_ascii=False))
        # _meta.published_at + _meta.published_by are written by the
        # Save-as-default flow (browser → GitHub PUT). The opt-in banner
        # (ported from game-pipeline #52) compares these to localStorage
        # so a viewer with local edits learns when a newer shared plan
        # has been published. Empty string = no publish marker yet.
        .replace("__SERVER_PUBLISHED_AT__", json.dumps((_CONFIG.get("_meta") or {}).get("published_at") or ""))
        .replace("__SERVER_PUBLISHED_BY__", json.dumps((_CONFIG.get("_meta") or {}).get("published_by") or ""))
        # SNAPSHOTS: last 30 days of projection history for the ghost-bar +
        # HISTORY tab. Each entry = {date, fvs:{key:{devEnd,qaEnd,…,spentH}}}.
        .replace("__SNAPSHOTS__",     json.dumps(snapshots, ensure_ascii=False))
        .replace("__REFRESH_LABEL__", refresh_label)
        .replace("__SPRINT_HEADER__", sprint_header)
    )
    Path("v2-timeline.html").write_text(rendered, encoding="utf-8")
    print("\n✅ v2-timeline.html written")


if __name__ == "__main__":
    main()
