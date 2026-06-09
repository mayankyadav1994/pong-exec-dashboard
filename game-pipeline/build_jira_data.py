"""
build_jira_data.py
==================
Jira -> data-file builder for the Game Pipeline dashboard (Phase 1).

Queries the Jira REST API directly (no Excel) and writes, per project, a
``dashboard-data-{project}.js`` containing:

    const GAMES = [...];
    const SPRINTS = [{id,label,start,end}, ...];
    const REFRESHED_AT = 'YYYY-MM-DD HH:MM';

One game = one Epic (``fixVersion IS NOT EMPTY``). Disciplines are child-issuetype
groupings; hours come from ``timeoriginalestimate`` / ``timespent``; sprint markers
come from the project's sprint custom field. See GAME_PIPELINE_KNOWLEDGE.md §2 and
Decisions #23-#28 in GAME_PIPELINE_LOGIC.md.

Usage:
    python build_jira_data.py --project v2
    python build_jira_data.py --project ig
    python build_jira_data.py --project both          # default
    python build_jira_data.py --project both --verbose
    python build_jira_data.py --project v2 --today 2026-06-02

Credentials are read from .env (python-dotenv): JIRA_BASE_URL, JIRA_EMAIL,
JIRA_API_TOKEN, JIRA_BOARD_ID_V2, JIRA_BOARD_ID_IG.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Optional

import requests
from dotenv import load_dotenv

# --- Status markers ----------------------------------------------------------
OK, WARN, ERR = "✓", "⚠", "✗"   # ✓ ⚠ ✗

# --- Paths -------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent
ARCHIVE_DIR = ROOT / "archive"

# --- Sprint anchor (Decision #27) --------------------------------------------
SPRINT_ANCHOR = date(2026, 5, 11)   # S1
SPRINT_DAYS = 14

# --- Per-project configuration ----------------------------------------------
PROJECTS: dict[str, dict[str, Any]] = {
    "v2": {
        "key": "V2",
        "sprint_field": "customfield_10020",
        "board_env": "JIRA_BOARD_ID_V2",
        "name_prefix": "Game:",          # only epics named like this are games (#4)
        "out": ROOT / "dashboard-data-v2.js",
    },
    "ig": {
        "key": "IG",
        "sprint_field": "customfield_10103",
        "board_env": "JIRA_BOARD_ID_IG",
        "name_prefix": "Gen2 Game:",     # (#4)
        "out": ROOT / "dashboard-data-ig.js",
    },
}

# A delivered game drops off the board this many days after its release (#3).
DELIVERED_GRACE_DAYS = 14

# --- Discipline mapping (Decision #23) ---------------------------------------
DISCIPLINE_BY_ISSUETYPE = {
    # art
    "creative task": "art", "creative subtask": "art", "pre-prod task": "art",
    "pre prod task": "art", "preprod task": "art",
    # math
    "math task": "math", "math subtask": "math",
    # dev
    "dev task": "dev", "dev subtask": "dev", "story": "dev",
    # sound
    "sound task": "sound", "sound subtask": "sound",
    # qa
    "qa task": "qa", "qa subtask": "qa",
    # design
    "design task": "design", "design subtask": "design",
    "design sub-task": "design", "gdd task": "design",
}
DISCIPLINE_ORDER = ["art", "design", "math", "dev", "sound", "qa"]
DISCIPLINE_LABEL = {
    "art": "Creative / Art", "design": "Design (GDD)", "math": "Math",
    "dev": "Development", "sound": "Sound", "qa": "QA",
}
# Downstream pipeline order used to pick the "current" discipline (Step 3).
STAGE_PIPELINE = ["design", "art", "math", "dev", "sound", "qa"]

# Release tickets are a status SIGNAL (testing/deploy), not a discipline lane
# and not counted in discipline hours.
RELEASE_ISSUETYPES = {"release", "release subtask"}
EXCLUDE_ISSUETYPES = {"bug", "enhancement", "live issue"}

# --- Status inference rule tables (EDIT THESE to tune; Decision #32) ----------
# Step 1: normalise every child issue's Jira status into one activity bucket.
STATUS_BUCKET = {
    # not started
    "new": "todo", "to do": "todo", "todo": "todo", "ready": "todo",
    "backlog": "todo", "open": "todo", "reopened from backlog": "todo",
    # work in progress (incl. pre-prod variants and reopened)
    "in progress": "wip", "in review": "wip", "review": "wip", "reopened": "wip",
    "pre-prod in progress": "wip", "pre-prod in review": "wip",
    "pre-prod reopened": "wip", "in development": "wip",
    # qa / testing
    "in qa": "qa", "ready for qa": "qa", "qa": "qa", "testing": "qa",
    # on hold / blocked
    "on hold": "hold", "blocked": "hold",
    # done
    "closed": "done", "signed off": "done", "released": "done",
    "deployed": "done", "to be closed": "done", "known issue": "done", "done": "done",
}
# Statuses that specifically indicate the pre-production phase.
PREPROD_STATUSES = {"pre-prod in progress", "pre-prod in review", "pre-prod reopened"}

# Step 7 fallback: map the epic's own Jira status when it has NO usable children.
EPIC_STATUS_MAP = {
    "new": "Not Started", "to do": "Not Started", "backlog": "Not Started",
    "in progress": "In Progress", "in qa": "In QA",
    "closed": "Signed Off", "done": "Signed Off", "released": "Signed Off",
    "on hold": "On Hold",
}


def bucket(status: Optional[str]) -> str:
    return STATUS_BUCKET.get((status or "").strip().lower(), "todo")

# Sprint custom-field string form (legacy GreenHopper):
#   com.atlassian.greenhopper.service.sprint.Sprint@x[id=123,...,name=Foo,startDate=...,endDate=...]
_SPRINT_STR_RE = re.compile(r"(\w+)=([^,\]]+)")


# ============================================================================
#  Jira client
# ============================================================================
class JiraClient:
    """Single Jira HTTP client. Basic auth; retries + rate limiting baked in."""

    def __init__(self, base_url: str, email: str, token: str, verbose: bool = False):
        self.base = base_url.rstrip("/")
        self.auth = (email, token)
        self.verbose = verbose
        self.session = requests.Session()
        self.session.headers.update({"Accept": "application/json"})

    def log(self, *args: Any) -> None:
        if self.verbose:
            print("   ", *args)

    def _request(self, method: str, path: str, *, params=None, body=None) -> dict:
        """Issue one request with 429-aware + network retries (cap 5)."""
        url = f"{self.base}{path}"
        backoff = 1.0
        for attempt in range(1, 6):
            try:
                resp = self.session.request(
                    method, url, auth=self.auth, params=params, json=body, timeout=45
                )
            except requests.RequestException as exc:
                if attempt >= 4:
                    raise
                self.log(f"{WARN} network error ({exc.__class__.__name__}); retry {attempt}/3 in {backoff:.0f}s")
                time.sleep(backoff)
                backoff *= 2
                continue
            if resp.status_code == 429:
                wait = float(resp.headers.get("Retry-After", backoff))
                self.log(f"{WARN} 429 rate-limited; waiting {wait:.0f}s (attempt {attempt}/5)")
                time.sleep(wait)
                backoff *= 2
                continue
            resp.raise_for_status()
            return resp.json() if resp.content else {}
        raise RuntimeError(f"Exhausted retries for {method} {path}")

    def search_jql(self, jql: str, fields: list[str]) -> list[dict]:
        """Paginate GET /rest/api/3/search/jql via nextPageToken until isLast."""
        issues: list[dict] = []
        token: Optional[str] = None
        while True:
            params = {"jql": jql, "fields": ",".join(fields), "maxResults": 100}
            if token:
                params["nextPageToken"] = token
            data = self._request("GET", "/rest/api/3/search/jql", params=params)
            issues.extend(data.get("issues", []))
            if data.get("isLast", True):
                break
            token = data.get("nextPageToken")
            if not token:
                break
            time.sleep(0.2)   # rate-limit between pages
        return issues

    def board_sprints(self, board_id: str) -> list[dict]:
        """Paginate GET /rest/agile/1.0/board/{id}/sprint (active,closed,future)."""
        out: list[dict] = []
        start_at = 0
        while True:
            params = {"state": "active,closed,future", "startAt": start_at, "maxResults": 50}
            data = self._request("GET", f"/rest/agile/1.0/board/{board_id}/sprint", params=params)
            out.extend(data.get("values", []))
            if data.get("isLast", True):
                break
            start_at += len(data.get("values", []) or [])
            if not data.get("values"):
                break
            time.sleep(0.2)
        return out


# ============================================================================
#  Sprint helpers
# ============================================================================
def _parse_date(value: Any) -> Optional[date]:
    if not value or not isinstance(value, str):
        return None
    txt = value.strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%S.%f%z",
                "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d"):
        try:
            return datetime.strptime(txt, fmt).date()
        except ValueError:
            continue
    # last resort: leading YYYY-MM-DD
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", txt)
    if m:
        return date(int(m[1]), int(m[2]), int(m[3]))
    return None


def parse_sprint_field(value: Any) -> list[dict]:
    """Normalise a sprint custom-field value to [{id,name,start,end}, ...].

    Handles both the modern object form (list of dicts) and the legacy
    GreenHopper string form.
    """
    out: list[dict] = []
    if not value:
        return out
    items = value if isinstance(value, list) else [value]
    for item in items:
        if isinstance(item, dict):
            sid = item.get("id")
            if sid is None:
                continue
            out.append({
                "id": int(sid),
                "name": item.get("name"),
                "start": _parse_date(item.get("startDate")),
                "end": _parse_date(item.get("endDate")),
            })
        elif isinstance(item, str):
            fields = dict(_SPRINT_STR_RE.findall(item))
            if "id" not in fields:
                continue
            try:
                sid = int(fields["id"])
            except ValueError:
                continue
            out.append({
                "id": sid,
                "name": fields.get("name"),
                "start": _parse_date(fields.get("startDate")),
                "end": _parse_date(fields.get("endDate")),
            })
    return out


def anchored_label(start: date) -> Optional[str]:
    """S{n} label anchored at S1=2026-05-11 (14-day). None if before S1."""
    n = round((start - SPRINT_ANCHOR).days / SPRINT_DAYS) + 1
    return f"S{n}" if n >= 1 else None


# ============================================================================
#  Issue classification
# ============================================================================
def classify_child(issuetype: str) -> str:
    """Classify a child issue: 'exclude' | 'release' | <discipline key> | 'unmapped'."""
    it = (issuetype or "").strip().lower()
    if it in EXCLUDE_ISSUETYPES:
        return "exclude"
    if it in RELEASE_ISSUETYPES:
        return "release"
    return DISCIPLINE_BY_ISSUETYPE.get(it, "unmapped")


def _secs_to_hours(v: Any) -> float:
    try:
        return float(v) / 3600.0
    except (TypeError, ValueError):
        return 0.0


def compute_delivered(fixversions: list[dict]) -> Optional[dict]:
    """Latest released fixVersion → {'fv': name, 'date': ISO}. None if undelivered."""
    dated = []
    for v in fixversions:
        if v.get("released") and v.get("releaseDate"):
            d = _parse_date(v["releaseDate"])
            if d:
                dated.append((d, v["name"]))
    if not dated:
        return None
    d, name = max(dated, key=lambda x: x[0])
    return {"fv": name, "date": d.isoformat()}


# ============================================================================
#  Status / stage inference  (Decision #32)
# ============================================================================
def _disc_flags(agg: dict) -> dict:
    """Collapse a discipline's bucket counts into convenience flags."""
    b = agg["buckets"]
    n = b["todo"] + b["wip"] + b["qa"] + b["hold"] + b["done"]
    return {
        "n": n, "wip": b["wip"], "qa": b["qa"], "hold": b["hold"],
        "done": b["done"], "todo": b["todo"], "preprod": agg.get("preprod", 0),
        "active": (b["wip"] > 0 or b["qa"] > 0),
    }


def discipline_phase(agg: dict) -> str:
    """Short phase label for a discipline (used in the detail tooltip)."""
    f = _disc_flags(agg)
    if f["qa"] > 0: return "qa"
    if f["wip"] > 0: return "wip"
    if f["n"] > 0 and f["done"] == f["n"]: return "done"
    if f["hold"] > 0 and not f["active"]: return "hold"
    if f["done"] > 0: return "partial"
    if f["todo"] > 0: return "todo"
    return "none"


def derive_stage(aggs: dict) -> str:
    """Step 3: the most-downstream discipline currently live (else done/concept)."""
    flags = {k: _disc_flags(aggs[k]) for k in DISCIPLINE_ORDER}
    rel = _disc_flags(aggs["_release"])
    with_tickets = [k for k in DISCIPLINE_ORDER if flags[k]["n"] > 0]
    if not with_tickets and not rel["n"]:
        return "concept"
    active = [k for k in STAGE_PIPELINE if flags[k]["active"]]
    all_done = bool(with_tickets) and all(flags[k]["done"] == flags[k]["n"] for k in with_tickets) and not active
    if (all_done and (rel["n"] == 0 or rel["done"] > 0)) or (rel["done"] > 0 and not active and not rel["active"]):
        return "done"
    if rel["active"]:
        return "qa"            # release/testing in flight
    if active:
        return active[-1]      # most downstream active discipline
    done_disc = [k for k in STAGE_PIPELINE if flags[k]["done"] > 0]
    if done_disc:
        return done_disc[-1]
    return "concept"


def derive_status(aggs: dict, epic_status: Optional[str]) -> str:
    """Step 4: overall workflow status, ticket-derived (epic status is fallback)."""
    flags = {k: _disc_flags(aggs[k]) for k in DISCIPLINE_ORDER}
    rel = _disc_flags(aggs["_release"])
    with_tickets = [k for k in DISCIPLINE_ORDER if flags[k]["n"] > 0]

    # 7. no usable children -> map the epic's own Jira status
    if not with_tickets and rel["n"] == 0:
        return EPIC_STATUS_MAP.get((epic_status or "").strip().lower(), "Not Started")

    active_any = any(flags[k]["active"] for k in DISCIPLINE_ORDER) or rel["active"]
    hold_any = any(flags[k]["hold"] > 0 for k in DISCIPLINE_ORDER)
    epic_hold = (epic_status or "").strip().lower() == "on hold"
    all_done = bool(with_tickets) and all(flags[k]["done"] == flags[k]["n"] for k in with_tickets) and not active_any
    # QA phase = the QA discipline is active (its tickets WIP or in-QA), or any
    # discipline has an explicit in-QA ticket.
    any_qa = flags["qa"]["active"] or any(flags[k]["qa"] > 0 for k in DISCIPLINE_ORDER)
    prod_wip = any(flags[k]["wip"] > 0 for k in ("art", "math", "dev", "sound"))
    design_wip = flags["design"]["wip"] > 0
    any_preprod = any(flags[k]["preprod"] > 0 for k in DISCIPLINE_ORDER)

    if (rel["done"] > 0 and not active_any) or all_done:
        return "Signed Off"
    if any_qa or rel["active"]:
        return "In QA"
    if not active_any and (hold_any or epic_hold):
        return "On Hold"
    if prod_wip:
        return "In Progress"
    if design_wip or any_preprod:
        return "In Pre-Prod"
    if active_any:
        return "In Progress"
    return "Not Started"


# ============================================================================
#  Build one project
# ============================================================================
def build_project(client: JiraClient, proj_key: str, today: date, verbose: bool) -> dict:
    cfg = PROJECTS[proj_key]
    jira_project = cfg["key"]
    sprint_field = cfg["sprint_field"]
    board_id = os.environ.get(cfg["board_env"], "").strip()

    print(f"{OK} Building {jira_project} (sprint field {sprint_field})")

    # 1. Epics
    epic_jql = (f"project = {jira_project} AND issuetype = Epic "
                f"AND fixVersion IS NOT EMPTY ORDER BY rank ASC")
    epics = client.search_jql(
        epic_jql,
        ["summary", "status", "assignee", "priority", "fixVersions",
         "customfield_10014", "duedate"],
    )
    print(f"   {OK} {len(epics)} epic(s)")

    # (#4) Only true games: epics whose name starts with the project prefix.
    prefix = cfg.get("name_prefix")

    def _epic_name(e: dict) -> str:
        f = e.get("fields", {}) or {}
        return str(f.get("customfield_10014") or f.get("summary") or e.get("key") or "")

    # (#47) Process ALL fixVersion epics; the name prefix only decides the DEFAULT
    # roster (in_roster). Non-prefixed epics stay in the data as "+ Add game"
    # candidates so the team can pull them onto the board on demand.
    if prefix:
        n_roster = sum(1 for e in epics if _epic_name(e).startswith(prefix))
        print(f"   {OK} {n_roster}/{len(epics)} epic(s) are roster games named {prefix!r}; the rest are add-game candidates")
    if not epics:
        print(f"   {WARN} no epics found - writing empty placeholder")

    sprint_meta: dict[int, dict] = {}     # id -> {start,end,name}
    games: list[dict] = []
    all_skips: list[str] = []

    # 2-5. Children per epic
    for ei, epic in enumerate(epics, 1):
        ekey = epic.get("key")
        ef = epic.get("fields", {}) or {}
        # Full subtree: epic's direct children + their sub-tasks (Decision #37).
        ISSUE_FIELDS = ["issuetype", "status", "timeoriginalestimate", "timespent",
                        "duedate", sprint_field]
        children = client.search_jql(f"parent = {ekey}", ISSUE_FIELDS + ["subtasks"])
        child_keys = [c.get("key") for c in children if c.get("key")]
        subtasks = []
        for i in range(0, len(child_keys), 50):     # chunk the parent-in query
            chunk = child_keys[i:i + 50]
            subtasks += client.search_jql(
                "parent in (" + ",".join(chunk) + ")", ISSUE_FIELDS + ["parent"])

        def _new_agg():
            return {"est": 0.0, "spent": 0.0, "sprints": set(), "due": [],
                    "buckets": {"todo": 0, "wip": 0, "qa": 0, "hold": 0, "done": 0},
                    "preprod": 0}
        aggs = {k: _new_agg() for k in DISCIPLINE_ORDER}
        aggs["_release"] = _new_agg()

        # Count classified sub-tasks per parent → drives the estimate fallback.
        classified_subs = {}
        for st in subtasks:
            sf = st.get("fields", {}) or {}
            pk = (sf.get("parent") or {}).get("key")
            if pk and classify_child(((sf.get("issuetype") or {}).get("name")) or "") not in ("exclude", "unmapped"):
                classified_subs[pk] = classified_subs.get(pk, 0) + 1

        def add_issue(fields, include_est):
            itype = ((fields.get("issuetype") or {}).get("name")) or ""
            cat = classify_child(itype)
            if cat == "exclude":
                return
            status_l = ((fields.get("status") or {}).get("name") or "").strip().lower()
            bk = bucket(status_l)
            if cat == "release":
                aggs["_release"]["buckets"][bk] += 1     # signal only — no hours
                return
            if cat == "unmapped":
                all_skips.append(f"{itype!r}")
                return
            d = aggs[cat]
            d["buckets"][bk] += 1
            if status_l in PREPROD_STATUSES:
                d["preprod"] += 1
            dd = _parse_date(fields.get("duedate"))   # department-task target (#40)
            if dd is not None:
                d["due"].append(dd)
            d["spent"] += _secs_to_hours(fields.get("timespent"))        # every issue's own time
            if include_est:
                d["est"] += _secs_to_hours(fields.get("timeoriginalestimate"))
            for spr in parse_sprint_field(fields.get(sprint_field)):
                sid = spr["id"]
                if spr["start"] is not None:
                    meta = sprint_meta.setdefault(sid, {"start": None, "end": None, "name": None})
                    meta["start"] = meta["start"] or spr["start"]
                    meta["end"] = meta["end"] or spr["end"]
                    meta["name"] = meta["name"] or spr["name"]
                d["sprints"].add(sid)

        # Children: count own estimate only when they have NO classified sub-task
        # (otherwise the sub-task estimates represent the work).
        for ch in children:
            add_issue(ch.get("fields", {}) or {}, classified_subs.get(ch.get("key"), 0) == 0)
        # Sub-tasks: always contribute their own est + spent.
        for st in subtasks:
            add_issue(st.get("fields", {}) or {}, True)

        disciplines = []
        for dkey in DISCIPLINE_ORDER:
            d = aggs[dkey]
            est = round(d["est"], 2)
            spent = round(d["spent"], 2)
            disciplines.append({
                "name": DISCIPLINE_LABEL[dkey],
                "key": dkey,
                "est": est,
                "spent": spent,
                "pct": round(spent / est, 4) if est > 0 else 0.0,
                "sprints": sorted(d["sprints"]),
                "phase": discipline_phase(d),
                # Department target = latest due date among this discipline's
                # tickets (the producer's targeted dates on dept tasks; #40).
                "target_date": max(d["due"]).isoformat() if d["due"] else None,
            })

        est = round(sum(x["est"] for x in disciplines), 2)
        spent = round(sum(x["spent"] for x in disciplines), 2)
        assignee = ef.get("assignee") or {}
        priority = ef.get("priority") or {}
        epic_status = (ef.get("status") or {}).get("name")
        epic_due = _parse_date(ef.get("duedate"))   # game-level target (#40)
        fvs = [{"name": v.get("name"), "released": bool(v.get("released")),
                "releaseDate": (_parse_date(v.get("releaseDate")).isoformat()
                                if _parse_date(v.get("releaseDate")) else None)}
               for v in (ef.get("fixVersions") or []) if v.get("name")]
        delivered = compute_delivered(fvs)          # (#2) {'fv','date'} or None
        if delivered:                                # delivered ⇒ shipped/signed off
            auto_status, stage = "Signed Off", "done"
        else:
            auto_status = derive_status(aggs, epic_status)
            stage = derive_stage(aggs)
        gname = ef.get("customfield_10014") or ef.get("summary") or ekey
        in_roster = (not prefix) or str(gname).startswith(prefix)   # (#47)
        games.append({
            "name": gname,
            "jira": ekey,
            "in_roster": in_roster,                 # default board vs add-game candidate (#47)
            "priority": str(priority.get("name") or "?"),
            "est": est,
            "spent": spent,
            "remaining": round(est - spent, 2),
            "pct": round(spent / est, 4) if est > 0 else 0.0,
            "dev_name": assignee.get("displayName"),
            "fixVersions": fvs,                     # [{name,released,releaseDate}]
            "delivered": delivered,                 # (#2/#3)
            "epic_status": epic_status,             # raw Jira status (reference)
            "target_date": epic_due.isoformat() if epic_due else None,  # (#40)
            "workflow_status": auto_status,         # derived (Decision #32)
            "disciplines": disciplines,
            "current_stage": stage,                 # derived (Decision #32)
        })
        if verbose:
            tag = f" delivered {delivered['fv']}@{delivered['date']}" if delivered else ""
            print(f"   [{ei}/{len(epics)}] {ekey}: {len(children)}+{len(subtasks)} issues, "
                  f"{est:.0f}h est/{spent:.0f}h spent · epic={epic_status} -> {auto_status} / {stage}{tag}")

    # (#3) Drop games that were delivered more than the grace window ago.
    kept_games, dropped = [], 0
    for g in games:
        dv = g.get("delivered")
        dd = _parse_date(dv["date"]) if (dv and dv.get("date")) else None
        if dd and (today - dd).days > DELIVERED_GRACE_DAYS:
            dropped += 1
            continue
        kept_games.append(g)
    if dropped:
        print(f"   {OK} dropped {dropped} game(s) delivered >{DELIVERED_GRACE_DAYS}d ago")
    games = kept_games

    # 6. SPRINTS — board query (preferred) merged with issue-derived metadata
    if board_id:
        try:
            for s in client.board_sprints(board_id):
                start = _parse_date(s.get("startDate"))
                if start is None:
                    continue
                sid = int(s["id"])
                sprint_meta[sid] = {
                    "start": start,
                    "end": _parse_date(s.get("endDate")),
                    "name": s.get("name"),
                }
            print(f"   {OK} board {board_id}: merged sprint dates")
        except requests.HTTPError as exc:
            print(f"   {WARN} board {board_id} query failed ({exc}); using issue-derived sprints")
    else:
        print(f"   {WARN} no board id ({cfg['board_env']}); using issue-derived sprint dates only")

    sprints, kept_ids = build_sprint_list(sprint_meta)

    # Keep only discipline sprint ids that resolved to a dated sprint.
    # (current_stage / workflow_status are ticket-derived, independent of sprints.)
    for g in games:
        for d in g["disciplines"]:
            d["sprints"] = [sid for sid in d["sprints"] if sid in kept_ids]

    if all_skips and verbose:
        uniq = sorted(set(all_skips))
        print(f"   {WARN} skipped {len(all_skips)} unmapped child issue(s); {len(uniq)} distinct type/summary:")
        for s in uniq[:25]:
            print(f"        - {s}")

    return {"games": games, "sprints": sprints}


def build_sprint_list(sprint_meta: dict[int, dict]) -> tuple[list[dict], set]:
    """Build the SPRINTS list using the boards' real sprint names + dates.

    Every sprint with a start date is kept (closed history + active + future),
    chronological. Date-less sprints (e.g. "Refined Backlog") are dropped — they
    can't be placed on a time axis. Label = the board's own sprint name.
    """
    rows = []
    kept: set = set()
    for sid, meta in sprint_meta.items():
        start = meta.get("start")
        if start is None:
            continue
        name = (meta.get("name") or "").strip() or f"Sprint {sid}"
        end = meta.get("end") or (start + timedelta(days=SPRINT_DAYS - 1))
        rows.append({"id": sid, "label": name, "start": start, "end": end})
        kept.add(sid)
    rows.sort(key=lambda r: r["start"])
    sprints = [{"id": r["id"], "label": r["label"],
                "start": r["start"].isoformat(), "end": r["end"].isoformat()} for r in rows]
    return sprints, kept


# ============================================================================
#  Output
# ============================================================================
def write_data_file(proj_key: str, payload: dict, refreshed_at: str) -> Path:
    """Emit dashboard-data-{project}.js as a namespaced global.

    Published as window.GP_DATA[<key>] so the combined game-pipeline.html can
    load both projects' data files at once without `const` collisions, while
    the standalone shells read the same global.
    """
    out = PROJECTS[proj_key]["out"]
    obj = {
        "games": payload["games"],
        "sprints": payload["sprints"],
        "refreshed_at": refreshed_at,
    }
    obj_js = json.dumps(obj, ensure_ascii=False)
    text = (
        f"// Auto-generated by build_jira_data.py — do not edit by hand.\n"
        f"// Project: {PROJECTS[proj_key]['key']} · refreshed {refreshed_at}\n"
        f"window.GP_DATA = window.GP_DATA || {{}};\n"
        f"window.GP_DATA[{proj_key!r}] = {obj_js};\n"
    )
    out.write_text(text, encoding="utf-8")
    return out


def archive_snapshot(proj_key: str, payload: dict, stamp: str) -> Path:
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    dest = ARCHIVE_DIR / f"{stamp}_{proj_key}_data.json"
    dest.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return dest


# ============================================================================
#  CLI
# ============================================================================
def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Build dashboard-data-{project}.js from Jira for the Game Pipeline dashboard.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--project", choices=["v2", "ig", "both"], default="both",
                   help="Which project(s) to build (default: both).")
    p.add_argument("--today", metavar="YYYY-MM-DD", default=None,
                   help="Snapshot date for current_stage derivation (default: system date).")
    p.add_argument("--verbose", action="store_true", help="Verbose per-epic + skip logging.")
    return p.parse_args(argv)


def resolve_today(value: Optional[str]) -> date:
    if value is None:
        return date.today()
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        raise SystemExit(f"{ERR} --today must be YYYY-MM-DD, got: {value!r}")


DEFAULT_BASE_URL = "https://ponggamestudios.atlassian.net"


def require_env() -> tuple[str, str, str]:
    load_dotenv(ROOT / ".env")
    # JIRA_BASE_URL defaults to the studio site so CI only needs the two
    # already-configured secrets (JIRA_EMAIL, JIRA_API_TOKEN).
    base = os.environ.get("JIRA_BASE_URL", "").strip() or DEFAULT_BASE_URL
    email = os.environ.get("JIRA_EMAIL", "").strip()
    token = os.environ.get("JIRA_API_TOKEN", "").strip()
    missing = [k for k, v in {"JIRA_EMAIL": email, "JIRA_API_TOKEN": token}.items() if not v]
    if missing:
        raise SystemExit(
            f"{ERR} Missing env: {', '.join(missing)}.\n"
            f"  Copy .env.template to .env and fill it in (see .env.example)."
        )
    return base, email, token


def print_summary(proj_key: str, payload: dict) -> None:
    games = payload["games"]
    sprints = payload["sprints"]
    total_est = round(sum(g["est"] for g in games))
    total_spent = round(sum(g["spent"] for g in games))
    stage_dist: dict[str, int] = {}
    status_dist: dict[str, int] = {}
    for g in games:
        stage_dist[g["current_stage"]] = stage_dist.get(g["current_stage"], 0) + 1
        status_dist[g["workflow_status"]] = status_dist.get(g["workflow_status"], 0) + 1
    print(f"   {OK} {PROJECTS[proj_key]['key']}: {len(games)} games · {total_est}h est · "
          f"{total_spent}h spent · {len(sprints)} sprints")
    if status_dist:
        print("        status -> " + " ".join(f"{k}:{v}" for k, v in sorted(status_dist.items(), key=lambda x: -x[1])))
    if stage_dist:
        print("        stage  -> " + " ".join(f"{k}:{v}" for k, v in sorted(stage_dist.items(), key=lambda x: -x[1])))


def main(argv: Optional[list[str]] = None) -> int:
    # Windows consoles default to cp1252 and can't encode ✓/⚠/✗. Force UTF-8.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    args = parse_args(argv)
    today = resolve_today(args.today)
    targets = ["v2", "ig"] if args.project == "both" else [args.project]

    base, email, token = require_env()
    client = JiraClient(base, email, token, verbose=args.verbose)

    now = datetime.now()
    refreshed_at = now.strftime("%Y-%m-%d %H:%M")
    stamp = now.strftime("%Y-%m-%d-%H%M")

    print(f"{OK} Game Pipeline Jira builder · base {base} · today {today.isoformat()}")
    rc = 0
    for proj_key in targets:
        try:
            payload = build_project(client, proj_key, today, args.verbose)
        except requests.HTTPError as exc:
            print(f"{ERR} {proj_key}: Jira HTTP error: {exc}")
            rc = 1
            continue
        except Exception as exc:  # pragma: no cover
            print(f"{ERR} {proj_key}: build failed: {exc}")
            rc = 1
            continue
        out = write_data_file(proj_key, payload, refreshed_at)
        snap = archive_snapshot(proj_key, payload, stamp)
        print(f"   {OK} wrote {out.name}  ·  snapshot {snap.relative_to(ROOT)}")
        print_summary(proj_key, payload)

    print(f"{OK} Done." if rc == 0 else f"{WARN} Finished with errors.")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
