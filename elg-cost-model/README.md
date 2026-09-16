# ELG Game Cost Model

Delivery-cost model for iGaming (project IG) ELG games: what each game actually
cost by department, average spend per category for budgeting, and t-shirt bands
rebased on real delivery instead of the 2024 estimates.

Sample is 8 games from the last ELG game-bearing releases (4.20–4.70), traced
through ~980 Jira tickets.

## Layout

Three scripts, split so the slow part runs once and the fast parts rebuild
instantly:

```
games.json               the model's inputs: games, release epics, hourly rate
elg_cost_data.py         Jira  -> elg_cost_data.json       ~2 min, hits the network
elg_cost_spreadsheet.py  cache -> .xlsx + .md              instant, offline
elg_cost_page.py         cache -> ../elg-cost-model.html   instant, offline
page_template.html       the page's layout, copy and styling
```

## Adding a game

The game list lives in `games.json`, not in the Python, and `elg_cost_data.py`
manages it for you. Nothing here needs a code edit:

```
python elg_cost_data.py --list                     what is in the model now
python elg_cost_data.py --find buffalo             search IG epics by summary
python elg_cost_data.py --add IG-7781 --cat Port   add one
python elg_cost_data.py --remove IG-7781           drop one
```

`--add` reads the display name and the ELG release off the epic itself, so
category is the only thing you have to supply -- nothing in Jira distinguishes
a Port from a Skin. Override the derived values with `--name` / `--rel`; you
will need `--rel` for an epic that carries no ELG fix version (Flaming Skulls
is one -- it has only PFH and Horse Play versions).

Adding a game only edits `games.json`. Re-run the pull and the page build to
see it:

```
python elg_cost_data.py     # picks up the new epic
python elg_cost_page.py
```

There is no `--add` for release epics; edit the `release_epics` block in
`games.json` directly. A game whose release has no entry there simply gets
0h of release overhead.

**The built page goes to the repo root**, as `elg-cost-model.html`, matching
every other dashboard here (`game-pipeline.html`, `team-board.html`). That is
deliberate: `deploy-pages.yml` uploads the whole repo (`path: '.'`), so writing
straight to root means there is no copy-to-root step to forget. Pushing to
`main` triggers a Pages deploy and the page is live at:

    https://mayankyadav1994.github.io/pong-exec-dashboard/elg-cost-model.html

That URL is **public**, like the rest of this Pages site.

All paths are anchored to this folder, not your working directory, so the
scripts work from anywhere.

`elg_cost_data.py` owns everything shared: it reads `games.json`, and holds
the department classification rules, the 2024 t-shirt legend, and the Jira
traversal. The other two import from it, so the rules can never drift between
the spreadsheet and the page.

## How a ticket gets a department

There is no department field in Jira. `classify()` walks `RULES` top to bottom
and the first match wins, so order is load-bearing:

- **Bugs** is issue type only -- `Bug` and `Live Issue`. It sits first, so a
  ticket typed Bug counts as Bugs whatever its summary says. `Enhancement` is
  deliberately *not* in that set: it is scoped feature work rather than a
  defect, and including it overstated Bugs by ~25 tickets.
- **Server** is inferred from the summary, and is the softest number here.
  Only the `[Server]` bracket tag is authoritative; the older keyword patterns
  (`config`, `pools`, `deploy on/to`) catch any summary that mentions them,
  whoever did the work. Server sits above Game Engine on purpose, so
  "deployment on New Game Engine" reads as releasing onto the platform rather
  than building it.
- **Release** never matches a game ticket at all. Release work does not live
  under a game epic -- see below.

## Release overhead

Release work sits under its own epics (`release_epics` in `games.json`), not
under any game, so no game ticket ever classifies as Release. Those epics are
pulled into a separate `rel` list and `release_overhead()` shares each
release's hours evenly across the games **this model tracks** in that release.

That denominator is an assumption worth knowing: if a release also shipped
games the model does not track, their share lands on the tracked ones and
per-game overhead reads high. The page states it under the category cards.

The shares are not flat -- they run from 0.25h (ELG 4.50) to 76.42h
(ELG 4.40). The page shows them in the Release column and as a separate
"Release OH" tile, kept out of `Actual` because they are not tickets in view.
Scope filters work on fix versions and release overhead carries none, so it
only applies in Full-game scope.

## Running it

```bash
python elg_cost_data.py          # refresh the numbers from Jira
python elg_cost_page.py          # rebuild the page, then commit + push to go live
python elg_cost_spreadsheet.py   # rebuild the workbook (only when needed)
```

Credentials come from the `JIRA_API_TOKEN` environment variable — the scripts
self-heal it from User-scope env on Windows, so there is no per-run setup.
**Never** put a token in a file here; `.gitignore` blocks `*.env` as a backstop.

Only `elg-cost-model.html` is published. The workbook and the JSON cache are
gitignored local artefacts — they never reach the Pages site.

To change the sample or the releases, edit the `RUN CONFIG` block at the top of
`elg_cost_data.py`. Nothing else needs touching.

## Which file to edit

- **The page** — `page_template.html`. It is plain HTML/CSS/JS with a single
  `__DATA__` placeholder; `elg_cost_page.py` only substitutes the ticket
  payload. Edit the template, re-run the script, refresh the browser.
- **The numbers or the sample** — `elg_cost_data.py`.
- **The workbook** — `elg_cost_spreadsheet.py`. Rarely changes.

## What the model found

- **Estimates over-shoot by 20% overall, but the error is not uniform.** Large
  Ports land at 0.82–1.04 of estimate; small games land at 0.37–0.54. The bands
  fail to *scale down*, not to scale. Cutting every band by a flat percentage
  would break the large end that currently works.
- **Concept, QA, Bugs and Release were never priced by the 2024 legend at all.**
  That is where the hidden cost sits — real spend no estimate accounted for.
- **143 tickets carry ~1,002h of estimate with zero time logged**, which is why
  the bands could never be validated: there was nothing to compare against.

## Things that will bite you

- **Issue type is unreliable.** Sound work is typed `Dev Subtask`; a 43h Review
  story is typed `Story`. Departments are therefore matched on summary patterns
  *before* issue type. The workbook's Ticket Detail tab records which rule fired
  for every ticket, so any call can be audited.
- **Only `POST /rest/api/3/search/jql` works.** `GET /search` is 410 Gone. Page
  with `nextPageToken`, never `startAt`, and chunk `parent in (...)` to ~8 keys
  or tickets vanish silently.
- **Bugs carry subtasks too** — 33 of them. Do not exclude Bug/Live Issue/
  Enhancement when collecting parents.
- **Hours stay in raw seconds** until output, then `round(secs/3600, 2)`.
  Integer division drops half-hours.
- **Epics do carry fix versions** here (7 of 8), contradicting the old "epics
  carry no fix version" rule — but ELG membership is still tested at
  story/subtask level, which is safer.
- **Never attribute by assignee.** Devs reassign to the Dev Lead at the Review
  handoff, so the field credits the wrong person.

## Not automated, on purpose

There is no workflow, cron or scheduled job attached to this. Run it when you
want fresh numbers. Re-running overwrites the outputs in place, so copy the
workbook aside first if you want to keep a snapshot to compare against.
