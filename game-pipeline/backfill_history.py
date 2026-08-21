"""One-time bootstrap for the HISTORY tab (#66).

Seeds history/<proj>/<YYYY-MM-DD>.json from the full snapshots already sitting in
archive/*_<proj>_data.json, so the completion-date / scope-change log isn't empty
on day one. Going forward build_jira_data.py appends one snapshot per day itself,
so this only needs to run once (it's idempotent — safe to re-run).

Per date we keep the LAST archive of that day (latest HH MM). `target` comes from
each game's target_date; `scope` is stored only when > 0 (0 == "no estimate yet",
which would otherwise read as a fake jump when the field first appeared ~Aug 19).

Usage:  python backfill_history.py            # both projects
        python backfill_history.py --project ig
"""
import argparse
import json
import re
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ARCHIVE_DIR = ROOT / "archive"
HISTORY_DIR = ROOT / "history"
DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})-(\d{4})_")


def backfill(proj_key: str) -> int:
    # group archive files by calendar date, keep the latest run of each day
    by_date: dict[str, tuple[str, Path]] = {}
    for f in sorted(ARCHIVE_DIR.glob(f"*_{proj_key}_data.json")):
        m = DATE_RE.match(f.name)
        if not m:
            continue
        day, hhmm = m.group(1), m.group(2)
        if day not in by_date or hhmm > by_date[day][0]:
            by_date[day] = (hhmm, f)

    proj_dir = HISTORY_DIR / proj_key
    proj_dir.mkdir(parents=True, exist_ok=True)
    written = 0
    for day, (_hhmm, path) in sorted(by_date.items()):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        games = payload.get("games") if isinstance(payload, dict) else payload
        recs = {}
        for g in games or []:
            jira = g.get("jira")
            if not jira:
                continue
            sc = round(g.get("scope") or 0)
            recs[jira] = {"target": g.get("target_date") or None, "scope": sc or None}
        (proj_dir / f"{day}.json").write_text(
            json.dumps({"date": day, "games": recs}, ensure_ascii=False), encoding="utf-8")
        written += 1
    print(f"  {proj_key}: wrote {written} day-snapshot(s) from {len(by_date)} archive date(s) -> {proj_dir.relative_to(ROOT)}")
    return written


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", choices=["v2", "ig", "both"], default="both")
    args = ap.parse_args()
    projects = ["v2", "ig"] if args.project == "both" else [args.project]
    print("Backfilling history/ from archive/ …")
    for p in projects:
        backfill(p)
    print("Done.")


if __name__ == "__main__":
    main()
