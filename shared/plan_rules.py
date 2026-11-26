"""
plan_rules.py
=============
Planning rules the PMO dashboards share, so every builder applies one
definition instead of its own copy.

  Parked epics  An epic whose status is Canceled / Cancelled or On Hold is not
                planned work: its open tickets stay off people's plans. Time
                already logged on it still counts as history.
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

CONFIG_DIR = Path(__file__).resolve().parents[1] / "config"
HOLIDAY_CONFIGS = ("igaming.json", "v2.json", "css.json")
HOURS_PER_DAY = 7.0

# Jira spells it "Canceled" (IG epics); the Game Pipeline board says "Cancelled".
PARKED_RE = re.compile(r"^\s*(cancell?ed|on\s*hold)\s*$", re.I)


def is_parked(status: str | None) -> bool:
    """True for an epic / game status that takes its work off the plan."""
    return bool(status and PARKED_RE.match(status))


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
