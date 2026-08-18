# Game Pipeline — TIMELINE tab enhancements (spec)

Status: **agreed design; partly deferred.** Scope is the per-game **TIMELINE tab**
in the Game Pipeline Lifecycle Detail (`tlPane` in `dashboard.js`), which is shared
by the **V2** and **iGaming** pipelines — one implementation covers both. No changes
to the standalone Release Timeline dashboards.

Today the tab renders one row per discipline in `LANE_ORDER`
(`ART / DESIGN / MATH / DEV / SOUND / QA`). Each bar spans the discipline's **sprint
range** (first sprint start → last sprint end); a 🎯 tick marks the discipline's
latest Jira due date; the right column shows `spent / est` hours. A discipline with
no sprints renders no bar (blank).

---

## Feature A — REVIEW row  ·  STATUS: DEFERRED (build when tickets are restructured)

Add a **REVIEW** row to the TIMELINE tab, inserted **before QA**:
`ART / DESIGN / MATH / DEV / SOUND / REVIEW / QA`. It shows whether a game is in
review and when the review work is planned to finish, rendered like the other lanes
(bar + 🎯 target + `spent / est`).

### Detection (build side, `build_jira_data.py`)
A ticket is a **Review** ticket when, within a game's ticket subtree, its **summary
contains "Review"** (case-insensitive) — regardless of issue type. Review work today
is spread across multiple issue types (confirmed against live Jira):

| Example ticket | Summary | Issue type | Currently lands in |
|---|---|---|---|
| IG-5630 | `Gen2 Game: 3 Chilli Riches - Review` | Story | dev |
| IG-5606 | `… - Review & Refinement` | Sound Subtask | sound |
| IG-7256 | `Fruit Splitter - Review [Justin]` | Dev Task | dev |
| IG-5613 | `… - Review & Refinement - Math` | Math Subtask | math |

"Code review" tickets that sit under a **Release**/**Merge** epic are release-admin
work and are already excluded by the existing admin-parent filter, so they will not
leak into a game's Review row.

### Hours semantics — **EXTRACT (hours move, no duplication)**  ← decided
Review tickets are **removed** from their current discipline (dev/math/sound/…) and
counted **only** in the Review lane. There is **no double-counting**.

Because disciplines feed more than the timeline, extraction necessarily changes:
- the **HOURS tab** (dev/math/sound totals drop; a new Review total appears),
- the **burndown department lanes**,
- the **forecast** (which sums discipline est/spent).

That cross-dashboard ripple is intended in the end state, but it is only clean once
Review is its own thing.

### Why deferred
Right now Review tickets are embedded inside dev/math/sound tickets, so extracting
them mid-structure would scatter special-case logic across the build. The plan is to
**restructure the tickets so all Review work lives in a dedicated Review story**
(owner: producer). Once that lands, the Review lane falls out naturally (a normal
issue-type → lane mapping) and extraction is clean. **Build Feature A then.**

Interim options if it's needed before the restructure (not chosen): a timeline-only
*additive* Review lane that duplicates hours — rejected because the decision is "no
duplication."

---

## Feature B — Ghost (planned, not-started) bars + editor drag  ·  STATUS: ready to build

Purpose: today a not-started lane is **blank**, so you can't see that its work is
estimated/planned. Show a **planned window** instead, and let editors set it.

### Behavior
- **Trigger:** a lane with **no sprints AND 0 hours logged** but estimated work
  renders a **hollow / dashed "ghost" bar** for its planned window. As soon as
  sprints are attached or any hours are logged, it becomes the **solid** colored bar
  (today's behavior). Applies to every lane, including REVIEW.
- **Planned dates:** an **editor-set override** `{start, due}` per **game (Jira key)
  + lane**, set by **dragging the bar's start and end handles**. Persisted to the
  shared plan and published via **Save-as-default** (subject to the stale-publish
  guard — last-writer, not append-only). Non-editors see the ghost bar **read-only**.
- **Default before any drag:** anchor the ghost to the lane's Jira due date
  (`target_date`) as the end, with a sensible default width, so it's visible; the
  editor then refines by dragging.
- **Gating:** drag handles only for signed-in editors (`getGhEditor()`), consistent
  with the override-banner gating.
- **No auto-dependencies in v1** — manual drag only (e.g. drag DEV to start after
  ART's due). Auto-chaining (DEV start snaps to ART end) is a possible later add-on
  built on the same planned-window primitive.

### Storage model
`timeline_plan[<jira>][<lane>] = { start: "YYYY-MM-DD", due: "YYYY-MM-DD" }` in the
shared plan (`plan-<key>.json`), Jira-keyed for rename stability. Editor-only writes,
published via Save-as-default.

---

## Decisions log
- **Scope:** TIMELINE tab only; both pipelines via shared code. Release-Timeline
  unification set aside for now.
- **Review hours:** EXTRACT (move, no duplication).
- **Review build timing:** DEFERRED until Review tickets are restructured into a
  dedicated story.
- **Review matching:** summary contains "Review" (case-insensitive), within a game's
  subtree; admin-parent tickets excluded.
- **Ghost "not started":** no sprints AND 0 hours logged.
- **Dependencies:** manual drag only in v1.

## Open questions
- **Feature B timing:** it does **not** depend on the ticket restructure and could
  ship independently now. Build now, or bundle with Feature A after the restructure?
