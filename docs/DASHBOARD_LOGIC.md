# Dashboard Logic Reference

Plain-language guide to every non-obvious decision made by the two generators in this repo. Read this when:
- A figure on the dashboard doesn't match your intuition and you want to trace where it came from
- You're about to change the math and want to know what depends on what
- A teammate inherits the project and asks "why does it do *that*?"

Each section names the rule, says **why** we picked it, and points at the code (`file:line`-ish references — they drift over time, so grep if the line is stale).

The repo ships two dashboards:

- **Exec Dashboard** — [build_dashboard.py](../build_dashboard.py) → [index.html](../index.html). A pong-wide release portfolio view.
- **V2 Timeline** — [v2_timeline.py](../v2_timeline.py) → [v2-timeline.html](../v2-timeline.html). A Gantt-style delivery forecast for V2 fix versions only.

Both run daily via [GitHub Actions](../.github/workflows/) and write back to the repo so [GitHub Pages](https://mayankyadav1994.github.io/pong-exec-dashboard/) serves them.

---

## 1. Section layout (Exec Dashboard)

**What you see:** Three team sections — V2, iGaming, CSS. Within each, "Shipped this month" renders **above** "In Progress".

**Why per-team grouping:** When the dashboard mixed all-team Shipped at the top and all-team In Progress below, it was hard to see whether a specific team had momentum. Grouping by team puts each team's wins and current work side by side.

**Why shipped first:** Wins land first in the reader's eye. The "what's done" answer comes before the "what's in flight" answer.

**Card colors are intentional:** every "In Progress" card is blue (`card-active`); every "Shipped" card is green (`card-done`). The team color lives in the section label (🔵 V2, 🟢 iGaming, 🩷 CSS) only. We tried color-per-team on the cards once — iGaming's green clashed visually with shipped-green, so colors now signal *status*, not *team*.

---

## 2. Shipped detection — two signals, primary + fallback

A release is "shipped" if **either** of these is true:

1. **Primary signal (authoritative):** the Jira fix-version's `released` flag is `true`. The ship date is the fix-version's `releaseDate`.
2. **Fallback signal (hygiene-only):** a Release-type Jira issue exists with `resolutiondate` set, and its `fixVersions` includes this version's name. The ship date is the `resolutiondate`.

**Why two signals:** the Pong team's habit is to mark a fix version "Released" in Jira *eventually* — sometimes days or weeks after the PRF goes out. During that gap, the dashboard would otherwise undercount shipped releases. The Release-ticket fallback bridges that gap.

**The "⚠️ Not yet marked Released in Jira" tag** fires when the fallback signal triggered (Release ticket resolved) but the primary signal hasn't (fix-version `released = false`). It's a nudge to the team to flip the version's released flag.

**Why we don't require `status = "Closed"` on the Release ticket:** an earlier version of the JQL did, and it missed several May releases because the team stamps `resolutiondate` while leaving the Release ticket sitting in "In QA" / "Ready For QA" for weeks. Presence of `resolutiondate` is the actual ship signal, not the status transition.

---

## 3. Health classification

Three buckets — Red / Yellow / Green — derived from ETA vs. deadline, not from progress percentage. Order matters: the first matching rule wins.

| If… | Color |
|---|---|
| Blockers > 0 (priority="Blocker" on any open ticket) | 🔴 Red |
| Deadline already passed **AND** <95% done (count or hour signal, whichever is higher) | 🔴 Red |
| Deadline already passed **AND** ≥95% done | 🟡 Yellow (trailing edge) |
| ETA misses the deadline by **more than 14 days** | 🔴 Red |
| ETA slips past the deadline by ≤14 days | 🟡 Yellow (recoverable) |
| No ETA, deadline within 7 days, progress < 80% | 🟡 Yellow |
| Everything else | 🟢 Green |
| No deadline at all (TBD) and no blockers | 🟢 Green |

**Why ETA-vs-deadline and not just progress %:** an earlier rule was "below 90% → yellow". That defaulted every TBD-deadline release to yellow regardless of trajectory, even ones with healthy velocity and no deadline pressure. The current rule asks the actual question execs want answered: *will this release miss its commitment?*

**Why TBD defaults to green:** without a deadline, there's nothing to miss. Calling a release "At Risk" because nobody's set a date yet is misleading — it conflates "no date" with "in trouble".

**The 14-day slip threshold** is calibrated to the team's typical recovery capacity. Slips under two weeks are usually absorbable; anything beyond means the plan needs to change.

---

## 4. Progress — hour-weighted, not ticket-weighted

The line you see under each release reads `X% by hours · Yh / Zh logged`.

**Per ticket, the math:**

- `scope = max(timeoriginalestimate, timespent + timeestimate)` — the best estimate of total effort for this ticket. Takes whichever signal is higher: original plan, or actual logged + remaining.
- `done_credit` =
  - For Done/Closed tickets: `max(timeoriginalestimate, timespent)`. Done means done — credit the larger of "what we planned" or "what we logged".
  - For open tickets: `timespent`. Partial credit for work logged so far.
- `pct_hours = sum(done_credit) / sum(scope)`

**Why hours instead of ticket count:** ticket count assumes every ticket is the same size. In practice a 40h "Build the API" ticket and a 0.5h "Fix typo" ticket contribute equally to a count-based percentage. Hour weighting gives execs the right shape — a release at "20% by tickets" with 80% of hours done is almost shipped, not nearly stalled.

**Fallback:** if a release has zero hour data anywhere (`hours_total == 0`), the line falls back to count-based `X% · A/B tickets`. So new versions with no estimates yet still show *something*.

---

## 5. ETA tiers and AI-fill

The "📅 Est. Jun 14" tag on each row comes from a tiered model. Higher tiers are tried first; the first one that has the data it needs wins.

| Tier | Inputs needed | Confidence emitted | Formula |
|---|---|---|---|
| 1 | ≥70% real estimate coverage on open tickets, measured 14-day velocity, work remaining | hi | `real_secs / velocity_secs_per_day` |
| 1b | ≥70% real estimate coverage, work remaining (no velocity) | med | `real_secs / (12 * 3600)` (assume 12h/day team capacity) |
| 2 | combined (real + AI-filled) hours, measured velocity | med | `total_secs / velocity_secs_per_day` |
| 2b | combined hours (no velocity) | lo | `total_secs / (12 * 3600)` |
| 3 | ≥3 issues resolved in last 14 days | med | `open_count / (recent_done_count / 14)` (count velocity) |
| 4 | planned `releaseDate` from Jira | lo | the planned date, or extrapolated from % done if overdue |
| — | none of the above | none | no tag shown |

**AI-fill (statistical imputation), used in Tiers 2 and 2b:** when an open ticket has no `timeestimate`, we infer hours from:

1. The median estimate of estimated tickets of the **same issuetype** within this fix version
2. Falling back to the cross-issuetype median in this fix version
3. Falling back to a sensible per-issuetype default (Subtask=4h, Task=8h, Story=16h, Bug=4h, etc.)

When AI-fill contributed to an ETA, the tag gets a `· 🤖 AI-filled` suffix so the reader knows the date isn't grounded in real estimates.

**Why we did imputation instead of calling an LLM:** deterministic, fast, free, auditable. You can read the formula, point at the median, and explain the projection. An LLM would drift between runs.

**The 365-day cap:** Tier formulas can produce projections like 800 days when velocity is very low. We clamp the rendered ETA at `today + 365 days`. **When the cap clamps, confidence drops to `lo`** — the date isn't real, it's "we honestly don't know, it's at least a year".

**`fmt_date` shows the year when it differs from today's:** so a Tier-2 projection capped at one year out reads `May 19 2027`, not `May 19` (which used to look exactly like today and was deeply misleading).

---

## 6. Unestimated-ticket callouts (QA / Math / Creative / Sound)

Each row can carry per-category tags like `🔶 QA: 16 tickets unestimated`. They fire when at least one ticket in that category lacks a `timeoriginalestimate`.

**The subtask-or-parent rule:** for each top-level issue of the category, *if it has subtasks check each subtask's estimate; otherwise check the parent's own estimate*. We don't double-count: a parent with subtasks is represented by its subtasks. A parent without subtasks counts on its own.

**Why per-category rather than one rollup count:** "🔶 47 tickets unestimated" doesn't help anyone act. "🔶 QA: 16 tickets unestimated" tells the QA lead exactly which inbox to look at.

---

## 7. Active sections — what counts as "in progress"

We skip a fix version from the active list entirely if its `total == 0` — i.e., the version exists in Jira but no tickets reference it. Without this filter, empty placeholder versions appear as ghostly "0% · 0/0" rows.

**Section assignment is by team mapping**, with deduplication by version name:

| Section | Jira projects queried |
|---|---|
| V2 | V2 |
| iGaming | IG |
| CSS (Cloud Services) | CSS |

CS and PFH were dropped as separate sections — CSS now holds all Cloud Services work (after the Scrum migration), and PFH2 Services 2.00/5.00 surface under CSS because their issues live there.

**When a fix version has the same name in multiple projects**, the dedup logic keeps a single entry and expands its `projects` list so issue queries hit every project. Without this, `CS VGTC 8.00` would show ~⅓ of its actual issues (the CS-only ones), and miss the 54 issues that live in CSS.

---

## 8. V2 Timeline — status label classification

The big colored badge in each row ("In Development" / "In QA" / "Scheduled") is auto-derived from the open-ticket status mix:

| Rule (checked in order) | Result |
|---|---|
| No open tickets at all | In QA |
| ≥50% of open tickets in QA-like status (`In QA`, `In QA R1/R2`, `Ready For QA`, `QA In Progress`) | In QA |
| ≥50% of open tickets in status `New` | Scheduled |
| Otherwise | In Development |

**Why 50% and not "all"**: an earlier version required *every* open ticket to be QA-like (or every ticket to be New) before flipping the label. That rule failed against reality — a few stray "In Progress" tickets pushed a clearly-In-QA release into "In Development", and a few exploratory "In Progress" tickets pushed a not-yet-started release out of "Scheduled". 50% is the threshold where one category genuinely dominates the work.

**Why QA wins ties:** the check order matters when a hypothetical release is exactly 50/50 QA/New. We resolve to "In QA" because it's the later phase — work that's both being QA'd and not-yet-started simultaneously means the QA pass is the immediate concern.

**Self-correcting:** when PT 14's "New" tickets get picked up by devs, "New" share drops below 50% → label auto-flips to "In Development". No human in the loop. Same logic flips releases into "In QA" when QA work crosses the threshold.

---

## 9. V2 Timeline — bottleneck logic

For each fix version, the **bottleneck** is the person whose projected completion date is the latest. Their projection determines the dev-done date for the release.

**Formula:** `days = (open_hours + queued_hours) / hpd(person)`, where `hpd` is hours-per-day after availability buffer:
- 4 h/day for Rejosh Samuel (cross-release load — he touches every release)
- 6 h/day for everyone else (75% of a nominal 8h day)

**Task-level bottleneck flag:** the single highest-hour open task assigned to the bottleneck person gets marked. This is the "one thing if you fixed it" handle the release manager needs.

---

## 10. V2 Timeline — queued hours (computed at render time, not in the generator)

Some people work across multiple fix versions. We don't want their work on a later release to start at "today" — it should start *after* their work on higher-priority releases finishes.

**The math:** for each person on release `n`, sum their open hours from releases `1` through `n-1`. Their projected start on release `n` is `today + queuedHours / hpd(person)`.

**Where it runs:** entirely in the browser. The template's `getDynamicQueued(fvIdx, name)` function walks the current FV order (which the user can drag to reorder) and computes queued hours on the fly. The generator does **not** emit `queuedHours` on person records — that would freeze the order at build time and break the drag-to-reprioritize feature.

**Generator-side use:** `mark_bottleneck_for_fv()` does the same calculation just to set the precomputed `bottleneck: true` flag for visual styling (red ⛓ pill). The JS uses this flag for the chip styling, but recomputes the dev-end *date* dynamically — so dragging a row updates projection dates correctly even if the chip's ⛓ flag is stale.

**Why this matters:** without queued hours, Rejosh appears to "start" 6 things simultaneously and projection dates ignore the fact that he's one human.

**Known limitation (V2_TIMELINE_EDGE_CASES.md §2):** the model assumes strict serial ordering. People context-switch in practice. The auto-calc may overstate cross-release dependencies.

---

## 11. V2 Timeline — lab phases (regulated releases only)

V2 P2P 16.00 and V2 PT 14.00 are regulated market releases. After QA they go through:

```
QA → Lab 1 + Revisions (4 wks) → Pilot (2 wks) → Lab 2 + Revisions (4 wks) → 🚀 Launch
```

Each phase is a fixed work-days block (`weeks × 5 × 8` work hours), added sequentially after the QA end date using business-day math (skips weekends and holidays).

**Why hardcoded weeks instead of dynamic:** the lab pipeline is a regulatory schedule, not a velocity-driven calculation. It takes as long as it takes regardless of dev capacity. The values per release (4 / 2 / 4 weeks for both P2P and PT 14) come from prior cycle data and live in `FV_CONFIG`.

---

## 12. V2 Timeline — Sales Trip pins

Non-Jira events pinned to the timeline. Two variants:

- **Confirmed date** (Georgia P2P, Jun 27): amber vertical pin + amber tag at the date
- **TBD** (Ohio PT 14): dashed grey tag floating near the dev-end date as a placeholder

These are config-driven — they don't come from any Jira field. Add or update them by editing the `salesTrip` field in `FV_CONFIG`.

---

## 13. V2 Timeline — department rows (Creative / Math / Sound)

Below the Dev/QA bar, each fix version can have up to three additional rows — one per non-dev discipline that has open work. Each shows a proportional bar from today to the projected completion date.

**Why three rows instead of one "Others" bar:** the old single-bar version told you "Creative + Math + Sound work exists" but not which one was blocking. Splitting reveals which discipline is the long pole.

**How we tag people into departments:** we look at the first word of their issuetype name — "Creative Task" → "Creative", "Math Subtask" → "Math", "Sound Task" → "Sound". The `type` field is set on the person record from the first matching task we see.

---

## 14. V2 Timeline — Scope tab (epics + tasks)

Each FV row's detail panel has a third tab, **SCOPE**, listing the parent epics (or stories) of the tickets in that release. Click the epic header to expand a list of the child tasks.

**How the data is built:** `compute_scope()` in `v2_timeline.py` walks every fetched issue's `parent` field. For each unique parent it emits one scope entry with: epic key, name (from `parent.fields.summary`), status, `done`/`total` counts, and the list of child task keys.

**Known limitation (V2_TIMELINE_EDGE_CASES.md §17):** we use the *direct* parent only. When an issue's direct parent is a Story rather than an Epic, the Story appears as the scope item — so a single Epic can split into multiple Story rows in the dashboard. Fixing this needs a second JQL pass to resolve Story→Epic. The keys are still real Jira keys so the click-through works.

---

## 15. V2 Timeline — Sprint Activity (currently empty)

Each person column in the detail panel has a Sprint Activity section that shows logged hours vs expected for the current sprint.

**Where the expected number comes from:** `sprintExpected(name)` in the template — `8 * (buffer/100) * workDaysToDate`. For Rejosh: `8 × 50% × 9 = 36h` expected by mid-sprint S1. For others at 75% buffer: `8 × 75% × 9 = 54h`.

**Where the actual logged hours come from:** **nowhere yet.** The generator emits `SPRINT_LOGS = {}` because Tempo / Jira-worklog integration is deferred (V2_TIMELINE_EDGE_CASES.md §18). The Sprint Activity panel renders "No hours logged this sprint" for every developer.

**What works today:** `SPRINT` info (sprint label, period, workdays elapsed/total) *is* computed by `current_sprint_info()` from the SPRINTS array and TODAY's date. The dashboard correctly shows which sprint is active and how many work days are elapsed. Only the per-person logged hours are missing.

---

## 16. V2 Timeline — drag-to-reprioritize

The drag handle (⠿) on each row lets the user reorder fix versions. Drop above/below a target to splice in. The reorder triggers a full `buildRows()` rebuild — every `getDynamicQueued()` recalculates, ETA dates shift, bottleneck flags stay (the JS uses the precomputed flag for chip styling but recomputes the projected date dynamically).

**Generator-side implication:** none. The generator emits FVs in priority order and the JS handles the rest. The reordered state lives only in the user's browser — not persisted to localStorage in this iteration.

---

## 17. V2 Timeline — What-If Scenario Planner

Orange ⚡ FAB (bottom-right) opens a slide-in panel where the user can model hypothetical work assignments. Scenarios add hypothetical tasks that cascade through queued hours downstream.

**Two placement modes:**
- **New standalone FV** — adds a dashed-orange "scenario" row to the timeline.
- **Inject into existing FV** — adds the hypothetical hours to a specific FV's person record; downstream FVs pick up the extra hours via `getInjectedHours()` in `getDynamicQueued()`.

**Persistence:** scenarios are stored in `localStorage` under `v2_scenarios` and a master enable/disable toggle under `v2_scenarios_master`. Survives page refresh. Per-user (not shared).

**Generator-side implication:** none. Scenarios are 100% client-side. The generator emits the baseline FV data and the JS handles all scenario math.

---

## 18. V2 Timeline — unlogged hours badge

Each FV label can show an amber `⚠ N tasks 0h logged` badge. It fires when there are tasks in active statuses (`In Progress`, `Ready`, `In QA`, `Pre-Prod In Progress`) but `hours === 0`.

**Why it matters:** if the team is working on a ticket but hasn't estimated it, the ETA model treats that ticket as zero remaining hours — projection becomes optimistic. The badge surfaces the data-hygiene gap so the right person can add an estimate.

**Generator-side implication:** none. Counted client-side from the same task data the generator already emits.

---

## 19. Shared infrastructure

### `jira_client.py`
Both generators import `jira_get` / `jira_post` / `jira_jql` from here. Single source of truth for HTTP auth (Basic auth via env vars `JIRA_EMAIL` and `JIRA_API_TOKEN`) and JQL pagination.

The pagination uses `POST /search/jql` (the v3 endpoint) because the legacy `GET /search` returns 410 Gone. Token-based pagination.

### Workflows
Two separate GitHub Actions workflows, each with its own cron and `workflow_dispatch`:

- `.github/workflows/exec_dashboard.yml` — runs `build_dashboard.py`, commits `index.html`. Cron at 13:00 UTC.
- `.github/workflows/v2_timeline.yml` — runs `v2_timeline.py`, commits `v2-timeline.html`. Cron at 13:15 UTC (staggered).

Both share a `concurrency: refresh-dashboards` group so their pushes can't race. Both `git pull --rebase` before pushing as a belt-and-braces safety net.

**Why two workflows instead of one:** so a bug in the V2 timeline generator can't block the exec dashboard refresh, and either can be manually rerun in isolation when you're debugging.

### Tab nav
Both pages have a shared header with two tabs (Overview / V2 Timeline). The HTML/CSS is 5 lines, duplicated in both pages rather than abstracted — small enough that a shared module isn't worth the indirection.

---

## 20. Where to look when something on the dashboard looks wrong

| Symptom | Likely cause | Where to debug |
|---|---|---|
| Release missing from Shipped section | Fix-version `released=false` AND no Release-ticket resolution OR Release-ticket has empty `fixVersions` | Check version in Jira's Releases view; check the Release ticket's fixVersions field |
| Release shown with wrong health | ETA tier picked the wrong inputs | Print `stats` dict for that FV — see which Tier matched in `compute_eta` |
| ETA shown as `May 19 2027` (year visible) | Tier hit the 365-day cap; underlying velocity is very low | Check `velocity_secs_per_day` for that FV — usually means no recent done tickets with `timeoriginalestimate` set |
| Unestimated callout is wrong | Subtask-or-parent rule's view of the issue tree | Check the FV in Jira — does the parent have subtasks? If yes, every subtask without `timeoriginalestimate` counts |
| V2 Timeline status label looks wrong | Open-ticket status mix shifted past the 50% threshold | Run the §16 status-distribution query in `V2_TIMELINE_EDGE_CASES.md` against that FV |
| V2 Timeline Scope tab shows a Story instead of an Epic | Direct-parent grouping in `compute_scope` — see `V2_TIMELINE_EDGE_CASES.md` §17 | Look up the Story in Jira; its `parent` field will point at the Epic. Grandparent resolution is a planned refinement |
| V2 Timeline Sprint Activity is empty everywhere | `SPRINT_LOGS = {}` by design — Tempo/worklog integration is deferred (§18) | Not a bug. Awaiting the follow-up PR that wires up the worklog endpoint |
| Same dashboard issue is "Mark Released in Jira" but the version IS released | The data refresh is stale | Manually trigger the workflow; or wait for the next cron |
| A release vanished from Active and isn't in Shipped either | Fix-version became `released=true` and no PRF override (also empty in Shipped section if shipped_date isn't current month) | Check the version's `released` flag and `releaseDate` in Jira |

---

*Last updated: 2026-05-25. Keep this file in sync when you ship a logic change — touch the section, leave the rest. If a heuristic gets resolved or replaced, mark it `[Resolved]` and keep the explanation so future-you can see the reasoning trail.*
