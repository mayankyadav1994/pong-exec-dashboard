# Game Pipeline Dashboard — Logic & Decisions Ledger

This document is the **living record** of every rule, computation, and design
decision behind the Game Pipeline dashboard. Append to it (don't rewrite)
whenever a new rule is added or an existing one is changed. Each entry includes
the decision, the rationale, and the date.

The file is structured as a chronological log first, then a current-state
reference at the end. Read the log to understand the *why*; read the reference to
know what's true *right now*.

---

## Conventions

- **Decisions are numbered chronologically.** Once assigned, a number never
  changes. If a decision is reversed, add a new numbered decision that references
  the old one — do not edit the old one.
- **Status of each decision** is one of: `ACTIVE`, `SUPERSEDED BY #n`,
  `PROPOSED`, `DEFERRED`.
- **Decisions are tagged** with one or more of: `data`, `ui`, `stage`, `status`,
  `heatmap`, `plan-mode`, `dependencies`, `persistence`, `architecture`, `axis`,
  `deployment`.

---

## Decision Log

### #1 · ACTIVE · `architecture` · 2026-06-02
> ⚠ **SUPERSEDED BY #23 — see below.** Jira is now the sole data source; Excel is
> out of the data path.

**Single static HTML file with embedded data block, pattern matched from iGaming
dashboard.**

- Refresh = re-run Python builder, paste output back into HTML.
- No backend / no runtime API calls.
- Persistence via `localStorage` only.

Rationale: keeps the dashboard portable (anyone can open in browser), refresh is
auditable (you see the data), and the pattern is already proven on iGaming and V2
release-timeline dashboards. Same team can maintain all three with the same mental
model.

---

### #2 · ACTIVE · `stage` `status` · 2026-06-02
**Separate "Lifecycle Stage" (auto, color-coded) from "Workflow Status" (manual,
monochrome outlined).**

The spreadsheet conflates these. The dashboard does not.

- **Lifecycle Stage:** which discipline is being worked on as of TODAY. Derived:
  latest marker whose date ≤ TODAY. One of the 9 legend colors. Cannot be
  manually edited.
- **Workflow Status:** overall release/management state. Set manually. Values
  from the configurable enum. Displayed as outlined IBM Plex Mono pill.

Visual contract: stage chips have a **colored dot** + colored background. Status
pills have **no dot**, monospace font, outlined border, monochrome.

Rationale: Birthday Bash's spreadsheet status of "Pre-prod only" was being shown
as the stage chip in early mockups, which confused everyone. They're genuinely
different things — keeping them visually distinct prevents that confusion
permanently.

---

### #3 · ACTIVE · `stage` · 2026-06-02
**The 9 lifecycle stages and their colors are taken directly from the spreadsheet
legend, plus one extra (`bugfix`) found in practice.**

| Stage key | Color | Source |
|---|---|---|
| `concept` | `#fde68a` | Legend |
| `art` | `#fed7aa` | Legend |
| `design` | `#bfdbfe` | Legend |
| `math` | `#bbf7d0` | Legend |
| `dev` | `#93c5fd` | Legend |
| `sound` | `#f5d0e0` | Legend |
| `qa` | `#fcd34d` | Legend |
| `bugfix` | `#fecaca` | Found in practice (Birthday Bash row 67), not in legend |
| `done` | `#86efac` | Legend |

In the spreadsheet, Concept and QA both use the same yellow `#FFE699`. The
dashboard distinguishes them by **discipline row context**. (In the Jira model,
stage = the discipline a child issue belongs to, so this disambiguation is
implicit — Decision #23.)

---

### #4 · ACTIVE · `status` · 2026-06-02
**Default workflow status enum:**

`Not Started`, `In Pre-Prod`, `In Production`, `In QA`, `Bug Fixing`, `On Hold`,
`Signed Off`

User-editable via Plan Mode config panel. Persisted to `{prefix}config.statuses`.

Rationale: we need an enum to filter/group by; this 7-value set covers every state
observed in practice. The user can extend it if needed.

---

### #5 · ACTIVE · `status` · 2026-06-02
> ⚠ **SUPERSEDED BY #24 — see below.** The spreadsheet status column is no longer
> a source; workflow status defaults to `Not Started` and is set only in Plan
> Mode.

**When the spreadsheet's raw status text doesn't map cleanly to the enum, infer
from `% complete`:**

```
if raw contains "signed off"  → Signed Off
if raw contains "on hold"     → On Hold
elif pct >= 0.95              → In QA
elif pct >= 0.40              → In Production
elif pct >  0                 → In Pre-Prod
else                          → Not Started
```

Rationale: most spreadsheet status notes are useful project chatter but not
status states. We needed a deterministic fallback so the dashboard always had
*some* workflow status to display.

---

### #6 · ACTIVE · `stage` · 2026-06-02
**Current lifecycle stage = the most recent timeline marker whose date is ≤
TODAY.**

```javascript
const all = game.disciplines.flatMap(d => d.markers).map(m => ({date: new Date(m.date), stage: m.stage}));
all.sort((a,b) => a.date - b.date);
const past = all.filter(m => m.date <= TODAY);
game.current_stage = past.length ? past[past.length-1].stage : 'concept';
```

If a game has no markers at all, default to `'concept'`. (In the Jira model the
"markers" are per-discipline active sprints — Decision #26.)

Rationale: matches how the spreadsheet implicitly works — the colored cell at
TODAY's column is "what's happening now".

---

### #7 · ACTIVE · `data` · 2026-06-02
**Each discipline's "timeline bar" in the dashboard spans from its earliest marker
to its latest marker.**

> Note: in the Jira model this is rendered as one sprint chip per active sprint
> rather than a single summarized span — see Decision #26.

Rationale: summarizing per-week touchpoints to one bar (with milestones in the
detail panel) keeps the Roadmap usable.

---

### #8 · ACTIVE · `heatmap` · 2026-06-02
> ⚠ **SUPERSEDED BY #26 — see below.** Heatmap load now sources from Jira sprints,
> not spreadsheet marker spans.

**Heatmap cells show estimated remaining hour-load, not game count.**

```
contribution = max(0, est - spent) * (overlap_days / span_days)
```

Summed across all games with markers in that discipline overlapping that month.
Game count shown as a small subtitle.

Rationale: "510h required against 320h capacity" tells you whether to hire;
"5 games active" doesn't.

---

### #9 · ACTIVE · `heatmap` · 2026-06-02
**Discipline monthly capacity defaults:**

| Discipline | Cap (h/mo) | Implicit FTE |
|---|---|---|
| Art / Creative | 240 | 1.5 FTE |
| Design | 80 | 0.5 FTE |
| Math | 320 | 2 FTE |
| Dev | 480 | 3 FTE |
| Sound | 160 | 1 FTE |
| QA | 200 | 1.25 FTE |

User-editable per discipline. Persisted to `{prefix}config.capacities`. Cells turn
red when load > cap.

---

### #10 · ACTIVE · `heatmap` · 2026-06-02
**Heatmap color thresholds:** `> capacity` red · `> 75%` amber bold · `> 40%`
amber · `> 0` light green · `= 0` neutral.

---

### #11 · ACTIVE · `plan-mode` · 2026-06-02
**Plan Mode is a single toggle in the header, not a separate view.**

When ON: amber banner, config panel, and per-row status dropdown replace the
read-only pill. Drag handles only react to drag when Plan Mode is ON.

Rationale: separating "view mode" from "edit mode" prevents accidental edits
during review.

---

### #12 · ACTIVE · `plan-mode` `persistence` · 2026-06-02
> ⚠ **SUPERSEDED BY #22 — see below.** Keys are now per-project namespaced
> (`gp_v2_*` / `gp_ig_*`).

**Plan Mode saves three pieces of state to localStorage:**

| Key | Contents |
|---|---|
| `gp_config` | Enums: statuses, stages, sizes, capacities |
| `gp_order` | Array of game names in display order |
| `gp_status` | Object: game name → workflow_status override |

Save is automatic on every change; the "Save plan" button is a confirmation
affordance.

---

### #13 · ACTIVE · `data` · 2026-06-02
**Size chips show 4 disciplines: Art (A), Math (M), Dev (D), Sound (S).**

> Note: sizes are no longer pulled from any source — they are manual Plan Mode
> overrides (Decision #25).

Rationale: those 4 are the disciplines where complexity sizing actually exists.

---

### #14 · ACTIVE · `ui` · 2026-06-02
**Three views, mutually exclusive, toggled in the filter bar:** Roadmap (time
questions), Heatmap (capacity), List (triage). Filter chips apply to all three.
View state is **not** persisted — defaults to Roadmap on load.

---

### #15 · DEFERRED · `dependencies` · 2026-06-02
**Game-to-game dependencies — design decided, build deferred to Phase 2.**

Planned: Jira "is blocked by" + manual entries; 🔗 icon + tooltip; dashed amber
arrows in Roadmap; Dependencies tab; storage key `{prefix}dependencies`.

Rationale to defer: Phase 1 delivers significant value without it; dependencies
pair naturally with the data builder.

---

### #16 · DEFERRED · `plan-mode` · 2026-06-02
**Drag-to-replan stage bars — Phase 3 feature.**

Planned: draggable horizontal stage bars, dependency-constrained drops, live
heatmap, capacity warnings on overload.

Rationale to defer: most complex feature; needs the read-only MVP and
dependencies in place first.

---

### #17 · ACTIVE · `ui` · 2026-06-02
**KPI strip metrics (fixed order):** Total Games · Signed Off · In Flight
(In Production/In QA/In Pre-Prod/Bug Fixing) · Not Started · Over Estimate
(spent > est AND est > 0; subtitle shows top 2 offenders).

---

### #18 · ACTIVE · `data` · 2026-06-02
**Status Report data is integrated inline on each row, not as a separate widget.**

> Note: sizes have since moved into Plan Mode (Decision #25); lead dev now comes
> from the Jira epic assignee (Decision #23).

---

### #19 · ACTIVE · `data` · 2026-06-02
**TODAY is hard-coded in the build and updated on every refresh.**

```javascript
const TODAY = new Date('2026-05-29');
```

The Python data builder writes this from the snapshot date automatically.

Rationale: makes the snapshot reproducible.

---

### #20 *(reserved — not previously assigned)*

---

### #21 *(reserved — not previously assigned)*

---

### #22 · ACTIVE · `persistence` · 2026-06-02
**localStorage namespacing — per-project prefixes.**

The two-page dashboard system shares an origin on GitHub Pages, so each page MUST
use a per-project prefix:
- V2 page → `gp_v2_*`
- iGaming page → `gp_ig_*`

The prefix is read from `window.PROJECT.ls_prefix` in `dashboard.js`. One page's
Plan Mode state never bleeds into the other.

**Supersedes #12.**

Rationale: without a prefix, V2 and iGaming would clobber each other's order,
status, sizes, and config in shared `localStorage`.

---

### #23 · ACTIVE · `architecture` · 2026-06-02
**Jira is the sole data source.**

The Python builder (`build_jira_data.py`) queries the Jira REST API directly.
Excel is no longer involved in the data flow (`Game_Pipeline_-_2026.xlsx` is kept
only as a visual reference and is never read). Each project (V2, IG) is built
independently into its own `dashboard-data-{project}.js` file containing
`const GAMES`, `const SPRINTS`, `const REFRESHED_AT`.

A game = one Epic (`issuetype = Epic AND fixVersion IS NOT EMPTY`). Disciplines =
child-issuetype groupings (Creative/Pre-Prod → art, Math → math, Dev/Story → dev,
Sound → sound, QA → qa, Design TBD → design). Bugs, Enhancements, and
Release/Merge-named issues are excluded. Hours from
`timeoriginalestimate`/`timespent`. Lead dev from the epic assignee.

**Supersedes #1.**

Rationale: the spreadsheet was always a lossy mirror of Jira. Sourcing Jira
directly removes the manual export/paste step and keeps both pages live.

---

### #24 · ACTIVE · `status` · 2026-06-02
> ⚠ **SUPERSEDED BY #32** — status is now derived from child-ticket states, not
> defaulted to "Not Started". Manual override still wins.

**Workflow status defaults to `Not Started`; manual override only.**

Workflow status is no longer inferred from Excel raw text. New games default to
`Not Started` until the user sets a value manually via the Plan Mode dropdown. The
progress-based inference of Decision #5 is obsolete because the raw spreadsheet
status column is no longer a source. Plan Mode dropdowns are the only authoritative
input for workflow status; overrides persist to `{prefix}status`.

**Supersedes #5.**

Rationale: Jira issue status ≠ the studio's release/management workflow state.
Rather than guess, we let the PMO set it explicitly and remember the choice.

---

### #25 · ACTIVE · `ui` `plan-mode` · 2026-06-02
**Size chips move into Plan Mode as manual overrides; not pulled from Jira.**

Size chips (XS/S/M/L/XL per discipline) are removed from the collapsed game row on
the main page. They move into Plan Mode as manual overrides per game, stored in
`localStorage` under `{prefix}sizes` (e.g. `gp_v2_sizes`). Sizes are **not** pulled
from Jira. The size column in the row label is **hidden** when no manual size has
been entered for that game.

Rationale: Jira has no reliable complexity-size field; sizing is a PMO judgment
call best captured as an explicit, persisted override.

---

### #26 · ACTIVE · `heatmap` · 2026-06-02
**Heatmap hour-load sources from Jira sprints.**

```
contribution per (game × discipline × month) =
    max(0, est - spent) × (sprints_active_in_month / total_sprints)
```

where `sprints_active_in_month` counts sprints whose date range overlaps the month
and have child issues in that discipline. This replaces the marker-overlap day-ratio
approximation from Decision #8.

**Supersedes #8.**

Rationale: sprints are the real unit of scheduled work in Jira, so allocating
remaining hours across a discipline's active sprints is more accurate than a
day-proportional span estimate.

---

### #27 · ACTIVE · `ui` `axis` · 2026-06-02
> ⚠ **Sprint LABELS superseded by #33** — the axis now uses the boards' real
> sprint names/dates instead of S1-anchored labels. The axis layout (chips,
> TODAY line, boundary lines) still stands.

**Sprint axis matches the exec dashboard pattern exactly.**

Sprint chips show label + date range (e.g. `S1 / May 11 – May 24`). The TODAY line
is a vertical red line with a TODAY chip at top; sprint boundaries are faint
vertical lines through the chart. Sprint numbering is anchored to the same
**S1 = 2026-05-11** (14-day cadence) used by the timeline dashboards. Sprints
predating S1 are not shown — the chart starts at S1. `CHART_START = 2026-05-11`.

Rationale: planners already read the release-timeline dashboards on this exact
axis; reusing it means one mental model across all studio dashboards.

---

### #28 · ACTIVE · `deployment` · 2026-06-02
**Deployed via the existing `pong-exec-dashboard` repo.**

Source code lives under a `pong-exec-dashboard/game-pipeline/` subfolder of the
existing repo. A GitHub Actions workflow builds `dashboard-data-{v2,ig}.js` from
Jira on a schedule (or manual trigger) and copies `v2-game-pipeline.html`,
`igaming-game-pipeline.html`, `dashboard.js`, `dashboard.css`,
`dashboard-data-v2.js`, `dashboard-data-ig.js` to the repo root for GitHub Pages.

URLs:
- https://mayankyadav1994.github.io/pong-exec-dashboard/v2-game-pipeline.html
- https://mayankyadav1994.github.io/pong-exec-dashboard/igaming-game-pipeline.html

Rationale: the exec-dashboard repo already serves GitHub Pages and holds the
sibling timeline dashboards; co-locating keeps deployment, secrets, and Pages
config in one place.

---

### #29 · ACTIVE · `ui` `architecture` · 2026-06-02
> ⚠ **Cross-nav integration + retained standalone shells SUPERSEDED BY #31.**
> The combined page + in-place toggle stand; the exec-dashboard cross-links and
> the single-project shells do not.

**Single combined `game-pipeline.html` page with V2 / iGaming sub-tabs.**

Instead of two separate URLs, the primary artifact is one `game-pipeline.html`
that carries the shared site nav (Overview · V2 Timeline · iGaming Timeline ·
Game Pipeline, matching `index.html`) plus a V2 | iGaming sub-toggle that swaps
the dataset **in place, no reload**. A "Game Pipeline" tab was added to the nav
of the other pages (and their templates / `build_dashboard.py`) for
discoverability. The standalone `v2-game-pipeline.html` /
`igaming-game-pipeline.html` shells are retained as deep-links.

To switch projects in one page the engine became re-mountable:
`window.GamePipeline.mount('v2'|'ig', containerEl)` rebuilds the dashboard into a
container; per-project `localStorage` namespacing (#22) keeps the two tabs'
state independent. The last-viewed tab is remembered in `gp_active_project`.

Rationale: the user asked for V2 and iGaming "on the same panel" like the
release-timeline pages share one nav. One page with tabs is the natural fit.

---

### #30 · ACTIVE · `data` `architecture` · 2026-06-02
**Data files publish `window.GP_DATA[<project>]` instead of bare `const GAMES`.**

Each `dashboard-data-{project}.js` now does:

```js
window.GP_DATA = window.GP_DATA || {};
window.GP_DATA['v2'] = { games:[...], sprints:[{id,label,start,end}], refreshed_at:'...' };
```

This lets the combined page (#29) load **both** project data files without a
duplicate top-level `const` collision. The engine reads
`window.GP_DATA[key]`; standalone shells (which set `window.PROJECT`) read the
same global. Replaces the earlier `const GAMES / SPRINTS / REFRESHED_AT` form.

Rationale: two `const GAMES` declarations in one page's global scope is a
SyntaxError; a namespaced object sidesteps it and is cleaner to extend.

---

### #31 · ACTIVE · `architecture` `ui` · 2026-06-02
**Game Pipeline is a standalone dashboard, fully decoupled from the exec dashboard.**

Reverses the cross-integration in #29:
- `game-pipeline.html` does **not** carry the exec/release-timeline site nav —
  it is its own page (header + V2 | iGaming toggle), served at
  `/game-pipeline.html` in the same repo but independent of the exec dashboard.
- **No** "Game Pipeline" tab is added to `index.html`, `v2-timeline.html`,
  `igaming-timeline.html`, their templates, or `build_dashboard.py`.
- The single-project shells (`v2-game-pipeline.html`,
  `igaming-game-pipeline.html`) are **removed**; `game-pipeline.html` with its
  toggle is the only Game Pipeline page. (The engine keeps a `window.PROJECT`
  standalone path internally, just unused.)

**Supersedes** the cross-nav + retained-shells parts of #29.

Rationale: the Game Pipeline is a separate dashboard from the release-timeline
exec dashboard; `index.html` was only a styling reference for the toggle
pattern, not an integration target.

---

### #32 · ACTIVE · `status` `stage` `data` · 2026-06-02
**Stage and workflow status are inferred from child-ticket states, not the epic.**

The epic's own Jira status is often stale, so the dashboard derives the truth
from the epic's child issues. Implemented as an editable rule table in
`build_jira_data.py`:

1. **Normalise** each child's Jira status → bucket: `todo` (New/To Do/Ready) ·
   `wip` (In Progress/Review/Pre-Prod*/Reopened) · `qa` (In QA/Ready For QA) ·
   `hold` (On Hold/Blocked) · `done` (Closed/Signed Off/Released/Deployed/…).
2. Group by discipline; `Release`/`Release Subtask` are a **release signal**
   (testing/deploy), not a discipline lane and not counted in hours.
3. **Stage** = the most-downstream discipline currently active
   (order design→art→math→dev→sound→qa); all-done → `done`; nothing started →
   `concept`. (e.g. only a Design ticket WIP ⇒ stage **Design**.)
4. **Workflow status** (first match): release done / all done → Signed Off ·
   QA active or release in testing → In QA · nothing active + hold → On Hold ·
   art/math/dev/sound WIP → In Production · design/pre-prod WIP → In Pre-Prod ·
   else Not Started. With **no** children, fall back to mapping the epic status.

**Supersedes #24** (the "default Not Started" rule) and the sprint-based
`current_stage` derivation (#6/#26 step). Manual Plan Mode overrides still win
(below).

Rationale: a release ticket in testing while the epic still says "In Progress"
should read as **In QA** — the tickets know the real state.

---

### #33 · ACTIVE · `axis` `data` · 2026-06-02
**Sprint axis uses the boards' real sprint names + dates (V2 board 316, IG 250).**

`SPRINTS` is built from `GET /rest/agile/1.0/board/{id}/sprint`: every sprint
with a start date is kept (closed history + active + future), labelled with the
board's own name (`V2 Sprint 2`, `IG Sprint 7`). Date-less sprints (e.g.
"Refined Backlog") are dropped. Lane chips show a compact `S{n}` with the full
name on hover; axis chips show the full name.

**Supersedes** the S1=2026-05-11 anchored re-labelling in #27 (the axis layout,
TODAY line, and sprint-boundary lines from #27 still stand). Board IDs are
configured per project (`JIRA_BOARD_ID_V2=316`, `JIRA_BOARD_ID_IG=250`).

---

### Manual override (Decision #24 era, refined under #32)
- A Plan Mode dropdown change stores `{prefix}status[game] = value` and
  **supersedes** the auto-derived value. The row shows a `✎` mark; picking the
  auto value again clears the override.
- **Revert-to-auto** (↺) per game in Plan Mode clears the override.
- **Drift flag**: when the auto-derived value later differs from a standing
  override, the row shows `auto: <X>` so stale overrides are visible.

---

### #34 · ACTIVE · `data` `ui` `plan-mode` · 2026-06-02
**Board membership: real games only, with delivery + a 2-week exit, and a Plan
Mode show/hide chooser.**

- **(#4) Only games.** Each project keeps only epics whose name starts with a
  prefix: V2 `"Game:"`, IG `"Gen2 Game:"` (`PROJECTS[key]["name_prefix"]`).
  Other fixVersion-bearing epics are excluded from the board.
- **(#2) Fix version / delivery.** Each game carries its `fixVersions`
  (`{name, released, releaseDate}`). A released fixVersion = **delivered**; the
  row shows a green `✓ Delivered · <FV> · <date>` chip and the game is forced to
  **Signed Off / done**. Undelivered games show their fixVersion name chips.
- **(#3) 2-week exit.** A delivered game is dropped from the build
  `DELIVERED_GRACE_DAYS = 14` days after its release date.
- **(#1) Show/hide chooser.** Plan Mode has a "Games on the board" list with an
  iOS-style toggle per game (mirrors the exec dashboard's scope editor),
  persisted to `{prefix}hidden`. Hidden games are excluded from KPIs, all three
  views, and the header count, but remain in the list to be re-shown. Reorder is
  still via row drag. Persistence is **local** (per browser), not a shared
  "save-for-everyone" default.

Rationale: the board should show only the studio's actual games, reflect what
has shipped, retire delivered games shortly after release, and let a planner
curate the visible set the same way they do on the release-timeline dashboard.

---

### #35 · ACTIVE · `plan-mode` `ui` · 2026-06-02
**Plan Mode is a slide-in "Edit Plan" drawer, mirroring the exec dashboard.**

Replaces the long inline Plan panel (which listed the game roster twice — once
for sizes, once for show/hide). Now a right-side drawer with two tabs:
- **Games** — one compact row per game: `⠿ drag · show/hide toggle · name (Jira)
  · status select`, and **click ▾ to expand** for the A/M/D/S size selectors +
  ↺ revert-status-to-auto. Drag the handle to reorder. Collapsed by default.
- **Settings** — the enum editors (statuses / stages / sizes) + discipline
  capacity ceilings.
- **Footer** — `↺ Reset local edits` (clears this project's order/status/sizes/
  hidden/config back to Jira-derived defaults) + a "N shown / M hidden" count.

The board row's status pill is now read-only (with the `✎`/drift markers);
status is edited only in the drawer. All edits autosave to `localStorage`.

Rationale: the user found the exec Edit Plan UX clearly better — one row per
game with everything in one place, short by default, in a focused drawer.

---

### #36 · DEFERRED · `plan-mode` `persistence` · 2026-06-02
**Shared "save as default for everyone" with an allowlisted editor — next phase.**

Planned: an allowlisted user can promote their local Plan edits to a shared
default that is committed to the repo and **overrides the auto-pull** for those
fields until someone manually flips it back. Mirrors the exec dashboard's
"Save as default for everyone" (GitHub token → config commit/PR). Until built,
all Plan edits are local-per-browser (#34/#35).

Rationale to defer: needs an auth/allowlist mechanism + repo-write flow; the
local editor delivers the immediate value first.

---

### #37 · ACTIVE · `data` · 2026-06-02
**Hours are summed over the full epic subtree (children + sub-tasks), not just
direct children.**

The original builder queried `parent = <epic>` only, so it summed `timespent` /
`timeoriginalestimate` on the epic's **direct children** and missed all the
**sub-task** worklogs/estimates nested under stories/tasks — undercounting by
~10–20× (e.g. Cleopatra showed 0h spent vs ~1000h real). The builder now:
- fetches direct children **and** their sub-tasks (`parent in (childKeys)`),
- classifies **every** issue by its own issuetype into a discipline,
- **spent** = sum of every issue's own `timespent` (leaf-level, no double-count),
- **est** = sub-task estimates when a child has classified sub-tasks, else the
  child's own estimate (sub-task-or-parent fallback — Decision per user),
- sub-task statuses now also feed the stage/status inference (#32) and sprint
  markers, making them more accurate.

Bug / Enhancement / Live Issue / Release issuetypes remain excluded from hours.

Rationale: the real estimates and worklogs live on the sub-tasks; the board must
sum the whole tree to show true game hours.

---

## Current-State Reference

Fast-lookup of the rules currently in effect. **If anything here conflicts with
the log above, the log is authoritative.**

### Data source (#23, #30)
- Jira REST API only. No Excel.
- Game = Epic (`fixVersion IS NOT EMPTY`). Discipline = child issuetype grouping.
- Hours from `timeoriginalestimate` / `timespent` (÷3600). Lead dev = epic assignee.
- Built per project → `dashboard-data-{v2,ig}.js`, each publishing
  `window.GP_DATA['v2'|'ig'] = { games, sprints, refreshed_at }`.

### Pages (#29, #31)
- **Only** page: `game-pipeline.html` — standalone dashboard (own header +
  V2/iGaming toggle), in-place switch via `window.GamePipeline.mount(key, el)`.
- Fully decoupled from the exec dashboard: no shared nav, no cross-links.
- Single-project shells removed. Last-viewed tab remembered in `gp_active_project`.

### Stage detection (#32)
- Ticket-derived: most-downstream active discipline (design→…→qa); all-done →
  `done`; nothing started → `concept`.

### Workflow status (#32)
- Ticket-derived (Signed Off / In QA / On Hold / In Production / In Pre-Prod /
  Not Started); no children → mapped from epic status. Manual Plan Mode override
  supersedes (✎ mark, ↺ revert-to-auto, drift flag). Persisted to `{prefix}status`.

### Sizes (#25)
- Manual Plan Mode override only, persisted to `{prefix}sizes`. Hidden on the row
  until set. Not from Jira.

### Heatmap math (#26)
```
contribution = max(0, est - spent) × (sprints_active_in_month / total_sprints)
```

### Capacity ceilings (h/mo, defaults) (#9)
Art 240 · Design 80 · Math 320 · Dev 480 · Sound 160 · QA 200

### Color thresholds (heatmap) (#10)
`>cap` red · `>75%` amber+bold · `>40%` amber · `>0` green · `=0` neutral

### Sprint axis (#27, #33)
- Real board sprints (V2 board 316, IG board 250): all dated sprints, board's
  own names (`V2 Sprint 2`, `IG Sprint 7`); date-less ones dropped.
- Sprint chips (full name + date range), TODAY line + chip, faint sprint
  boundary lines. Lane chips show compact `S{n}`. `SPRINTS = [{id,label,start,end}]`.

### Board membership (#34)
- Only epics named `Game:` (V2) / `Gen2 Game:` (IG) are games.
- Released fixVersion ⇒ delivered ⇒ Signed Off; dropped 14 days after release.
- Plan Mode show/hide → `{prefix}hidden`; hidden games leave KPIs/views/count.

### localStorage keys (#22, #34)
Per-project prefix `gp_v2_` / `gp_ig_`:
- `{prefix}config` — editable enums (statuses, stages, sizes scale, capacities)
- `{prefix}order` — game name order
- `{prefix}status` — per-game workflow_status overrides
- `{prefix}sizes` — per-game discipline size overrides
- `{prefix}hidden` — hidden game names (Plan Mode show/hide chooser, #34)
- `gp_active_project` — last-viewed V2/iGaming tab (global, not prefixed)
- `{prefix}dependencies` — dependency edges (Phase 2, not yet built)

### Deployment (#28)
- Source under `pong-exec-dashboard/game-pipeline/`; CI builds from Jira and
  copies the 6 runtime files to repo root for GitHub Pages.

### Stage colors (CSS vars)
```
--stg-concept:#fde68a   --stg-art:#fed7aa     --stg-design:#bfdbfe
--stg-math:#bbf7d0      --stg-dev:#93c5fd     --stg-sound:#f5d0e0
--stg-bugfix:#fecaca    --stg-qa:#fcd34d      --stg-done:#86efac
```

---

## How to add a new decision

1. Append to the log above, never edit historical entries.
2. Pick the next sequential `#n`.
3. Mark status: `ACTIVE`, `SUPERSEDED BY #m`, `PROPOSED`, or `DEFERRED`.
4. Tag with one or more keywords.
5. If superseding an old decision, add an inline note to the old one (don't delete
   its content).
6. Update the Current-State Reference if the decision changes the rules in effect.
7. If it affects architecture, also update `GAME_PIPELINE_KNOWLEDGE.md`.

---

*Last updated: June 2, 2026*
