# Game Pipeline Dashboard — Knowledge Base

**Project:** Pong Game Studios · Game-Epic Lifecycle Roadmap
**System:** Two-page dashboard — **V2** + **iGaming**
**Current artifacts:** `v2-game-pipeline.html` and `igaming-game-pipeline.html` —
thin shells over a shared engine (`dashboard.js` + `dashboard.css`), each with its
own Jira-built data file.
**Data source:** Jira REST API (sole source — see Decision #23)
**Architecture pattern:** mirrors the iGaming/V2 release-timeline dashboards in the
`pong-exec-dashboard` repo (see `IG_RELEASE_TIMELINE_KNOWLEDGE.md` and
`V2_RELEASE_TIMELINE_KNOWLEDGE.md` in that repo's `docs/`).

> This document is the authoritative **what**. The numbered rationale for every
> rule lives in `GAME_PIPELINE_LOGIC.md` (the **why**).

---

## 1. Purpose

A **two-page system** that replaces the V2 Game Pipeline spreadsheet with a
better-UI, more-customizable view of every game epic in the studio's roadmap —
one page for **V2** games, one for **iGaming** games. The unit of work is one
game (= one Jira Epic); lifecycle stages are tracked across all disciplines.

The dashboard answers questions the spreadsheet can't:
- Which discipline will be overloaded which month? (Heatmap)
- What's the actual current stage of game X right now? (Auto-derived from the
  latest sprint with active work)
- Where do we stand on hours vs estimate, globally? (KPI strip)
- What if we reprioritize? (Drag-to-reorder + Plan Mode)
- What does the full lifecycle look like for any single game? (Detail panel)

Both pages share one engine and differ only by a `window.PROJECT` config block
and their per-project data file.

---

## 2. Jira Data Model — Understanding the Data

Jira is the **sole** data source (Decision #23). The builder
(`build_jira_data.py`) queries the REST API directly; the old
`Game_Pipeline_-_2026.xlsx` is retained only as a visual reference and is **never
read** by the build.

### 2.1 One game = one Epic

```
project = {PROJECT_KEY} AND issuetype = Epic AND fixVersion IS NOT EMPTY
ORDER BY rank ASC
```

`PROJECT_KEY` is `V2` or `IG`. Rank order from Jira is the default display order
(the user can override it via Plan Mode drag-reorder).

Epic fields pulled: `summary`, `status`, `assignee`, `priority`, `fixVersions`,
`customfield_10014` (Epic Name).

### 2.2 Discipline = child-issuetype grouping

Each game's child issues (`parent = {epicKey}`) are grouped into disciplines by
issuetype:

| Child issuetype(s) | Discipline key |
|---|---|
| Creative Task · Creative Subtask · Pre-Prod Task | `art` |
| Math Task · Math Subtask | `math` |
| Dev Task · Dev Subtask · Story | `dev` |
| Sound Task · Sound Subtask | `sound` |
| QA Task · QA Subtask | `qa` |
| Design tasks (**TBD** — confirm the exact issuetype with the user) | `design` |

**Exclusions** (by issuetype **or** summary regex):
- issuetype = `Bug`
- issuetype = `Enhancement`
- summary matches `/^Release/i` or `/^Merge/i`

Anything that maps to none of the above is logged as a warning (with
`--verbose`) and skipped.

### 2.3 Hours (full subtree — Decision #37)

Hours are summed over the **whole epic subtree** — direct children **and** their
sub-tasks (`parent in (childKeys)`), each classified by its own issuetype:
- `spent` = Σ every issue's own `timespent` / 3600 (leaf-level, no double-count)
- `est`   = sub-task estimates where a child has classified sub-tasks, else the
  child's own `timeoriginalestimate` (sub-task-or-parent fallback)
- `pct`   = `spent / est` if `est > 0` else `0`

Aggregated per (game × discipline) and per game. (A direct-children-only sum
undercounted by ~10–20× because the real worklogs live on sub-tasks.)

### 2.4 Sprint markers (the timeline signal)

Sprints come from the sprint custom field on **child** issues:

| Project | Sprint field |
|---|---|
| V2 | `customfield_10020` |
| iGaming | `customfield_10103` |

For each (game × discipline), the builder records the **set of sprint IDs** that
any active child issue belongs to. On the Roadmap timeline, each discipline lane
renders **one colored chip per sprint** in which that discipline has active work
(replacing the old start-to-end summarized bars — Decision #8/#26).

### 2.5 The 9 lifecycle stages

Same nine stages and colors as before (legend-derived). Stage of a marker is the
discipline it belongs to; `done` and `bugfix` are special-cased by status /
issuetype where applicable.

| Stage | Display label | CSS var |
|---|---|---|
| `concept` | Concept | `--stg-concept` |
| `art` | Art | `--stg-art` |
| `design` | Design | `--stg-design` |
| `math` | Math | `--stg-math` |
| `dev` | Dev | `--stg-dev` |
| `sound` | Sound | `--stg-sound` |
| `qa` | QA | `--stg-qa` |
| `bugfix` | Bug Fix | `--stg-bugfix` |
| `done` | Done | `--stg-done` |

### 2.6 Sizes are NOT in Jira

Complexity sizes (XS/S/M/L/XL per discipline) are **not** a Jira field. They are
manual Plan Mode overrides stored in `localStorage`. See §7.

---

## 3. Dashboard Architecture

### 3.1 One standalone page + shared engine (Decisions #29, #30, #31)

The Game Pipeline is its **own** dashboard — **not** part of the exec/release-
timeline dashboard and not linked into its nav.

```
game-pipeline.html     ← THE page: own header + V2|iGaming toggle, loads BOTH
                          data files, switches in place via
                          window.GamePipeline.mount(key, container)
dashboard.css   ← shared styling (toggle + sprint axis + everything)
dashboard.js    ← shared engine; re-mountable; reads window.GP_DATA[key]
dashboard-data-v2.js   ← window.GP_DATA['v2'] = {games,sprints,refreshed_at}
dashboard-data-ig.js   ← window.GP_DATA['ig'] = {games,sprints,refreshed_at}
```

The page loads both data files because each publishes a namespaced
`window.GP_DATA[<project>]` object rather than a bare `const GAMES` (which would
collide). The engine exposes `window.GamePipeline.mount(projectKey, el)` to
render/switch a project into a container. (A `window.PROJECT` single-project
path still exists in the engine but is unused — the single-project shells were
removed in #31.)

Each shell sets a `window.PROJECT` object **before** loading the data file and
the engine:

```js
window.PROJECT = {
  key: 'v2',                       // 'v2' | 'ig'
  title: 'V2 Game Pipeline',
  subtitle: 'Pong Game Studios · V2 game-epic lifecycle dashboard',
  jira_project: 'V2',              // 'V2' | 'IG'
  sprint_field: 'customfield_10020',// V2; IG uses customfield_10103
  ls_prefix: 'gp_v2_',             // 'gp_v2_' | 'gp_ig_'
};
```

`dashboard.js` reads `window.PROJECT` for the document title, header text, and —
critically — the `localStorage` prefix.

### 3.2 localStorage namespacing (Decision #22)

Both pages share an origin on GitHub Pages, so every key is prefixed per project:

| Logical key | V2 | iGaming |
|---|---|---|
| editable config | `gp_v2_config` | `gp_ig_config` |
| game order | `gp_v2_order` | `gp_ig_order` |
| status overrides | `gp_v2_status` | `gp_ig_status` |
| size overrides | `gp_v2_sizes` | `gp_ig_sizes` |
| hidden games | `gp_v2_hidden` | `gp_ig_hidden` |

One page's Plan Mode state never affects the other.

### 3.3 The status/stage distinction (unchanged, still critical)

- **Lifecycle Stage** — auto-derived, color-coded. The most-downstream
  discipline currently active, inferred from child-ticket states (Decision #32).
  Cannot be edited.
- **Workflow Status** — auto-derived from child-ticket states (Signed Off / In QA
  / On Hold / In Production / In Pre-Prod / Not Started; epic status is the
  no-children fallback). A Plan Mode **manual override** supersedes it (shown with
  a ✎ mark, with ↺ revert-to-auto and a drift flag). Persisted to `{prefix}status`.

See Decisions #2 and #32 in the logic ledger.

### 3.4 Sprint axis (matches the exec timeline dashboards exactly — Decision #27)

The Roadmap view uses a **sprint axis**, not a month axis:
- Sprint chips along the axis show label + date range, e.g. `S1 / May 11 – May 24`.
- A vertical red **TODAY** line with a TODAY chip at the top.
- Faint vertical **sprint boundary lines** run through the chart.
- Sprint numbering is anchored to **S1 = 2026-05-11** (14-day cadence), the same
  anchor the timeline dashboards use. Sprints predating S1 are not shown — the
  chart starts at S1.
- `SPRINTS` is a global array shaped `[{label, start, end}, …]`, sorted
  chronologically. (The timeline pages omit `end`; we include it.)

`CHART_START = 2026-05-11` (S1). `CHART_END` extends to the last sprint with data
(at least `2027-12-07`).

### 3.5 Views (toggle in header)

1. **Roadmap** (default) — per-game rows with stacked discipline lanes, sprint
   markers on a sprint axis.
2. **Heatmap** — discipline × month grid, hour-load with capacity ceilings.
3. **List** — flat sortable table for triage.

### 3.6 Heatmap hour-load (Decision #26)

For each (game × discipline × month):

```
contribution = max(0, est - spent) × (sprints_active_in_month / total_sprints)
```

where `sprints_active_in_month` counts the discipline's active sprints whose date
range overlaps the month. Summed across all games per discipline per month.
Color thresholds unchanged: `>cap` red · `>75%` amber-bold · `>40%` amber · `>0`
green · `=0` neutral. Capacity ceilings are editable defaults (Art 240 · Design
80 · Math 320 · Dev 480 · Sound 160 · QA 200 h/mo).

### 3.7 Plan Mode — "Edit Plan" drawer (Decision #35)

The header **✎ Plan Mode** button opens a slide-in right drawer (mirrors the
exec dashboard's Edit Plan), with two tabs:
- **Games** — one compact row per game: drag handle (reorder) · show/hide toggle
  · name (Jira) · workflow-status select; **click ▾** to expand the A/M/D/S size
  selectors + ↺ revert-status-to-auto. Collapsed by default.
- **Settings** — enum editors (statuses / stages / sizes) + discipline capacity
  ceilings (heatmap).
- **Footer** — `↺ Reset local edits` + a shown/hidden count.

The board status pill is read-only (✎/drift markers); status is edited in the
drawer. Everything autosaves to `localStorage` (local per browser). A shared
"save as default for everyone" with an allowlisted editor is **deferred**
(Decision #36).

---

## 4. Build Phases

### Phase 1 — current build
- [x] Jira-sourced data (no Excel) via `build_jira_data.py`
- [x] Two pages (V2 + iGaming) over a shared engine
- [x] Sprint axis + per-discipline sprint markers
- [x] KPI strip · filter bar · Roadmap/Heatmap/List views · detail panel
- [x] Plan Mode: workflow-status dropdowns, drag-reorder, **size overrides**
- [x] localStorage persistence, per-project namespaced
- [x] Deployment to GitHub Pages via the `pong-exec-dashboard` repo

### Phase 2 — dependencies + richer planning
- [ ] Game-to-game dependency model (Jira "is blocked by") — Decision #15
- [ ] 🔗 icon + hover, dashed-amber arrows in Roadmap, Dependencies tab
- [ ] Hide/restore games

### Phase 3 — drag-to-replan
- [ ] Draggable stage/sprint bars, live heatmap, dependency-constrained drops —
      Decision #16

---

## 5. Python Data Builder — `build_jira_data.py` (Phase 1, this build)

Queries the Jira REST API directly. No Excel. One builder, run per project.

```
python build_jira_data.py --project v2
python build_jira_data.py --project ig
python build_jira_data.py --project both          # default
python build_jira_data.py --project both --verbose
```

Reads credentials from `.env` (`python-dotenv`): `JIRA_BASE_URL`, `JIRA_EMAIL`,
`JIRA_API_TOKEN`, `JIRA_BOARD_ID_V2`, `JIRA_BOARD_ID_IG`.

Flow per project:
1. Fetch epics — `project = {KEY} AND issuetype = Epic AND fixVersion IS NOT EMPTY ORDER BY rank ASC` via `POST /rest/api/3/search/jql` (paginate on `nextPageToken` until `isLast`).
2. Per epic, fetch children — `parent = {epicKey}` with the project's sprint field.
3. Group children by discipline (§2.2), applying exclusions.
4. Aggregate hours + `sprints_active` per (game × discipline).
5. Derive `current_stage` and `workflow_status` from child-ticket states (#32)
   (default `Not Started`).
6. Build the global `SPRINTS` list from the project's agile board
   (`GET /rest/agile/1.0/board/{boardId}/sprint?state=active,closed,future`),
   falling back to the S1=2026-05-11 anchor when a board ID is absent.
7. Write `dashboard-data-{project}.js` with `const GAMES`, `const SPRINTS`,
   `const REFRESHED_AT`.
8. Snapshot to `archive/YYYY-MM-DD-HHMM_{project}_data.json`.

Rate limiting: 0.2 s between paginated calls; honor `429 Retry-After`; cap 5
retries. Missing env → clear exit. Network error → retry 3× with backoff.

---

## 6. Visual Design Tokens (matched to the exec dashboards)

| Token | Value | Used for |
|---|---|---|
| Font | IBM Plex Sans (body) + IBM Plex Mono (numbers) | All text |
| Background | `#f4f6fb` | Page background |
| Surface | `#ffffff` | Cards, rows |
| Border | `#dde2ee` | All borders |
| Text | `#1a2035` | Primary text |
| Muted | `#6b7a99` | Secondary text |
| Today line | `rgba(185,28,28,.55)` | Vertical TODAY indicator |
| Sprint line | faint vertical rule | Sprint boundaries through the chart |
| Plan mode accent | `#d97706` (amber) | Plan Mode UI |

Lane bar height 9px · Row min-height 74px · Label column 262px · Hours column
120px.

---

## 7. Plan Mode Overrides (sizes + status + order)

These are **not** in Jira and are owned entirely by the dashboard, persisted to
`localStorage` and surviving every data refresh:

- **Sizes (XS/S/M/L/XL per discipline)** — Decision #25. Edited in the Plan Mode
  config panel (Art/Math/Dev/Sound selectors per game). Stored at
  `{prefix}sizes`. The size column on a collapsed row is **hidden** until a
  manual size is entered for that game.
- **Workflow status** — Decision #24. Default `Not Started`; manual dropdown
  only. Stored at `{prefix}status`.
- **Display order** — drag-reorder. Stored at `{prefix}order`.
- **Show/hide games** — Plan Mode "Games on the board" toggle list (Decision
  #34). Hidden games leave the KPIs, all views, and the count. Stored at
  `{prefix}hidden`.

Board membership is otherwise automatic (Decision #34): only epics named
`Game:` (V2) / `Gen2 Game:` (IG) appear; a game with a released fixVersion shows
as **Delivered** (green chip, forced Signed Off) and drops off 14 days after its
release date.

Because overrides are keyed by game name and stored separately from the embedded
`GAMES` array, a Jira refresh re-derives stage/hours/sprints while preserving the
user's curation.

---

## 8. Files

| File | Purpose |
|---|---|
| `game-pipeline.html` | The standalone dashboard — own header + V2/iGaming toggle |
| `dashboard.css` | Shared styling (incl. sprint axis: `.sp-chip`, `.sp-line`, `.ax-today`) |
| `dashboard.js` | Shared engine, parameterized by `window.PROJECT` |
| `dashboard-data-v2.js` | Built V2 data (`GAMES`, `SPRINTS`, `REFRESHED_AT`) |
| `dashboard-data-ig.js` | Built iGaming data |
| `build_jira_data.py` | Jira → data-file builder |
| `requirements.txt` | `requests`, `python-dotenv` |
| `refresh.bat` | Local refresh: build both + open both pages |
| `.env` / `.env.example` | Jira credentials (gitignored) |
| `.github-workflow.yml` | CI template to copy into `pong-exec-dashboard` |
| `GAME_PIPELINE_KNOWLEDGE.md` | This file — authoritative reference |
| `GAME_PIPELINE_LOGIC.md` | Decisions ledger — logic + rationale |
| `README.md` | Quick-start |

---

## 9. Open Questions

1. **Design discipline issuetype** — which Jira issuetype maps to `design`?
   Confirm with the user.
2. **iGaming design tasks** — does the **IG** project even have a Design Task
   issuetype, or an equivalent? (V2 may differ from IG here.)
3. **Dependency source** — Jira "is blocked by" links only, or also a manual
   table maintained in Plan Mode? (Phase 2)
4. **Heatmap time window** — fixed 12 months, or scrollable / TODAY-anchored?
5. **Bug Fix as 9th stage** — keep separate, or merge with QA?
6. **`[V]` variants** — separate epics or sub-rows of the parent?
7. **Cross-project view** — eventually combine V2 + iGaming into one roadmap?

---

*Last updated: June 2, 2026 · Pong Game Studios PMO*
