#!/usr/bin/env python3
"""
elg_cost_page.py
----------------
Builds the interactive ELG cost report page (.html) from the cache written by
elg_cost_data.py. No network, no Jira.

This is the file we iterate on. The layout, copy and styling all live in
`page_template.html` -- edit that directly and re-run this to rebuild; the only
thing this script does is aggregate the cache into the payload the page reads
and substitute it for the __DATA__ placeholder.

    python elg_cost_data.py     # first, if you want fresh numbers
    python elg_cost_page.py

The page is self-contained: open the .html directly, or publish it.
Deps: none beyond the standard library.
"""

import io
import json
import os
import sys

from elg_cost_data import GAMES, DEPT_ORDER, load, HERE, PFH_RE

TEMPLATE_FILE = os.path.join(HERE, "page_template.html")

# The built page goes to the REPO ROOT, not this folder: deploy-pages.yml
# uploads the whole repo (`path: '.'`) and every other dashboard in this repo
# is served from root as kebab-case HTML (game-pipeline.html, team-board.html).
# Writing here directly means there is no copy-to-root step to forget.
OUTPUT_FILE = os.path.join(HERE, "..", "elg-cost-model.html")
PLACEHOLDER = "__DATA__"


def payload(rows):
    """
    Compact the cached tickets into the array the page consumes.

    Positional, not keyed -- 980 rows of objects would roughly triple the
    page size for no benefit. Index order is contractual; page_template.html
    reads these by position:

        0 game   1 category  2 key    3 type   4 department  5 fix versions
        6 elg    7 est h     8 spent h         9 summary    10 matched rule
       11 pfh

    `elg` and `pfh` are independent flags, not a two-state field: a ticket can
    carry both (a few do) or neither (83 do). The page's scope picker treats
    them as overlapping filters for that reason.
    """
    out = []
    for d in rows:
        name, cat, _rel = GAMES.get(d["epic"], ("?", "?", "?"))
        out.append([
            name,
            cat,
            d["key"],
            d["type"],
            d["dept"],
            "; ".join(d["fv"]),
            1 if d["elg"] else 0,
            round(d["oe_s"] / 3600, 2),
            round(d["ts_s"] / 3600, 2),
            (d["summary"] or "")[:118],
            d["rule"],
            1 if any(PFH_RE.match(v) for v in d["fv"]) else 0,
        ])
    return out


def meta():
    return {
        "games": [{"epic": e, "name": n, "cat": c, "rel": r}
                  for e, (n, c, r) in GAMES.items()],
        "depts": DEPT_ORDER,
    }


def build(rows):
    if not os.path.exists(TEMPLATE_FILE):
        sys.exit(f"No template at {TEMPLATE_FILE} -- cannot build the page.")
    html = io.open(TEMPLATE_FILE, encoding="utf-8").read()
    if PLACEHOLDER not in html:
        sys.exit(f"{TEMPLATE_FILE} has no {PLACEHOLDER} placeholder to inject into.")

    data = json.dumps({"meta": meta(), "tickets": payload(rows)},
                      separators=(",", ":"))
    # the payload sits inside <script type="application/json">; a literal
    # closing tag anywhere in the data would end that block early
    if "</script" in data.lower():
        sys.exit("Ticket data contains a literal </script — refusing to emit a broken page.")

    io.open(OUTPUT_FILE, "w", encoding="utf-8", newline="\n").write(
        html.replace(PLACEHOLDER, data))
    return len(data)


def main():
    rows, _rel, _counts, _agg = load()
    n = build(rows)
    spent = sum(r["ts_s"] for r in rows) / 3600
    print(f"Saved {OUTPUT_FILE}")
    print(f"  {len(rows)} tickets · {spent:,.2f}h · {n / 1024:,.0f} KB of embedded data")
    print(f"  -> {os.path.abspath(OUTPUT_FILE)}")
    print("  Served by GitHub Pages at /elg-cost-model.html once pushed.")
    print("  Edit page_template.html and re-run to restyle.")


if __name__ == "__main__":
    main()
