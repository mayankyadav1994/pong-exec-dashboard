"""
plan_rules.py
=============
Planning rules the PMO dashboards share, so every builder applies one
definition instead of its own copy.

  Off-plan epics  An epic that is Closed / Done (finished: tickets left open
                under it are leftovers), Canceled / On Hold in Jira, or that
                the Game Pipeline board shows as On Hold / Cancelled, is not
                planned work: its open tickets stay off people's plans and
                boards. Finished work and time already logged on it still count
                as history. Used by People, Sprint Load and Team Board.
                ("Review In Progress" is in Jira's done category but the game
                is still being reviewed, so it stays on the plan.)
  Working days  Monday to Friday, minus the public holidays in the timeline
                configs (config/igaming.json, v2.json, css.json; the union of
                all three, the one list PMO edits in the timelines' config panel).
  Hours a day   7h of planned work per person per working day (70h over a
                standard 10-day sprint), as Sprint Load measures capacity.
"""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = ROOT / "config"
HOLIDAY_CONFIGS = ("igaming.json", "v2.json", "css.json")
HOURS_PER_DAY = 7.0
PIPELINE_FEEDS = ("ig", "igf", "v2")     # Game Pipeline boards: IG games, IG features, V2

# Jira spells it "Canceled" (IG epics); the Game Pipeline board says "Cancelled".
PARKED_RE = re.compile(r"^\s*(cancell?ed|on\s*hold)\s*$", re.I)
# A finished epic: open tickets still under it are leftovers, not planned work.
FINISHED = {"closed", "done"}

# Mirrors STATUS_PROGRESSION in game-pipeline/dashboard.js (stale-override rule).
STATUS_PROGRESSION = ["Not Started", "On Hold", "In Pre-Prod", "In Progress", "In QA",
                      "Signed Off", "Delivered"]


def is_parked(status: str | None) -> bool:
    """True for an epic / game status that takes its work off the plan."""
    return bool(status and PARKED_RE.match(status))


def is_finished(status: str | None) -> bool:
    """True for an epic status that means its work is done (Closed / Done)."""
    return bool(status and status.strip().lower() in FINISHED)


def pipeline_status(auto: str | None, shared: str | None) -> str:
    """The status the Game Pipeline board shows for a game: the PMO's shared
    override, unless Jira's own status has moved past it (resolveGameStatus in
    game-pipeline/dashboard.js)."""
    auto = auto or ""
    if shared and shared != auto:
        ai = STATUS_PROGRESSION.index(auto) if auto in STATUS_PROGRESSION else -1
        si = STATUS_PROGRESSION.index(shared) if shared in STATUS_PROGRESSION else -1
        if ai >= 0 and si >= 0 and ai > si:
            return auto                        # Jira moved past a stale override
        return shared
    return auto


def pipeline_parked(root: Path = ROOT) -> dict[str, str]:
    """epic key -> status, for games the Game Pipeline board shows as On Hold or
    Cancelled (board roster or added in Edit Plan; delivered games skipped)."""
    out: dict[str, str] = {}
    for feed in PIPELINE_FEEDS:
        df = root / "game-pipeline" / f"data-{feed}.json"
        if not df.exists():
            continue
        data = json.loads(df.read_text(encoding="utf-8"))
        pf = root / "game-pipeline" / f"plan-{feed}.json"
        plan = json.loads(pf.read_text(encoding="utf-8")) if pf.exists() else {}
        added, shared = set(plan.get("added") or []), plan.get("status") or {}
        for g in data.get("games") or []:
            if not g.get("jira") or g.get("delivered"):
                continue
            if g.get("in_roster") is False and g["jira"] not in added:
                continue
            ws = pipeline_status(g.get("workflow_status"), shared.get(g.get("name")))
            if is_parked(ws):
                out[g["jira"]] = ws
    return out


def parked_reason(epic_key: str | None, jira_status: str | None, pipe: dict[str, str]) -> str | None:
    """Why an epic's open work is off the plan, or None. `pipe` = pipeline_parked()."""
    if is_parked(jira_status) or is_finished(jira_status):
        return f"{jira_status} in Jira"
    if epic_key and epic_key in pipe:
        return f"{pipe[epic_key]} on the Game Pipeline board"
    return None


def load_holidays(config_dir: Path = CONFIG_DIR) -> set[date]:
    """Public holidays from every timeline config that exists."""
    days: set[date] = set()
    for name in HOLIDAY_CONFIGS:
        p = config_dir / name
        if not p.exists():
            continue
        for s in json.loads(p.read_text(encoding="utf-8")).get("holidays") or []:
            try:
                days.add(date.fromisoformat(s))
            except (TypeError, ValueError) as e:
                raise ValueError(f"{p}: holiday {s!r} is not a YYYY-MM-DD date") from e
    return days
