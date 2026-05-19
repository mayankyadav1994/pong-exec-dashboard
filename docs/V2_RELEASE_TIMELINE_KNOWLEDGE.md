# V2 Release Timeline — Knowledge Base
**Project:** Pong Game Studios · V2 Fix Version Delivery Forecast  
**Jira Instance:** `ponggamestudios.atlassian.net` · Project key: `V2`  
**Cloud ID:** `85005dc7-cde3-4a2c-8e65-2d746db228ed`  
**Current prototype:** `v2-timeline.html` (single-file static report, built iteratively in Claude)  
**Goal for Claude Code:** Rebuild this as a fully dynamic report that pulls live from the Jira API on demand.

---

## 1. Purpose

A Gantt-style fix version delivery forecast that shows:
- Dev complete date per fix version (projected from remaining hours + resource availability)
- QA window per fix version (fixed duration added after dev complete)
- Per-resource task breakdown with remaining hours (dev work separately from creative/sound/math)
- Per-resource availability buffers adjustable interactively
- Sprint boundaries as visual reference lines

---

## 2. Fix Versions in Scope (Priority Order)

| # | Fix Version | Jira Name | Theme | Dev Status | QA Weeks |
|---|---|---|---|---|---|
| 1 | V2 SW 15.00 | `V2 SW 15.00` | Sweepstakes · Games | **In QA** (dev complete) | 2 |
| 2 | V2 SW 16.00 | `V2 SW 16.00` | Offline Upgrader · Pay It Forward | In Development | 2 |
| 3 | V2 PT 13.30 | `V2 PT 13.30` | 8× Game Ports · Server Cleanup | In Development | 2 |
| 4 | V2 P2P 16.00 | `V2 P2P 16.00` | Georgia P2P · Task Handler R7 | In Development | **3** |
| 5 | V2 HHR 3.00 | `V2 HHR 3.00` | User Role Restrictions · Maintenance Menu | In Development | 2 |
| 6 | V2 PT 14.00 | `V2 PT 14.00` | Ohio Central Server · Location Server | Scheduled | **3** |

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

**Parent stories/tasks often have 0h remaining.** The actual time estimates live on **subtasks**. Always query both parent tasks and their subtasks. The Jira "Version Report" (shown in screenshot) confirms this — e.g. V2 SW 16.00 shows 112.25h total, almost all on `Dev Subtask` type.

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

| Resource | Fix Version | Queued Hours | Reason |
|---|---|---|---|
| Rejosh Samuel | V2 SW 16.00 | 40h | SW 15.00 Build Tool task still in progress |
| Rejosh Samuel | V2 P2P 16.00 | 45h | SW 15.00 (40h) + SW 16.00 (5h) must complete first |
| Krupa Kanani | V2 P2P 16.00 | 36h | PT 13.30 testing (24h) + some overlap buffer |
| Krupa Kanani | V2 HHR 3.00 | 48h | PT 13.30 (24h) + P2P (12h) + buffer |

> In the dynamic version, queued hours should be auto-calculated by summing a person's open hours on all higher-priority fix versions.

---

## 6. QA Window

```javascript
const qaEnd = addWD(devEnd, fv.qaWeeks * 5 * 8, 8);
// qaWeeks × 5 work days × 8h/day, skipping weekends and holidays
// Uses the same addWD() function as dev, with hpd=8 (no availability buffer on duration)
```

QA duration is counted in **work days (Mon–Fri)**, skipping weekends and public holidays. It accounts for both QA testing and bug fixing cycles. QA team works a standard 5-day week, same cadence as sprints.

| Fix Version | QA Duration |
|---|---|
| V2 SW 15.00 | 2 weeks (14 days) |
| V2 SW 16.00 | 2 weeks |
| V2 PT 13.30 | 2 weeks |
| V2 P2P 16.00 | **3 weeks (21 days)** |
| V2 HHR 3.00 | 2 weeks |
| V2 PT 14.00 | **3 weeks** |

P2P 16.00 and PT 14.00 have 3-week QA due to higher complexity (multi-jurisdiction, large scope).

> All teams work **Monday to Friday, 5 days/week**. QA windows skip weekends and public holidays (same HOLIDAYS list as dev). 2 weeks = 10 work days, 3 weeks = 15 work days.

---

## 7. Sprint Configuration

Sprints are 2-week cycles starting every other Monday from S1.

```javascript
const SPRINTS = [
  { label: 'S1', start: '2026-05-11' },
  { label: 'S2', start: '2026-05-25' },
  { label: 'S3', start: '2026-06-08' },
  { label: 'S4', start: '2026-06-22' },
  { label: 'S5', start: '2026-07-06' },
  { label: 'S6', start: '2026-07-20' },
];
```

Sprint labels and start dates render as chips on the timeline axis. Each sprint label shows the sprint name + start date (e.g. `S2 / May 25`).

---

## 8. UI Structure

### 8.1 Gantt Row (per fix version)

```
[ Label (214px) ] [ Track (flex) .............................................. ] [▾]
  - FV name          - Sprint boundary lines (dashed, low opacity)
  - Subtitle         - TODAY marker (red vertical line)
  - Status badge     - Dev bar (blue, shows "Dev → {date}")
                     - QA bar (amber, shows "QA → {date}")
                     - Others bar (purple, thin, shows "Others → {date}") if hours exist
                     - Resource pills row: Name · Xh · Y% [⛓ if bottleneck]
                       + Others pill: "Others · N tasks · Xh"
```

The left label has a **3px colour bar** on the far left edge matching the fix version's accent colour.

### 8.2 Resource Pills

One pill per developer with tracked hours or active tasks:
```
Krupa K. · 24h · 75% ⛓
Aleksey V. · 40h · 75%
Others · 27 tasks · 31h
```
- `⛓` = bottleneck (latest projected done date for this release)
- Buffer % shown so user can see current setting at a glance
- Buffer % updates live when sliders are adjusted in the detail panel

### 8.3 Detail Panel (drill-down, toggled by clicking Gantt row)

**Header:** Fix version name, subtitle/note, dates. Stats row: Dev remaining (h), Dev open (count), Dev done (count), Others remaining (h), Others open, Others done.

**Tabs:** `DEV WORK` | `CREATIVE · SOUND · MATH` (only shown if others tasks exist)

**Person columns** (one per assigned resource):
- Name (red if bottleneck, purple if "others" type)
- Remaining hours (large, coloured)
- Type label (Creative / Sound / Math for others)
- **Buffer slider** (10%–100%, step 5%) with live readouts:
  - `AVAILABILITY BUFFER: 75%`
  - `6.0h/day effective`
  - `+Xh cross-release queue` (if applicable)
  - `Proj. done: → Jun 8` (updates live on drag)
- Task cards (open tasks, then collapsed "X completed" section)

### 8.4 Task Cards

```
V2-29195  (clickable → opens Jira)
Server — PT testing after code cleanup
24h remaining                    [To Do]
```

Colour coding for remaining hours:
- `≥ 40h` → red
- `≥ 16h` → amber  
- `> 0h` → green
- `0h` → grey ("0h logged" — estimate not entered)

### 8.5 Status Badges (fix version label)

| Phase | Label | Colour |
|---|---|---|
| Dev complete, in QA testing | `In QA` | Amber |
| Development started/wrapping | `In Development` | Blue |
| Not yet started | `Scheduled` | Red |

---

## 9. Colour System (Light Mode)

### 9.1 CSS Variables

```css
:root {
  --bg:      #f4f6fb;
  --surface: #ffffff;
  --surf2:   #f0f3f9;
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

### 9.2 Fix Version Accent Colours

| Fix Version | Colour |
|---|---|
| V2 SW 15.00 | `#60a5fa` (blue) |
| V2 SW 16.00 | `#93c5fd` (light blue) |
| V2 PT 13.30 | `#c4b5fd` (violet) |
| V2 P2P 16.00 | `#fb923c` (orange) |
| V2 HHR 3.00 | `#4ade80` (green) |
| V2 PT 14.00 | `#f87171` (red) |

### 9.3 Fonts

- Body: `IBM Plex Sans` (weights 300, 400, 500, 600)
- Monospace (hours, keys, projections): `IBM Plex Mono` (weights 400, 500)
- Source: Google Fonts CDN

---

## 10. People Reference

### 10.1 Developers (Dev group)

| Name | Role | Default Buffer | Notes |
|---|---|---|---|
| Rejosh Samuel | Lead Dev / Release Manager | **50%** | Touches every fix version; bottleneck on SW 16, P2P 16 |
| Aleksey Vorotilin | Developer | 75% | Wwise specialist; bottleneck on P2P 16 (40h) |
| Hemmat Rezvani | Developer | 75% | System-level work; 28h on SW 16 |
| Justin Kwon | Developer | 75% | Common modules / popups |
| Krupa Kanani | Server Dev | 75% | Server testing; bottleneck on PT 13.30, HHR 3 |
| Linda Zhang | Developer | 75% | Game ports / QA overlap |
| Asif Masood | Backend Dev | 75% | Location Server architecture; bottleneck on PT 14 (222h) |
| Yves Griber | Developer | 75% | Offline Upgrader (SW 16) |

### 10.2 Others (Creative / Sound / Math group)

| Name | Type | Appears in |
|---|---|---|
| Amanda Langford | Creative | SW 15, PT 13.30, P2P 16, HHR 3 |
| John Di Mauro | Creative | SW 15 |
| Amer Nabulsi | Creative | P2P 16 |
| Joel Kazmi | Sound | P2P 16 (Wwise, 17h remaining) |
| Sonali Mehra | Math | SW 15, SW 16, PT 13.30, P2P 16, HHR 3 |
| Seyeon Oh | Math | P2P 16 (Viking's Voyage GDD, 14h remaining) |

---

## 11. Bottleneck Logic

A person is flagged as ⛓ bottleneck if:
1. They have the `bottleneck: true` flag on their person record, OR
2. Any of their tasks has `bottleneck: true`

In the dynamic version, the bottleneck should be **auto-calculated** as the person whose projected done date is latest within a given fix version (i.e. determines the dev complete date for that release).

---

## 12. Dynamic Version — Architecture Notes for Claude Code

### 12.1 Recommended Stack

- **Backend:** Node.js or Python service that proxies Jira API calls (avoids CORS, keeps credentials server-side)
- **Frontend:** Single-page app (React or vanilla JS) that calls the backend
- **Auth:** Jira API token (Basic auth: `email:token` base64 encoded) — never expose in frontend

### 12.2 Data Fetch Strategy

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

### 12.3 Data Transformation

```javascript
// Group issues by assignee → person
// Sum timeestimate (seconds) → remaining hours
// Flag if parent task summary contains "Release" or "Merge" → exclude
// Identify bottleneck per fix version = person with max projected done date

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

### 12.4 Cross-Release Queued Hours (Auto-calculation)

```javascript
// For each person, sum their remaining hours across all higher-priority fix versions
// Use that sum as queuedHours on lower-priority versions

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

### 12.5 Buffer Persistence

In the dynamic version, save buffer settings to `localStorage` so they persist across page refreshes:

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

### 12.6 Refresh Cadence

- Add a **"Refresh from Jira"** button that re-fetches all data
- Show a **"Last updated: {timestamp}"** in the header
- Consider auto-refresh every 30 minutes if the page stays open
- Cache Jira responses with a short TTL (5 minutes) to avoid hammering the API

### 12.7 Additional Features to Add

- [ ] **Export to PNG/PDF** of the Gantt view
- [ ] **Date override controls** — allow manually setting a dev complete date if Jira data is unreliable
- [ ] **"Flag unlogged hours"** — highlight tasks that are In Progress but have 0h remaining
- [ ] **Resource view** — pivot from "by fix version" to "by person" showing all their tasks across versions
- [ ] **Sprint velocity** — compare planned vs actual completion per sprint
- [ ] **Fix version config panel** — allow changing QA weeks duration in the UI, not just code

---

## 13. Key Business Rules Summary

1. **Exclude:** Bugs, Enhancements, QA Tasks/Subtasks, Release tasks + all subtasks, Merge tasks + all subtasks
2. **Include:** Dev Tasks, Dev Subtasks, Stories (dev), Creative Tasks/Subtasks, Sound Tasks/Subtasks, Math Tasks/Subtasks, Pre-Prod Tasks
3. **Remaining hours** must be pulled from **subtask level**, not parent task level
4. **Release/Merge subtasks** have summaries like "Prepare Builds", "Release", "Deployment", "Prepare Release Notes", "Merge Code — {Name}", "Update Payout Sheet" — all excluded
5. **Dev complete** = latest projected date across all assignees for a fix version
6. **QA window** = fixed **work days** (Mon–Fri, excluding holidays) after dev complete — 2 weeks = 10 work days, 3 weeks = 15 work days
7. **Rejosh defaults to 50%** availability (all other devs 75%)
8. **May 18, 2026** is a holiday — skip in work day calculations
9. **Sprint 1 starts May 11, 2026** — new sprint every 14 days (every other Monday)
10. **"V2 SW P2P 16.00"** (whiteboard label) = `V2 SW 16.00` in Jira — needs team confirmation

---

## 14. File Reference

| File | Description |
|---|---|
| `v2-timeline.html` | Current working prototype — single HTML file, all data hardcoded, fully interactive |
| `V2_RELEASE_TIMELINE_KNOWLEDGE.md` | This file — authoritative reference for rebuilding dynamically |

---

*Last updated: May 14, 2026 · Pong Game Studios PMO*
