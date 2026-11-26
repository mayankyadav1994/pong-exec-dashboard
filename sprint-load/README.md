# Sprint capacity dashboard

`sprint_load.py` pulls the open sprints for four Jira boards and writes `dashboard.html`, a single self-contained page you can open locally or host anywhere. All data is inlined at build time. The page makes no network calls when it renders, except to load the Google Fonts typeface.

**It rebuilds itself daily.** `.github/workflows/sprint_load.yml` runs at 06:30 UTC. It also runs on any push that changes `sprint_load.py` or `template.html`, and on demand from the Actions tab ("Sprint Load" → *Run workflow*). It commits `dashboard.html` and `bias.json` back to main, and that push deploys the page. If any fatal check fails, the run fails and nothing is committed, so the page keeps the last good build.

To refresh between scheduled runs, trigger the workflow rather than running the script locally and pushing. A local run updates your own copy of `bias.json`, which then diverges from the copy in the repo.

```
python sprint_load.py            # normal run
python sprint_load.py --seed     # first run only: seed bias.json from last closed sprints, then build
python sprint_load.py --allow-empty   # build even if a board has zero tickets (between sprints)
```

Needs Python 3.11+ and `requests` (plus `tzdata` on Windows, which has no built-in time-zone database). Credentials come from the environment: `JIRA_EMAIL` and `JIRA_TOKEN` (an Atlassian API token). `JIRA_API_TOKEN` is accepted as a fallback because the other Pong dashboards use that name.

Files beside the script:

| File | What it is |
|---|---|
| `template.html` | Page shell. Contains `__SNAPSHOTS__` exactly once; the script swaps in the data. |
| `dashboard.html` | Output. Rewritten on every successful run. |
| `bias.json` | Estimate-bias memory, committed to the repo and updated by every scheduled run. **Don't delete it.** See *Estimate bias*. The repo is public, so this file is too. It holds the same per-person factors the page shows, plus their earlier values. |

---

## The boards

| Board | Jira board | What it counts |
|---|---|---|
| DAMS | 349 | `project IN (IG, V2, CSS)`, assigned to the **12-person DAMS roster**, issue type in the DAMS set (Creative / Math / Sound / Design tasks and subtasks, Bug, Story, Live Issue) |
| iGaming | 250 | `project = IG`, everyone **except** the roster. Also excludes Ehsan Mamakani, Yves Griber and Boris Itskov on this board only. |
| V2 | 316 | `project = V2`, everyone except the roster |
| Cloud Services | 217 | `project = CSS`, everyone except the roster |

The roster is excluded from the project boards so that nobody is counted twice. The three iGaming exclusions apply to iGaming only. Those people belong to other teams, and their own boards show them normally.

The DAMS board has no sprint of its own. Its tickets sit in IG Sprint *n* and V2 Sprint *n*, and the page shows both. If those sprints' end dates drift apart by more than two days, the page warns you, because the 70h capacity assumes a single sprint window.

### The roster is hand-maintained

`ROSTER` at the top of `sprint_load.py` lists 12 Jira account IDs, copied from board 349's saved filter. **If someone is missing from the DAMS board, or appears on a project board when they shouldn't, check the roster first.** Every roster and exclusion ID is verified against Jira at the start of each run. Jira answers a query on an unknown account ID with an empty result rather than an error, so without that check a typo would silently drop a person.

Capacity, thresholds, issue-type lists and exclusions are all constants in the same block.

---

## Current load tab

Each row is one person on one board, grouped by discipline. Every group ends with a subtotal measured against *people × capacity*.

| Column | Meaning | Source |
|---|---|---|
| **Person** | Current assignee | `assignee`. The link opens their tickets on this board in the Jira issue navigator. |
| **Tickets** | Open-sprint tickets they hold on this board. "*n* unestimated" = tickets with no original estimate. | count; `originalEstimate IS EMPTY` |
| **Assigned** | Sum of original estimates | `timeoriginalestimate` |
| **Spent** | Sum of logged time | `timespent` |
| **Remaining** | Sum of remaining estimates on tickets that are **not Closed** | `timeestimate` |
| **Load bar** | Remaining (or Assigned) against capacity. The black tick is the capacity line. Blue = under 85%, amber = 85% up to the line, red = over. | |
| **Verdict** | *Over by N h (×multiple)*, *At capacity* (within ±3.5h), or *Room for N h* | |

Every figure links to the Jira issue navigator, scoped to that person and board plus the matching predicate (`originalEstimate > 0`, `timeSpent > 0`, `remainingEstimate > 0`, `originalEstimate IS EMPTY`). The Remaining link also excludes Closed tickets so it matches the number. Every link also leaves out tickets that were already Closed when the sprint started (see below).

### Why Remaining, not Assigned

Assigned is the size of the work handed out. It never goes down. A person who has finished 200h of a 250h load still shows 250h assigned. Remaining is the estimate of work still to do, which is what has to fit into the time left. The toggle exists so you can see how much was handed out, but the verdict only means something on Remaining.

**Closed tickets count as 0 remaining.** Some Closed tickets still carry a remaining estimate. No work is left on them, so those hours are excluded. The row gets a ⚑ chip and the board gets a warning that lists the keys, so someone can zero them in Jira.

### Tickets already Closed when the sprint started are left out

Some tickets sit in the open sprint even though they were Closed before it began. They aren't this sprint's work, so they are dropped from **every** figure (Tickets, Assigned, Spent and estimate bias) and listed in a notice on each board. There are two causes:

- **Subtasks of an unfinished parent.** Jira always keeps a subtask in its parent's sprint. When a Story rolls over, all of its subtasks roll with it, including the ones closed months ago. A Story that has rolled through seven sprints drags its closed subtasks through all seven. The fix in Jira is to close or split long-running Stories rather than letting them roll.
- **Standard tickets moved into a sprint before it started.** For example, when an earlier sprint is completed, its open tickets get moved into a sprint two ahead. They are finished in the meantime and are already Closed when that sprint starts. The fix is to move unfinished work into the next sprint, or remove Closed tickets from a sprint before starting it.

The test is the ticket's status history: `status WAS "Closed" ON "<sprint start>"`. It deliberately does **not** use the resolution date. In this Jira, Resolution is set when Dev moves a ticket to *Ready*, well before QA closes it. A resolution-date test would therefore drop genuine carryover: tickets that were in Ready For QA when the sprint rolled over and were closed during the current sprint.

JQL reads that date in the viewer's Jira time zone. The script converts each sprint start to the token user's time zone, so the figures and the links agree for that user. For someone in another time zone, the links can differ only for tickets closed within a few hours of the sprint starting.

### Open work under a cancelled or on-hold epic is left out

If a ticket's epic is **Canceled or On Hold in Jira**, or the **Game Pipeline board** shows the game as On Hold or Cancelled, its open tickets are not planned work. They are dropped from every figure and listed in a notice on the board, with their epic, the reason and the hours. Closed tickets under such an epic still count, because that work happened and still measures estimate bias.

The rule lives in `shared/plan_rules.py`, and the People page and Team Board use the same one. The Game Pipeline status is the one the board shows: the PMO's status override, unless Jira has since moved the game past it.

### Capacity model

**7 planned hours per person per working day**, which is 70h over a standard 10-day sprint (`HOURS_PER_DAY`). A separate 10h allowance for unplanned work sits *outside* capacity and is never deducted.

**Capacity shrinks as the sprint goes on.** Remaining is measured against the hours **left**: 7h × the working days from the build date to the end of the person's sprint, counting the build day. The scheduled build runs at 06:30 UTC, before the working day starts. On the last day someone has 7h; after the end date they have none, and anything still open shows as carrying over. Assigned, the whole sprint's work, is measured against the whole sprint.

- **Working days** are Monday to Friday, minus public holidays. The holidays come from the `holidays` lists in `config/igaming.json`, `config/v2.json` and `config/css.json`, the same lists the release timelines use. The union of the three is taken. If a sprint runs past the last listed holiday, each board shows a warning to add next year's.
- **A sprint's working days** run from its start date through its end date, inclusive, in the Jira user's time zone. End dates are entered inconsistently: IG sprints end on a Friday at 00:00, V2 sprints on a Monday at 23:00. So a V2 sprint counts its hand-over Monday, and the next V2 sprint starts counting the day after.
- **Which sprint a person is measured against:** the sprint holding most of their remaining hours. This matters on DAMS, where people work in both the IG and the V2 sprint and the two end on different days.

The capacity box at the top changes the hours per day for the whole board. The change is remembered in your browser only.

---

## Next sprint tab

What each person can take on next sprint, given what they're carrying over.

| Column | Meaning |
|---|---|
| **Carryover** | **Projected**: the remaining work, at the person's bias, that won't fit into the hours left this sprint. Shown in estimate hours, with today's remaining underneath. |
| **Bias** | Estimate-bias factor (see below), with its source: *measured*, *carried* or *inferred* ⚑ |
| **True load** | Carryover × bias: how many real hours the carryover will actually take |
| **Capacity** | 7h × the next sprint's working days, public holidays excluded. The dates come from the board's next future sprint in Jira. If it has no dates yet, two weeks after the current sprint's end is assumed. Editable per person; lower it for PTO. Edits are kept in your browser only. |
| **Intake** | How much new *estimated* work fits |

```
carryover (real hours) = max(0, remaining × bias − hours left this sprint)
room   = next-sprint capacity − carryover (real hours)
intake = room > 0 ? room ÷ bias : 0
```

Intake is divided by bias because new work arrives as *estimates*. Someone with a bias of 1.5 who has 30 real hours free can take on 20h of estimate. If room is negative, the row shows the overflow in real hours plus the *estimate* hours that would have to move off them (overflow ÷ their bias).

### Estimate bias

`bias = closed spent ÷ closed estimate`, from that person's tickets on this board that were **Closed during this sprint**. Tickets that were already Closed when the sprint started don't count. 1.0 means estimates are accurate, and 1.4 means work takes 40% longer than estimated. Each row records which of three sources it used:

| Source | When | Meaning |
|---|---|---|
| **measured** | ≥ 40h of closed original estimate on this board | Their own current data |
| **carried** | Not enough closed work now, but `bias.json` holds a factor from an earlier run (same board first, otherwise their best-evidenced other board) | Their own past data |
| **inferred** ⚑ | Neither of the above | Median of measured + carried factors for the same discipline across all boards. If there are none, 1.0 as an explicit placeholder. Flagged in the UI. |

Every measured factor is written to `bias.json` after each run, and the scheduled job commits it. **That file is load-bearing.** Early in a sprint almost nothing has closed, and without carry-forward every person would collapse to the discipline median. Don't delete it casually. `--seed` fills it once from each board's most recently closed sprint, and never overwrites an existing entry.

Checks on bias:

- **Some closed tickets are left out of bias**, because they don't measure one person's estimating. A board notice lists each one with its reason. They still count in Tickets, Assigned and Spent.
  - **More than 10h logged on the ticket in one day.** Several people log on it, as with team test rounds assigned to a QA lead. Jira can't say who logged, because every worklog is authored by the Tempo bot, so this is the available signal. A single person's long day can trip it.
  - **Logged time more than 10× the estimate.** These are time buckets, such as a general test-case review ticket that collects a team's time over many sprints.
- A measured factor outside 0.25–4.0 is **rejected** (not used, not saved) and flagged. It usually means time was logged on the wrong ticket.
- If more than 10% of a person's closed spent time sits on tickets with **no** original estimate, the row is flagged. That time inflates the factor.
- Only status `Closed` counts. Other done-category statuses (e.g. V2's *Signed Off*) are listed in a warning and left out of bias.

### Discipline governs redistribution

How each person's discipline is decided:

- **Project boards: Jira group first.** Membership of **QA Team** means QA, and **Development** means Dev (`DISCIPLINE_GROUPS` in the script). Ticket types are a poor signal here. QA verify and close Bugs, so a tester's open-sprint tickets are mostly Bugs, which would otherwise vote Dev. A notice lists everyone whose group overrode their ticket types.
- **Fallback: majority vote of issue types.** This applies to anyone in both groups or neither, such as admins and PMs. Dev covers Dev Task, Dev Subtask, Story, Bug, Enhancement, CR and Live Issue. QA covers QA Task and QA Subtask. Release covers Release and Release Subtask. DAMS types are matched by prefix if they appear. These rows carry a ⚑ *discipline from tickets* chip.
- **DAMS:** issue-type prefix match only: Math / Sound / Creative / Design. Bug, Story and Live Issue don't vote. The Jira groups don't separate Math from Creative.

Ties in the vote are broken in the fixed order Math → Sound → Creative → Design → Dev → QA → Release and flagged. A person with no group and no classifiable type goes into *Unclassified* with a bias of 1.0, also flagged.

Work can only move between people who can do it. Spare Creative hours don't help an overloaded Math designer. So each discipline gets a plain statement:

- **Can absorb:** the estimate hours that must move off overloaded people fit inside the room others in the discipline have. Names and hours are listed.
- **Partial:** some of it fits. The remainder has nowhere to go inside the discipline.
- **No internal slack:** nobody in the discipline has room. Reshuffling won't fix it. The levers are **descope**, **move the date**, or **add capacity**.

The comparison is made in estimate hours, so each receiver's own bias is already applied. Room held by cross-board people is **not** counted as slack, because their time is already spoken for on another board.

---

## Red rows: people on more than one board

After all four boards are built, anyone who appears on more than one board gets a red left bar, a red name and chips naming the other boards with their remaining hours there, plus a combined remaining total.

Each board grants a separate 70-hour capacity, so a person on two boards can look fine on both while carrying 140h of commitments. Judge them on the combined figure. They are usually leads. Leads hold parent tickets in their own name and open more work than they close, so their numbers tend to grow through a sprint rather than burn down.

---

## Console output and checks

Each run prints tickets pulled per board, then per board: people, tickets, total remaining, total intake, total overflow (true-load hours above capacity), and how many bias factors are measured / carried / inferred. Then it lists every warning. The same warnings appear on the page as collapsible notices.

| Check | Level |
|---|---|
| **Tripwire:** DAMS-roster tickets in the open sprint whose issue type is outside the DAMS set. They are dropped from every total, so the console prints a loud banner with the keys. | alert |
| Unknown roster / exclusion account ID | fatal |
| A board with zero tickets (Jira returns an empty 200 for an unknown project key or issue type) | fatal, unless `--allow-empty` |
| Missing credentials, missing template, any non-200 from Jira, runaway paging | fatal |
| Closed tickets carrying remaining hours | warn |
| Tickets already Closed when the sprint started (left out of every figure) | info |
| Open tickets under a cancelled or on-hold epic (left out of every figure) | info |
| Discipline set by Jira group over ticket types, or from ticket types because the groups are ambiguous | info |
| Done-category statuses other than `Closed` | warn |
| Remaining above 3× capacity for one person | warn |
| Roster members with no DAMS tickets | warn |
| Tickets in another board's sprint, in several active sprints, or with no active sprint | warn |
| Rejected bias factors, unclassified people, unmapped issue types | warn |

A fatal error writes nothing. `dashboard.html` and `bias.json` are only replaced, atomically, after every board has built successfully.

---

## Known limits

- **Hours follow the current assignee.** Jira stores estimate, spent and remaining on the ticket, not per person. When a ticket is reassigned (for example handed to a reviewer at Review), all of its hours, including time the previous owner logged, move to the new assignee. The original worker's load and bias are understated and the reviewer's are overstated.
- **Bias carried in `bias.json` from before 2026-11-18 includes stale tickets.** Earlier runs measured bias on every Closed ticket in the sprint, including ones that had closed in earlier sprints. A *carried* factor written before that date can still reflect that older mix until the person closes 40h of work in a sprint and is re-measured.
- **A pull is a smear, not an instant.** The four boards are fetched one after another, and a full run can take a few minutes on a busy day. A ticket that changes mid-pull can be seen in its old state on one board and its new state on another. The page shows each board's pull window.
- **Bias is noisy where closed history is thin.** 40h of closed estimate is a few tickets. One badly logged ticket can swing a factor a long way. Treat inferred factors as estimates of an estimate. Remember that bias is measured from the current assignee's closed tickets, so the reassignment problem above leaks into it too.
- **Parent estimates count alongside subtask estimates.** Per-issue fields are summed, not the aggregate fields. In this Jira, parent estimates are mostly independent of their subtasks' estimates (not roll-ups), so this isn't double counting. But if a team starts typing roll-ups onto parents, Assigned will double.
