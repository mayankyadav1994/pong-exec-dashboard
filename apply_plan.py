"""
apply_plan.py
=============
Applies one Team Board plan change to Jira. Runs inside the apply-plan.yml
workflow, triggered by a repository_dispatch from the Team Board page. The
page never touches Jira directly (the token would be exposed and Jira Cloud
blocks browser CORS); it dispatches the intent and this script — which holds
the token via CI secrets — performs the write.

Payload (env PAYLOAD, JSON):
    { "action": "setDueDate",       "key": "IG-1234", "date": "2026-06-30" }
    { "action": "setDueDate",       "key": "IG-1234", "date": null }        # unschedule
    { "action": "addToSprint",      "key": "IG-1234" }
    { "action": "removeFromSprint", "key": "IG-1234" }

Guardrails:
  * setDueDate is allowed on any ticket — Tasks/Stories AND Subtasks are all
    draggable onto calendar days.
  * Before the first due-date change, the original value is stashed in the issue
    property ``teamboard.originalDueDate`` so a move is reversible.
"""

from __future__ import annotations

import json
import os
import sys

import requests
from dotenv import load_dotenv

from pathlib import Path

from jira_client import (agile_get, agile_post, jira_get, jira_get_raw,
                         jira_put, JIRA_BASE, _auth, JSON_HEADERS)

# Credentials: real env vars in CI; locally fall back to .env / game-pipeline/.env.
# load_dotenv does NOT override existing env vars, so CI secrets always win.
_ROOT = Path(__file__).resolve().parent
load_dotenv(_ROOT / ".env")
load_dotenv(_ROOT / "game-pipeline" / ".env")

PROJECT_BOARD = {"IG": "250", "V2": "316"}     # from refresh-game-pipeline.yml
ORIG_DUE_PROP = "teamboard.originalDueDate"
START_FIELD = "customfield_10684"              # "Target start" (paired with duedate)


def fail(msg):
    print(f"[err] {msg}")
    raise SystemExit(1)


def get_property(key, prop):
    r = jira_get_raw(f"/issue/{key}/properties/{prop}")
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.json().get("value")


def put_property(key, prop, value):
    r = requests.put(f"{JIRA_BASE}/issue/{key}/properties/{prop}", auth=_auth(),
                     headers={**JSON_HEADERS, "Content-Type": "application/json"},
                     json=value)
    r.raise_for_status()


def set_due_date(key, date):
    meta = jira_get(f"/issue/{key}", params={"fields": "issuetype,duedate"})
    # Both Task/Story and Subtask due dates are editable from the board (every
    # ticket is draggable onto a day). No issuetype restriction.
    # stash original due date once, for reversibility
    if get_property(key, ORIG_DUE_PROP) is None:
        original = (meta.get("fields", {}) or {}).get("duedate")
        put_property(key, ORIG_DUE_PROP, {"value": original})
        print(f"[ok] stashed original due date for {key}: {original!r}")
    jira_put(f"/issue/{key}", {"fields": {"duedate": date}})
    print(f"[ok] {key} duedate -> {date!r}")


def set_dates(key, start, due):
    """Write Target start + Due date together (either may be null). Used by the
    span calendar: drag the body to move both, an edge to change one."""
    meta = jira_get(f"/issue/{key}", params={"fields": f"issuetype,duedate,{START_FIELD}"})
    if get_property(key, ORIG_DUE_PROP) is None:
        f = meta.get("fields", {}) or {}
        put_property(key, ORIG_DUE_PROP, {"start": f.get(START_FIELD), "due": f.get("duedate")})
        print(f"[ok] stashed original dates for {key}: start={f.get(START_FIELD)!r} due={f.get('duedate')!r}")
    jira_put(f"/issue/{key}", {"fields": {"duedate": due, START_FIELD: start}})
    print(f"[ok] {key} start -> {start!r} · due -> {due!r}")


def project_of(key):
    return key.split("-", 1)[0].upper()


def active_sprint_id(project):
    board = PROJECT_BOARD.get(project)
    if not board:
        fail(f"no board configured for project {project}")
    data = agile_get(f"/board/{board}/sprint", params={"state": "active"})
    vals = data.get("values", [])
    if not vals:
        fail(f"no active sprint on board {board} ({project})")
    return vals[0]["id"]


def add_to_sprint(key):
    sid = active_sprint_id(project_of(key))
    agile_post(f"/sprint/{sid}/issue", {"issues": [key]})
    print(f"[ok] added {key} to active sprint {sid}")


def remove_from_sprint(key):
    agile_post("/backlog/issue", {"issues": [key]})   # backlog == out of sprint
    print(f"[ok] moved {key} to backlog (out of sprint)")


def main():
    raw = os.environ.get("PAYLOAD", "").strip()
    if not raw:
        fail("no PAYLOAD provided")
    try:
        p = json.loads(raw)
    except json.JSONDecodeError as e:
        fail(f"bad PAYLOAD json: {e}")
    action = p.get("action")
    key = (p.get("key") or "").strip().upper()
    if not key or "-" not in key:
        fail(f"missing/invalid issue key: {key!r}")

    print(f"[ok] apply {action} on {key}")
    if action == "setDueDate":
        set_due_date(key, p.get("date"))
    elif action == "setDates":
        set_dates(key, p.get("start"), p.get("due"))
    elif action == "addToSprint":
        add_to_sprint(key)
    elif action == "removeFromSprint":
        remove_from_sprint(key)
    else:
        fail(f"unknown action: {action!r}")
    print("[ok] done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
