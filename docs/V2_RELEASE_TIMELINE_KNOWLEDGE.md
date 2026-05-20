# V2 Release Timeline — Knowledge Base
**Project:** Pong Game Studios · V2 Fix Version Delivery Forecast  
**Jira Instance:** `ponggamestudios.atlassian.net` · Project key: `V2`  
**Cloud ID:** `85005dc7-cde3-4a2c-8e65-2d746db228ed`  
**Current prototype:** `v2_release_timeline.html` (single-file static report, built iteratively in Claude)  
**Goal for Claude Code:** Rebuild this as a fully dynamic report that pulls live from the Jira API on demand.

---

## 1. Purpose

A Gantt-style fix version delivery forecast that shows:
- Dev complete date per fix version (projected from remaining hours + resource availability)
- QA window per fix version (fixed duration added after dev complete)
- Per-department bars (Creative, Math, Sound) stacked below Dev/QA row per fix version
- Per-resource task breakdown with remaining hours (dev work separately from creative/sound/math)
- Per-resource availability buffers adjustable interactively
- Sprint boundaries as visual reference lines with date ranges
- Lab regulatory phase bars (Lab 1 + Rev → Pilot → Lab 2 + Rev → Launch) for regulated releases
- Sales Trip pins per regulated release

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

> **Note:** "V2 SW P2P 16.00" mentioned on the whiteboard maps to `V2 SW 16.00` in Jira. Confirm this is correct with the team.

---

## 3. Jira Query Rules

### 3.1 What to INCLUDE

```
issuetype in ("Story", "Dev Task", "Dev Subtask", "Creative Task", "Sound Task", "Math Task", "Creative Subtask", "Sound Subtask", "Math Subtask", "Pre-Prod Task")
```

Split into two groups per fix version:
- **Dev group:** `Dev Task`, `Dev Subtask`, `Story` (where assignee is a developer)
- **Others group:** `Creative Task`, `Creative Subtask`, `Sound Task`, `Sound Subtask`, `Math Task`, `Math Subtask`, `Pre-Prod Task`

### 3.2 What to EXCLUDE

```
issuetype in (Bug, Enhancement)                   -- never include
issuetype = "QA Task" OR issuetype = "QA Subtask" -- never include  
summary ~ "Release"                               -- exclude Release parent tasks and ALL their subtasks
summary ~ "Merge"                                 -- exclude Merge parent tasks and ALL their subtasks
```

The exclusion of Release and Merge tasks is critical — these are release admin overhead, not development work. In Jira, the "Release" dev task and the "Merge Code" dev task each have subtasks per person (e.g. "Merge Code — Rejosh", "Prepare Builds", "Release", "Deployment", "Prepare Release Notes"). **All of these must be excluded** from remaining hour calculations.

### 3.3 JQL Template (per fix version)

```jql
project = V2 
AND fixVersion = "V2 SW 16.00" 
AND issuetype in ("Story","Dev Task","Dev Subtask","Creative Task","Sound Task","Math Task","Creative Subtask","Sound Subtask","Math Subtask")
AND summary !~ "Release"
AND summary !~ "Merge"
AND summary !~ "Merge Code"
ORDER BY issuetype ASC, assignee ASC
```

### 3.4 Fields to Fetch Per Issue

```
summary, status, issuetype, assignee, 
timeestimate,       -- remaining time in seconds → divide by 3600 for hours
timeoriginalestimate,
parent,             -- to identify subtask parentage
fixVersions
```

### 3.5 Subtasks Are Required

**Parent stories/tasks often have 0h remaining.** The actual time estimates live on **subtasks**. Always query both parent tasks and their subtasks.

---

## 4. Resource Availability Model

### 4.1 Defaults

| Resource | Default Availability | Effective h/day (8h day) | Reason |
|---|---|---|---|
| All developers (default) | **75%** | 6.0 h/day | Meetings, reviews, overhead |
| Rejosh Samuel | **50%** | 4.0 h/day | Cross-release load; acts as lead across all versions |
| Creative/Sound/Math (others) | 75% | 6.0 h/day | Same default as dev |

### 4.2 Buffer Adjustment

The UI allows per-person, per-fix-version buffer adjustment via a slider (10%–100%, step 5%). Changes are stored in a `BUFFERS` map keyed as `"${fvKey}::${personName}"` and trigger a full recalculation and re-render.

### 4.3 Work Day Calculation

```javascript
function addWD(date, hours, hpd) {
  // hpd = effective hours per day after buffer
  const days = Math.ceil(hours / hpd);
  let r = new Date(date), added = 0;
  while (added < days) {
    r.setDate(r.getDate() + 1);
    const ds = r.toISOString().slice(0, 10);
    // Skip weekends and holidays
    if (r.getDay() !== 0 && r.getDay() !== 6 && !HOLIDAYS.includes(ds)) added++;
  }
  return r;
}
```

### 4.4 Holidays

```javascript
const HOLIDAYS = ['2026-05-18']; // Victoria Day (Canada)
```
Add future holidays here as `YYYY-MM-DD` strings.

---

## 5. Dev Complete Date Calculation

For each fix version, the dev complete date = the **latest projected done date** across all assigned developers.

```javascript
function calcDevEnd(fv) {
  if (!fv.devStart) return TODAY; // already in QA
  let latest = new Date(TODAY);
  for (const person of fv.devPeople) {
    const openHours = person.tasks
      .filter(t => !isDone(t.status))
      .reduce((sum, t) => sum + t.hours, 0);
    const totalHours = openHours + (person.queuedHours || 0);
    if (totalHours <= 0) continue;
    const projDone = addWD(TODAY, totalHours, hpd(fv.key, person.name));
    if (projDone > latest) latest = projDone;
  }
  return latest;
}
```

### 5.1 Queued Hours (Cross-Release Sequencing)

Some resources are shared across multiple fix versions. Their time on higher-priority releases is added as `queuedHours` to offset their start on lower-priority releases. This prevents double-booking.

As of May 19, 2026:

| Resource | Fix Version | Queued Hours | Reason |
|---|---|---|---|
| Rejosh Samuel | V2 P2P 16.00 | 5h | SW 16.00 tasks (CRM + TLS, ~5h) |
| Krupa Kanani | V2 P2P 16.00 | 24h | PT 13.30 server testing (24h) |
| Krupa Kanani | V2 HHR 3.00 | 44h | PT 13.30 (24h) + P2P (20h) |
| Asif Masood | V2 PT 14.00 | 13.2h | SW 16 + P2P + HHR tasks (~13h) |
| Hemmat Rezvani | V2 HHR 3.00 | 164.4h | SW 16 (28.25h) + P2P 16 (136.25h) |
| Hemmat Rezvani | V2 PT 14.00 | 218.9h | SW 16 + P2P + HHR combined |
| Krupa Kanani | V2 PT 14.00 | 56h | PT 13.30 (24h) + P2P (20h) + HHR (12h) |

> In the dynamic version, queued hours should be auto-calculated by summing a person's open hours on all higher-priority fix versions.

---

## 6. QA Window

```javascript
const qaEnd = addWD(devEnd, fv.qaWeeks * 5 * 8, 8);
// qaWeeks × 5 work days × 8h/day, skipping weekends and holidays
// Uses the same addWD() function as dev, with hpd=8 (no availability buffer on duration)
```

QA duration is counted in **work days (Mon–Fri)**, skipping weekends and public holidays.

| Fix Version | QA Duration |
|---|---|
| V2 SW 15.00 | 2 weeks (10 work days) |
| V2 SW 16.00 | 2 weeks |
| V2 PT 13.30 | 2 weeks |
| V2 P2P 16.00 | **3 weeks (15 work days)** |
| V2 HHR 3.00 | 2 weeks |
| V2 PT 14.00 | **3 weeks** |

---

## 7. Lab / Regulatory Phase (Regulated Releases Only)

**V2 P2P 16.00** and **V2 PT 14.00** are regulated market releases requiring lab certification. After QA, these have a fixed additional pipeline:

```
QA  →  Lab 1 + Revisions (4 wks)  →  Pilot/Sales (2 wks)  →  Lab 2 + Revisions (4 wks)  →  🚀 Launch
```

### 7.1 FV Data Fields

```javascript
{
  isLab: true,
  lab1Weeks: 4,    // 4 sprints = 2 sprints Lab 1 + revisions
  pilotWeeks: 2,   // 1 sprint Pilot (Sales)
  lab2Weeks: 4,    // 4 sprints = 2 sprints Lab 2 + revisions
}
```

### 7.2 Calculation

```javascript
const lab1End  = addWD(qaEnd,   fv.lab1Weeks  * 5 * 8, 8);
const pilotEnd = addWD(lab1End, fv.pilotWeeks * 5 * 8, 8);
const lab2End  = addWD(pilotEnd,fv.lab2Weeks  * 5 * 8, 8);
// lab2End = Launch date, marked with 🚀 flag
```

### 7.3 Bar Colours (Lab phases)

| Phase | CSS class | Colour |
|---|---|---|
| Lab 1 + Revisions | `.bar-lab1` | Orange `rgba(249,115,22,.13)` / `#c2410c` |
| Pilot (Sales) | `.bar-pilot` | Pink `rgba(236,72,153,.12)` / `#9d174d` |
| Lab 2 + Revisions | `.bar-lab2` | Red `rgba(239,68,68,.14)` / `#991b1b` |

### 7.4 UI Labels

Each regulated release shows:
- **🔬 Regulated · Lab Required** badge in the left label panel (below status badge)
- Lab phase bars rendered inline after the QA bar on the same row
- 🚀 launch emoji flag pinned at `lab2End`

---

## 8. Sales Trip Pins

Non-Jira events pinned as vertical markers on the timeline per regulated release.

| Release | Date | Label | Status |
|---|---|---|---|
| V2 P2P 16.00 (Georgia) | 2026-06-27 | ✈ Sales Trip · Georgia | Confirmed |
| V2 PT 14.00 (Ohio) | TBD | ✈ Sales Trip · Ohio | TBD |

### 8.1 FV Data Fields

```javascript
// Confirmed date:
salesTrip: { date: '2026-06-27', label: 'Sales Trip · Georgia' }

// TBD:
salesTrip: { tbd: true, label: 'Sales Trip · Ohio' }
```

### 8.2 Rendering

- **Confirmed:** Amber vertical line (`.sales-pin`) + amber label tag (`.sales-tag`) positioned at `top:32px` — below Dev/QA bars, above department rows
- **TBD:** Dashed grey label (`.sales-tag-tbd`) floating near dev complete date

---

## 9. Sprint Configuration

Sprints are 2-week cycles starting every other Monday from S1. Chart currently runs S1–S15.

```javascript
const SPRINTS = [
  { label: 'S1',  start: '2026-05-11' },
  { label: 'S2',  start: '2026-05-25' },
  { label: 'S3',  start: '2026-06-08' },
  { label: 'S4',  start: '2026-06-22' },
  { label: 'S5',  start: '2026-07-06' },
  { label: 'S6',  start: '2026-07-20' },
  { label: 'S7',  start: '2026-08-03' },
  { label: 'S8',  start: '2026-08-17' },
  { label: 'S9',  start: '2026-08-31' },
  { label: 'S10', start: '2026-09-14' },
  { label: 'S11', start: '2026-09-28' },
  { label: 'S12', start: '2026-10-12' },
  { label: 'S13', start: '2026-10-26' },
  { label: 'S14', start: '2026-11-09' },
  { label: 'S15', start: '2026-11-23' },
  { label: '',    start: '2026-12-07' }, // sentinel end
];
```

Sprint chips in the axis show the **full date range**: `S2 / May 25 – Jun 7`. Sprint boundary lines run through all rows as subtle vertical guides (`rgba(148,163,184,.22)`).

**Chart bounds:** `CHART_START = 2026-05-11`, `CHART_END = 2026-12-07`

---

## 10. UI Structure

### 10.1 Gantt Row (per fix version)

```
[ Label (214px) ] [ Track (flex) .............................................. ] [▾]
  - FV name (accent colour)         - Sprint boundary lines (subtle vertical)
  - Subtitle                        - TODAY line (red vertical, runs full height)
  - Status badge                    - Row 1 (top:8px, h:22px):
  - 🔬 Regulated badge (if lab)         Dev bar  [Dev → date]  QA bar  [QA → date]
                                        Lab 1 bar  Pilot bar  Lab 2 bar  🚀 (if lab)
                                    - Sales Trip pin + tag (top:32px, if present)
                                    - Dept rows (stacked from top:34px, 20px each):
                                        CREATIVE  [proportional bar → date]
                                        MATH      [proportional bar → date]
                                        SOUND     [proportional bar → date]
                                    - Resource pills row (top: dynamic, after dept rows)
```

### 10.2 Department Bars (Creative / Math / Sound)

Each department that has remaining hours gets its own row beneath the Dev/QA bar. Layout:

```
[CREATIVE badge 58px] [dept-track: proportional bar → Jun 12]
[MATH    badge 58px] [dept-track: proportional bar → May 27]
[SOUND   badge 58px] [dept-track: proportional bar → Jun 5 ]
```

Only types with `hours > 0` on open tasks are rendered. Row height and pill position are calculated dynamically:

```javascript
const typeRowCount = [hasCreH, hasMatH, hasSndH].filter(Boolean).length;
const pillsTop = 34 + typeRowCount * 20 + (typeRowCount > 0 ? 4 : 0);
const minH = pillsTop + (devPills.length > 0 ? 30 : 12);
```

### 10.3 Dept Bar Colours

| Type | Badge colour | Bar background | Bar border |
|---|---|---|---|
| Creative | `#4c1d95` | `rgba(124,58,237,.10)` | `rgba(167,139,250,.5)` |
| Math | `#0f766e` | `rgba(13,148,136,.10)` | `rgba(20,184,166,.5)` |
| Sound | `#9d174d` | `rgba(219,39,119,.10)` | `rgba(236,72,153,.5)` |

### 10.4 TODAY Indicator

TODAY is rendered in the **axis header** (not inside any row) as a badge with a downward-pointing triangle arrow:

```css
.ax-today { bottom: 3px; /* sits at bottom of axis, arrow points into rows */ }
.ax-today::after { border-top-color: rgba(185,28,28,.5); /* downward arrow */ }
```

A red vertical line (`.today-line`, `z-index:3`) still passes through all rows.

### 10.5 Resource Pills

One pill per developer with active work:
```
Hemmat R. · 136h · 75% ⛓     Justin K. · 115h · 75%     Others · 8 tasks · 25.75h
```
- `⛓` = bottleneck (this person determines dev complete date for this release)
- Pills appear below all department rows; `top` position is computed dynamically

### 10.6 Detail Panel (drill-down)

**Header:** FV name, subtitle/note, dates. Stats row: Dev remaining (h), Dev open (count), Dev done (count), Others remaining (h), Others open, Others done.

**Tabs:** `DEV WORK` | `CREATIVE · SOUND · MATH` (only shown if others tasks exist)

**Person columns:** Name → remaining hours → type (if others) → buffer slider → queued hours indicator → projected done date → task cards

### 10.7 Task Cards

```
V2-29201  (clickable → opens Jira)
System — Show pending hard meter updates in MM
24h remaining                    [In Progress]
```

Hour colour coding:
- `≥ 40h` → red · `≥ 16h` → amber · `> 0h` → green · `0h` → grey ("0h logged")

---

## 11. Colour System (Light Mode)

### 11.1 CSS Variables

```css
:root {
  --bg:      #f4f6fb;
  --surface: #ffffff;
  --surf2:   #f0f3f9;
  --surf3:   #e8ecf4;
  --border:  #dde2ee;
  --border2: #c8cedf;
  --muted:   #6b7a99;
  --text:    #1a2035;
  --sub:     #8a94ad;
  --dev:     #2563eb;  --dev-bg: rgba(37,99,235,.10);
  --qa:      #d97706;  --qa-bg:  rgba(217,119,6,.12);
  --done:    #16a34a;  --done-bg:rgba(22,163,74,.11);
  --oth:     #7c3aed;  --oth-bg: rgba(124,58,237,.10);
}
```

### 11.2 Fix Version Accent Colours

| Fix Version | Colour |
|---|---|
| V2 SW 15.00 | `#60a5fa` (blue) |
| V2 SW 16.00 | `#93c5fd` (light blue) |
| V2 PT 13.30 | `#c4b5fd` (violet) |
| V2 P2P 16.00 | `#fb923c` (orange) |
| V2 HHR 3.00 | `#4ade80` (green) |
| V2 PT 14.00 | `#f87171` (red) |

### 11.3 Fonts

- Body: `IBM Plex Sans` (weights 300, 400, 500, 600)
- Monospace (hours, keys, projections): `IBM Plex Mono` (weights 400, 500)
- Source: Google Fonts CDN

---

## 12. People Reference (as of May 19, 2026)

### 12.1 Developers (Dev group)

| Name | Role | Default Buffer | Key Notes |
|---|---|---|---|
| Rejosh Samuel | Lead Dev / Release Manager | **50%** | Touches every release; small remaining hours after Build Tool completion |
| Aleksey Vorotilin | Developer | 75% | Wwise specialist; 88h on P2P 16 (Wwise update + volume buttons) |
| Hemmat Rezvani | Developer | 75% | **Major bottleneck on P2P 16** (136h mech meters); 64h on PT 14 |
| Justin Kwon | Developer | 75% | **Major bottleneck on P2P 16** (115h); **bottleneck on PT 13.30** (68h) |
| Krupa Kanani | Server Dev | 75% | Server testing across all releases; bottleneck on PT 13.30 (24h) and HHR 3 |
| Linda Zhang | Developer | 75% | Game ports; active on SW 15 QA |
| Asif Masood | Backend Dev | 75% | **Bottleneck on PT 14** (391.5h Ohio/RabbitMQ); also active on P2P 16 |
| Yves Griber | Developer | 75% | **Bottleneck on SW 16** (44h offline upgrader testing); small P2P work |
| Niloo Tehrani | Developer | 75% | Zombie Hunt PT (27h); volume buttons P2P/PT |
| Maria Vlah | Developer | 75% | Jewels of Cleopatra + Arctic Buffalo PT conversions (17.25h) |

### 12.2 Others (Creative / Sound / Math group)

| Name | Type | Appears in | Key Hours |
|---|---|---|---|
| Amanda Langford | Creative | SW 15, PT 13.30, P2P 16, HHR 3 | Mostly 0h (tasks unestimated) |
| John Di Mauro | Creative | SW 15 | 0h (tasks unestimated) |
| Amer Nabulsi | Creative | P2P 16 | 0h (tasks unestimated) |
| Joel Kazmi | Sound | P2P 16 | **11.75h** Wwise game sound |
| Sonali Mehra | Math | SW 15, SW 16, PT 13.30, P2P 16, HHR 3 | 0.5h SW 15 denom sheet |
| Seyeon Oh | Math | P2P 16 | **14h** Viking's Voyage GDD |

---

## 13. Bottleneck Logic

A person is flagged as ⛓ bottleneck if:
1. They have the `bottleneck: true` flag on their person record, OR
2. Any of their tasks has `bottleneck: true`

In the dynamic version, the bottleneck should be **auto-calculated** as the person whose projected done date is latest within a given fix version (i.e. determines the dev complete date for that release).

Current bottlenecks (May 19, 2026):
- **SW 15:** Justin Kwon (8.5h remaining after Build Tool done)
- **SW 16:** Yves Griber (44h offline upgrader testing)
- **PT 13.30:** Justin Kwon (68h volume buttons + Fruit Splitter)
- **P2P 16:** Hemmat Rezvani (136.25h mechanical meters)
- **HHR 3.00:** Hemmat Rezvani (54.5h user role restrictions, but ~164h queued ahead)
- **PT 14.00:** Asif Masood (391.5h Ohio Central Server / RabbitMQ)

---

## 14. Dynamic Version — Architecture Notes for Claude Code

### 14.1 Recommended Stack

- **Backend:** Node.js or Python service that proxies Jira API calls (avoids CORS, keeps credentials server-side)
- **Frontend:** Single-page app (React or vanilla JS) that calls the backend
- **Auth:** Jira API token (Basic auth: `email:token` base64 encoded) — never expose in frontend

### 14.2 Data Fetch Strategy

For each fix version, make **two JQL queries**:

**Query 1 — Dev work (parent tasks + subtasks):**
```jql
project = V2 AND fixVersion = "{version}" 
AND issuetype in ("Story","Dev Task","Dev Subtask")
AND summary !~ "Release" AND summary !~ "Merge"
AND statusCategory != Done
ORDER BY assignee ASC
```

**Query 2 — Creative/Sound/Math work:**
```jql
project = V2 AND fixVersion = "{version}"
AND issuetype in ("Creative Task","Creative Subtask","Sound Task","Sound Subtask","Math Task","Math Subtask","Pre-Prod Task")
AND statusCategory != Done
ORDER BY assignee ASC
```

Also fetch closed/done tasks for the "completed" section:
```jql
... AND statusCategory = Done ORDER BY assignee ASC
```

### 14.3 Data Transformation

```javascript
function buildPersonData(issues) {
  const byAssignee = {};
  for (const issue of issues) {
    const name = issue.fields.assignee?.displayName ?? 'Unassigned';
    const hours = (issue.fields.timeestimate ?? 0) / 3600;
    const summary = issue.fields.summary;
    
    // Skip Release/Merge tasks and their children
    if (/release|merge code/i.test(summary)) continue;
    if (isChildOfReleaseMerge(issue)) continue;

    if (!byAssignee[name]) byAssignee[name] = { name, tasks: [] };
    byAssignee[name].tasks.push({
      key: issue.key,
      summary,
      hours,
      status: issue.fields.status.name,
      issuetype: issue.fields.issuetype.name,
    });
  }
  return Object.values(byAssignee);
}
```

### 14.4 Cross-Release Queued Hours (Auto-calculation)

```javascript
function calcQueuedHours(personName, currentFvIndex, allFvData) {
  let queued = 0;
  for (let i = 0; i < currentFvIndex; i++) {
    const higherFv = allFvData[i];
    const person = higherFv.devPeople.find(p => p.name === personName);
    if (person) {
      queued += person.tasks
        .filter(t => !isDone(t.status))
        .reduce((s, t) => s + t.hours, 0);
    }
  }
  return queued;
}
```

### 14.5 Buffer Persistence

```javascript
const BUFFER_KEY = 'v2_timeline_buffers';
function loadBuffers() {
  try { return JSON.parse(localStorage.getItem(BUFFER_KEY)) ?? {}; }
  catch { return {}; }
}
function saveBuffers(buffers) {
  localStorage.setItem(BUFFER_KEY, JSON.stringify(buffers));
}
```

### 14.6 Refresh Cadence

- Add a **"Refresh from Jira"** button that re-fetches all data
- Show a **"Last updated: {timestamp}"** in the header
- Consider auto-refresh every 30 minutes if the page stays open
- Cache Jira responses with a short TTL (5 minutes) to avoid hammering the API

### 14.7 Additional Features to Add

- [ ] **Export to PNG/PDF** of the Gantt view
- [ ] **Date override controls** — allow manually setting a dev complete date if Jira data is unreliable
- [ ] **"Flag unlogged hours"** — highlight tasks that are In Progress but have 0h remaining
- [ ] **Resource view** — pivot from "by fix version" to "by person" showing all their tasks across versions
- [ ] **Sprint velocity** — compare planned vs actual completion per sprint
- [ ] **Fix version config panel** — allow changing QA weeks / lab weeks in the UI, not just code
- [ ] **Lab phase config** — make lab1Weeks, pilotWeeks, lab2Weeks editable per release
- [ ] **Sales Trip date editor** — allow setting/updating sales trip date in UI for TBD entries

---

## 15. Key Business Rules Summary

1. **Exclude:** Bugs, Enhancements, QA Tasks/Subtasks, Release tasks + all subtasks, Merge tasks + all subtasks
2. **Include:** Dev Tasks, Dev Subtasks, Stories (dev), Creative Tasks/Subtasks, Sound Tasks/Subtasks, Math Tasks/Subtasks, Pre-Prod Tasks
3. **Remaining hours** must be pulled from **subtask level**, not parent task level
4. **Release/Merge subtasks** have summaries like "Prepare Builds", "Release", "Deployment", "Prepare Release Notes", "Merge Code — {Name}", "Update Payout Sheet" — all excluded
5. **Dev complete** = latest projected date across all assignees for a fix version
6. **QA window** = fixed **work days** (Mon–Fri, excluding holidays) after dev complete — 2 weeks = 10 work days, 3 weeks = 15 work days
7. **Lab pipeline** = QA → Lab 1+Rev (4wks) → Pilot (2wks) → Lab 2+Rev (4wks) → Launch — for **P2P 16.00 and PT 14.00 only**
8. **Rejosh defaults to 50%** availability (all other devs 75%)
9. **May 18, 2026** is a holiday — skip in work day calculations
10. **Sprint 1 starts May 11, 2026** — new sprint every 14 days (every other Monday)
11. **"V2 SW P2P 16.00"** (whiteboard label) = `V2 SW 16.00` in Jira — needs team confirmation
12. **Dept bars (Creative/Math/Sound)** are stacked under the Dev/QA row — only rendered if the type has open hours > 0
13. **TODAY** is shown in the axis header (above all rows) with a downward arrow, not inside rows
14. **Sales Trip pins** sit at `top:32px` inside the track — below Dev/QA/Lab bars, above dept rows

---

## 16. File Reference

| File | Description |
|---|---|
| `v2_release_timeline.html` | Current working prototype — single HTML file, all data hardcoded, fully interactive |
| `V2_RELEASE_TIMELINE_KNOWLEDGE.md` | This file — authoritative reference for rebuilding dynamically |

---

*Last updated: May 19, 2026 · Pong Game Studios PMO*
