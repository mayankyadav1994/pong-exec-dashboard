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
        "out": ROOT / "dashboard-data-v2.js",
    },
    "ig": {
        "key": "IG",
        "sprint_field": "customfield_10103",
        "board_env": "JIRA_BOARD_ID_IG",
        "out": ROOT / "dashboard-data-ig.js",
    },
}

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
    # design (TBD - confirm with user; harmless if these issuetypes don't exist)
    "design task": "design", "design subtask": "design", "gdd task": "design",
}
DISCIPLINE_ORDER = ["art", "design", "math", "dev", "sound", "qa"]
DISCIPLINE_LABEL = {
    "art": "Creative / Art", "design": "Design (GDD)", "math": "Math",
    "dev": "Development", "sound": "Sound", "qa": "QA",
}

EXCLUDE_ISSUETYPES = {"bug", "enhancement"}
EXCLUDE_SUMMARY_RE = re.compile(r"^\s*(release|merge)\b", re.IGNORECASE)

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
def discipline_for(issuetype: str, summary: str, verbose_skips: list) -> Optional[str]:
    """Map a child issue to a discipline key, or None if excluded/unmapped."""
    it = (issuetype or "").strip().lower()
    summ = summary or ""
    if it in EXCLUDE_ISSUETYPES or EXCLUDE_SUMMARY_RE.match(summ):
        return None
    key = DISCIPLINE_BY_ISSUETYPE.get(it)
    if key is None:
        verbose_skips.append(f"{issuetype!r} :: {summary!r}")
    return key


def _secs_to_hours(v: Any) -> float:
    try:
        return float(v) / 3600.0
    except (TypeError, ValueError):
        return 0.0


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
        ["summary", "status", "assignee", "priority", "fixVersions", "customfield_10014"],
    )
    print(f"   {OK} {len(epics)} epic(s)")
    if not epics:
        print(f"   {WARN} no epics found - writing empty placeholder")

    sprint_meta: dict[int, dict] = {}     # id -> {start,end,name}
    games: list[dict] = []
    all_skips: list[str] = []

    # 2-5. Children per epic
    for ei, epic in enumerate(epics, 1):
        ekey = epic.get("key")
        ef = epic.get("fields", {}) or {}
        children = client.search_jql(
            f"parent = {ekey}",
            ["summary", "issuetype", "status", "timeoriginalestimate", "timespent",
             "assignee", sprint_field],
        )
        disc_map: dict[str, dict] = {}
        for ch in children:
            cf = ch.get("fields", {}) or {}
            itype = ((cf.get("issuetype") or {}).get("name")) or ""
            summ = cf.get("summary") or ""
            dkey = discipline_for(itype, summ, all_skips)
            if dkey is None:
                continue
            d = disc_map.setdefault(dkey, {"est": 0.0, "spent": 0.0, "sprints": set()})
            d["est"] += _secs_to_hours(cf.get("timeoriginalestimate"))
            d["spent"] += _secs_to_hours(cf.get("timespent"))
            for spr in parse_sprint_field(cf.get(sprint_field)):
                sid = spr["id"]
                if spr["start"] is not None:
                    meta = sprint_meta.setdefault(sid, {"start": None, "end": None, "name": None})
                    meta["start"] = meta["start"] or spr["start"]
                    meta["end"] = meta["end"] or spr["end"]
                    meta["name"] = meta["name"] or spr["name"]
                d["sprints"].add(sid)

        disciplines = []
        for dkey in DISCIPLINE_ORDER:
            d = disc_map.get(dkey)
            est = round(d["est"], 2) if d else 0.0
            spent = round(d["spent"], 2) if d else 0.0
            disciplines.append({
                "name": DISCIPLINE_LABEL[dkey],
                "key": dkey,
                "est": est,
                "spent": spent,
                "pct": round(spent / est, 4) if est > 0 else 0.0,
                "sprints": sorted(d["sprints"]) if d else [],
            })

        est = round(sum(x["est"] for x in disciplines), 2)
        spent = round(sum(x["spent"] for x in disciplines), 2)
        assignee = ef.get("assignee") or {}
        priority = ef.get("priority") or {}
        games.append({
            "name": ef.get("customfield_10014") or ef.get("summary") or ekey,
            "jira": ekey,
            "priority": str(priority.get("name") or "?"),
            "est": est,
            "spent": spent,
            "remaining": round(est - spent, 2),
            "pct": round(spent / est, 4) if est > 0 else 0.0,
            "dev_name": assignee.get("displayName"),
            "fixVersions": [v.get("name") for v in (ef.get("fixVersions") or []) if v.get("name")],
            "status": (ef.get("status") or {}).get("name"),
            "disciplines": disciplines,
            "workflow_status": "Not Started",   # Decision #24
            "current_stage": "concept",         # filled below
        })
        if verbose:
            print(f"   [{ei}/{len(epics)}] {ekey}: {len(children)} children, {est:.0f}h est")

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
        print(f"   {WARN} no board id ({cfg['board_env']}); using issue-derived sprints + S1 anchor")

    sprints, kept_ids = build_sprint_list(sprint_meta)

    # Drop discipline sprint ids that didn't resolve to a kept (>= S1) sprint.
    for g in games:
        for d in g["disciplines"]:
            d["sprints"] = [sid for sid in d["sprints"] if sid in kept_ids]
        g["current_stage"] = derive_current_stage(g["disciplines"], sprint_meta, kept_ids, today)

    if all_skips and verbose:
        uniq = sorted(set(all_skips))
        print(f"   {WARN} skipped {len(all_skips)} unmapped child issue(s); {len(uniq)} distinct type/summary:")
        for s in uniq[:25]:
            print(f"        - {s}")

    return {"games": games, "sprints": sprints}


def build_sprint_list(sprint_meta: dict[int, dict]) -> tuple[list[dict], set]:
    """Turn collected sprint metadata into the anchored, chronological SPRINTS."""
    rows = []
    kept: set = set()
    for sid, meta in sprint_meta.items():
        start = meta.get("start")
        if start is None:
            continue
        label = anchored_label(start)
        if label is None:               # before S1 -> not shown (Decision #27)
            continue
        end = meta.get("end") or (start + timedelta(days=SPRINT_DAYS - 1))
        rows.append({"id": sid, "label": label, "start": start, "end": end})
        kept.add(sid)
    rows.sort(key=lambda r: r["start"])
    sprints = [{"id": r["id"], "label": r["label"],
                "start": r["start"].isoformat(), "end": r["end"].isoformat()} for r in rows]
    return sprints, kept


def derive_current_stage(disciplines: list[dict], sprint_meta: dict[int, dict],
                         kept_ids: set, today: date) -> str:
    """Discipline whose most recent active sprint is the latest one <= TODAY."""
    best_key = None
    best_start: Optional[date] = None
    for d in disciplines:
        for sid in d["sprints"]:
            if sid not in kept_ids:
                continue
            start = sprint_meta.get(sid, {}).get("start")
            if start is None or start > today:
                continue
            if best_start is None or start > best_start:
                best_start = start
                best_key = d["key"]
    return best_key or "concept"


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
    for g in games:
        stage_dist[g["current_stage"]] = stage_dist.get(g["current_stage"], 0) + 1
    print(f"   {OK} {PROJECTS[proj_key]['key']}: {len(games)} games · {total_est}h est · "
          f"{total_spent}h spent · {len(sprints)} sprints")
    if stage_dist:
        dist = " ".join(f"{k}:{v}" for k, v in sorted(stage_dist.items(), key=lambda x: -x[1]))
        print(f"        stages -> {dist}")


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
