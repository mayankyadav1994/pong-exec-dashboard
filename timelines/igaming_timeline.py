"""
igaming_timeline.py
Pulls iGaming fix-version delivery data from Jira and renders
igaming-timeline.html from igaming-timeline.template.html.

See docs/IG_RELEASE_TIMELINE_KNOWLEDGE.md for the spec. Same structure as
v2_timeline.py but with iGaming-specific data-shaping rules (4 department
lanes including Design, no Lab pipeline, customfield_10103 for Sprint).
"""

import json
import math
import os
import re
import sys
from datetime import date, timedelta
from pathlib import Path

from dotenv import load_dotenv

HERE = Path(__file__).resolve().parent     # timelines/ — template, page, snapshots
REPO = HERE.parent                         # repo root — config/, shared/
sys.path.insert(0, str(REPO / "shared"))   # shared/jira_client.py
from jira_client import agile_get, jira_get, jira_jql  # noqa: E402

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
# These are *defaults*. The live values come from config/igaming.json if the
# file exists (see load_config() below). Hand-edits to the defaults here keep
# working as fallbacks if the config file is missing or unparseable.
DEFAULT_FV_ORDER = [
    "ELG 3.30", "ELG 4.00", "PFH2 Games 2.10", "Horse Play 1.00", "ELG 4.10",
    "PFH2 Games 2.00", "PFH2 Games 3.00", "PFH2 Games 4.00", "PFH2 Services 3.00",
    "ELG Game Configs", "ELG Website", "ELG New Games", "ELG Feature Backlog",
    "PFH2 Games Backlog", "New Games - iGaming",
]

DEFAULT_FV_META = {
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
DEFAULT_HOLIDAYS = {"2026-05-18"}  # Victoria Day
DEFAULT_HIDDEN_FVS: list = []


def load_config():
    """Read config/igaming.json if present. Return a dict with fv_order,
    fv_meta, holidays, hidden_fvs keys (any may be missing → fall back to
    DEFAULT_*). Never raises — bad JSON falls through to defaults so the
    workflow can't be bricked by a malformed save."""
    cfg_path = REPO / "config" / "igaming.json"
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


_CONFIG = load_config()

# ── FV list — auto-discovered from Jira, layered with config overrides ──
# These are the *initial* values at module load. main() refreshes them after
# fetching the live list of unreleased fix versions from Jira. The config is
# used for priority order and per-FV metadata overrides (color, qaWeeks,
# milestones, etc.) — not for deciding which FVs to render. FVs released
# or archived in Jira drop out automatically; new unreleased FVs appear
# with default styling that the manager can customize via Plan Editor.
FV_ORDER    = _CONFIG.get("fv_order")    or DEFAULT_FV_ORDER
FV_META     = _CONFIG.get("fv_meta")     or DEFAULT_FV_META
HIDDEN_FVS_DEFAULT = _CONFIG.get("hidden_fvs") or DEFAULT_HIDDEN_FVS


def fetch_unreleased_fvs():
    """Fetch all unreleased, non-archived fix versions for the IG project.
    Returns list of dicts: {name, id, releaseDate, description}."""
    data = jira_get("/project/IG/versions")
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


def merge_fv_lists(jira_fvs, config_order, config_meta):
    """Combine Jira's live list of unreleased FVs with the config's priority
    order + metadata overrides. Returns (active_order, active_meta).

    Rules:
      * FVs in config_order that exist in Jira keep their config position.
      * FVs in Jira but not in config_order are appended, sorted by
        releaseDate ascending (null/missing → bottom, ties by name).
      * FVs in config_order that no longer exist in Jira (released, archived,
        renamed) drop out silently.

    Metadata layering for each rendered FV:
      * Start with defaults sourced from the Jira version (release date,
        description). Color defaults to grey so newly-discovered FVs are
        visually distinguishable.
      * Layer config_meta[name] on top — any field the config sets wins,
        but fields the config doesn't set keep their auto-defaults.
    """
    jira_names = {fv["name"]: fv for fv in jira_fvs}

    out_order = []
    seen = set()
    for name in config_order:
        if name in jira_names and name not in seen:
            out_order.append(name)
            seen.add(name)
    extras = [fv for fv in jira_fvs if fv["name"] not in seen]
    extras.sort(key=lambda f: (f.get("releaseDate") or "9999-99-99", f["name"]))
    for fv in extras:
        out_order.append(fv["name"])

    out_meta = {}
    for name in out_order:
        jira_fv = jira_names[name]
        defaults = {
            "color":   "#94a3b8",  # grey — easy to spot until manager customizes
            "sub":     jira_fv.get("description") or "",
            "qaWeeks": 2,
            "release": jira_fv.get("releaseDate"),
        }
        out_meta[name] = {**defaults, **(config_meta.get(name) or {})}
        # Jira's own date survives a config override so the editor can fall
        # back to it when the Target Date is cleared.
        out_meta[name]["jira_release"] = jira_fv.get("releaseDate")
    return out_order, out_meta

# Status badge styles (§3.5)
STATUS_STYLES = {
    "In QA":           "color:#92400e;border-color:rgba(217,119,6,.35);background:rgba(217,119,6,.10)",
    "In Dev":          "color:#1e40af;border-color:rgba(37,99,235,.35);background:rgba(37,99,235,.10)",
    "Scheduled":       "color:#475569;border-color:rgba(100,116,139,.35);background:rgba(100,116,139,.10)",
    "No Active Work":  "color:#475569;border-color:rgba(100,116,139,.35);background:rgba(100,116,139,.10)",
}

# Sprint axis — real dates from the IG board (same board the game pipeline
# reads, JIRA_BOARD_ID_IG), with the 14-day cadence synthesised past the last
# scheduled sprint so the axis never runs out. Labels follow the board's own
# numbering: "IG Sprint 6" started 2026-05-11 (the chart start) → chip "S6".
# The previous hand-maintained list stopped at S20 (Nov 23).
SPRINT_ANCHOR = date(2026, 5, 11)   # IG Sprint 6
SPRINT_ANCHOR_N = 6
SPRINT_DAYS = 14
IG_BOARD_ID = os.getenv("JIRA_BOARD_ID_IG", "250")
CHART_END = date(2027, 3, 15)       # keep in sync with CHART_END in the template

# Populated by build_sprints() at the top of main(); [] until then so importing
# this module never fires a Jira call.
SPRINTS = []


def anchored_label(start):
    """S{n} label on the IG board's numbering; None before the chart's S6."""
    n = round((start - SPRINT_ANCHOR).days / SPRINT_DAYS) + SPRINT_ANCHOR_N
    return f"S{n}" if n >= SPRINT_ANCHOR_N else None


def fetch_board_sprints():
    """Scheduled IG sprints from the board, oldest first. The board also lists
    V2/CSS sprints and dateless future rows, so filter by name and skip
    sprints without dates (covered by the synthesised tail)."""
    raw, start_at = [], 0
    while True:
        data = agile_get(f"/board/{IG_BOARD_ID}/sprint",
                         {"state": "active,closed,future",
                          "startAt": start_at, "maxResults": 50})
        values = data.get("values") or []
        raw.extend(values)
        if data.get("isLast", True) or not values:
            break
        start_at += len(values)
    out = []
    for v in raw:
        if not (v.get("name") or "").strip().lower().startswith("ig sprint"):
            continue
        start, end = v.get("startDate"), v.get("endDate")
        if not start or not end:
            continue
        out.append({"start": date.fromisoformat(start[:10])})
    out.sort(key=lambda x: x["start"])
    return out


def build_sprints():
    """Sprint axis: board start dates where Jira has them, anchored cadence
    beyond. Same shape and sentinel convention as v2_timeline.build_sprints."""
    try:
        real = fetch_board_sprints()
        print(f"  sprint axis: {len(real)} scheduled sprint(s) from board {IG_BOARD_ID}")
    except Exception as exc:
        real = []
        print(f"  ⚠ sprint axis: board {IG_BOARD_ID} unavailable ({exc}) — "
              f"falling back to the anchored cadence")
    out = []
    for sp in real:
        label = anchored_label(sp["start"])
        if label:
            out.append({"label": label, "start": sp["start"].isoformat()})
    nxt = (date.fromisoformat(out[-1]["start"]) + timedelta(days=SPRINT_DAYS)
           if out else SPRINT_ANCHOR)
    projected = 0
    while nxt <= CHART_END:
        out.append({"label": anchored_label(nxt), "start": nxt.isoformat()})
        projected += 1
        nxt += timedelta(days=SPRINT_DAYS)
    out.append({"label": "", "start": nxt.isoformat()})   # sentinel
    print(f"    {len(out) - 1} chips: {len(real)} from Jira + {projected} projected "
          f"→ last chip {out[-2]['label']} starting {out[-2]['start']}")
    return out

DONE_STATUSES = {"Closed", "Done"}
HOLIDAYS = set(_CONFIG.get("holidays") or DEFAULT_HOLIDAYS)  # keep in sync with template


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


# ── Drift snapshot machinery (history of projected end dates) ───────────────
# Ported from the template's calcDevEnd so daily Python runs produce the same
# projection a viewer would see with default buffers. Snapshots live in
# snapshots/igaming/<YYYY-MM-DD>.json and feed the ghost-bar + HISTORY tab UI.

def _hpd_for(_name):
    """iGaming uses a single default buffer (75%) for everyone — no per-person
    override like V2's Rejosh case. 8 * 75/100 = 6h/day effective."""
    return 6


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
        open_t = [t for t in p.get("tasks", []) if not is_done(t["status"])]
        if not open_t:
            continue   # nothing left here → their other work can't delay it
        open_h = sum(t["hours"] for t in open_t)
        # Queued = this person's open hours on higher-priority (lower-index)
        # FVs that have a release/target date — undated backlogs never delay.
        queued = 0
        for j in range(fv_idx):
            other = fvs[j]
            if other.get("isScenario") or not other.get("targetDate"):
                continue
            for op in other.get("devPeople", []):
                if op.get("name") == name:
                    queued += sum(t["hours"] for t in op.get("tasks", []) if not is_done(t["status"]))
        total = open_h + queued
        if total <= 0:
            continue
        done = _add_wd_hours(TODAY, total, _hpd_for(name))
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
        # iGaming has no lab phases — finalEnd equals qaEnd.
        all_tasks = (
            [t for p in fv.get("devPeople", []) for t in p.get("tasks", [])]
            + [t for p in fv.get("otherPeople", []) for t in p.get("tasks", [])]
        )
        snap["fvs"][fv["key"]] = {
            "devEnd":   dev_end.isoformat() if dev_end else None,
            "qaEnd":    qa_end.isoformat()  if qa_end  else None,
            "lab1End":  None,
            "pilotEnd": None,
            "finalEnd": qa_end.isoformat()  if qa_end  else None,
            "totalEstH": round(sum(t.get("origH", 0) for t in all_tasks)),
            "spentH":    round(sum(t.get("spentH", 0) for t in all_tasks)),
        }
    return snap


def write_snapshot(dashboard_key, snap):
    """Persist snap to snapshots/<dashboard>/<date>.json; trim files >90d old."""
    snap_dir = HERE / "snapshots" / dashboard_key
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
    snap_dir = HERE / "snapshots" / dashboard_key
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


# ── Scope-deviation baseline — see v2_timeline.py for shape rationale ───────
def _scope_baseline_path(dashboard_key):
    return HERE / "snapshots" / f"{dashboard_key}-scope-baseline.json"


def load_scope_baseline(dashboard_key):
    p = _scope_baseline_path(dashboard_key)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_scope_baseline(dashboard_key, baseline):
    p = _scope_baseline_path(dashboard_key)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps(baseline, indent=2, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )


def _fv_current_tickets(fv):
    out = {}
    for p in fv.get("devPeople", []) + fv.get("otherPeople", []):
        for t in p.get("tasks", []):
            key = t.get("key")
            if key:
                out[key] = float(t.get("origH") or 0)
    return out


def _fv_task_lookup(fv):
    out = {}
    for p in fv.get("devPeople", []) + fv.get("otherPeople", []):
        for t in p.get("tasks", []):
            key = t.get("key")
            if key:
                out[key] = t
    return out


def _ticket_created_date(ticket):
    """YYYY-MM-DD of the ticket's Jira `created` field, or None."""
    created = (ticket or {}).get("created")
    if not created:
        return None
    return str(created)[:10] or None


def compute_scope_delta(fv, baseline_entry):
    """Diff current vs baseline for one FV — see v2_timeline.compute_scope_delta.
    Added tickets carry `addedAt` (later of Jira created and baseline date) and
    are grouped into `addedByDate` for the scope-added markers on the dev bar."""
    if not baseline_entry:
        return None
    baseline_hours = baseline_entry.get("orig_hours_by_key") or {}
    baseline_date  = baseline_entry.get("date")
    current = _fv_current_tickets(fv)
    lookup  = _fv_task_lookup(fv)
    added, resized, removed = [], [], []
    for key, cur_h in current.items():
        if key not in baseline_hours:
            ticket = lookup.get(key) or {}
            created = _ticket_created_date(ticket)
            added.append({
                "key":     key,
                "summary": ticket.get("summary") or "",
                "hours":   round(cur_h, 1),
                "addedAt": max(baseline_date, created) if created else baseline_date,
            })
        else:
            delta = cur_h - baseline_hours[key]
            if abs(delta) >= 0.5:
                resized.append({
                    "key":     key,
                    "summary": (lookup.get(key) or {}).get("summary") or "",
                    "fromH":   round(baseline_hours[key], 1),
                    "toH":     round(cur_h, 1),
                    "delta":   round(delta, 1),
                })
    for key, bh in baseline_hours.items():
        if key not in current:
            removed.append({"key": key, "hours": round(bh, 1)})
    added_h   = round(sum(a["hours"] for a in added), 1)
    resized_h = round(sum(r["delta"] for r in resized), 1)
    removed_h = round(sum(r["hours"] for r in removed), 1)
    if not added and not resized and not removed:
        return None
    by_date = {}
    for t in added:
        d = t.get("addedAt") or baseline_date
        bucket = by_date.setdefault(d, {"date": d, "hours": 0.0, "tickets": []})
        bucket["hours"] += t["hours"]
        bucket["tickets"].append({"key": t["key"], "summary": t["summary"], "hours": t["hours"]})
    added_by_date = sorted(by_date.values(), key=lambda b: b["date"])
    for b in added_by_date:
        b["hours"] = round(b["hours"], 1)
    return {
        "addedByDate":      added_by_date,
        "baselineDate":     baseline_date,
        "addedHours":       added_h,
        "resizedHours":     resized_h,
        "removedHours":     removed_h,
        "netHours":         round(added_h + resized_h - removed_h, 1),
        "addedTickets":     added,
        "resizedTickets":   resized,
        "removedTickets":   removed,
    }


def apply_scope_baseline(dashboard_key, fvs):
    baseline = load_scope_baseline(dashboard_key)
    for fv in fvs:
        if fv.get("isScenario"):
            continue
        key = fv.get("key")
        if not key:
            continue
        current = _fv_current_tickets(fv)
        if key not in baseline:
            baseline[key] = {"date": TODAY.isoformat(), "orig_hours_by_key": current}
            continue
        delta = compute_scope_delta(fv, baseline[key])
        if delta:
            fv["scopeDelta"] = delta
    save_scope_baseline(dashboard_key, baseline)


# ── Jira fetches (§3.3) ──────────────────────────────────────────────────────

FV_TASKS_FIELDS = [
    "summary", "status", "issuetype", "assignee",
    "timeestimate", "timeoriginalestimate", "timespent",
    "fixVersions", "parent",
    SPRINT_FIELD,
    # `created` pins scope-added markers to the day scope grew;
    # `resolutiondate` dates M2 (Dev complete) = last dev task closed.
    "created", "resolutiondate",
]

SPRINT_TASKS_FIELDS = FV_TASKS_FIELDS  # same shape for now


def fetch_fv_tasks():
    """All FV-eligible tasks across all unreleased fix versions, INCLUDING
    done ones — they belong in the scope tab even when work is complete, and
    without them an all-done FV (e.g. Horse Play 1.00 just before release)
    silently disappears from the dashboard. is_done() in Python downstream
    flags the closed tasks so progress maths still works correctly."""
    return jira_jql(
        jql=(
            'project = IG '
            'AND fixVersion in unreleasedVersions() '
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


# ── Scope rollup to Epic (port of v2_timeline.compute_scope) ────────────────
# Tasks are grouped by their direct Jira parent; Story/Task parents are walked
# up (<= SCOPE_ROLLUP_MAX_HOPS) to their Epic so one Epic shows as one Scope
# card, with the intermediate parent kept per task as `via`.

SCOPE_ROLLUP_MAX_HOPS = 3
PARENT_CHAIN = {}   # filled in main() before build_fv_list()


def _resolve_parent_chain(parent_keys):
    """key -> {type, summary, status, parent_key}, batched 50 keys per JQL per hop."""
    chain = {}
    frontier = {k for k in parent_keys if k}
    for _hop in range(SCOPE_ROLLUP_MAX_HOPS):
        if not frontier:
            break
        rows = []
        batch_keys = list(frontier)
        for i in range(0, len(batch_keys), 50):
            kstr = ",".join(batch_keys[i:i + 50])
            try:
                rows += jira_jql(f"key in ({kstr})",
                                 fields=["parent", "issuetype", "status", "summary"])
            except Exception as e:
                print(f"   ! scope-rollup lookup batch failed: {e}")
        next_frontier = set()
        for r in rows:
            fields = r.get("fields") or {}
            parent_key = (fields.get("parent") or {}).get("key")
            chain[r["key"]] = {
                "type":       ((fields.get("issuetype") or {}).get("name") or "?"),
                "summary":    fields.get("summary") or r["key"],
                "status":     ((fields.get("status") or {}).get("name") or "New"),
                "parent_key": parent_key,
            }
            if parent_key and parent_key not in chain:
                next_frontier.add(parent_key)
        frontier = next_frontier
    return chain


def _nearest_epic(start_key, chain):
    """Walk `chain` upward from start_key to the first Epic; None if unreachable."""
    cur, seen = start_key, set()
    while cur and cur not in seen:
        seen.add(cur)
        info = chain.get(cur)
        if not info:
            return None
        if info["type"] == "Epic":
            return {"key": cur, **info}
        cur = info.get("parent_key")
    return None


# ── Release template: QA rounds + milestone evidence (port of V2) ───────────
# IG releases carry the same Jira template as V2: QA Task "Release <FV>" with
# QA Subtasks "QA Testing R<n>" (also "QA Testing Staging R<n>" and
# "<FV> QA Testing R<n>"), "Dev Testing - …" subtasks, and a Release ticket
# whose subtasks "Release <FV> [<build> - R<n>]" are the QA builds (ELG
# releases often have none). Unused rounds get closed, so "every round
# closed" is the QA-complete signal (M3). M1 is PMO's call — nothing here
# marks it; these are only the evidence shown next to it.

_ROUND_RE = re.compile(r'\bR(\d+)\b', re.IGNORECASE)
_QA_ROUND_RE = re.compile(r'\bQA\s+Testing\s+(Staging\s+)?R(\d+)\b', re.IGNORECASE)
_DEV_TEST_RE = re.compile(r'\bDev\s+Test', re.IGNORECASE)
_SCOPE_DEV_DONE = {"In QA", "Closed", "Done"}
_NOT_STARTED = {"To Do", "New", "Open", "Backlog"}
_VERSION_RE = re.compile(r'\b\d+\.\d{2}\b')


def _for_this_fv(summary, fv_name):
    """False when the ticket names a different release — IG FVs carry stale
    template tickets from earlier names (e.g. "QA Testing R1 - PFH2 Games 4.00"
    sitting in PFH Games 3.00). Tickets naming no version are kept."""
    if not _VERSION_RE.search(summary or ""):
        return True
    norm = lambda x: " ".join((x or "").lower().split())
    return norm(fv_name) in norm(summary)


def _status_done(fields):
    st = fields.get("status") or {}
    return ((st.get("statusCategory") or {}).get("key") == "done"
            or st.get("name") in DONE_STATUSES)


def fetch_release_template():
    """Release tickets (with their build subtasks as `_children`) and QA
    Task/Subtask tickets for every unreleased FV, keyed by FV name."""
    rel = jira_jql(
        jql='project = IG AND fixVersion in unreleasedVersions() AND issuetype = "Release"',
        fields=["summary", "status", "fixVersions"],
    )
    kids = {}
    keys = [r["key"] for r in rel]
    for i in range(0, len(keys), 50):
        for c in jira_jql(f"parent in ({','.join(keys[i:i + 50])})",
                          fields=["summary", "status", "parent", "created"]):
            kids.setdefault(((c.get("fields") or {}).get("parent") or {}).get("key"), []).append(c)
    qa = jira_jql(
        jql='project = IG AND fixVersion in unreleasedVersions() '
            'AND issuetype in ("QA Task", "QA Subtask")',
        fields=["summary", "status", "created", "resolutiondate",
                "timeoriginalestimate", "timespent", "fixVersions"],
    )
    by_fv = {}
    for r in rel:
        r["_children"] = kids.get(r["key"], [])
        for fv in (r.get("fields") or {}).get("fixVersions") or []:
            by_fv.setdefault(fv.get("name"), {"release": [], "qa": []})["release"].append(r)
    for q in qa:
        for fv in (q.get("fields") or {}).get("fixVersions") or []:
            by_fv.setdefault(fv.get("name"), {"release": [], "qa": []})["qa"].append(q)
    return by_fv


def _qa_rounds(qa_items, fv_name):
    """QA Testing round tickets, R-number ascending (staging rounds after)."""
    out = []
    for q in qa_items:
        f = q.get("fields") or {}
        m = _QA_ROUND_RE.search(f.get("summary", ""))
        if not m or not _for_this_fv(f.get("summary", ""), fv_name):
            continue
        status = (f.get("status") or {}).get("name", "")
        out.append({"round": f"{'Staging ' if m.group(1) else ''}R{m.group(2)}",
                    "n": int(m.group(2)), "staging": bool(m.group(1)),
                    "key": q["key"], "status": status, "done": _status_done(f),
                    "started": status not in _NOT_STARTED,
                    "resolved": (f.get("resolutiondate") or "")[:10] or None,
                    "origH": (f.get("timeoriginalestimate") or 0) / 3600.0,
                    "spentH": (f.get("timespent") or 0) / 3600.0})
    out.sort(key=lambda r: (r["staging"], r["n"]))
    return out


def _builds(release_tickets, fv_name):
    out = []
    for p in release_tickets:
        for c in p.get("_children", []):
            cf = c.get("fields") or {}
            m = _ROUND_RE.search(cf.get("summary", ""))
            if m and _for_this_fv(cf.get("summary", ""), fv_name):
                out.append({"round": f"R{m.group(1)}", "key": c["key"],
                            "date": (cf.get("created") or "")[:10] or None})
    out.sort(key=lambda b: (b["date"] or "", int(b["round"][1:])))
    return out


def detect_qa_round(rounds, builds):
    """'R<n>' = highest round that has a build sent to QA or a QA Testing
    ticket that has started. IG pre-creates R1–R3 tickets at release setup,
    so unstarted round tickets don't count (V2 reads only build tickets)."""
    ns = [int(b["round"][1:]) for b in builds]
    ns += [r["n"] for r in rounds if r["started"]]
    return f"R{max(ns)}" if ns else None


def compute_qa_progress(rounds):
    """Hour-based progress across QA Testing round tickets — same maths as
    v2_timeline.compute_qa_progress. None when the FV has no round tickets."""
    if not rounds:
        return None
    total_h = sum(r["origH"] for r in rounds)
    done_h = sum(r["origH"] if r["done"] else min(r["spentH"], r["origH"]) for r in rounds)
    done_n = sum(1 for r in rounds if r["done"])
    progress = (round(done_h / total_h * 100, 1) if total_h > 0
                else round(done_n / len(rounds) * 100, 1))
    return {"totalH": round(total_h, 1), "doneH": round(done_h, 1),
            "totalN": len(rounds), "doneN": done_n, "progress": progress}


def compute_milestone_signals(fv_name, dev_tasks, builds, rounds, qa_items, scope):
    """Jira facts behind the milestone line — same contract as V2's
    compute_milestone_signals. M2 actual = day the last dev task closed (none
    open); M3 actual = day the last QA Testing round closed (all closed)."""
    open_dev = [t for t in dev_tasks if not is_done(t["status"])]
    tot_h = sum(t.get("origH") or 0 for t in dev_tasks)
    done_h = sum(t.get("origH") or 0 for t in dev_tasks if is_done(t["status"]))
    resolved = [t["resolved"] for t in dev_tasks if is_done(t["status"]) and t.get("resolved")]
    dev_tests = sorted(
        ({"key": q["key"], "summary": (q.get("fields") or {}).get("summary", ""),
          "date": ((q.get("fields") or {}).get("created") or "")[:10] or None}
         for q in qa_items
         if _DEV_TEST_RE.search((q.get("fields") or {}).get("summary", ""))
         and not _QA_ROUND_RE.search((q.get("fields") or {}).get("summary", ""))
         and _for_this_fv((q.get("fields") or {}).get("summary", ""), fv_name)),
        key=lambda d: d["date"] or "")
    rounds_done = bool(rounds) and all(r["done"] for r in rounds)
    epics = [e for e in scope if e.get("key")]
    return {
        "devOpenN":      len(open_dev),
        "devTotalN":     len(dev_tasks),
        "devHoursPct":   round(done_h / tot_h * 100) if tot_h else None,
        "devClosedDate": max(resolved) if dev_tasks and not open_dev and resolved else None,
        "builds":        builds,
        "qaDevTest":     dev_tests[0] if dev_tests else None,
        "rounds":        [{k: r[k] for k in ("round", "key", "status", "done", "resolved")}
                          for r in rounds],
        "qaRoundsClosedDate": (max(r["resolved"] for r in rounds if r["resolved"])
                               if rounds_done and any(r["resolved"] for r in rounds) else None),
        "epicsDevDone":  sum(1 for e in epics if e.get("status") in _SCOPE_DEV_DONE),
        "epicsTotal":    len(epics),
    }


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

        parent_obj    = f.get("parent") or {}
        parent_key    = parent_obj.get("key")
        parent_fields = parent_obj.get("fields") or {}
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
            "created": f.get("created"),
            "resolved": (f.get("resolutiondate") or "")[:10] or None,
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

            # Scope tracking — group by whatever the parent is (Epic OR Story),
            # mirroring V2's compute_scope. The IG project's hierarchy is
            # Epic → Story → Subtask for most work, so requiring parent ∈ epics
            # would drop ~86% of tasks into the "no epic" bucket. When the
            # parent IS a known epic we prefer that cached record; otherwise
            # we read inline from the task's parent.fields (summary + status).
            if parent_key:
                slot["epics_taskkeys"].setdefault(parent_key, []).append(issue["key"])
                if parent_key not in slot["epics_status"]:
                    ptype = ((parent_fields.get("issuetype") or {}).get("name")
                             or ("Epic" if parent_key in epic_by_key else "?"))
                    slot["epics_status"][parent_key] = {
                        **(epic_by_key.get(parent_key) or {
                            "summary": parent_fields.get("summary") or parent_key,
                            "status":  (parent_fields.get("status") or {}).get("name") or "New",
                        }),
                        "type": ptype,
                    }
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


def build_fv_list(fv_struct, release_by_fv=None):
    """Assemble the FV list in priority order with critical-path auto-flagging.
    `release_by_fv` (from fetch_release_template) drives the QA-round status,
    QA progress and milestone evidence. Returns the list ready to be
    JSON-dumped into the template."""
    out = []
    release_by_fv = release_by_fv or {}

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

        # QA-round signal (port of V2): a build sent to QA or a started QA
        # Testing round puts the release "In QA · R<n>" whatever the task mix
        # says. The dev bar keeps running while estimated dev work remains.
        tmpl = release_by_fv.get(fv_name) or {"release": [], "qa": []}
        rounds = _qa_rounds(tmpl["qa"], fv_name)
        builds = _builds(tmpl["release"], fv_name)
        qa_round = detect_qa_round(rounds, builds)
        dev_tasks_all = [t for tasks in slot["dev_people"].values() for t in tasks]
        if qa_round:
            open_dev_h = sum(t["hours"] for t in dev_tasks_all
                             if not is_done(t["status"]) and (t["hours"] or 0) > 0)
            status_label = f"In QA · {qa_round}"
            dev_start = TODAY.isoformat() if open_dev_h > 0 else None
        qa_progress = compute_qa_progress(rounds) if qa_round else None

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
        # Non-Epic parents roll up to their Epic (PARENT_CHAIN, resolved in
        # main); unresolvable ones stay as their own card.
        done_keys = {t["key"] for t in all_tasks if is_done(t["status"])}
        merged = {}
        for pkey, task_keys in slot["epics_taskkeys"].items():
            einfo = slot["epics_status"].get(pkey, {"summary": pkey, "status": "New"})
            home, via = {"key": pkey, "name": einfo["summary"], "status": einfo["status"]}, None
            if einfo.get("type") != "Epic":
                epic = _nearest_epic(pkey, PARENT_CHAIN)
                if epic:
                    home = {"key": epic["key"], "name": epic["summary"], "status": epic["status"]}
                    via = {"key": pkey, "type": einfo.get("type") or "?", "name": einfo["summary"]}
            b = merged.setdefault(home["key"], {**home, "done": 0, "total": 0,
                                                "taskKeys": [], "viaMap": {}})
            for k in task_keys:
                b["taskKeys"].append(k)
                b["total"] += 1
                b["done"] += k in done_keys
                if via:
                    b["viaMap"][k] = via
        for b in merged.values():
            if not b["viaMap"]:
                del b["viaMap"]
        scope = sorted(merged.values(), key=lambda x: -x["total"])
        if slot["unscoped_taskkeys"]:
            done_unscoped = sum(1 for k in slot["unscoped_taskkeys"] if k in done_keys)
            scope.append({
                "key":      None,
                "name":     "Other tasks (no epic in this FV)",
                "status":   "In Progress",
                "done":     done_unscoped,
                "total":    len(slot["unscoped_taskkeys"]),
                "taskKeys": slot["unscoped_taskkeys"],
            })

        # Target date for the right-side summary panel: config override
        # (meta["target_date"]) wins over Jira's releaseDate (meta["release"]).
        target = meta.get("target_date") or meta.get("release")
        out.append({
            "key":            fv_name,
            "color":          meta["color"],
            "sub":            meta["sub"],
            "devStart":       dev_start,
            "qaWeeks":        meta["qaWeeks"],
            "statusLabel":    status_label,
            # "In QA · R3" reuses the base "In QA" style.
            "statusStyle":    STATUS_STYLES.get(status_label.split(" · ", 1)[0], STATUS_STYLES["Scheduled"]),
            "release":        meta.get("release"),
            "jiraRelease":    meta.get("jira_release"),
            "targetDate":     target,
            "devPeople":      dev_people,
            "otherPeople":    other_people,
            "scope":          scope,
            # Config-only fields — passed straight through from config/igaming.json
            # so the dashboard JS can render scope-tab overrides and milestone pins.
            "epic_overrides": meta.get("epic_overrides") or {},
            "milestones":     meta.get("milestones") or [],
            # Release milestones: Jira evidence + PMO plan/done dates from
            # config fv_meta[fv].release_milestones (keyed "M1".."M12").
            "releaseMilestones": meta.get("release_milestones") or {},
            "msSignals":      compute_milestone_signals(fv_name, dev_tasks_all, builds,
                                                        rounds, tmpl["qa"], scope),
        })
        if qa_progress:
            out[-1]["qaProgress"] = qa_progress

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

    # Sprint axis first; the template render reads the module-level SPRINTS.
    global SPRINTS
    SPRINTS = build_sprints()

    # Refresh FV_ORDER and FV_META against Jira's live unreleased FV list.
    # The config (loaded at module init) provides priority order + metadata
    # overrides, but Jira decides what actually exists. See merge_fv_lists()
    # for the rules.
    global FV_ORDER, FV_META
    print("  Fetching unreleased fix versions from Jira …")
    jira_fvs = fetch_unreleased_fvs()
    print(f"    {len(jira_fvs)} unreleased fix versions: {[f['name'] for f in jira_fvs]}")
    FV_ORDER, FV_META = merge_fv_lists(jira_fvs, _CONFIG.get("fv_order") or [], _CONFIG.get("fv_meta") or {})
    # Diagnostics: what landed where
    cfg_order = set(_CONFIG.get("fv_order") or [])
    new_fvs   = [n for n in FV_ORDER if n not in cfg_order]
    gone_fvs  = [n for n in cfg_order if n not in {f["name"] for f in jira_fvs}]
    if new_fvs:  print(f"    auto-added (not in config): {new_fvs}")
    if gone_fvs: print(f"    dropped (released/archived/renamed in Jira): {gone_fvs}")

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

    print("  Fetching release template (Release builds, QA rounds) …")
    release_by_fv = fetch_release_template()
    print(f"    {len(release_by_fv)} FV(s) with Release/QA tickets")

    print("  Building FV structure …")
    fv_struct = build_fv_structure(fv_tasks, epic_by_key)
    non_epic = {k for slot in fv_struct.values()
                for k, info in slot["epics_status"].items() if info.get("type") != "Epic"}
    PARENT_CHAIN.update(_resolve_parent_chain(non_epic) if non_epic else {})
    print(f"    scope rollup: {len(non_epic)} non-Epic parent(s) resolved up to their Epic")
    fv_list = build_fv_list(fv_struct, release_by_fv)
    for fv in fv_list:
        dev_open = sum(
            t["hours"] for p in fv["devPeople"]
            for t in p["tasks"] if not is_done(t["status"])
        )
        crit = next((p["name"] for p in fv["devPeople"] if p.get("bottleneck")), None)
        sig = fv["msSignals"]
        print(f"    {fv['key']:<22} {fv['statusLabel']:<14} {dev_open:>6.1f}h dev open"
              + (f" · ⛓ {crit}" if crit else "")
              + f" · dev closed {sig['devClosedDate'] or '—'}"
              + f" · rounds {sum(r['done'] for r in sig['rounds'])}/{len(sig['rounds'])}"
              + f" · QA closed {sig['qaRoundsClosedDate'] or '—'}")

    print("  Building active sprint board …")
    sprint_data = build_sprint_data(sprint_tasks)
    sprint_header = make_sprint_header(sprint_data)
    if sprint_data:
        n_devs = len(sprint_data["byAssignee"])
        n_items = sum(len(v) for v in sprint_data["byAssignee"].values())
        print(f"    {sprint_header} · {n_devs} devs · {n_items} items")

    refresh_label = f"{TODAY.strftime('%B')} {TODAY.day}, {TODAY.year}"

    # Drift snapshot — compute today's projections, persist, then load the
    # recent history. The template uses these for the ghost bar + HISTORY tab.
    today_snap = compute_snapshot(fv_list)
    write_snapshot("igaming", today_snap)
    snapshots = load_snapshots("igaming", n=30)
    print(f"\n  snapshot: wrote {len(today_snap['fvs'])} FV projections · loaded {len(snapshots)} historical snapshots")

    # Scope-deviation baseline — first pass seeds it; subsequent runs attach
    # fv.scopeDelta = { addedHours, resizedHours, removedHours, ..., baselineDate }.
    apply_scope_baseline("igaming", fv_list)
    n_with_delta = sum(1 for f in fv_list if f.get("scopeDelta"))
    if n_with_delta:
        print(f"  scope baseline: {n_with_delta} FV(s) show a deviation vs baseline")

    template_path = HERE / "igaming-timeline.template.html"
    template = template_path.read_text(encoding="utf-8")
    rendered = (template
        .replace("__TODAY__",          TODAY.isoformat())
        .replace("__FV_DATA__",        json.dumps(fv_list, ensure_ascii=False))
        .replace("__SPRINTS_DATA__",   json.dumps(SPRINTS, ensure_ascii=False))
        .replace("__SPRINT_DATA__",    json.dumps(sprint_data, ensure_ascii=False))
        .replace("__HOLIDAYS__",       json.dumps(sorted(HOLIDAYS), ensure_ascii=False))
        # SERVER_FV_META = the fv_meta dict actually persisted in config/igaming.json
        # (NOT the merged-with-auto-discovery version). The Save-as-default flow
        # uses this as the base for the payload so auto-discovered FVs with grey
        # defaults don't appear as fake "changes" in the diff. See template
        # buildConfigPayload() for the consumer.
        .replace("__SERVER_FV_META__", json.dumps(_CONFIG.get("fv_meta") or {}, ensure_ascii=False))
        # _meta.published_at + _meta.published_by are written by the
        # Save-as-default flow (browser → GitHub PUT). The opt-in banner
        # (ported from game-pipeline #52) compares these to localStorage
        # so a viewer with local edits learns when a newer shared plan
        # has been published. Empty string = no publish marker yet.
        .replace("__SERVER_PUBLISHED_AT__", json.dumps((_CONFIG.get("_meta") or {}).get("published_at") or ""))
        .replace("__SERVER_PUBLISHED_BY__", json.dumps((_CONFIG.get("_meta") or {}).get("published_by") or ""))
        # SNAPSHOTS: last 30 days of projection history for the ghost-bar +
        # HISTORY tab. Each entry = {date, fvs:{key:{devEnd,qaEnd,…,spentH}}}.
        .replace("__SNAPSHOTS__",      json.dumps(snapshots, ensure_ascii=False))
        .replace("__REFRESH_LABEL__",  refresh_label)
        .replace("__SPRINT_HEADER__",  sprint_header)
    )
    (HERE / "igaming-timeline.html").write_text(rendered, encoding="utf-8")
    print("\n✅ igaming-timeline.html written")


if __name__ == "__main__":
    main()
