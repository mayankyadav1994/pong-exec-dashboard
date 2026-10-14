"""
backfill_release_history.py
===========================
Seed release-history-{feed}.json from the dashboard's own archived data files.

Jira stores only a version's CURRENT release date — once a release train is
re-planned, the date it used to carry is gone. But this repo has committed a
full fixVersion snapshot on every nightly build since 2026-06-02, so the slip
history can be reconstructed from git: replay each commit oldest-first and feed
it through the SAME observe_releases() the nightly build uses, so a backfilled
revision is indistinguishable from one recorded live.

Run once to seed; after that build_jira_data.py maintains it incrementally.

    python backfill_release_history.py --project both
    python backfill_release_history.py --project ig --dry-run
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import build_jira_data as B

REPO = B.ROOT.parent
# The data file has lived at two paths: the game-pipeline/ source and the root
# copy the workflow publishes. Follow both so the history reaches back as far
# as the repo actually goes.
PATHS = {
    "v2": ["game-pipeline/dashboard-data-v2.js", "dashboard-data-v2.js"],
    "ig": ["game-pipeline/dashboard-data-ig.js", "dashboard-data-ig.js"],
    "igf": ["game-pipeline/dashboard-data-igf.js", "dashboard-data-igf.js"],
}
_ASSIGN = re.compile(r"window\.GP_DATA\[[^\]]+\]\s*=\s*", re.S)


def git(*args: str) -> str:
    return subprocess.run(["git", "-C", str(REPO), *args],
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", check=True).stdout


def commits_for(paths: list[str]) -> list[tuple[str, str, str]]:
    """(sha, authored-date, path) oldest-first across every known path."""
    seen: dict[str, tuple[str, str, str]] = {}
    for path in paths:
        out = git("log", "--reverse", "--format=%H%x09%ad", "--date=short", "--", path)
        for line in out.splitlines():
            if not line.strip():
                continue
            sha, _, day = line.partition("\t")
            seen.setdefault(sha, (sha, day, path))
    return sorted(seen.values(), key=lambda r: r[1])


def payload_at(sha: str, path: str) -> dict | None:
    """Parse the window.GP_DATA assignment out of one historical revision."""
    try:
        text = git("show", f"{sha}:{path}")
    except subprocess.CalledProcessError:
        return None
    m = _ASSIGN.search(text)
    if not m:
        return None
    body = text[m.end():].strip()
    if body.endswith(";"):
        body = body[:-1]
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return None


def backfill(feed: str, dry_run: bool) -> int:
    rows = commits_for(PATHS[feed])
    if not rows:
        print(f"{B.WARN} {feed}: no history found")
        return 1
    # Rebuild from scratch so a re-run can never double-count revisions.
    doc = {"schema_version": B.RELEASE_HISTORY_SCHEMA, "feed": feed,
           "jira_project": B.PROJECTS[feed]["key"], "releases": {}}
    # Collapse to one observation per calendar day. Several builds land on the
    # same day (subfolder copy + root copy + manual dispatches); replaying each
    # separately can make an ordinary lag between copies look like a same-day
    # A->B->A flap and invent two revisions that never happened. Only the last
    # usable state of a day is the state that day ended in.
    by_day: dict[str, dict] = {}
    skipped = 0
    for sha, day, path in rows:
        payload = payload_at(sha, path)
        games = (payload or {}).get("games") or []
        if not any(isinstance(fv, dict)
                   for g in games for fv in (g.get("fixVersions") or [])):
            skipped += 1          # unparsable, or pre-dates dated fixVersions
            continue
        by_day[day] = payload

    applied = moves = 0
    last_day = None
    for day in sorted(by_day):
        obs = datetime.strptime(day, "%Y-%m-%d").date()
        moves += B.observe_releases(doc, by_day[day]["games"], obs)
        applied += 1
        last_day = day
    doc["history_from"] = rows[0][1]
    doc["history_to"] = last_day
    doc["backfilled_from_days"] = applied
    doc["generated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    n_moved = sum(1 for r in doc["releases"].values()
                  if any(x.get("kind") == "moved" for x in r["revisions"]))
    print(f"{B.OK} {feed}: {applied} day(s) replayed ({skipped} commit(s) skipped) "
          f"· {rows[0][1]} -> {last_day} · {len(doc['releases'])} version(s) "
          f"· {moves} date move(s) across {n_moved} version(s)")
    for rec in sorted(doc["releases"].values(),
                      key=lambda r: -len([x for x in r["revisions"] if x.get("kind") == "moved"])):
        mv = [x for x in rec["revisions"] if x.get("kind") == "moved"]
        if not mv:
            continue
        trail = " -> ".join([mv[0]["from"] or "?"] + [x["to"] or "?" for x in mv])
        print(f"    {rec['name'][:26]:26} {trail}  ({len(mv)} move(s))")
    if dry_run:
        print(f"{B.WARN} dry run - nothing written")
        return 0
    path = B.save_release_history(feed, doc)
    print(f"{B.OK} wrote {path.name}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--project", choices=["v2", "ig", "igf", "both"], default="both")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    feeds = ["v2", "ig", "igf"] if args.project == "both" else [args.project]
    rc = 0
    for feed in feeds:
        rc |= backfill(feed, args.dry_run)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
