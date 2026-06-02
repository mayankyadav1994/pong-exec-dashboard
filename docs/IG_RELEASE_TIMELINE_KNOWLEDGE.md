# iGaming Release Timeline — Knowledge Base
**Project:** Pong Game Studios · iGaming Fix Version Delivery Forecast
**Jira Instance:** `ponggamestudios.atlassian.net` · Project key: `IG`
**Cloud ID:** `85005dc7-cde3-4a2c-8e65-2d746db228ed`
**Current prototype:** `igaming-timeline.html` (single-file static report, rendered from `igaming-timeline.template.html` by `igaming_timeline.py`)
**Companion doc:** `V2_RELEASE_TIMELINE_KNOWLEDGE.md` — iGaming follows the same conventions except where noted in §10.

---

## 1. Purpose

A Gantt-style fix version delivery forecast for iGaming releases. Same shape as V2 with iGaming-specific additions:

- Dev complete date per fix version (projected from remaining hours + resource availability)
- QA window per fix version (fixed duration after dev complete)
- Per-department bars: **Creative · Math · Sound · Design** (Design is iGaming-only, 4th lane)
- Per-resource task breakdown with remaining hours
- Per-resource availability buffers (default 75%, individually adjustable)
- Sprint boundaries as visual reference lines aligned to iGaming's cadence
- **Auto-flagged critical path** per FV (assignee with highest remaining hours) and per active sprint (most overcommitted dev)
- Drag-to-reprioritize fix versions with live queued hours recalculation
- Scope tab per FV with real Jira epics and expandable task lists
- **Hidden FVs chip** — hide any FV with ✕, restore from collapsible chip above the list
- Last refreshed timestamp in header
- Unlogged hours warning badges per FV
- Sprint worklog summary per person inside each FV detail panel (filtered to that FV)
- **Active Sprint Board** at the bottom — dev-only per-person columns showing original/logged/remaining hours, capacity overage warnings, critical-path flag
- What-If Scenario Planner (per-person hours, toggle on/off, cascade to downstream FVs)

---

## 1.5 How the FV list is built (auto-discovery + config overrides)

The dashboard's list of fix versions is **not** dictated by `config/igaming.json`. It's auto-discovered from Jira on every run, then layered with config priority + metadata overrides. This means new FVs created in Jira appear automatically, and FVs released or archived in Jira drop out without any human touching the config.

Specifically, on every workflow run:

1. `igaming_timeline.py` calls `GET /project/IG/versions` and keeps only those where `released:false` and `archived:false` — call this set **J**.
2. `config/igaming.json` is read for `fv_order` (priority hint) and `fv_meta` (per-FV overrides — call them **C_order** and **C_meta**).
3. `merge_fv_lists(J, C_order, C_meta)` produces the active list:
   - FVs in **C_order** that exist in **J** keep their config position.
   - FVs in **J** that aren't in **C_order** are appended, sorted by `releaseDate` ascending (null/missing → bottom, ties by name).
   - FVs in **C_order** that no longer exist in **J** (released, archived, renamed) are dropped silently.
4. For each rendered FV, metadata defaults come from the Jira version (`releaseDate`, `description` as subtitle, `#94a3b8` grey colour, 2 QA weeks). Anything **C_meta** specifies wins.

**Implication:** A FV with no entry in `fv_meta` shows up in grey with default styling. The first time the manager customizes it through the Plan Editor and clicks Save-as-default, `fv_meta[name]` is written to the config; the FV's appearance is now "configured" and persists.

The §2 table below is the historical/current expected list of FVs. It can drift from what's actually rendered — Jira is the source of truth.

---

## 2. Fix Versions in Scope (Priority Order)

iGaming spans **three product lines**: ELG, PFH2, and Horse Play. Initial order: real releases by date ascending, then bucket FVs.

| # | Fix Version | Stream | Release Date | Theme | QA Weeks |
|---|---|---|---|---|---|
| 1 | ELG 3.30 | ELG | 2026-04-30 | Round 4 + Bug Fixes | 2 |
| 2 | ELG 4.00 | ELG | 2026-05-29 | Round 5 + New Games | 2 |
| 3 | PFH2 Games 2.10 | PFH2 | 2026-05-19 | Maintenance | 2 |
| 4 | Horse Play 1.00 | Horse Play | 2026-05-29 | Launch | 2 |
| 5 | ELG 4.10 | ELG | — | Round 6 | 2 |
| 6 | PFH2 Games 2.00 | PFH2 | — | Maintenance (prev) | 2 |
| 7 | PFH2 Games 3.00 | PFH2 | — | Round 3 | 2 |
| 8 | PFH2 Games 4.00 | PFH2 | — | Round 4 | 2 |
| 9 | PFH2 Services 3.00 | PFH2 | — | Backend services | 2 |
| 10 | ELG Game Configs | ELG (bucket) | — | Game config tasks | 2 |
| 11 | ELG Website | ELG (bucket) | — | Website work | 2 |
| 12 | ELG New Games | ELG (bucket) | — | New games pipeline | 2 |
| 13 | ELG Feature Backlog | ELG (bucket) | — | Feature backlog | 2 |
| 14 | PFH2 Games Backlog | PFH2 (bucket) | — | Games backlog | 2 |
| 15 | New Games - iGaming | iGaming (bucket) | — | New games backlog | 2 |

> **No regulated/Lab pipeline** for any iGaming FV. The V2 Lab1+Rev → Pilot → Lab2+Rev → Launch flow is not used in iGaming.

> **Bucket FVs** (`ELG Game Configs`, `ELG New Games`, `ELG Feature Backlog`, `PFH2 Games Backlog`, `New Games - iGaming`) are rolling work pools, not date-driven releases. Users typically hide these via the ✕ button.

---

## 3. Jira Query Rules

Same V2 include/exclude logic. iGaming-specific additions called out.

### 3.1 What to INCLUDE

```
issuetype in ("Story", "Dev Task", "Dev Subtask",
              "Creative Task", "Creative Subtask",
              "Sound Task", "Sound Subtask",
              "Math Task", "Math Subtask",
              "Design Task", "Design Subtask",     -- iGaming addition
              "Pre-Prod Task", "Pre-Prod Subtask")
```

Grouping:
- **Dev group:** `Dev Task`, `Dev Subtask`, `Story`, `Pre-Prod Task`, `Pre-Prod Subtask`
- **Creative group:** `Creative Task`, `Creative Subtask`
- **Math group:** `Math Task`, `Math Subtask`
- **Sound group:** `Sound Task`, `Sound Subtask`
- **Design group:** `Design Task`, `Design Subtask` (**iGaming-only — new 4th lane**)

### 3.2 What to EXCLUDE

```
issuetype in (Bug, Enhancement)
issuetype in ("QA Task", "QA Subtask")
summary ~ "Release"    -- parent tasks AND all subtasks
summary ~ "Merge"      -- parent tasks AND all subtasks
```

### 3.3 JQL Templates

**All active FV-eligible work across all unreleased fix versions:**

```jql
project = IG
  AND fixVersion in unreleasedVersions()
  AND statusCategory != Done
  AND issuetype not in ("Bug", "Enhancement", "QA Task")
  AND summary !~ "Release"
  AND summary !~ "Merge"
```

**Active sprint (dev work only):**

```jql
project = IG
  AND sprint in openSprints()
  AND statusCategory != Done
  AND issuetype in ("Dev Task", "Dev Subtask", "Story", "Pre-Prod Task", "Pre-Prod Subtask")
  AND summary !~ "Release"
  AND summary !~ "Merge"
```

**All epics for SCOPE tab:**

```jql
project = IG
  AND issuetype = Epic
  AND fixVersion in unreleasedVersions()
```

### 3.4 Fields to Fetch Per Issue

```
summary, status, issuetype, assignee,
timeestimate, timeoriginalestimate, timespent,
fixVersions, parent,
customfield_10103          -- Sprint (NOT customfield_10020)
```

> **CRITICAL:** Sprint custom field in this Jira instance is `customfield_10103`, not the standard `customfield_10020`. The Sprint field returns an array like:
> ```json
> [{ "id": 680, "name": "IG Sprint 7", "state": "active", "boardId": 250,
>    "startDate": "2026-05-25T17:37:41.873Z", "endDate": "2026-06-08T04:00:00.000Z" }]
> ```

### 3.5 Status Mapping

All V2 statuses plus three iGaming-specific ones:

| Status | Category | Active? | Badge CSS |
|---|---|---|---|
| New | To Do | ✓ | `s-new` |
| To Do | To Do | ✓ | `s-todo` |
| In Progress | In Progress | ✓ | `s-prog` |
| Ready | In Progress | ✓ | `s-rdy` |
| **Ready For QA** | In Progress | ✓ | `s-rfqa` (NEW, purple) |
| In QA | In Progress | ✓ | `s-inqa` |
| Pre-Prod In Progress | In Progress | ✓ | `s-pp` |
| **Reopened** | To Do | ✓ | `s-reop` (NEW, red) |
| **To Be Closed** | To Do | ✓ | `s-tbc` (NEW, teal) |
| Closed | Done | ✗ | `s-done` |
| Done | Done | ✗ | `s-done` |

---

## 4. Forecast Calculation Model

Identical to V2.

### 4.1 Per-Person Availability

- **Default buffer: 75%** (6h productive per 8h day) — applies to all resources
- No special cross-release exceptions (V2's "Rejosh 50%" is V2-specific, does not apply)
- Buffer adjustable per-person per-FV via inline slider (persists in `BUFFERS` object, not localStorage)

### 4.2 Dev Complete Date Per FV

For each FV (in priority order):
1. For each dev assignee in the FV:
   - `personOpenHours` = sum of `timeestimate` across non-Done tasks in this FV
   - `personQueuedHours` = sum of `personOpenHours` across all higher-priority FVs (cross-release queue)
   - `total = personOpenHours + personQueuedHours`
   - `projectedDoneDate` = TODAY + ceil(total / (8h × buffer%)) working days, skipping weekends + holidays
2. **FV dev complete** = MAX(projectedDoneDate across all dev assignees) — the slowest person sets the date.
3. **Critical-path person** = whoever has the latest projected date (auto-flagged with ⛓).

### 4.3 QA End Date

```
qaEnd = devEnd + (qaWeeks × 5 × 8h) ÷ 8h/day
      = devEnd + (qaWeeks × 5) working days
```

QA = 2 weeks default. No 3-week exceptions in iGaming (V2's P2P / PT 14.00 3-week rule does not apply).

### 4.4 Department End Dates (Creative · Math · Sound · Design)

Same formula as dev, but no cross-release queuing. Each department lane shows TODAY → max(person's projected done) across that department.

### 4.5 Holidays

`HOLIDAYS = ['2026-05-18']` — Victoria Day. Add additional iGaming-team holidays here if/when the team is in different jurisdictions.

### 4.6 Critical-Path Auto-Flagging

Two independent flags:
- **Per FV:** assignee with the highest remaining hours gets `bottleneck:true`, displayed as ⛓ CRITICAL PATH next to their name in the detail panel
- **Per sprint:** in the Active Sprint Board, the dev whose `remaining > (remainingWorkdays × 6h)` by the largest margin gets `⛓ CRITICAL PATH` flag. If no one is over capacity, the dev with the most remaining hours gets it (provided they have any work left).

---

## 5. Active Sprint Board (iGaming-Only Feature)

Full-width section at the bottom of the dashboard, below the FV list.

### 5.1 Source Data

JQL above (§3.3 "Active sprint"). Dev-only filter — Creative/Math/Sound/Design assignees are intentionally excluded from this section.

### 5.2 Per-Dev Column Contents

For each dev assignee in the active sprint:
- Name + ⛓ flag if critical-path
- **Original / Logged / Remaining hours** (3-column number block, sum across all their sprint items)
- **Progress bar** = Logged ÷ Original
- **Capacity status badge:**
  - `🚨 OVER CAPACITY +Nh beyond Xh available` (red) — when `remaining > remainingWorkdays × 6h`
  - `✓ on track · Nh headroom in X days` (green) — when remaining ≤ capacity
  - `✓ no work remaining` — when remaining = 0
- **Task list** sorted by status (In Progress first), each showing:
  - Key (linked to Jira)
  - Summary
  - `Orig / Logged / Remain` hours
  - Status badge
  - FV tag (or "no FV" if unassigned to any FV)

### 5.3 Sprint-Level Totals

Footer of the section: total devs · total items · Original / Logged (%) / Remaining hours.

### 5.4 Collapse State

Header is clickable, toggles `.collapsed` class. State persisted to `localStorage['ig_sprintboard_collapsed']`.

---

## 6. Hidden FVs Feature (iGaming-Only)

### 6.1 Behavior

- Every non-scenario FV row has a ✕ button that appears on row hover (top-right of row)
- Clicking ✕ adds the FV key to `HIDDEN_FVS` Set, persists to `localStorage['ig_hidden_fvs']`, hides the DOM row, and shows a toast
- A `⌃ Hidden (N)` chip renders above the FV list when any FV is hidden
- Clicking the chip expands a row of hidden-FV pills, each with a `↩ restore` button

### 6.2 Persistence

`localStorage['ig_hidden_fvs']` = JSON array of FV key strings, e.g. `["ELG Game Configs","New Games - iGaming"]`.

---

## 7. Visual Conventions

### 7.1 FV Accent Colors

| FV | Color | Hex |
|---|---|---|
| ELG 3.30 | Light blue | `#60a5fa` |
| ELG 4.00 | Blue | `#3b82f6` |
| ELG 4.10 | Deep blue | `#2563eb` |
| PFH2 Games 2.10 | Orange | `#fb923c` |
| PFH2 Games 2.00 | Amber | `#f59e0b` |
| PFH2 Games 3.00 | Deep orange | `#f97316` |
| PFH2 Games 4.00 | Burnt orange | `#ea580c` |
| PFH2 Services 3.00 | Peach | `#fed7aa` |
| Horse Play 1.00 | Green | `#4ade80` |
| ELG Game Configs | Lavender | `#a78bfa` |
| ELG Website | Light violet | `#c4b5fd` |
| ELG New Games | Purple | `#a855f7` |
| ELG Feature Backlog | Deep violet | `#8b5cf6` |
| PFH2 Games Backlog | Slate | `#94a3b8` |
| New Games - iGaming | Dark slate | `#64748b` |

**Logic:** ELG = blue family · PFH2 = orange family · Horse Play = green (new line stands out) · buckets = muted purples/slates.

### 7.2 Department Lane Colors

| Lane | Color | Hex |
|---|---|---|
| Creative | Violet | `#7c3aed` |
| Math | Teal | `#0f766e` |
| Sound | Pink/magenta | `#9d174d` |
| **Design** | **Cyan** | `#0e7490` (iGaming addition) |

### 7.3 Sprint Cadence

iGaming runs on the same 14-day cadence as V2 starting from the same May 11 baseline, but **numbered as iGaming Sprint N**. Currently in IG Sprint 7 (May 25 — Jun 8).

Timeline chips renumbered S6 → S20 (covering May 11 → Dec 7).

---

## 8. localStorage Keys

See §11.9 for the complete and current list (Phase 5 added Plan Editor + PAT auth keys).

All keys are `ig_*` prefixed to avoid collision with the V2 dashboard if both are open in the same browser.

---

## 9. Refresh / Rebuild Workflow

The dashboard is regenerated by `igaming_timeline.py` on three different triggers (see `.github/workflows/igaming_timeline.yml`):

- **Daily cron** at `30 6 * * *` UTC (15 min after V2 timeline, 30 min after exec dashboard; shared `refresh-dashboards` concurrency group)
- **`workflow_dispatch`** — manual trigger from GitHub UI or REST API
- **`push.paths: config/igaming.json`** — fires when the Plan Editor's "Save as default" lands a config commit on `main`. Gives a ~2-3 minute round-trip from clicking Save to the new HTML being live for all viewers.

### 9.1 Automated Refresh

Inputs from GitHub Actions secrets:
- `JIRA_EMAIL`, `JIRA_API_TOKEN` (basic auth for Jira REST API)
- `CONFLUENCE_API_TOKEN` (not currently used; reserved for future scope sync)

Pipeline (any trigger):
1. `igaming_timeline.py` calls `load_config()` first — reads `config/igaming.json` for FV order, FV metadata, holidays, hidden FVs. Falls back to `DEFAULT_*` Python literals if the file is missing or unparseable.
2. Calls Jira REST `/rest/api/3/search/jql` (via `jira_client.jira_jql`) with the three JQL queries from §3.3.
3. Pagination uses `nextPageToken` until `isLast: true` (~1100 issues for the FV-tasks query).
4. The script transforms results in-process: assignee normalization, type grouping, status mapping, FV ordering, critical-path auto-flagging. Passes through `epic_overrides` and `milestones` from each FV's `fv_meta` entry untouched.
5. The script injects assembled constants into `igaming-timeline.template.html` via the placeholders: `__FV_DATA__`, `__SPRINT_DATA__`, `__SPRINTS_DATA__`, `__TODAY__`, `__HOLIDAYS__`, `__REFRESH_LABEL__`, `__SPRINT_HEADER__`.
6. Output `igaming-timeline.html` is committed back to the branch by the workflow if the diff is non-empty.

### 9.2 Source-of-Truth Data-Shaping Rules

All encoded in `igaming_timeline.py`:

- `norm_name()` — normalize `"Sonali.Mehra"` → `"Sonali Mehra"`, `"BinZhang"` → `"Bin Zhang"`
- `group_for(itype)` — map issuetype to one of `dev` / `Creative` / `Math` / `Sound` / `Design` / `None`
- `FV_ORDER` and `FV_META` — initial priority order, colors, subtitles, release dates
- Critical-path: highest remaining hours in the dev group per FV
- Scope: tasks grouped by `parent` field; tasks whose parent isn't in the FV's epics fall into "Other tasks (no epic in this FV)"

---

## 10. Differences From V2 (Quick Reference)

| Aspect | V2 | iGaming |
|---|---|---|
| Project key | `V2` | `IG` |
| Product lines | 1 (V2 SW/PT/P2P/HHR/C2) | 3 (ELG, PFH2, Horse Play) |
| Sprint custom field | `customfield_10020` (spec, not verified) | `customfield_10103` (verified) |
| Active sprint | S1 / Sprint 1 starting May 11 | IG Sprint 7 starting May 25 |
| Department lanes | 3 (Creative · Math · Sound) | **4** (+ Design) |
| Regulated/Lab pipeline | P2P 16, PT 14 | **None** |
| Sales Trip pins | Yes (per regulated FV) | **None** |
| Resource buffer exceptions | Rejosh @ 50% | None — all 75% |
| QA week exceptions | P2P/PT14 @ 3 weeks | None — all 2 weeks |
| Hide FV feature | Yes (via Plan Editor only) | **Yes** (Plan Editor + inline ✕ chip) |
| Active Sprint Board section | No | **Yes** (dedicated section at bottom, dev-only) |
| Critical-path flagging | Manual flags in data | **Auto-calculated** per FV and per sprint |
| New statuses | — | Ready For QA, Reopened, To Be Closed |
| Plan Editor (Phase 5) | **Yes** | **Yes** (identical UI) |
| Scope editor (per-epic toggle/rename) | **Yes** | **Yes** |
| Milestones (gantt pins) | **Yes** | **Yes** |
| Save-as-default + PR auto-merge | **Yes** | **Yes** |
| Config file | `config/v2.json` | `config/igaming.json` |

---

## 11. Plan Editor + Save-as-default Flow (Phase 5)

The dashboard ships with an in-page editor that lets authorized users change the editable bits of the dashboard's behaviour for everyone else, without touching the Python script or the HTML directly. This is the same pattern in V2 — copy-pasted with a four-line identity swap.

### 11.1 Top-right nav buttons

Two buttons sit on the same level as the page tabs:

```
[Overview] [V2 Timeline] [iGaming Timeline]              [⚙ Edit Plan]  [⚡ What-If]
```

- **⚙ Edit Plan** (orange) — opens the right-side panel in *planeditor* mode (wider, 680px). Hosts the Fix Versions + Settings tabs.
- **⚡ What-If** (purple) — opens the same panel in *whatif* mode (narrower, 560px). Hosts the existing Scenarios sandbox. Carries a small red badge with the count of active scenarios; greys out when the master toggle is off.

The two modes share the same panel element (`#scenarioPanel`) with a `data-mode` attribute that drives tab visibility via CSS (`.scenario-panel[data-mode="whatif"] .sp-tab[data-tab="fvs"]{display:none}` etc.).

### 11.2 Three tabs

| Tab | What it edits | Persists to |
|---|---|---|
| **⚡ Scenarios** | Existing what-if scenarios (add fake work to a person / new FV) | `localStorage.ig_scenarios` (browser-local) |
| **📋 Fix Versions** | Per-FV color, visibility, release date, QA weeks, subtitle, scope, milestones | `localStorage.ig_local_config` → `config/igaming.json` via PR |
| **⚙ Settings** | Global holidays list | `localStorage.ig_local_config` → `config/igaming.json` via PR |

### 11.3 Fix Versions tab — per-row stacked editor

Each FV row is collapsed by default. Hover reveals the inline color picker, visibility toggle, and ▾ chevron. Clicking ▾ expands the row to three stacked sections:

1. **DETAILS** — release date (`date` input), QA weeks (`number` input 0-12), subtitle (`textarea`)
2. **SCOPE** — one row per Jira epic in the FV (plus the "no epic" virtual bucket if applicable)
   - Toggle (green = visible / grey = hidden from the FV's Scope tab)
   - Jira key link (or `—` for the unscoped bucket)
   - Inline-editable label (`contenteditable` — click to rename, Enter to save, Escape to cancel)
   - ↺ reset button — clears both hidden and label overrides for this epic
3. **MILESTONES** — list of pins displayed on the FV's gantt bar
   - Label (text input)
   - Anchor select: `after Dev` (uses `offset_weeks`) or `fixed date` (uses `date`)
   - Value input (`number` weeks or `date` calendar) — swaps based on anchor
   - Color picker
   - `×` delete button
   - `+ Add milestone` button at the bottom

Edits land in `LOCAL_CONFIG.fv_meta[fvKey]` and the FV array is mutated in-place so `buildRows()` picks up the new color/qaWeeks/etc immediately.

### 11.4 Milestone rendering on the gantt

Inside `buildRows()`, after the dev/QA/lab bars are placed, each milestone in `effectiveFv.milestones` is resolved to a date:

```js
if (ms.anchor === 'date') msDate = D(ms.date);
else                      msDate = devEnd + ms.offset_weeks * 7 days;
```

Renders as a 1.5px-wide vertical `<div class="ms-pin">` (full row height) plus a small `<div class="ms-pin-label">` above it. The label uses the milestone color at 22% opacity for the background, the color itself for the text, and 55% opacity for the border. Multiple labels on the same FV row are staggered vertically by `(msIdx % 3) * 16px` so close-together milestones don't overlap.

### 11.5 LOCAL_CONFIG — browser-local overlay

```js
LOCAL_CONFIG = {
  fv_order: ['ELG 4.00', 'ELG 3.30', ...] | null,   // present only when reordered locally
  fv_meta: {                                          // per-FV per-field overrides
    'ELG 4.00': {
      color: '#ff0000',
      qaWeeks: 3,
      release: '2026-06-05',
      sub: 'Custom subtitle',
      epic_overrides: { 'IG-1685': { hidden: true, label: 'Custom name' } },
      milestones: [{ id, label, anchor, offset_weeks|date, color }, ...]
    }
  },
  hidden_fvs: ['ELG 3.30'],                          // panel-driven hide list
  holidays: ['2026-05-18', '2026-07-01'] | null      // present only when locally edited
};
```

Loaded from `localStorage.ig_local_config` on every page load. Two `apply*` functions replay it over the in-memory FV array and HOLIDAYS list so the dashboard renders with the user's tentative edits even after refresh.

### 11.6 Server config — `config/igaming.json`

The Python script (`igaming_timeline.py`) reads this file at the top, falls back to hardcoded defaults if it's missing or unparseable. The dashboard's JS doesn't read this directly — values are baked into the rendered HTML via `__FV_DATA__`, `__HOLIDAYS__`, etc.

Schema:

```json
{
  "fv_order": ["ELG 3.30", "ELG 4.00", ...],
  "fv_meta": {
    "ELG 4.00": {
      "color": "#3b82f6",
      "sub": "iGaming · ELG Round 5 + New Games",
      "qaWeeks": 2,
      "release": "2026-05-29",
      "epic_overrides": { "IG-1685": { "hidden": true, "label": "..." } },
      "milestones": [{ "id": "ms_xyz", "label": "QA Round 2", "anchor": "dev_end", "offset_weeks": 2, "color": "#d97706" }]
    }
  },
  "hidden_fvs": [],
  "holidays": ["2026-05-18"]
}
```

`epic_overrides` and `milestones` are optional per FV. The "no epic" virtual bucket stores under the sentinel key `"__unscoped__"`.

### 11.7 Save-as-default flow

The `💾 Save as default` button in the FVs/Settings tab footers triggers `onSaveClicked()`. The flow:

1. **Diff** — `buildConfigPayload()` serialises the current in-browser state. `fetchServerConfig()` GETs the current `config/igaming.json` from `raw.githubusercontent.com`. `diffConfigs()` produces a human-readable list.
2. **Auth** — first save prompts for a fine-grained GitHub PAT (one-time, stored in `localStorage.ig_github_pat`). `verifyPat()` checks `GET /user` + the user's `permissions.push` on the repo.
3. **Confirm** — modal shows the diff (categorised: priority / FV / scope / hide / show / holiday / pin), commit message editable, "Save & merge" button.
4. **Commit** — `saveConfigToRepo()`:
   - `GET /repos/.../contents/config/igaming.json` for the file SHA
   - `POST /repos/.../git/refs` to create branch `config/igaming-{timestamp}`
   - `PUT /repos/.../contents/config/igaming.json` to commit the new JSON
   - `POST /repos/.../pulls` to open a PR
   - `PUT /repos/.../pulls/{n}/merge` to auto-merge
   - `DELETE /repos/.../git/refs/heads/{branch}` cleanup
5. **Trigger** — the merge to `main` matches `push.paths: config/igaming.json` in `.github/workflows/igaming_timeline.yml`, firing the workflow. The script re-reads the config, regenerates the HTML, commits and pushes. Pages redeploys. Total: ~2-3 minutes from click to live.
6. **Local cleanup** — on successful merge the panel clears `LOCAL_CONFIG` (since it's now the server default) and reloads the page.

All GitHub API calls are made directly from the browser. There is no backend.

### 11.8 PAT scope

Fine-grained PAT scoped to `mayankyadav1994/pong-exec-dashboard` with:

- **Contents**: read & write (commit the config file)
- **Pull requests**: read & write (open & merge the PR)
- **Metadata**: read-only (auto-required)

Setup walkthrough is in [`docs/CONFIG_EDITOR_SETUP.md`](CONFIG_EDITOR_SETUP.md).

### 11.9 localStorage keys (iGaming dashboard)

| Key | Type | Purpose |
|---|---|---|
| `ig_scenarios` | JSON array | What-If scenarios |
| `ig_scenarios_master` | JSON bool | Master enable/disable for all scenarios |
| `ig_hidden_fvs` | JSON array | Backwards-compat hide list (the panel writes to both this and `ig_local_config.hidden_fvs`) |
| `ig_local_config` | JSON object | All Plan Editor browser-local edits (see §11.5) |
| `ig_github_pat` | string | Fine-grained PAT for Save-as-default |
| `ig_github_pat_user` | string | Last verified GitHub username (display only) |
| `ig_sprintboard_collapsed` | string `'1'`/`'0'` | Sprint board collapse state |

All `ig_*` prefixed to avoid colliding with the V2 dashboard's `v2_*` keys if both are open in the same browser.

### 11.10 Portability — what changes per dashboard

The editor JS is byte-identical between iGaming and V2 except for four dashboard-identity constants. To add this to a future game-pipeline dashboard:

| Constant | iGaming value | What it controls |
|---|---|---|
| `GH_CONFIG_PATH` | `'config/igaming.json'` | Which file the save flow writes to |
| `GH_PAT_KEY` | `'ig_github_pat'` | localStorage key for the PAT |
| `GH_PAT_USER_KEY` | `'ig_github_pat_user'` | localStorage key for the verified username |
| `localStorage.{ig_local_config, ig_hidden_fvs, ig_scenarios, ig_scenarios_master, ig_sprintboard_collapsed}` | `ig_*` prefix | All other panel state |

Plus the Python script needs its own `load_config()` pointing at the new JSON file, and its own `.github/workflows/<dashboard>.yml` push trigger. Everything else (CSS, modal markup, editor JS, save flow, milestone rendering) ports verbatim.

---

## 12. Known Issues / Open Items

- **Hour logging coverage is low (~30%)** — many active sprint items have `timeestimate: 0` or `null`. The forecast under-estimates capacity needs for those tasks. Show as unlogged-task warning badges.
- **No project mapping for backlog buckets** — `New Games - iGaming` mixes ELG, PFH2, and Horse Play work; the dashboard treats it as one row but it's effectively three streams. Users should hide it unless triaging.
- **`PFH2 Services 3.00`** has 32 tasks with 133.5h remaining but is not on the whiteboard — confirm with team whether to track or hide.
- **`ELG Feature Backlog`** has one outsize 320h `Jesse Pirrotta` task — looks like a placeholder; consider splitting in Jira.

---

## 13. File Reference

| File | Description |
|---|---|
| `igaming-timeline.html` | Generated output — committed by the workflow on every refresh |
| `igaming-timeline.template.html` | Source template — placeholders injected by the Python script. Contains all CSS, dashboard JS, Plan Editor + Save-as-default flow. |
| `igaming_timeline.py` | Refresh script — calls Jira, transforms results, renders the template. Reads `config/igaming.json` with hardcoded fallback. |
| `config/igaming.json` | **Editable config** — FV order, per-FV metadata (color, sub, qaWeeks, release), scope overrides, milestones, holidays, hidden FVs. Edited via the Plan Editor's Save-as-default button (auto-PR). |
| `jira_client.py` | Shared `jira_jql` POST `/search/jql` paginator (used by all three refresh scripts: exec, v2, igaming) |
| `.github/workflows/igaming_timeline.yml` | Workflow — daily cron (`30 6 * * *` UTC) + `workflow_dispatch` + `push.paths: config/igaming.json` |
| `docs/IG_RELEASE_TIMELINE_KNOWLEDGE.md` | This file |
| `docs/V2_RELEASE_TIMELINE_KNOWLEDGE.md` | Sister dashboard (V2) — same Plan Editor pattern |
| `docs/CONFIG_EDITOR_SETUP.md` | One-time PAT setup guide for users who'll edit dashboards |

---

*Document version: v2.0 · 2026-06-02 · Phase 5 added Plan Editor + Save-as-default + Milestones · Project: iGaming Release Timeline · Pong Game Studios*
