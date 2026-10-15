#!/usr/bin/env python3
"""
netnew_page.py
--------------
Builds the net-new vs ported game cost page (.html) from the cache written by
elg_cost_data.py. No network, no Jira.

    python elg_cost_data.py     # first, if you want fresh numbers
    python netnew_page.py

Sister script to elg_cost_page.py. Both read the same cache and the same
department rules out of elg_cost_data.py, so the two pages can never disagree
about what a ticket is or which game it belongs to.

The STYLESHEET is lifted from page_template.html at build time rather than
copied into netnew_page_template.html, so the tokens, dark mode and component
styling stay identical across both pages. Edit the CSS there.

Deps: none beyond the standard library.
"""

import io
import json
import os
import re
import sys

from elg_cost_data import (GAMES, COMPLETE, load, HERE, HOURLY_RATE,
                           JIRA_BASE)

TEMPLATE_FILE = os.path.join(HERE, "netnew_page_template.html")
STYLE_SOURCE = os.path.join(HERE, "page_template.html")

# Repo root, like every other dashboard here -- deploy-pages.yml uploads the
# whole repo, so writing straight to root means no copy-to-root step.
OUTPUT_FILE = os.path.join(HERE, "..", "netnew-vs-port.html")

# Only these two categories appear on this page. Skin and Branded games are in
# the cost model but are not part of the net-new/port question.
CATS = ("New", "Port")


def stylesheet():
    """The <style> body from page_template.html -- one source of truth."""
    html = io.open(STYLE_SOURCE, encoding="utf-8").read()
    m = re.search(r"<style>(.*?)</style>", html, re.S)
    if not m:
        sys.exit("No <style> block in %s -- cannot build the page." % STYLE_SOURCE)
    return m.group(1)


def payload(rows):
    """
    Same positional contract as elg_cost_page.payload, trimmed to the games
    this page shows:

        0 game  1 category  2 key  3 type  4 department  5 fix versions
        6 elg   7 est h     8 spent h      9 summary    10 matched rule
       11 pfh  12 remaining h              13 done

    Hours are rounded per ticket here; see the note in elg_cost_page.payload
    about the ~0.02h this can cost on a group total.
    """
    out = []
    for d in rows:
        g = GAMES.get(d["epic"])
        if not g or g[1] not in CATS:
            continue
        name, cat, _rel = g
        out.append([
            name, cat, d["key"], d["type"], d["dept"],
            "; ".join(d["fv"]),
            1 if d["elg"] else 0,
            round(d["oe_s"] / 3600, 2),
            round(d["ts_s"] / 3600, 2),
            (d["summary"] or "")[:118],
            d["rule"],
            1 if d.get("pfh") else 0,
            round(d["re_s"] / 3600, 2),
            1 if d.get("done") else 0,
        ])
    return out


def meta():
    return {
        # `complete` drives both the % complete row and whether Remaining
        # counts at all -- see COMPLETE in elg_cost_data.py.
        "games": [{"epic": e, "name": n, "cat": c, "rel": r,
                   "complete": COMPLETE.get(e, None)}
                  for e, (n, c, r) in GAMES.items() if c in CATS],
        "rate": HOURLY_RATE,
        "jira": JIRA_BASE,
    }


def build(rows):
    if not os.path.exists(TEMPLATE_FILE):
        sys.exit("No template at %s -- cannot build the page." % TEMPLATE_FILE)
    html = io.open(TEMPLATE_FILE, encoding="utf-8").read()
    for ph in ("__STYLE__", "__DATA__"):
        if ph not in html:
            sys.exit("%s has no %s placeholder." % (TEMPLATE_FILE, ph))

    data = json.dumps({"meta": meta(), "tickets": payload(rows)},
                      separators=(",", ":"))
    # the payload sits inside <script type="application/json">; a literal
    # closing tag anywhere in the data would end that block early
    if "</script" in data.lower():
        sys.exit("Ticket data contains a literal </script — refusing to emit "
                 "a broken page.")

    html = html.replace("__STYLE__", stylesheet()).replace("__DATA__", data)
    io.open(OUTPUT_FILE, "w", encoding="utf-8", newline="\n").write(html)
    return len(data), len(payload(rows))


def main():
    rows, _rel, _counts, _agg = load()
    n_data, n_tix = build(rows)
    games = [n for e, (n, c, r) in GAMES.items() if c in CATS]
    nn = sum(1 for e, (n, c, r) in GAMES.items() if c == "New")
    pt = sum(1 for e, (n, c, r) in GAMES.items() if c == "Port")
    print("Saved %s" % OUTPUT_FILE)
    print("  %d games (%d net-new, %d ported) · %d tickets · %.0f KB of data"
          % (len(games), nn, pt, n_tix, n_data / 1024))
    print("  -> %s" % os.path.abspath(OUTPUT_FILE))
    print("  Served by GitHub Pages at /netnew-vs-port.html once pushed.")
    print("  Styling comes from page_template.html -- edit the CSS there.")


if __name__ == "__main__":
    main()
