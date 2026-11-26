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

Every published page also gets the shared navigation drawer: a
<script src="site-nav.js" defer> before </body> (source: shared/site-nav.js,
which holds the one list of dashboards). Templates don't carry it.

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
    "timelines": ["v2-timeline.html", "igaming-timeline.html", "css-timeline.html"],
    "roadmap": ["roadmap-igaming.html", "roadmap-v2.html", "roadmap-css.html",
                "roadmap-*-planning.html", "roadmap-history.json"],
    "people": ["people.html"],
    "team-board": ["team-board.html", "team-board.js", "team-board-data.js",
                   "team-board-write.js"],
    "game-pipeline": [
        "game-pipeline.html", "ceo-summary.html", "dashboard.js", "dashboard.css",
        # One set per board (v2, ig, igf, v2f, cssf …). Globs: a board's files
        # appear as soon as the builder or a plan save creates them.
        "dashboard-data-*.js", "data-*.json", "release-history-*.json",
        # Saved from the page through the GitHub API (repo path game-pipeline/…),
        # read back by the page from the site root.
        "plan-*.json", "editors.json", "ceo-review.json",
        "release-policy.json", "alerts.json",
    ],
    "elg-cost-model": ["elg-cost-model.html", "netnew-vs-port.html"],
    "shared": ["site-nav.js"],
}

# Published pages outside the site root that also get the drawer.
NAV_EXTRA_PAGES = ["sprint-load/dashboard.html"]
NAV_MARK = "<!-- site-nav -->"

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
        names = []
        for name in files:
            if "*" in name:      # glob: any number of matches, none required
                names += sorted(p.name for p in (REPO / folder).glob(name) if p.is_file())
            else:
                names.append(name)
        for name in names:
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

    pages = [n for files in SITE_ROOT.values() for n in files if n.endswith(".html") and "*" not in n]
    for rel in pages + NAV_EXTRA_PAGES:
        if not add_site_nav(OUT / rel, rel):
            return 1
    print(f"site assembled in {OUT.relative_to(REPO)}/: {placed} files at the root, "
          f"navigation on {len(pages) + len(NAV_EXTRA_PAGES)} pages")
    return 0


def add_site_nav(page: Path, rel: str) -> bool:
    """Load the shared navigation drawer on a published page (before </body>)."""
    if not page.is_file():
        print(f"error: navigation page {rel} is missing", file=sys.stderr)
        return False
    with page.open(encoding="utf-8", newline="") as f:
        html = f.read()
    if NAV_MARK in html:
        return True
    prefix = "../" * rel.count("/")
    tag = f'{NAV_MARK}<script src="{prefix}site-nav.js" defer></script>\n'
    i = html.lower().rfind("</body>")
    html = html[:i] + tag + html[i:] if i >= 0 else html + "\n" + tag
    with page.open("w", encoding="utf-8", newline="") as f:
        f.write(html)
    return True


if __name__ == "__main__":
    raise SystemExit(build())
