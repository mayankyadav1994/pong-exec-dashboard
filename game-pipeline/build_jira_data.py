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
HISTORY_DIR = ROOT / "history"     # compact daily target/scope snapshots for the HISTORY tab (#66)
HISTORY_KEEP_DAYS = 180

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
# "review" is a synthetic lane (#59): it has no Jira issue type of its own — it is
# assembled from tickets across the other disciplines whose title contains
# "Review" (see add_issue). It sits between Sound and QA.
DISCIPLINE_ORDER = ["art", "design", "math", "dev", "sound", "review", "qa"]
DISCIPLINE_LABEL = {
    "art": "Creative / Art", "design": "Design (GDD)", "math": "Math",
    "dev": "Development", "sound": "Sound", "review": "Review", "qa": "QA",
}
# Downstream pipeline order used to pick the "current" discipline (Step 3).
STAGE_PIPELINE = ["design", "art", "math", "dev", "sound", "review", "qa"]
# Issue-type disciplines that a Review-titled ticket can be pulled OUT of into the
# synthetic review lane (everything except the synthetic lane itself).
REVIEWABLE_DISCIPLINES = {"art", "design", "math", "dev", "sound", "qa"}
# Match "Review" as a whole word (case-insensitive) so "Preview"/"previewed" don't
# get miscounted as review work. Catches "… - Review", "Review & Refinement", etc.
REVIEW_RE = re.compile(r"\breview\b", re.I)

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

    def all_worklogs(self, key: str) -> list[dict]:
        """Every worklog for an issue (paginated) — used only when the inline
        `worklog` field on a search result is truncated (>maxResults)."""
        out: list[dict] = []
        start_at = 0
        while True:
            data = self._request("GET", f"/rest/api/3/issue/{key}/worklog",
                                  params={"startAt": start_at, "maxResults": 100})
            vals = data.get("worklogs", []) or []
            out.extend(vals)
            start_at += len(vals)
            if start_at >= (data.get("total") or len(out)) or not vals:
                break
            time.sleep(0.15)
        return out

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


def _worklog_week_hours(entries: list) -> dict:
    """{Monday-ISO -> hours} from worklog entries, keyed by each log's `started`
    date snapped back to that week's Monday (#60)."""
    out: dict = {}
    for w in entries or []:
        st = (w.get("started") or "")[:10]
        dd = _parse_date(st)
        if dd is None:
            continue
        monday = (dd - timedelta(days=dd.weekday())).isoformat()
        out[monday] = out.get(monday, 0.0) + _secs_to_hours(w.get("timeSpentSeconds"))
    return out


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
    # Open Bug tickets anywhere in the subtree → game is effectively in QA,
    # even when the epic's own Jira status still says "In Progress". Matches
    # the on-the-ground rule: bugs filed = the game is being QA'd. Bugs are
    # still excluded from hour aggregation; this is just a status signal.
    has_open_bug = (aggs.get("_open_bugs") or 0) > 0
    prod_wip = any(flags[k]["wip"] > 0 for k in ("art", "math", "dev", "sound", "review"))
    design_wip = flags["design"]["wip"] > 0
    any_preprod = any(flags[k]["preprod"] > 0 for k in DISCIPLINE_ORDER)

    if (rel["done"] > 0 and not active_any) or all_done:
        return "Signed Off"
    if any_qa or has_open_bug or rel["active"]:
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

    # 1. Epics — fetch ALL, then keep board-eligible + active candidates (#50).
    epic_jql = f"project = {jira_project} AND issuetype = Epic ORDER BY rank ASC"
    epics = client.search_jql(
        epic_jql,
        ["summary", "status", "assignee", "priority", "fixVersions",
         "customfield_10014", "duedate"],
    )
    print(f"   {OK} {len(epics)} epic(s) total")

    prefix = cfg.get("name_prefix")

    def _epic_name(e: dict) -> str:
        f = e.get("fields", {}) or {}
        return str(f.get("customfield_10014") or f.get("summary") or e.get("key") or "")

    def _has_fv(e: dict) -> bool:
        return bool((e.get("fields", {}) or {}).get("fixVersions"))

    def _is_done(e: dict) -> bool:
        sc = (((e.get("fields", {}) or {}).get("status") or {}).get("statusCategory") or {}).get("key")
        return (sc or "").lower() == "done"

    # (#50) Keep an epic if it has a fixVersion (board-eligible + existing infra
    # candidates) OR it's an ACTIVE (non-Done) prefixed game without a fixVersion
    # (add-game candidate). Drop non-prefixed no-fixVersion epics and closed
    # no-fixVersion games — they'd be pure noise / history.
    before = len(epics)
    epics = [e for e in epics
             if _has_fv(e) or (prefix and _epic_name(e).startswith(prefix) and not _is_done(e))]
    n_board = sum(1 for e in epics if _has_fv(e) and (not prefix or _epic_name(e).startswith(prefix)))
    print(f"   {OK} kept {len(epics)}/{before} epic(s): ~{n_board} board (prefixed + fixVersion), rest are add-game candidates")
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
                        # timeestimate = Jira's Remaining Estimate — required for
                        # the scope = spent + remaining formula. Missing this in
                        # the fetch list means every ticket's remaining reads as
                        # 0 regardless of what Jira actually holds.
                        "timeestimate",
                        # summary is needed to detect Review tickets by title (#59).
                        "summary",
                        # worklog powers the per-lane weekly activity "pulse" (#60):
                        # each entry's `started` date + timeSpentSeconds → hours/week.
                        "worklog",
                        "duedate", "assignee", sprint_field]
        children = client.search_jql(f"parent = {ekey}", ISSUE_FIELDS + ["subtasks"])
        child_keys = [c.get("key") for c in children if c.get("key")]
        subtasks = []
        for i in range(0, len(child_keys), 50):     # chunk the parent-in query
            chunk = child_keys[i:i + 50]
            subtasks += client.search_jql(
                "parent in (" + ",".join(chunk) + ")", ISSUE_FIELDS + ["parent"])

        def _new_agg():
            # people: assignee displayName -> spent hours, for the per-department
            # "who worked on this" breakdown (#51). Assignees are kept even at 0h
            # so assigned-but-not-started owners still surface (dimmed in the UI).
            # `remaining` = sum of Jira `timeestimate` (Remaining Estimate).
            # Together with `spent` it defines the live scope per the formula
            # scope = spent + remaining — how much total work the ticket will
            # actually take, not what someone originally thought.
            # weeks: Monday-ISO -> hours logged that week (from worklogs), for the
            # activity "pulse" bar in the timeline tab (#60).
            return {"est": 0.0, "spent": 0.0, "remaining": 0.0,
                    "sprints": set(), "due": [],
                    "buckets": {"todo": 0, "wip": 0, "qa": 0, "hold": 0, "done": 0},
                    "preprod": 0, "people": {}, "weeks": {}}
        aggs = {k: _new_agg() for k in DISCIPLINE_ORDER}
        aggs["_release"] = _new_agg()
        # Open bug signal — set by add_issue() when it encounters an open Bug
        # ticket anywhere in the epic subtree. Used by derive_status to
        # auto-classify the epic as "In QA" even when the epic's own Jira
        # status hasn't been moved yet.
        aggs["_open_bugs"] = 0

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
            # Review extraction (#59): a discipline ticket whose title contains
            # "Review" is review work — route it to the synthetic `review` lane so
            # its hours/sprints/people MOVE there instead of counting under its
            # issue-type discipline (no double counting). Bugs/release/unmapped are
            # left alone. "Code review" tickets live under Release epics, which are
            # outside a game's subtree, so they never reach here.
            if cat in REVIEWABLE_DISCIPLINES and REVIEW_RE.search(fields.get("summary") or ""):
                cat = "review"
            status_l = ((fields.get("status") or {}).get("name") or "").strip().lower()
            bk = bucket(status_l)
            if cat == "exclude":
                # Bugs are excluded from hour aggregation but ARE counted as a
                # QA signal so the epic gets classified "In QA" when bug
                # tickets are filed even if the epic itself hasn't been moved.
                # Only OPEN bugs count — closed/signed-off ones don't reopen QA.
                if itype.strip().lower() == "bug" and bk != "done":
                    aggs["_open_bugs"] += 1
                return
            if cat == "release":
                aggs["_release"]["buckets"][bk] += 1     # signal only — no hours
                return
            if cat == "unmapped":
                # Log with the issue KEY and hours so scope audits can find
                # exactly which tickets got dropped (not just the issuetype).
                orig_h = _secs_to_hours(fields.get("timeoriginalestimate"))
                rem_h  = _secs_to_hours(fields.get("timeestimate"))
                spent_h = _secs_to_hours(fields.get("timespent"))
                # `_issue_key_for_unmapped` is stashed onto `fields` a few lines
                # above the call site so we can identify it here without
                # threading a new argument through.
                key = fields.get("_issue_key_for_unmapped") or "?"
                all_skips.append(f"{ekey}/{key} type={itype!r} orig={orig_h}h spent={spent_h}h rem={rem_h}h")
                return
            d = aggs[cat]
            d["buckets"][bk] += 1
            if status_l in PREPROD_STATUSES:
                d["preprod"] += 1
            dd = _parse_date(fields.get("duedate"))   # department-task target (#40)
            if dd is not None:
                d["due"].append(dd)
            issue_spent = _secs_to_hours(fields.get("timespent"))        # every issue's own time
            d["spent"] += issue_spent
            # Attribute this issue's logged hours to its assignee (#51). Worklog
            # authors are unusable here (all logged via the "Timesheets by Tempo"
            # bot), so the assignee is the reliable per-person signal.
            who = ((fields.get("assignee") or {}).get("displayName") or "").strip()
            if who:
                d["people"][who] = d["people"].get(who, 0.0) + issue_spent
            # Weekly activity pulse (#60): fold this issue's worklogs into the
            # discipline's per-week hours. If the inline worklog was truncated,
            # page the full set so old weeks aren't missed.
            wl = fields.get("worklog") or {}
            entries = wl.get("worklogs") or []
            if (wl.get("total") or 0) > (wl.get("maxResults") or len(entries)):
                wkey = fields.get("_issue_key_for_unmapped")
                if wkey:
                    try:
                        entries = client.all_worklogs(wkey)
                    except Exception:
                        pass
            for mon, hrs in _worklog_week_hours(entries).items():
                d["weeks"][mon] = d["weeks"].get(mon, 0.0) + hrs
            if include_est:
                d["est"] += _secs_to_hours(fields.get("timeoriginalestimate"))
                # Remaining Estimate — gated the same way so a parent Story
                # with classified sub-tasks doesn't double-count its own
                # remaining alongside the sub-tasks' remaining.
                d["remaining"] += _secs_to_hours(fields.get("timeestimate"))
            for spr in parse_sprint_field(fields.get(sprint_field)):
                sid = spr["id"]
                if spr["start"] is not None:
                    meta = sprint_meta.setdefault(sid, {"start": None, "end": None, "name": None})
                    meta["start"] = meta["start"] or spr["start"]
                    meta["end"] = meta["end"] or spr["end"]
                    meta["name"] = meta["name"] or spr["name"]
                d["sprints"].add(sid)

        # Children: count own estimate only when they have NO classified sub-task
        # (otherwise the sub-task estimates represent the work). Exception per
        # the Jul 22 directive: if any hours are logged directly on the parent
        # (timespent > 0), then real parent-level work happened — include its
        # est + remaining as well so that work isn't dropped from scope. This
        # matters for Stories where reviews / meetings / integration hours get
        # logged on the Story itself while sub-tasks track the granular work.
        for ch in children:
            f = ch.get("fields", {}) or {}
            f["_issue_key_for_unmapped"] = ch.get("key")   # for the loud log
            has_classified_subs = classified_subs.get(ch.get("key"), 0) > 0
            parent_own_spent = _secs_to_hours(f.get("timespent")) > 0
            include_own_est = (not has_classified_subs) or parent_own_spent
            add_issue(f, include_own_est)
        # Sub-tasks: always contribute their own est + spent.
        for st in subtasks:
            f = st.get("fields", {}) or {}
            f["_issue_key_for_unmapped"] = st.get("key")
            add_issue(f, True)

        disciplines = []
        for dkey in DISCIPLINE_ORDER:
            d = aggs[dkey]
            est = round(d["est"], 2)
            spent = round(d["spent"], 2)
            remaining = round(d["remaining"], 2)
            scope = round(spent + remaining, 2)   # live scope per team directive
            disciplines.append({
                "name": DISCIPLINE_LABEL[dkey],
                "key": dkey,
                # est = original planning estimate (kept for reference / audits)
                "est": est,
                "spent": spent,
                # remaining = Jira's Remaining Estimate (what's still to do)
                "remaining": remaining,
                # scope = spent + remaining = live total this discipline will
                # actually take, per the user directive from Jul 22.
                "scope": scope,
                # pct is now spent / scope so "100%" means done (not "budget
                # exhausted") — matches how the number reads on the pill.
                "pct": round(spent / scope, 4) if scope > 0 else 0.0,
                "sprints": sorted(d["sprints"]),
                "phase": discipline_phase(d),
                # Department target = latest due date among this discipline's
                # tickets (the producer's targeted dates on dept tasks; #40).
                "target_date": max(d["due"]).isoformat() if d["due"] else None,
                # Who worked in this department, most hours first (#51).
                "people": sorted(
                    ({"name": n, "hours": round(h, 2)} for n, h in d["people"].items()),
                    key=lambda p: (-p["hours"], p["name"].lower())),
                # Weekly activity pulse (#60): Monday-ISO -> hours logged.
                "weeks": {k: round(v, 2) for k, v in sorted(d["weeks"].items())},
            })

        est = round(sum(x["est"] for x in disciplines), 2)
        spent = round(sum(x["spent"] for x in disciplines), 2)
        remaining = round(sum(x["remaining"] for x in disciplines), 2)
        scope = round(spent + remaining, 2)
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
        # Board roster = prefixed game WITH a fixVersion. Prefixed games lacking a
        # fixVersion are add-game candidates, not on the board by default (#50).
        in_roster = bool(prefix) and str(gname).startswith(prefix) and bool(fvs)
        games.append({
            "name": gname,
            "jira": ekey,
            "in_roster": in_roster,                 # default board vs add-game candidate (#47)
            "priority": str(priority.get("name") or "?"),
            # est = original planning estimate (kept for audits + backward-compat).
            "est": est,
            "spent": spent,
            # remaining now comes straight from Jira's Remaining Estimate
            # summed across the same tickets — not "est - spent" which was
            # wrong for anything with scope growth. See directive from Jul 22.
            "remaining": remaining,
            # scope = live total effort (spent + remaining), the new denominator
            # used everywhere the dashboard shows spent/est or spent/scope.
            "scope": scope,
            # pct = spent / scope so 100% means done, not "budget exhausted".
            "pct": round(spent / scope, 4) if scope > 0 else 0.0,
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

    # 6. SPRINTS — board query (preferred) merged with issue-derived metadata.
    # board_sprint_ids = this project's OWN board sprints; used to keep other
    # teams' sprints (e.g. "CSS Sprint 7", "V2 Sprint 5") that an IG ticket may be
    # assigned to OFF the IG axis (#64).
    board_sprint_ids: set[int] = set()
    if board_id:
        try:
            for s in client.board_sprints(board_id):
                start = _parse_date(s.get("startDate"))
                if start is None:
                    continue
                sid = int(s["id"])
                board_sprint_ids.add(sid)
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

    sprints, kept_ids = build_sprint_list(sprint_meta, board_sprint_ids, f"{jira_project} Sprint")

    # Keep only discipline sprint ids that resolved to a dated sprint.
    # (current_stage / workflow_status are ticket-derived, independent of sprints.)
    for g in games:
        for d in g["disciplines"]:
            d["sprints"] = [sid for sid in d["sprints"] if sid in kept_ids]

    # Loud unmapped-ticket log — always printed (not gated on `verbose`) so
    # scope audits can see exactly which tickets were dropped. Bug lookups
    # like "why isn't IG-XXXX in my math total?" resolve here in one grep.
    if all_skips:
        uniq = sorted(set(all_skips))
        print(f"   {WARN} skipped {len(all_skips)} unmapped child issue(s); {len(uniq)} distinct:")
        for s in uniq[:50]:
            print(f"        - {s}")
        if len(uniq) > 50:
            print(f"        … {len(uniq) - 50} more (raise the cap in build_jira_data.py if you need them all)")

    return {"games": games, "sprints": sprints}


# A sprint named like "IG Sprint 11" / "CSS Sprint 7" / "V2 Sprint 5" is OWNED by
# the leading team token. Used to keep other teams' sprints off a project's axis
# even when they live on the same shared board. (#64)
SPRINT_OWNER_RE = re.compile(r"^([A-Za-z0-9]+)\s+Sprint\b", re.I)


def build_sprint_list(sprint_meta: dict[int, dict], board_ids: set = None,
                      name_prefix: str = "") -> tuple[list[dict], set]:
    """Build the SPRINTS list using the boards' real sprint names + dates.

    Every dated sprint is kept (closed history + active + future), chronological;
    date-less sprints (e.g. "Refined Backlog") are dropped. Label = the board's
    own sprint name.

    Axis hygiene (#64): teams SHARE boards — "CSS Sprint 7" and "V2 Sprint 5" are
    created on the IG board (250), so board membership is NOT proof of ownership.
    A sprint whose name is "<TEAM> Sprint N" is owned by <TEAM>; drop it when
    <TEAM> isn't this project, regardless of which board carries it. Sprints with
    a non-team name (e.g. a one-off "Hotfix") are kept when on our board or when
    they match this project's prefix.
    """
    board_ids = board_ids or set()
    px = (name_prefix or "").strip().lower()            # e.g. "ig sprint"
    own_token = px.split(" ", 1)[0] if px else ""       # e.g. "ig"
    rows = []
    kept: set = set()
    for sid, meta in sprint_meta.items():
        start = meta.get("start")
        if start is None:
            continue
        name = (meta.get("name") or "").strip() or f"Sprint {sid}"
        low = name.lower()
        # Foreign-team sprint sitting on our shared board → off the axis.
        m = SPRINT_OWNER_RE.match(name)
        if own_token and m and m.group(1).lower() != own_token:
            continue
        # Keep our own-prefixed sprints + any on-board sprint that isn't a foreign
        # team's (falls through above), else drop non-board foreign issue sprints.
        if board_ids and sid not in board_ids and not (px and low.startswith(px)):
            continue
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
#  History — completion-date moves + scope growth (#66)
#
#  Each build writes one compact snapshot per day per project under
#  history/<proj>/<YYYY-MM-DD>.json capturing every game's planned target date
#  and total scope. compute_history_events() then diffs consecutive days into a
#  change-event log baked onto each game (g["history"]) that the HISTORY tab
#  renders. Mirrors the release-timeline snapshot pattern; git-committed so the
#  log survives across CI runs. Forecast movement is added in phase 2.
# ============================================================================
def _hist_date(s: Any) -> Optional[date]:
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", str(s or ""))
    return date(int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None


def workday_delta(from_iso: str, to_iso: str) -> Optional[int]:
    """Signed count of working days (Mon–Fri) from `from_iso` to `to_iso`.
    Positive => `to` is later (a slip); negative => pulled earlier. Holidays are
    not modelled (kept simple; the release timeline's holiday table can be ported
    later if needed)."""
    a, b = _hist_date(from_iso), _hist_date(to_iso)
    if not a or not b:
        return None
    sign = 1 if b >= a else -1
    lo, hi = (a, b) if b >= a else (b, a)
    days, cur = 0, lo
    while cur < hi:
        cur += timedelta(days=1)
        if cur.weekday() < 5:
            days += 1
    return sign * days


def write_history_snapshot(proj_key: str, payload: dict, today: date) -> Path:
    proj_dir = HISTORY_DIR / proj_key
    proj_dir.mkdir(parents=True, exist_ok=True)
    games = {}
    for g in payload["games"]:
        jira = g.get("jira")
        if not jira:
            continue
        sc = round(g.get("scope") or 0)
        # scope 0 == "no estimate yet" (unknown), stored as None so it never reads
        # as real growth when the field first appears.
        games[jira] = {"target": g.get("target_date") or None, "scope": sc or None}
    dest = proj_dir / f"{today.isoformat()}.json"
    dest.write_text(json.dumps({"date": today.isoformat(), "games": games}, ensure_ascii=False), encoding="utf-8")
    cutoff = today - timedelta(days=HISTORY_KEEP_DAYS)
    for f in proj_dir.glob("*.json"):
        fd = _hist_date(f.stem)
        if fd and fd < cutoff:
            try:
                f.unlink()
            except OSError:
                pass
    return dest


def load_history(proj_key: str) -> list[dict]:
    proj_dir = HISTORY_DIR / proj_key
    if not proj_dir.exists():
        return []
    snaps = []
    for f in sorted(proj_dir.glob("*.json")):
        try:
            snaps.append(json.loads(f.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            continue
    snaps.sort(key=lambda s: s.get("date", ""))
    return snaps


def compute_history_events(proj_key: str, games: list[dict]) -> None:
    """Diff consecutive daily snapshots into per-game change events; bake onto
    each game as g["history"] = {"target": [...], "scope": [...]}.

    target event: {date, from, to, days}  (days = signed working-day delta)
    scope  event: {date, from, to, delta} (hours; only real growth/shrink)
    The first-ever target assignment (null→date) is NOT a move. Scope deltas are
    only emitted between two known (>0) values so the field's first appearance
    doesn't read as a jump."""
    series: dict[str, list] = {}
    for snap in load_history(proj_key):
        d = snap.get("date")
        for jira, rec in (snap.get("games") or {}).items():
            series.setdefault(jira, []).append((d, rec.get("target"), rec.get("scope")))
    for g in games:
        pts = series.get(g.get("jira"), [])
        tgt_events, scope_events = [], []
        prev_t = prev_s = None
        for (d, t, s) in pts:
            if prev_t and t and prev_t != t:
                tgt_events.append({"date": d, "from": prev_t, "to": t, "days": workday_delta(prev_t, t)})
            if prev_s and s and abs(s - prev_s) >= 1:
                scope_events.append({"date": d, "from": round(prev_s), "to": round(s), "delta": round(s - prev_s)})
            if t:
                prev_t = t
            if s:
                prev_s = s
        g["history"] = {"target": tgt_events, "scope": scope_events}


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
        write_history_snapshot(proj_key, payload, today)     # persist today's point (#66)
        compute_history_events(proj_key, payload["games"])    # bake the change-event log
        out = write_data_file(proj_key, payload, refreshed_at)
        snap = archive_snapshot(proj_key, payload, stamp)
        n_moves = sum(len(g["history"]["target"]) for g in payload["games"])
        n_scope = sum(len(g["history"]["scope"]) for g in payload["games"])
        print(f"   {OK} wrote {out.name}  ·  snapshot {snap.relative_to(ROOT)}  ·  history {n_moves} date-move(s) / {n_scope} scope-change(s)")
        print_summary(proj_key, payload)

    print(f"{OK} Done." if rc == 0 else f"{WARN} Finished with errors.")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
