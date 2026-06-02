# V2 Release Timeline — Knowledge Base
**Project:** Pong Game Studios · V2 Fix Version Delivery Forecast  
**Jira Instance:** `ponggamestudios.atlassian.net` · Project key: `V2`  
**Cloud ID:** `85005dc7-cde3-4a2c-8e65-2d746db228ed`  
**Current prototype:** `v2_release_timeline.html` (single-file static report, built iteratively in Claude)  
**Goal for Claude Code:** Rebuild this as a fully dynamic report that pulls live from the Jira API on demand.

---

## 1. Purpose

A Gantt-style fix version delivery forecast showing:
- Dev complete date per fix version (projected from remaining hours + resource availability)
- QA window per fix version (fixed duration after dev complete)
- Per-department bars (Creative, Math, Sound) stacked below Dev/QA row
- Per-resource task breakdown with remaining hours
- Per-resource availability buffers adjustable interactively
- Sprint boundaries as visual reference lines with full date ranges
- Lab regulatory phase bars (Lab 1+Rev → Pilot → Lab 2+Rev → Launch) for regulated releases
- Sales Trip labels per regulated release
- Drag-to-reprioritize fix versions with live queued hours recalculation
- Scope tab per FV showing real Jira epics with expandable task lists
- Last refreshed timestamp in header
- Unlogged hours warning badges per fix version
- Sprint worklog summary per person in the detail panel
- What-If Scenario Planner with per-person hours, toggle on/off, cascade to downstream FVs

---

## 2. Fix Versions in Scope (Priority Order)

| # | Fix Version | Jira Name | Theme | Dev Status | QA Weeks | Regulated? |
|---|---|---|---|---|---|---|
| 1 | V2 SW 15.00 | `V2 SW 15.00` | Sweepstakes · Games | **In QA** (dev complete) | 2 | No |
| 2 | V2 SW 16.00 | `V2 SW 16.00` | Offline Upgrader · Pay It Forward | In Development | 2 | No |
| 3 | V2 PT 13.30 | `V2 PT 13.30` | 8× Game Ports · Volume Buttons · Server Cleanup | In Development | 2 | No |
| 4 | V2 P2P 16.00 | `V2 P2P 16.00` | Georgia P2P · Mechanical Meters · Task Handler R7 | In Development | **3** | **Yes** |
| 5 | V2 HHR 3.00 | `V2 HHR 3.00` | User Role Restrictions · Maintenance Menu | In Development | 2 | No |
| 6 | V2 PT 14.00 | `V2 PT 14.00` | Ohio Central Server · Location Server · Pull Tab Print | Scheduled | **3** | **Yes** |
| 7 | V2 C2 5.10 | `V2 C2 5.10` | C2 Game Ports · Bingo Paytables · 4 Games | In Development | 2 | No |

> **Note:** "V2 SW P2P 16.00" mentioned on the whiteboard maps to `V2 SW 16.00` in Jira. Confirm with team.

---

## 3. Jira Query Rules

### 3.1 What to INCLUDE

```
issuetype in ("Story", "Dev Task", "Dev Subtask", "Creative Task", "Sound Task", "Math Task",
              "Creative Subtask", "Sound Subtask", "Math Subtask", "Pre-Prod Task")
```

- **Dev group:** `Dev Task`, `Dev Subtask`, `Story`
- **Others group:** `Creative Task`, `Creative Subtask`, `Sound Task`, `Sound Subtask`, `Math Task`, `Math Subtask`, `Pre-Prod Task`

### 3.2 What to EXCLUDE

```
issuetype in (Bug, Enhancement)
issuetype in ("QA Task", "QA Subtask")
summary ~ "Release"    -- parent tasks AND all subtasks
summary ~ "Merge"      -- parent tasks AND all subtasks
```

### 3.3 JQL Template (per fix version)

```jql
project = V2 AND fixVersion = "V2 SW 16.00"
AND issuetype in ("Story","Dev Task","Dev Subtask","Creative Task","Sound Task","Math Task",
                  "Creative Subtask","Sound Subtask","Math Subtask")
AND summary !~ "Release" AND summary !~ "Merge" AND summary !~ "Merge Code"
ORDER BY issuetype ASC, assignee ASC
```

### 3.4 Fields to Fetch Per Issue

```
summary, status, issuetype, assignee,
timeestimate,        -- remaining time in seconds → divide by 3600 for hours
timeoriginalestimate,
parent,              -- for Scope tab epic lookup
fixVersions
```

### 3.5 Important: Math Tasks (Flares & Help Pages)

Math tasks for **Flares** and **Help Pages** are tracked as `Math Subtask` or `Math Task` type. They must be included — they were previously missing from the report. When querying, ensure `Math Task` and `Math Subtask` are both included in the issuetype filter.

Each PT game typically has 4 math tasks per game:
- Help Pages (5h typical)
- Flares (5h typical)
- Prepare & Verify Flares (separate task, may be assigned to dev)

### 3.6 Scope / Epic Extraction

Walk each issue's `parent` field:
- `parent.issuetype == "Epic"` → group under that epic
- `parent.issuetype == "Story"` → need second-level lookup for grandparent epic

`done`/`total` counts must be scoped per fix version (generic epics like V2-484 Server span all FVs).

---

## 4. Resource Availability Model

### 4.1 Defaults

| Resource | Availability | Effective h/day | Notes |
|---|---|---|---|
| All developers | **75%** | 6.0 h/day | Default |
| Rejosh Samuel | **50%** | 4.0 h/day | Cross-release lead |
| Creative/Sound/Math | 75% | 6.0 h/day | Same as dev |

### 4.2 Work Day Calculation

```javascript
function addWD(date, hours, hpd) {
  const days = Math.ceil(hours / hpd);
  let r = new Date(date), added = 0;
  while (added < days) {
    r.setDate(r.getDate() + 1);
    const ds = r.toISOString().slice(0, 10);
    if (r.getDay() !== 0 && r.getDay() !== 6 && !HOLIDAYS.includes(ds)) added++;
  }
  return r;
}
const HOLIDAYS = ['2026-05-18']; // Victoria Day
```

---

## 5. Dev Complete Date Calculation

Queued hours are **never hardcoded** — computed dynamically from current FV order, including scenario injections.

```javascript
function getInjectedHours(fvKey, personName) {
  if (!scenariosMasterEnabled) return 0;
  return SCENARIOS
    .filter(s => s.placement === fvKey && !s.disabled && s.assignees.includes(personName))
    .reduce((sum, s) => sum + s.hours, 0);
}

function getDynamicQueued(fvIdx, personName) {
  const ordered = allFvOrdered();
  let q = 0;
  for (let i = 0; i < fvIdx; i++) {
    const fv = ordered[i];
    const p = fv.devPeople.find(x => x.name === personName);
    if (p) q += p.tasks.filter(t => !isDone(t.status)).reduce((s, t) => s + t.hours, 0);
    if (!fv.isScenario) q += getInjectedHours(fv.key, personName);
  }
  return q;
}

function calcDevEnd(fv, fvIdx) {
  if (!fv.devStart) return TODAY;
  let latest = new Date(TODAY);
  for (const p of fv.devPeople) {
    const openH = p.tasks.filter(t => !isDone(t.status)).reduce((s, t) => s + t.hours, 0);
    const total = openH + getDynamicQueued(fvIdx, p.name);
    if (total <= 0) continue;
    const done = addWD(TODAY, total, hpd(fv.key, p.name));
    if (done > latest) latest = done;
  }
  return latest;
}
```

### 5.1 `allFvOrdered()` — Merged FV List

Real FVs and new-FV scenarios merged into one ordered array:

```javascript
function allFvOrdered() {
  const base = [...FV];
  if (!scenariosMasterEnabled) return base;
  const newScenarios = SCENARIOS.filter(s => s.placement === 'new' && !s.disabled);
  newScenarios.forEach(s => {
    const pos = Math.min(s.priority ?? 0, base.length);
    base.splice(pos, 0, scenarioToFv(s));
  });
  return base;
}
```

### 5.2 Queued Hours (default order, May 22, 2026)

| Resource | Fix Version | ~Queued Hours | Source |
|---|---|---|---|
| Rejosh Samuel | V2 P2P 16.00 | ~2h | SW 16.00 (CRM) |
| Krupa Kanani | V2 P2P 16.00 | ~24h | PT 13.30 server testing |
| Krupa Kanani | V2 HHR 3.00 | ~44h | PT 13.30 + P2P |
| Asif Masood | V2 PT 14.00 | ~1.25h | SW 16 research task |
| Hemmat Rezvani | V2 HHR 3.00 | ~169h | SW 16 + P2P 16 |
| Hemmat Rezvani | V2 PT 14.00 | ~202h | SW 16 + P2P + HHR |
| Krupa Kanani | V2 PT 14.00 | ~56h | PT 13.30 + P2P + HHR |
| Maria Vlah | V2 C2 5.10 | ~12.5h | PT 13.30 conversions |
| Aleksey Vorotilin | V2 C2 5.10 | ~96h | PT 13.30 + P2P volume buttons |
| Krupa Kanani | V2 C2 5.10 | ~72h | PT 13.30 + P2P + HHR + PT14 |

---

## 6. QA Window

```javascript
const qaEnd = addWD(devEnd, fv.qaWeeks * 5 * 8, 8);
```

| Fix Version | QA Duration |
|---|---|
| V2 SW 15.00 | 2 weeks |
| V2 SW 16.00 | 2 weeks |
| V2 PT 13.30 | 2 weeks |
| V2 P2P 16.00 | **3 weeks** |
| V2 HHR 3.00 | 2 weeks |
| V2 PT 14.00 | **3 weeks** |
| V2 C2 5.10 | 2 weeks |

---

## 7. Lab / Regulatory Phase

**V2 P2P 16.00** and **V2 PT 14.00** only.

```
QA → Lab 1 + Rev (4 wks) → Pilot (2 wks) → Lab 2 + Rev (4 wks) → 🚀 Launch
```

FV fields: `isLab: true, lab1Weeks: 4, pilotWeeks: 2, lab2Weeks: 4`

Bar CSS: `.bar-lab1` (orange), `.bar-pilot` (pink), `.bar-lab2` (red)

UI: **🔬 Regulated · Lab Required** badge + 🚀 flag at launch date.

---

## 8. Sales Trip Labels

Label tag only — no vertical pin line.

| Release | Date | Label |
|---|---|---|
| V2 P2P 16.00 | 2026-06-27 | ✈ Sales Trip · Georgia (confirmed) |
| V2 PT 14.00 | TBD | ✈ Sales Trip · Ohio (TBD) |

```javascript
salesTrip: { date: '2026-06-27', label: 'Sales Trip · Georgia' }
salesTrip: { tbd: true, label: 'Sales Trip · Ohio' }
```

---

## 9. Sprint Configuration

```javascript
const SPRINTS = [
  { label: 'S1',  start: '2026-05-11' }, { label: 'S2',  start: '2026-05-25' },
  { label: 'S3',  start: '2026-06-08' }, { label: 'S4',  start: '2026-06-22' },
  { label: 'S5',  start: '2026-07-06' }, { label: 'S6',  start: '2026-07-20' },
  { label: 'S7',  start: '2026-08-03' }, { label: 'S8',  start: '2026-08-17' },
  { label: 'S9',  start: '2026-08-31' }, { label: 'S10', start: '2026-09-14' },
  { label: 'S11', start: '2026-09-28' }, { label: 'S12', start: '2026-10-12' },
  { label: 'S13', start: '2026-10-26' }, { label: 'S14', start: '2026-11-09' },
  { label: 'S15', start: '2026-11-23' }, { label: '',    start: '2026-12-07' },
];
```

Sprint chips show full range: `S2 / May 25 – Jun 7`.  
Sprint lines: `rgba(148,163,184,.22)`.  
**Chart bounds:** `CHART_START = 2026-05-11`, `CHART_END = 2026-12-07`

---

## 10. UI Structure

### 10.1 Axis

```
[ TODAY badge (top:0, downward arrow)  ]
[ month labels (top:14px)              ]
[ sprint chips (top:30px)              ]
```
Axis height: `64px`. Red `.today-line` through all rows.

### 10.2 Gantt Row

```
[⠿] [ Label 196px ] [ Track .......................... ] [▾]
  #N priority badge     Sprint lines + TODAY line
  FV name (accent)      Dev bar  QA bar  [Lab1 Pilot Lab2 🚀]
  Subtitle              Sales trip tag (top:32px)
  Status badge          Dept rows (CREATIVE / MATH / SOUND)
  🔬 Regulated          Resource pills (⛓ CRITICAL PATH, ⚡ scenario)
  ⚠ Unlogged badge
```

Drag handle (18px) + label (196px) = 214px = axis left margin.

### 10.3 Unlogged Hours Badge

Amber `⚠ N tasks 0h logged` badge in the label panel. Counts active tasks (In Progress / Ready / In QA / Pre-Prod In Progress) with `hours === 0`.

```javascript
function countUnlogged(fv) {
  const ACTIVE = ['In Progress','Ready','In QA','Pre-Prod In Progress'];
  return fv.devPeople.flatMap(p => p.tasks)
    .filter(t => ACTIVE.includes(t.status) && t.hours === 0).length;
}
```

### 10.4 Department Bar Colours

| Type | Badge | Bar bg | Bar border |
|---|---|---|---|
| Creative | `#4c1d95` | `rgba(124,58,237,.10)` | `rgba(167,139,250,.5)` |
| Math | `#0f766e` | `rgba(13,148,136,.10)` | `rgba(20,184,166,.5)` |
| Sound | `#9d174d` | `rgba(219,39,119,.10)` | `rgba(236,72,153,.5)` |

### 10.5 Resource Pills

```
Hemmat R. · 136h · 75% ⛓     Asif M. · 120h · 75% ⚡     Others · 8 tasks
```

- `⛓` = CRITICAL PATH
- `⚡` = has scenario task injected

### 10.6 Detail Panel Tabs

| Tab | Content |
|---|---|
| **DEV WORK** | Per-person columns: buffer slider, queued hours, projected date, task cards, sprint activity |
| **CREATIVE · SOUND · MATH** | Same layout for others group |
| **SCOPE** | Epic accordion |

### 10.7 Task Cards

```
V2-29201  ⛓ CRITICAL PATH   (clickable → Jira)
System — Show pending hard meter updates in MM
24h remaining                    [In Progress]
```

Hour colours: `≥ 40h` red · `≥ 16h` amber · `> 0h` green · `0h` grey

---

## 11. Sprint Worklog Summary

Each person column in DEV WORK has a **Sprint Activity** section at the bottom showing total hours logged vs expected, colour-coded bar, and per-issue breakdown.

```javascript
const SPRINT = {
  label: 'S1', period: 'May 11 – 24',
  workDaysToDate: 9,   // working days elapsed through May 22 (excl. May 18 holiday)
  workDaysTotal: 9,    // total working days in sprint
};
function sprintExpected(name) {
  const buf = name === 'Rejosh Samuel' ? 50 : 75;
  return Math.round(8 * (buf / 100) * SPRINT.workDaysToDate * 10) / 10;
}
```

**Tempo note:** Tempo logs hours under a service account. Hours are attributed by issue assignee. Some attribution may be imprecise where the actual worker differs from the assignee.

**S1 Sprint summary (May 11–22, 9 working days):**

| Person | Logged | Expected | % |
|---|---|---|---|
| Rejosh Samuel | 55h | 36h | ~153% |
| Asif Masood | 45h | 54h | 83% |
| Hemmat Rezvani | 23h | 54h | 43% |
| Yves Griber | 23h | 54h | 43% |
| Niloo Tehrani | 14h | 54h | 26% |
| Krupa Kanani | 10.75h | 54h | 20% |
| Joel Kazmi | 6.75h | 54h | 13% |
| Maria Vlah | 6.75h | 54h | 13% |
| Justin Kwon | 3.25h | 54h | 6% ⚠️ |
| Amanda Langford | 1.0h | 54h | 2% |

---

## 12. Scope Tab

### 12.1 Data Structure

```javascript
scope: [
  {
    key: 'V2-29114',              // Epic Jira key — clickable link
    name: 'System Mechanical Meters',
    status: 'In Progress',
    done: 0,                      // child tasks that are Closed/Done in this FV
    total: 7,                     // total child tasks in this FV
    taskKeys: ['V2-29201', ...],  // tasks shown in expanded accordion
  },
]
```

### 12.2 Accordion Behaviour

- **Collapsed:** epic key (link) · name · status badge · done/total · thin progress bar
- **Click header:** expands task list
- **Task rows:** key (link) · summary · hours (colour-coded) · status badge
- Done tasks faded to `opacity: 0.45`
- `stopPropagation` on key links so clicking opens Jira without toggling accordion

### 12.3 Current Epic Data (May 22, 2026)

**V2 SW 15.00** (12 epics) — mostly in QA; Arctic Buffalo epic now includes GAB math/art tasks

**V2 SW 16.00** (5 epics) — V2-29401 Offline Upgrader, V2-29402 Pay it Forward SW, V2-29396 Server VM, V2-621 System, V2-484 Server

**V2 PT 13.30** (12 epics) — 9 game epics + Game Updates + Server + System. **Math tasks now included:** Sonali 83h (17 tasks: flares + help pages per game @ 5h each), Seyeon Oh 5h (Arctic Buffalo Flares)

**V2 P2P 16.00** (11 epics) — Mechanical Meters, PIF, Fixed Redemption, Task Handler R7, Win Payout, Viking's Voyage (9/14 done), Flaming Skulls, Game Menu, Game Updates, Server, System

**V2 HHR 3.00** (2 epics) — V2-29369 Maintenance Menu User Roles, V2-484 Server

**V2 PT 14.00** (1 epic) — V2-29209 Centralized Server (Ohio)

**V2 C2 5.10** (4 epics):

| Key | Epic | Done/Total |
|---|---|---|
| V2-7996 | Game: Plenty O' Coins | 1/8 |
| V2-20802 | Game: Mayan Madness | 0/6 |
| V2-17197 | Game: Day of the Dead [V] | 1/8 |
| V2-16002 | Game: Blue Bird Bonus | 1/8 |

---

## 13. Drag-to-Reprioritize

Each `.fv-item` is `draggable="true"`. Drop line above/below target. On drop:
1. FV spliced + re-inserted in `FV[]` (or scenario `priority` updated)
2. `buildRows()` rebuilds everything
3. All `getDynamicQueued()` values recalculate automatically
4. Toast confirms the move

Scenario FVs are also draggable — dropping updates `s.priority`.

---

## 14. What-If Scenario Planner

### 14.1 Overview

Orange `⚡ What-If` FAB (bottom-right) opens a slide-in panel. Scenarios add hypothetical tasks that cascade through queued hours downstream.

### 14.2 Scenario Data Structure

```javascript
{
  id: 'abc123',           // unique ID
  label: 'Urgent Auth Fix',
  hours: 160,             // hours for this specific person
  assignees: ['Asif Masood'],  // always one person per record
  placement: 'new',       // 'new' = standalone FV | fv.key = inject into existing FV
  priority: 3,            // position in ordered list (for 'new' placement)
  disabled: false,        // per-scenario eye toggle
}
```

Multiple people with different hours = multiple scenario records sharing the same label.

### 14.3 Two Placement Modes

**New standalone FV:** ghost row (dashed orange border), draggable, no QA bar, `⚡ SCENARIO` badge.

**Inject into existing FV:** no new row; scenario hours added to person's task list in that FV, `⚡` pill indicator, all downstream FVs cascade via `getInjectedHours()`.

### 14.4 Cascade Fix — Critical

Injected scenario hours MUST flow into `getDynamicQueued` for all downstream FVs:

```javascript
// Inside getDynamicQueued loop:
if (!fv.isScenario) q += getInjectedHours(fv.key, personName);
```

Without this, injected scenarios only affect the target FV and don't cascade downstream.

### 14.5 Toggle Controls

**Per-scenario eye toggle (👁️):** sets `s.disabled` — greys out card, excludes from all calculations.

**Master toggle:** `scenariosMasterEnabled` boolean — OFF shows pure baseline with no scenarios at all. FAB turns grey when master is OFF.

### 14.6 Per-Person Hours Form

Each developer listed as a chip. Click chip → inline hours input appears (default 120h). Each person can have different hours. One scenario record created per selected person on submit.

### 14.7 Persistence

```javascript
localStorage.setItem('v2_scenarios', JSON.stringify(SCENARIOS));
localStorage.setItem('v2_scenarios_master', JSON.stringify(scenariosMasterEnabled));
```

---

## 15. Refresh Timestamp

Static line in header, updated each refresh:

```html
Last refreshed from Jira: <strong>May 22, 2026 — 9:00 AM</strong> · Sprint S1: May 11–24
```

**On each refresh, update:**
1. `TODAY` constant
2. Timestamp text in header
3. `SPRINT.workDaysToDate` (working days elapsed in current sprint)
4. `SPRINT_LOGS` per-person sprint hours
5. All FV `devPeople` / `otherPeople` task arrays with latest hours and statuses
6. All `scope` epic `done`/`total` counts

---

## 16. Colour System

### 16.1 CSS Variables

```css
:root {
  --bg: #f4f6fb; --surface: #ffffff; --surf2: #f0f3f9; --surf3: #e8ecf4;
  --border: #dde2ee; --border2: #c8cedf;
  --muted: #6b7a99; --text: #1a2035; --sub: #8a94ad;
  --dev: #2563eb;  --dev-bg: rgba(37,99,235,.10);
  --qa:  #d97706;  --qa-bg:  rgba(217,119,6,.12);
  --done: #16a34a; --done-bg: rgba(22,163,74,.11);
  --oth:  #7c3aed; --oth-bg:  rgba(124,58,237,.10);
}
```

### 16.2 Fix Version Accent Colours

| FV | Colour |
|---|---|
| V2 SW 15.00 | `#60a5fa` (blue) |
| V2 SW 16.00 | `#93c5fd` (light blue) |
| V2 PT 13.30 | `#c4b5fd` (violet) |
| V2 P2P 16.00 | `#fb923c` (orange) |
| V2 HHR 3.00 | `#4ade80` (green) |
| V2 PT 14.00 | `#f87171` (red) |
| V2 C2 5.10 | `#a78bfa` (purple) |
| Scenario rows | `#fb923c` (dashed border) |

### 16.3 Fonts

- Body: `IBM Plex Sans` (300, 400, 500, 600)
- Monospace: `IBM Plex Mono` (400, 500)

---

## 17. People Reference (May 22, 2026)

### 17.1 Developers

| Name | Buffer | Critical Path On | Notes |
|---|---|---|---|
| Rejosh Samuel | **50%** | — (lead/release mgr) | Touches all releases |
| Asif Masood | 75% | PT 14.00 (255.8h) | Major progress since May 20 |
| Hemmat Rezvani | 75% | P2P 16.00 (136h) | Also HHR + PT14 |
| Justin Kwon | 75% | PT 13.30 (68h), P2P 16.00 (110h) | Low S1 hours ⚠️ |
| Yves Griber | 75% | SW 16.00 (31h) | Offline upgrader testing |
| Krupa Kanani | 75% | PT 13.30 (24h), HHR 3.00 (12h) | Server testing all releases |
| Niloo Tehrani | 75% | PT 13.30 (34h) | Zombie Hunt in progress |
| Maria Vlah | 75% | C2 5.10 (81h) | Plenty O'Coins + Blue Bird Bonus |
| Aleksey Vorotilin | 75% | P2P 16.00 (88h) | Wwise + volume buttons |
| Linda Zhang | 75% | — | SW 15 in QA |

### 17.2 Others (Creative / Sound / Math)

| Name | Type | Key Work |
|---|---|---|
| Amanda Langford | Creative | SW 15, PT 13.30, P2P 16, HHR 3, C2 5.10 |
| James Zhang | Creative | SW 15 — 8h GAB art (new contributor) |
| John Di Mauro | Creative | SW 15 |
| Amer Nabulsi | Creative | P2P 16 |
| Joel Kazmi | Sound | P2P 16 — 11.75h Wwise |
| Seyeon Oh | Math | SW 15 (6h GAB), PT 13.30 (5h Arctic Buffalo Flares), P2P 16 (14h Viking's Voyage) |
| Sonali Mehra | Math | All releases — **83h on PT 13.30** (flares + help pages now logged) |

---

## 18. Critical Path Logic

Flagged as `⛓ CRITICAL PATH` if `bottleneck: true` on person record or any task.

In dynamic version: auto-calculate as person whose projected done date is latest for the FV.

**Current (May 22, 2026):**

| FV | Critical Path | Hours |
|---|---|---|
| SW 15 | Justin Kwon | 7.5h |
| SW 16 | Yves Griber | 31h |
| PT 13.30 | Justin Kwon | 68h |
| P2P 16 | Hemmat Rezvani | 136h |
| HHR 3.00 | Hemmat Rezvani | 33h (+169h queued) |
| PT 14.00 | Asif Masood | 255.8h |
| C2 5.10 | Maria Vlah | 81h |

---

## 19. Dynamic Version — Architecture Notes

### 19.1 Recommended Stack

- **Backend:** Node.js or Python proxying Jira API
- **Frontend:** SPA (React or vanilla JS)
- **Auth:** Jira API token (Basic `email:token` base64)

### 19.2 Scope / Epic Fetch Strategy

Two queries per fix version:

```jql
-- Stories/tasks to find epic parents
project = V2 AND fixVersion = "{version}"
AND issuetype in ("Story","Dev Task","Dev Subtask")
AND summary !~ "Release" AND summary !~ "Merge"
ORDER BY assignee ASC
```

For stories whose parent is another Story (not Epic), fetch grandparent epic separately.

### 19.3 Math Tasks — Must Include

Always include `Math Task` and `Math Subtask` in the others query:

```jql
project = V2 AND fixVersion = "{version}"
AND issuetype in ("Creative Task","Creative Subtask","Sound Task","Sound Subtask",
                  "Math Task","Math Subtask","Pre-Prod Task")
AND statusCategory != Done
ORDER BY assignee ASC
```

Sonali Mehra and Seyeon Oh are the primary Math assignees. Flares and Help Pages tasks are typically 5h each.

### 19.4 Additional Features to Add

- [ ] Export to PNG/PDF
- [ ] Date override controls per FV
- [ ] Flag unlogged hours (In Progress with 0h remaining)
- [ ] Resource view — pivot by person across all FVs
- [ ] Live Scope tab — fetch epic data on panel open
- [ ] Sprint velocity tracking
- [ ] Lab / QA weeks editable in UI
- [ ] Sales Trip date editor
- [ ] Priority persistence to localStorage across sessions

---

## 20. localStorage Keys

| Key | Contents |
|---|---|
| `v2_scenarios` | `JSON.stringify(SCENARIOS)` array |
| `v2_scenarios_master` | `JSON.stringify(scenariosMasterEnabled)` boolean |
| `v2_timeline_priority` | *(not yet implemented)* FV order persistence |
| `v2_timeline_buffers` | *(not yet implemented)* per-person buffer persistence |

---

## 21. Key Business Rules Summary

1. **Exclude:** Bugs, Enhancements, QA Tasks/Subtasks, Release + subtasks, Merge + subtasks
2. **Include:** Dev Tasks/Subtasks/Stories, Creative/Sound/Math Tasks/Subtasks, Pre-Prod Tasks
3. **Math tasks (Flares, Help Pages)** — `Math Task` and `Math Subtask` types — must be included; previously missing from report
4. **Remaining hours** from subtask level, not parent
5. **Dev complete** = latest projected date across all assignees
6. **QA** = fixed work days (Mon–Fri, skip holidays)
7. **Lab pipeline** = P2P 16 and PT 14 only: QA → Lab1+Rev (4wks) → Pilot (2wks) → Lab2+Rev (4wks) → 🚀
8. **Rejosh 50%** availability; all others 75%
9. **May 18, 2026** = holiday
10. **TODAY = May 22, 2026** — update on each refresh along with sprint data
11. **Sprint 1** starts May 11, 2026 — 14-day cycles
12. **No vertical line for Sales Trip** — label tag only
13. **Critical path** everywhere (not "bottleneck")
14. **Queued hours never hardcoded** — `getDynamicQueued()` + `getInjectedHours()` only
15. **Drag reorder** triggers full `buildRows()` rebuild
16. **Scope epics** from Jira parent chain; done/total scoped per FV
17. **Scope accordion** — epic and task keys are Jira links; stopPropagation on links
18. **Scenario injections cascade downstream** — `getInjectedHours()` in `getDynamicQueued()`
19. **Scenario per-person hours** — one scenario record per person; same label groups them
20. **Master scenario toggle** — `scenariosMasterEnabled` gates all scenario logic
21. **Unlogged badge** — counts In Progress/Ready/In QA tasks with 0h remaining
22. **V2 C2 5.10** — lowest priority (#7), colour `#a78bfa`, 2 weeks QA, not regulated

---

## 22. Plan Editor + Save-as-default Flow (Phase 5)

V2 ships with an in-page editor that lets authorized users change the editable bits of the dashboard's behaviour for everyone else, without touching the Python script or HTML directly. This is the same pattern in iGaming — same JS, CSS, modal flow; only the dashboard-identity constants differ.

### 22.1 Top-right nav buttons

Two buttons sit on the same level as the page tabs:

```
[Overview] [V2 Timeline] [iGaming Timeline]              [⚙ Edit Plan]  [⚡ What-If]
```

- **⚙ Edit Plan** (orange) — opens the right-side panel in *planeditor* mode (wider, 680px). Hosts the Fix Versions + Settings tabs.
- **⚡ What-If** (purple) — opens the same panel in *whatif* mode (narrower, 560px). Hosts the existing Scenarios sandbox. Carries a small red badge with the count of active scenarios; greys out when the master toggle is off.

The two modes share the same panel element (`#scenarioPanel`) with a `data-mode` attribute that drives tab visibility via CSS.

### 22.2 Three tabs

| Tab | What it edits | Persists to |
|---|---|---|
| **⚡ Scenarios** | Existing what-if scenarios (add fake work to a person / new FV) | `localStorage.v2_scenarios` (browser-local) |
| **📋 Fix Versions** | Per-FV color, visibility, QA weeks, subtitle, scope, milestones | `localStorage.v2_local_config` → `config/v2.json` via PR |
| **⚙ Settings** | Global holidays list | `localStorage.v2_local_config` → `config/v2.json` via PR |

V2-specific note: the Fix Versions editor exposes `color`, `qaWeeks`, `sub`. The lab pipeline (`isLab`, `lab1Weeks`, `pilotWeeks`, `lab2Weeks`) and `salesTrip` fields are **read-through only** in v1 — they're preserved through Save-as-default but not editable via the panel. Edit `config/v2.json` directly for those (or wait for a Phase 6 that adds lab/sales-trip editor fields).

### 22.3 Fix Versions tab — per-row stacked editor

Each FV row is collapsed by default. Click ▾ to expand into three stacked sections:

1. **DETAILS** — QA weeks (0-12), subtitle (`textarea`). The status label is shown read-only (it's derived from Jira data, not editable).
2. **SCOPE** — one row per Jira parent in the FV (plus the "no epic" virtual bucket if applicable).
   - Toggle (green = visible / grey = hidden from the FV's Scope tab)
   - Jira key link (or `—` for the unscoped bucket)
   - Inline-editable label (`contenteditable` — click to rename, Enter to save, Escape to cancel)
   - ↺ reset button — clears both hidden and label overrides
3. **MILESTONES** — list of pins displayed on the FV's gantt bar.
   - Label, anchor (`after Dev` with offset_weeks / `fixed date`), value, color, delete
   - `+ Add milestone` button at the bottom

Edits land in `LOCAL_CONFIG.fv_meta[fvKey]` and the FV array is mutated in-place so `buildRows()` picks up the new color/qaWeeks/etc immediately.

### 22.4 Milestone rendering on the gantt

Inside `buildRows()`, after the dev/QA/Lab1/Pilot/Lab2 bars are placed (and after the Sales Trip tag), each milestone in `effectiveFv.milestones` is resolved:

```js
if (ms.anchor === 'date') msDate = D(ms.date);
else                      msDate = devEnd + ms.offset_weeks * 7 days;
```

Renders as a 1.5px vertical pin + small label above. Labels stagger by `(msIdx % 3) * 16px` so close-together milestones don't overlap. Note the visual stacking with existing V2 markers: dev/QA bars at `top:8px`, dept rows from `top:34px+`, Sales Trip tag at `top:32px`. Milestone labels start at `top:34px` and shouldn't collide with Sales Trip in practice (different x-positions).

### 22.5 LOCAL_CONFIG — browser-local overlay

```js
LOCAL_CONFIG = {
  fv_order: ['V2 SW 15.00', 'V2 P2P 16.00', ...] | null,
  fv_meta: {
    'V2 P2P 16.00': {
      color: '#ff0000',
      qaWeeks: 3,
      sub: 'Custom subtitle',
      epic_overrides: { 'V2-1234': { hidden: true, label: 'Custom name' } },
      milestones: [{ id, label, anchor, offset_weeks|date, color }, ...]
    }
  },
  hidden_fvs: [],
  holidays: ['2026-05-18'] | null
};
```

Loaded from `localStorage.v2_local_config` on every page load. Two `apply*` functions replay it over the in-memory FV array and HOLIDAYS list.

### 22.6 Server config — `config/v2.json`

`v2_timeline.py` reads this file at the top via `load_config()`, falls back to `DEFAULT_FV_CONFIG` / `DEFAULT_HOLIDAYS` Python literals if missing or unparseable.

Schema (full V2-specific fields preserved):

```json
{
  "fv_order": ["V2 SW 15.00", "V2 SW 16.00", "V2 PT 13.30", "V2 P2P 16.00", "V2 HHR 3.00", "V2 PT 14.00", "V2 C2 5.10"],
  "fv_meta": {
    "V2 P2P 16.00": {
      "color": "#fb923c",
      "sub": "Georgia P2P · Mechanical Meters · Task Handler R7",
      "qaWeeks": 3,
      "indev_style": "color:#7c2d12;border-color:rgba(234,88,12,.3);background:rgba(234,88,12,.09)",
      "isLab": true,
      "lab1Weeks": 4,
      "pilotWeeks": 2,
      "lab2Weeks": 4,
      "salesTrip": {"date": "2026-06-27", "label": "Sales Trip · Georgia"},
      "epic_overrides": { "V2-1234": { "hidden": true, "label": "..." } },
      "milestones": [{ "id": "ms_xyz", "label": "QA Round 2", "anchor": "dev_end", "offset_weeks": 2, "color": "#d97706" }]
    }
  },
  "hidden_fvs": [],
  "holidays": ["2026-05-18"]
}
```

`epic_overrides` and `milestones` are optional per FV. The "no epic" virtual bucket stores under sentinel key `"__unscoped__"`. The `isLab` / `lab*Weeks` / `salesTrip` / `indev_style` / `note` fields are V2-only and are not in iGaming's config schema (they're optional, so the JSON parser doesn't care).

### 22.7 Save-as-default flow

Identical to iGaming. The `💾 Save as default` button triggers:

1. **Diff** — `buildConfigPayload()` serialises current state; `fetchServerConfig()` GETs current `config/v2.json` from `raw.githubusercontent.com`; `diffConfigs()` produces a categorised change list.
2. **Auth** — first save prompts for a fine-grained GitHub PAT (stored in `localStorage.v2_github_pat`). `verifyPat()` checks `GET /user` + `permissions.push` on the repo.
3. **Confirm modal** — shows the diff, commit message editable, "Save & merge" button.
4. **Commit** — `saveConfigToRepo()` creates branch `config/v2-{timestamp}`, commits the new JSON, opens a PR, auto-merges, deletes the branch.
5. **Trigger** — merge to `main` matches `push.paths: config/v2.json` in `.github/workflows/v2_timeline.yml`, fires the workflow. ~2-3 min from click to live.
6. **Cleanup** — panel clears `LOCAL_CONFIG` and reloads the page.

All GitHub API calls go directly browser → `api.github.com`. No backend.

### 22.8 PAT scope

Same fine-grained scopes as iGaming: Contents read+write, Pull requests read+write, Metadata read. See [`docs/CONFIG_EDITOR_SETUP.md`](CONFIG_EDITOR_SETUP.md) for setup walkthrough.

The PAT is stored under a **separate** key (`v2_github_pat`) from iGaming's (`ig_github_pat`). Same physical token works for both (same repo, same permissions) but the user has to paste it into each dashboard once. This is intentional — revoking the token in one dashboard's UI doesn't break the other.

### 22.9 localStorage keys (V2 dashboard)

| Key | Type | Purpose |
|---|---|---|
| `v2_scenarios` | JSON array | What-If scenarios |
| `v2_scenarios_master` | JSON bool | Master enable/disable for all scenarios |
| `v2_hidden_fvs` | JSON array | Backwards-compat hide list (Plan Editor writes to both this and `v2_local_config.hidden_fvs`) |
| `v2_local_config` | JSON object | All Plan Editor browser-local edits (see §22.5) |
| `v2_github_pat` | string | Fine-grained PAT for Save-as-default |
| `v2_github_pat_user` | string | Last verified GitHub username (display only) |

All `v2_*` prefixed to avoid colliding with iGaming's `ig_*` keys if both are open in the same browser.

### 22.10 Portability

The editor JS is byte-identical between iGaming and V2 except for four dashboard-identity constants:

| Constant | V2 value | What it controls |
|---|---|---|
| `GH_CONFIG_PATH` | `'config/v2.json'` | Which file the save flow writes to |
| `GH_PAT_KEY` | `'v2_github_pat'` | localStorage key for the PAT |
| `GH_PAT_USER_KEY` | `'v2_github_pat_user'` | localStorage key for the verified username |
| `localStorage.{v2_local_config, v2_hidden_fvs, v2_scenarios, v2_scenarios_master}` | `v2_*` prefix | All other panel state |

Plus the Python script needs its own `load_config()` reading the new JSON file, and its own `.github/workflows/<dashboard>.yml` with a matching `push.paths` trigger. Everything else ports verbatim.

---

## 23. File Reference

| File | Description |
|---|---|
| `v2-timeline.html` | Generated output — committed by the workflow on every refresh |
| `v2-timeline.template.html` | Source template — placeholders `__FV_DATA__`, `__SPRINTS_DATA__`, etc. injected by the Python script. Contains all CSS, dashboard JS, Plan Editor + Save-as-default flow. |
| `v2_timeline.py` | Refresh script — calls Jira, transforms results, renders the template. Reads `config/v2.json` with hardcoded fallback. |
| `config/v2.json` | **Editable config** — FV order, per-FV metadata (color, sub, qaWeeks, lab/sales-trip), scope overrides, milestones, holidays, hidden FVs. Edited via the Plan Editor's Save-as-default button (auto-PR). |
| `jira_client.py` | Shared `jira_jql` POST `/search/jql` paginator (used by all three refresh scripts: exec, v2, igaming) |
| `.github/workflows/v2_timeline.yml` | Workflow — daily cron (`15 6 * * *` UTC) + `workflow_dispatch` + `push.paths: config/v2.json` |
| `docs/V2_RELEASE_TIMELINE_KNOWLEDGE.md` | This file |
| `docs/V2_TIMELINE_EDGE_CASES.md` | Companion: tricky cases the model intentionally doesn't try to solve |
| `docs/IG_RELEASE_TIMELINE_KNOWLEDGE.md` | Sister dashboard (iGaming) — same Plan Editor pattern |
| `docs/CONFIG_EDITOR_SETUP.md` | One-time PAT setup guide for users who'll edit dashboards |
| `docs/DASHBOARD_LOGIC.md` | Exec dashboard logic reference (separate dashboard, separate codepath) |

---

*Last updated: 2026-06-02 · Phase 5 added Plan Editor + Save-as-default + Milestones · Pong Game Studios PMO*
