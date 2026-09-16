# ELG Game Cost Model

Delivery-cost knowledge for iGaming (project IG) ELG games at Pong Game Studios. Generated from Jira by `game_cost_report.py`; every figure below is measured, not estimated.

## Scope

- **Sample**: 8 games from the last ELG game-bearing releases (ELG 4.20, ELG 4.30, ELG 4.40, ELG 4.50, ELG 4.70).
- **Tickets traced**: 981, walking epic -> children -> subtasks.
- **Total estimated**: 5,424h. **Total actual**: 4,356h. **Actual / Estimate: 0.80**.
- **Categories**: Port, Skin, Branded, New. No New game has ever shipped to ELG -- everything ELG is built in V2 first and ported.

## Headline finding

**The 2024 t-shirt bands do not fail high. They fail to scale down.**

Large Ports land close to estimate; small games come in at roughly half. Cutting every band by a flat percentage would break the large end that already works -- the correct fix is to steepen the small sizes.

| Game | Category | Est h | Actual h | Actual/Est |
|---|---|---:|---:|---:|
| Flaming Skulls | Port | 1,678 | 1,563 | 0.93 |
| Northern Buffalo Fortune [V] | Port | 983 | 1,022 | 1.04 |
| Lost Totem | Port | 820 | 682 | 0.83 |
| Viking's Voyage of Fortune | Port | 814 | 582 | 0.71 |
| Lantern's Rising [V] | Port | 422 | 154 | 0.37 |
| American Wins | Skin | 301 | 141 | 0.47 |
| Fortune Diamond 10X | Skin | 226 | 122 | 0.54 |
| Luck Party Blue Bird Bonanza | Branded | 180 | 90 | 0.50 |

## Budget per game by category

Build hours plus 29.79h release overhead per game (208h across 7 game epics).

| Category | Games | Avg build h | + Release o/h | Budget h | Confidence |
|---|---:|---:|---:|---:|---|
| Port | 5 | 801 | 29.79 | 830 | Moderate (n=5) |
| Skin | 2 | 131 | 29.79 | 161 | Low (n=2) |
| Branded | 1 | 90 | 29.79 | 120 | Indicative only (n=1) |
| New | 0 | 0 | 29.79 | 0 | NO DATA - cannot budget |

> Only Port rests on enough games to average. Skin (n=2) and Branded (n=1) are single data points -- a starting hypothesis, not a budget. New cannot be budgeted from delivery data at all.

## Department cost profile

Across all 8 games, full-game scope. `Actual/Est` below 0.80 means the department is over-estimated; above 1.15 means under-estimated.

| Department | Est h | Actual h | Actual/Est | Read as |
|---|---:|---:|---:|---|
| Concept | 69 | 30 | 0.43 | OVER-estimated ~2x |
| Creative | 657 | 528 | 0.80 | about right |
| Math | 362 | 244 | 0.67 | over-estimated |
| Sound | 322 | 139 | 0.43 | OVER-estimated ~2x |
| Game Engine | 736 | 502 | 0.68 | over-estimated |
| Server | 772 | 432 | 0.56 | OVER-estimated ~2x |
| Dev | 982 | 847 | 0.86 | about right |
| Review | 561 | 595 | 1.06 | about right |
| QA | 539 | 554 | 1.03 | about right |
| Bugs | 424 | 486 | 1.15 | about right |

Departments the 2024 legend never priced at all: Concept, QA, Bugs, Release. That is where the hidden cost lives -- real spend no estimate ever accounted for.

## How work maps to departments

Issue type alone is NOT reliable in project IG: Sound work is typed `Dev Subtask`, a 43h Review story is typed `Story`. Summary-pattern rules are therefore evaluated BEFORE issue-type fallbacks. First match wins, top to bottom:

| # | Department | Match on | Pattern / issue types |
|---:|---|---|---|
| 1 | Bugs | type | `Bug`, `Live Issue` |
| 2 | QA | type | `QA Subtask`, `QA Task` |
| 3 | Release | type | `Release`, `Release Subtask` |
| 4 | Review | text | `review\s*(&\|and)\s*refinement\|review\s*[-–]\s*(dev\|math\|art\|sound\|creative\|qa)\|code review\|game review\|art review\|review changes\|implement review\|[-–]\s*review\s*$` |
| 5 | Concept | text | `design doc\|concept layout\|\bgdd\b\|game info package\|\bgip\b\|[-–]\s*concept\b` |
| 6 | Server | text | `\[server\]` |
| 7 | Game Engine | text | `\[ge\]` |
| 8 | Math | text | `\[math\]` |
| 9 | Dev | text | `\[fe\]` |
| 10 | Server | text | `tickets?\s*(&\|and)\s*pools\|prizes,\s*tickets\|prepare and verify pools\|weighted outcome\|game config\|\bconfigs?\b\|\bpools?\b\|\bdeploy\w*\s+(on\|to)\b` |
| 11 | Game Engine | text | `game engine\|simulation\|simulator` |
| 12 | Sound | text | `\bsounds?\b\|\bsfx\b\|\baudio\b\|\bmusic\b\|wwise\|soundtrack` |
| 13 | Math | text | `\bmath\b\|par sheet\|\brtp\b` |
| 14 | Creative | type | `Creative Subtask`, `Creative Task`, `Design Sub-Task`, `Design Subtask`, `Design Task` |
| 15 | Math | type | `Math Subtask`, `Math Task` |
| 16 | Sound | type | `Sound Subtask`, `Sound Task` |
| 17 | Dev | type | `Dev Subtask`, `Dev Task`, `Story`, `Task` |

Notes on deliberate calls:

- Bracket tags (`[Server]`, `[GE]`, `[Math]`, `[FE]`) are authoritative and beat keywords -- they are the newer naming convention.
- Server sits ABOVE Game Engine in keyword order: "deployment on New Game Engine" is releasing onto the engine platform, not building the engine.
- All `Review & Refinement` / `Review - <dept>` work is routed to **Review**, not to the department being reviewed.
- `[FE] - Sounds` lands in Dev, because the bracket tag wins. Debatable (~14h).

## Jira traversal rules (hard-won)

- **Search endpoint**: only `POST /rest/api/3/search/jql` works. `GET /search` returns 410 Gone.
- **Paging**: use `nextPageToken`, never `startAt`. 100 rows per page.
- **Chunking**: `parent in (...)` must be chunked to ~8 keys or tickets are silently lost.
- **Two-stage traversal**: `parent = EPIC` for children, then `parent in (child keys)` for subtasks. Recursive JQL is unreliable on this plan tier.
- **Bugs carry subtasks too** -- do not exclude Bug/Live Issue/Enhancement when collecting parents (33 subtasks hide there).
- **Rollup**: sum each ticket's own `timespent` across the walked tree. Epic-level `aggregatetimespent` only rolls up one level and understates by 5-20x.
- **Hours**: keep raw seconds, convert once with `round(secs/3600, 2)`. Integer division drops half-hours.
- **Never attribute by assignee**: devs reassign to the Dev Lead at the Review handoff, so the assignee field credits the wrong person. Jira's worklog author is the Tempo app account and is useless. Rebuild from changelog if per-person attribution is ever needed.
- **Naming drift**: older games use `Game Engine - X`; newer use `[GE] - Simulation` and `[Server] - X`. Match both. `[Server] - Tickets & Pools` and `[Server] - Game config` contain no "engine" keyword and are easy to miss.

## Corrections to previously held rules

- **IG epics DO carry fix versions.** The standing rule "epics carry no fix version" is false: 7 of these 8 epics are tagged. Only Flaming Skulls (IG-1506) has no ELG version on the epic. Still test ELG membership at story/subtask level, but any "games per release" count built on epic fixVersion undercounts.
- **PFH leakage is smaller than assumed.** ELG-only scope captures 3,961h of 4,356h; the gap is 395h (9.1%), confined to the three oldest Ports. The single largest item is QA tagged `New Games - iGaming`, not PFH.

## Data hygiene

- **140 tickets carry 998h of estimate with zero time logged.** This is the single reason the bands were never validated: there was nothing to compare an actual against. Fix this first.
- **99 tickets (229h)** sit under an ELG game epic but carry a non-ELG fix version (PFH, Horse Play, 'New Games - iGaming').
- **50 tickets (166h)** carry no fix version at all and cannot be attributed to any release.
- **12 tickets (39h)** have time logged against no estimate -- invisible to any capacity forecast.

## What this model cannot tell you

- **New-game cost.** Zero delivery data. Do not infer it from Port.
- **Per-person or per-team cost.** Assignee is unreliable (see above).
- **Anything at size granularity.** These are whole-game actuals; the sample cannot say what an "M Port" costs, only what five real Ports cost.
- **Branded as a category.** One game. Treat 'Branded' figures as that one game's number wearing a category label.

