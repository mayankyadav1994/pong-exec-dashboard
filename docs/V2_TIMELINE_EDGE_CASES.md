# V2 Release Timeline — Edge Cases & Heuristics Glossary

**Purpose.** When `v2_timeline.py` calculates dev-complete dates, bottlenecks, queued hours, and projected timelines from raw Jira data, the math is *mechanically correct* but doesn't always match how a producer or release manager would read the situation. This glossary captures the cases where human judgment currently outperforms the auto-calculation, so when the AI/automation layer is built, these heuristics can be encoded explicitly rather than rediscovered after a missed forecast.

Each entry follows the same shape: **what the auto-calc does → why a human reads it differently → suggested refinement when ready to automate**.

---

## 1. Bottleneck attribution skews toward shared resources

**Auto-calc behaviour.** Bottleneck = person with the latest `(open_hours + queued_hours) / daily_rate` value. Because `queued_hours` sums all open hours on higher-priority FVs, anyone touching multiple releases (Rejosh, Krupa) accumulates queue debt and almost always wins the "latest" comparison.

**Observed in the simulation.** Rejosh ends up bottleneck on 5/6 fix versions because he touches every release. In the hand-tuned prototype, the producer knew Krupa's server-testing tasks were the actual gate on PT 13.30 and HHR 3.00 — Rejosh's queued hours were real but his tasks weren't on the critical path for those releases.

**Why the human reads it differently.** A producer asks "what *type* of work is blocking this release?" not "who has the most hours stacked up?" For PT 13.30 and HHR 3.00 the answer is server testing, and Krupa is the only person who does that. Rejosh's queue is real but parallelizable; Krupa's task is sequential and exclusive.

**Suggested refinement.**
- Track per-task **critical path** independent of resource queue. A task is critical if it has no parallel substitute (e.g. server testing) and downstream work depends on it.
- Allow per-FV bottleneck pinning in a config file (`bottleneck_overrides: {"V2 PT 13.30": "Krupa Kanani"}`) that takes precedence over the auto-calc.
- Long-term: classify tasks by category (server, game port, system, integration) and let each FV declare which category is its gate.

---

## 2. Queued hours assume strict priority ordering

**Auto-calc behaviour.** `queued_hours[person, fv_n]` = sum of person's open hours on fv_1 ... fv_{n-1}. Implies a person finishes all work on FV-1 before touching FV-2.

**Why this is wrong in practice.** People context-switch. A dev with 40h on FV-1 and 10h on FV-2 doesn't actually finish FV-1 then start FV-2; they interleave based on priority pressure, blockers, and what's reviewable that day. The "queue" overstates serial dependency.

**Visible symptom.** Projected dev-complete dates on lower-priority FVs slide far into the future even when those FVs have small remaining scope, because the upstream queue dominates the math.

**Suggested refinement.**
- Introduce a **parallelism factor** per person (e.g. Rejosh: 0.6 → only 60% of his queue blocks downstream work, the rest is parallelizable).
- Or: model "effective queue" as a weighted decay across FVs, not a hard sum.
- Or: ask the producer once a week to confirm/override the queue distribution. Cheaper than getting the model right.

---

## 3. `0h remaining` does not mean "done"

**Auto-calc behaviour.** Tasks with `timeestimate=0` contribute zero to remaining hours. If all of a person's open tasks have 0h logged, their projected done date is "today."

**Why this is wrong.** Many in-progress tasks have no logged estimate — either the dev forgot, the task was carried over without re-estimation, or the original estimate was burned down to zero but the work isn't actually finished. The prototype's note explicitly flags this: *"Many tasks show 0h remaining — estimates may not be logged."*

**Visible symptom.** A fix version with 15 "In Progress" tasks and zero total remaining hours will project a dev-complete date of today. False optimism.

**Suggested refinement.**
- Flag any task that is `In Progress` or `Ready` with `timeestimate=0` as **"unlogged estimate"** and surface in the UI.
- Apply a **default fallback estimate** based on issue type (e.g. Dev Subtask default = 8h, Game port = 16h) only for unlogged tasks.
- Threshold alert: if >X% of a FV's open tasks have 0h, mark the dev-complete projection as "low confidence."
- Long-term: weekly Slack/email nudge listing unlogged tasks per assignee.

---

## 4. QA duration is fixed in code; QA team capacity isn't modelled

**Auto-calc behaviour.** QA = `qaWeeks × 5 × 8` work hours starting the moment dev completes. Assumes infinite QA capacity and immediate handoff.

**Why this is wrong.** QA is a real team with finite parallel slots. If three fix versions all hit dev-complete on the same week, the third one waits for QA bandwidth. The model treats them as independent.

**Visible symptom in the simulation.** SW 16, PT 13.30, and HHR 3.00 all project Dev → Jun 4 / Jun 25 / Jun 25, with QA starting immediately on those dates. In reality, the QA team has to triage.

**Suggested refinement.**
- Model QA as a shared resource pool with a parallel-slot count (e.g. "QA team can run 2 fix versions concurrently").
- Add **QA queue overflow**: if more FVs land on the same week, the third gets a wait before its QA bar starts.
- Reflect QA's own holiday/PTO calendar separately from dev.
- Even simpler stopgap: a single config `qa_parallel_capacity = 2` and the renderer pushes overflow downstream.

---

## 5. Holiday list is hardcoded, single-region

**Auto-calc behaviour.** `HOLIDAYS = ['2026-05-18']` in the template JS. Single global list, Canada-centric.

**Why this is wrong.** Pong has team members across regions (offshore dev, on-site QA in different countries). A US holiday might affect QA but not offshore dev. A Canada holiday might affect Rejosh but not Asif.

**Suggested refinement.**
- Per-person holiday calendar, derived from team metadata.
- For QA bar calculation, use the QA team's holiday set.
- Pull from a single source-of-truth `holidays.yaml` keyed by region, with people tagged by region.

---

## 6. Sprint boundaries are decorative, not enforced

**Auto-calc behaviour.** Sprint chips render on the axis but the math doesn't snap to sprint boundaries. A dev-complete date of Jun 4 lands mid-sprint S3 and that's just where the bar ends.

**Why this is wrong, sometimes.** Some releases have a hard rule: "must wrap before sprint end." Others have soft sprint commitments where mid-sprint is fine. The current model treats them all the same.

**Suggested refinement.**
- Add an optional `snap_to_sprint: true` flag per FV. If true and projected dev-end falls past the target sprint's end date, mark the FV as "slipping."
- Surface a "this release missed its sprint commitment" indicator distinct from generic at-risk red.

---

## 7. Cross-FV person identity matching

**Auto-calc behaviour.** Person matching is by Jira `displayName`. "Rejosh Samuel" on FV-1 = "Rejosh Samuel" on FV-2.

**Why this can be wrong.** Jira display names occasionally change (people get married, accounts get renamed, name capitalisation drifts). The queue calc breaks silently if "Rejosh Samuel" appears as "Rejosh Samuel " (trailing space) or "rejosh samuel" on a different ticket.

**Suggested refinement.**
- Match by `accountId` (stable) not `displayName` (mutable).
- Normalize display names in a lookup table for rendering only.
- Log a warning when the same accountId appears under multiple displayName variants — surfaces upstream Jira data hygiene issues.

---

## 8. "Release" and "Merge" exclusion via summary regex

**Auto-calc behaviour.** Excludes any task whose summary matches `Release` or `Merge` (case-insensitive).

**Why this can over-exclude.** A legitimate task titled "Server release configuration cleanup" or "Pre-release performance test" gets dropped. False negative.

**Why this can under-exclude.** A release admin task titled "Prepare Builds" or "Deployment" doesn't match the regex but is the same category of work the exclusion is trying to drop.

**Suggested refinement.**
- Use **parent-task identity** instead of summary regex. The "Release" and "Merge Code" parent tasks in V2 have stable issue keys per fix version; exclude any subtask whose parent is on the known release-admin list.
- Maintain a known-bad summary list rather than a single regex: `["Prepare Builds", "Deployment", "Prepare Release Notes", "Update Payout Sheet", "Merge Code — *"]`.
- Long-term: tag release-admin tasks with a Jira label (e.g. `release-admin`) and exclude by label, not summary.

---

## 9. `In QA` status at the FV level vs. task level

**Auto-calc behaviour.** A fix version's "status" (In Dev / In QA / Scheduled) is currently set manually per FV in the prototype. The auto-calc has no notion of when to flip an FV from "In Development" to "In QA."

**Producer heuristic.** "When 80%+ of dev tasks are closed and the remaining ones are either In QA or non-blocking, it's an In QA release."

**Suggested refinement.**
- Auto-derive FV status: `% open tasks < threshold AND open dev hours < threshold AND has_blockers = false` → flip to In QA.
- Surface the flip as a notification ("V2 SW 15.00 moved to In QA today") so the team sees the transition.
- Or: trust Jira's own fix-version "released" / "in progress" flag and stop reinventing the wheel.

---

## 10. "TBD" / unscheduled fix versions

**Auto-calc behaviour.** Every FV in scope gets a dev bar from `devStart` (defaulting to today) and a QA bar after. No concept of "this FV isn't scheduled yet."

**Why this is wrong.** PT 14.00 in the prototype is labelled "Scheduled" but its devStart is set to today anyway, making the bar look like it's actively in progress. In reality, no one has been told to start work on it yet.

**Suggested refinement.**
- Add a third devStart state: `null` (already in QA), date (active), `"unscheduled"` (no start date assigned).
- Render unscheduled FVs with a dashed/ghost bar floating at the far right of the timeline, or a different colour entirely.
- Don't auto-calculate a dev-complete date for unscheduled FVs — show "TBD" instead.

---

## 11. Same person on dev + others (creative/sound/math)

**Auto-calc behaviour.** `devPeople` and `otherPeople` are separate arrays. The prototype occasionally lists the same person in both (e.g. Rejosh Samuel on V2 HHR 3.00 has a `Math` task in `otherPeople`).

**Risk.** Their `queuedHours` is calculated from `devPeople` only. A person who's also doing math/help-pages work has hidden load the model doesn't see.

**Suggested refinement.**
- Calculate `queuedHours` from a person's *total* open hours across both groups, not just dev.
- Or: keep groups separate but warn when the same name appears in both, so the producer knows the projection is approximate.

---

## 12. Blockers are invisible to the timeline

**Auto-calc behaviour.** No notion of `blocked` / `blocker` issue links. A task with 0h remaining and `In Progress` status that's waiting on a Blazesoft dependency looks identical to one that's actively being worked.

**Producer heuristic.** "If this ticket has a blocker link or a comment saying 'waiting on X', it's not really 'In Progress' — it's stuck."

**Suggested refinement.**
- Fetch `issuelinks` for each task and check for `blocks` / `is blocked by` relationships.
- Render blocked tasks with a different visual treatment (striped bar, red icon).
- Don't count blocked-task hours toward dev-complete projection until unblocked.
- Surface aggregate blocker count per FV at the row level (the exec dashboard already does this with "🔴 1 blocker" — bring the same data into the timeline).

---

## 13. Estimate inflation/deflation over time

**Auto-calc behaviour.** Trusts `timeestimate` at face value as of right now.

**Producer reality.** Estimates drift. A task that started at 40h and is now showing 38h after two weeks of work is almost certainly underburned — the dev hasn't been logging time. The "true" remaining is closer to the original estimate minus elapsed effort.

**Suggested refinement.**
- Track `timeoriginalestimate` vs. `timeestimate` vs. `timespent`.
- Flag tasks where `timespent + timeestimate ≠ timeoriginalestimate` and the delta is large — these are estimates that haven't been groomed.
- For unlogged-time scenarios, project remaining as `max(timeestimate, timeoriginalestimate - timespent)`.

---

## 14. "Unassigned" as a person

**Auto-calc behaviour.** Tasks without an assignee get bucketed under "Unassigned" as if it were a person. The prototype shows this on P2P 16.00 with 1 unassigned task.

**Why this is wrong.** Unassigned work isn't a resource with availability — it's *missing* resource allocation. Treating it as a 75%-availability person artificially extends the timeline as if someone were working on it.

**Suggested refinement.**
- Surface "Unassigned" as a warning, not a resource. "FV has N hours of unassigned work — needs allocation."
- Optionally: distribute unassigned hours proportionally across the existing devPeople for projection purposes, with a visible disclaimer.
- Block the FV from going green until all hours are assigned.

---

## 15. Cross-release dependency vs. cross-release queue

**Auto-calc behaviour.** Treats all cross-release work as a serial queue (FV-2 starts after FV-1 finishes for shared people).

**What's missing.** Some work on FV-2 *depends on* deliverables from FV-1 (e.g. a server API change that FV-2 game ports rely on). That's a hard dependency, not just a queue. Other FV-2 work is fully independent and could parallelize.

**Suggested refinement.**
- Detect cross-FV dependencies via Jira `is depended on by` links.
- Render dependency arrows between bars when an FV's work is gated on another FV's deliverable.
- Calculate two projections per FV: optimistic (no cross-release blockers) and realistic (with dependencies enforced).

---

## 16. Mechanical status classifier misses "In QA" and "Scheduled" — [Resolved May 20, 2026]

**Original auto-calc behaviour.** `classify_status_label()` in `v2_timeline.py` originally read the set of open ticket statuses and emitted:
- `In QA` only when **every** open ticket was in a QA-like status
- `Scheduled` only when **every** open ticket was `New`
- `In Development` otherwise

**Why it failed.** A few stray tickets in non-matching statuses were enough to push a release into "In Development" even when it was effectively In QA or Scheduled.

**Live evidence (May 20, 2026).**

| FV | Producer label | Open ticket mix | Original output |
|---|---|---|---|
| V2 SW 15.00 | In QA | 9 In QA (50%) · 7 In Progress · 2 To Do | In Development |
| V2 PT 14.00 | Scheduled | 64 New (64%) · 14 Ready · 12 In Progress · 10 To Do | In Development |

For PT 14.00, "New"-status tickets held 444h / 848h of original-estimated work (52%). The "In Progress" tickets were mostly Rejosh's exploratory items with 0h estimate — placeholders, not real dev activity.

**Signals that *didn't* work.**
- Jira fix-version `startDate` / `releaseDate` — PT 14 had `startDate=2026-04-07` and `releaseDate=2026-08-14` set, yet no work had begun. Dates are set by planning, not by activity.
- Release-type issue workflow status — only 1 of 6 V2 FVs had a Release ticket (SW 15), and it was on the dev-done release, not the unstarted one. So Release-ticket status couldn't generally distinguish Scheduled from In Dev.

**Resolution — 50% threshold.** Replaced the strict subset check with a majority check on the open-ticket status mix:

```python
n_qa  = sum(1 for t in open_tasks if t["status"] in QA_LIKE)
n_new = sum(1 for t in open_tasks if t["status"] == "New")
if n_qa  / n_total >= 0.5: return ("In QA",      None)
if n_new / n_total >= 0.5: return ("Scheduled",  TODAY.isoformat())
return ("In Development", TODAY.isoformat())
```

QA check runs first so a hypothetical 50/50 QA/New tie lands on "In QA" (QA is the later phase). Inclusive `>= 0.5` so SW 15.00 at exactly 50% stays in QA.

**Self-correcting behaviour.** When PT 14.00's team is told to start, "New" tickets will move to "In Progress" / "Ready" / "To Do". As soon as "New" share drops below 50% the label auto-flips to "In Development" on the next refresh. Same on the other end: once a release's QA share crosses 50% it auto-flips to "In QA". No manual touch required.

**Previously-shipped stopgap (now removed).** A `force_status` field on `FV_CONFIG` entries used to pin SW 15 → "In QA" and PT 14 → "Scheduled". Both overrides have been deleted; the heuristic produces the correct labels on its own.

---

## 17. The producer's tacit knowledge isn't in Jira

**Meta-issue.** Most of the heuristics above ("Rejosh isn't really the bottleneck on PT 13.30, Krupa is", "PT 14.00 hasn't actually been kicked off yet", "Blazesoft dependency is blocking everything") live in the producer's head and side conversations, not in Jira fields.

**The honest answer.** No fully-automated model will get this right without that context. The realistic ceiling for the auto-calc is ~80% accurate, and the last 20% needs a human in the loop.

**Suggested approach when AI layer ships.**
- Add a **"Producer Notes"** layer per FV: free text the producer maintains weekly that overrides specific projections.
- Daily build pipeline: render auto-calc → producer sees it → producer adjusts overrides → re-render with overrides applied → that's the published version.
- The AI layer's job isn't to replace producer judgment — it's to surface the *delta* between auto-calc and producer reality, so we can see where the model needs work.
- Track override frequency per FV / per heuristic over time. Heuristics that get overridden often = candidates for the next refinement pass.

---

## How to use this glossary

When the AI layer / automation work begins:

1. **Day 1**: Read this whole file. Don't try to implement everything; pick the 2–3 highest-impact entries.
2. **Each refinement**: Add a `## N. [Resolved]` section at the bottom describing what was implemented, when, and the observed accuracy improvement. Don't delete the original entry — keep the history.
3. **New edge cases**: When the model surprises someone (producer disagrees with the projection, a release slips for a reason the model missed), add a new entry here *before* fixing the code. Capture the reasoning while it's fresh.
4. **Weekly review** (first month after AI layer ships): scan recent overrides, identify patterns, decide whether they warrant a code change or a glossary entry.

---

## Open questions still to answer

These don't have clear refinements yet — they need producer input or further investigation:

- **How are "Pre-Prod In Progress" tasks weighted?** The status exists in the prototype but it's unclear whether these count toward dev-complete or are post-dev work.
- **What's the policy for `New` status tasks?** Are these committed scope or wish-list? PT 14.00 is full of them.
- **Should `Bug` issuetypes ever count?** The current exclusion is total, but a critical bug found in QA could genuinely push a release.
- **What does "Story" mean in V2 vs. Dev Task?** The JQL includes both but the producer's mental model may differ.
- **Wwise updates touch every FV — is there an implicit dependency on Aleksey?** This shows up as parallel work in the model but feels like it should serialize.

---

*Created May 19, 2026 · Living document — add to it whenever the model surprises someone*
*See also: V2_RELEASE_TIMELINE_KNOWLEDGE.md for the baseline rules; CLAUDE_CODE_PROMPT.md for the daily build pipeline*
