#!/usr/bin/env python3
"""Assemble the GitHub Pages site in _site/.

Each dashboard's source lives in its own folder (exec/, timelines/, roadmap/,
people/, team-board/, game-pipeline/, elg-cost-model/ …), but the public
addresses stay what they have always been — …/pong-exec-dashboard/v2-timeline.html,
…/game-pipeline.html, …/plan-ig.json — because people bookmark them, emails link
to them and email_updates/release_readiness.py downloads /release-policy.json.

So the site is: the whole repo (folder addresses keep working too), plus each
dashboard's public files copied to the site root (SITE_ROOT below). A file
missing from SITE_ROOT's sources fails the build, so a broken site never ships.

Used by .github/workflows/deploy-pages.yml. Local preview:
    python tools/build_site.py && python -m http.server 8000 -d _site
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "_site"

# folder -> files it publishes at the site root (the addresses people use).
SITE_ROOT: dict[str, list[str]] = {
    "exec": ["index.html"],
    "timelines": ["v2-timeline.html", "igaming-timeline.html"],
    "roadmap": ["roadmap-igaming.html", "roadmap-v2.html", "roadmap-css.html",
                "roadmap-history.json"],
    "people": ["people.html"],
    "team-board": ["team-board.html", "team-board.js", "team-board-data.js",
                   "team-board-write.js"],
    "game-pipeline": [
        "game-pipeline.html", "ceo-summary.html", "dashboard.js", "dashboard.css",
        "dashboard-data-v2.js", "dashboard-data-ig.js", "dashboard-data-igf.js",
        "data-v2.json", "data-ig.json", "data-igf.json",
        # Saved from the page through the GitHub API (repo path game-pipeline/…),
        # read back by the page from the site root.
        "plan-v2.json", "plan-ig.json", "plan-igf.json", "editors.json", "ceo-review.json",
        "release-policy.json", "alerts.json",
        "release-history-v2.json", "release-history-ig.json", "release-history-igf.json",
    ],
    "elg-cost-model": ["elg-cost-model.html", "netnew-vs-port.html"],
}

SKIP_DIRS = {".git", ".github", ".claude", ".venv", "venv", "node_modules",
             "__pycache__", "_site"}


def _ignore(directory: str, names: list[str]) -> set[str]:
    skip = set()
    for n in names:
        if n in SKIP_DIRS or n.endswith(".pyc"):
            skip.add(n)
        # Local credential files never go on the site (CI has none anyway).
        elif n == ".env" or (n.startswith(".env.") and not n.endswith((".template", ".example"))):
            skip.add(n)
    return skip


def build() -> int:
    if OUT.exists():
        shutil.rmtree(OUT)
    shutil.copytree(REPO, OUT, ignore=_ignore)

    missing, placed = [], 0
    for folder, files in SITE_ROOT.items():
        for name in files:
            src = REPO / folder / name
            if not src.is_file():
                missing.append(f"{folder}/{name}")
                continue
            dst = OUT / name
            if dst.exists():
                print(f"error: {name} from {folder}/ would overwrite an existing site entry",
                      file=sys.stderr)
                return 1
            shutil.copy2(src, dst)
            placed += 1
    if missing:
        print("error: missing published files:\n  " + "\n  ".join(missing), file=sys.stderr)
        return 1
    print(f"site assembled in {OUT.relative_to(REPO)}/: {placed} files at the root")
    return 0


if __name__ == "__main__":
    raise SystemExit(build())
