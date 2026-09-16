# ELG Game Cost Model

Operating guide for the ELG delivery-cost dashboard — what each figure means, and
how to change what the model covers. Read this when:

- You need to add a game to the model, or drop one
- A number on the page doesn't match your intuition and you want to trace it
- You're about to change the classification rules and want to know what moves

The page is built by [elg-cost-model/](../elg-cost-model/) and served at
<https://mayankyadav1994.github.io/pong-exec-dashboard/elg-cost-model.html>.
That URL is **public**, like the rest of this Pages site.

---

## 1. The pipeline

Four files, split so the slow part runs once and the fast parts rebuild instantly:

```
games.json               the model's inputs: games, release epics, hourly rate
elg_cost_data.py         Jira  -> elg_cost_data.json       ~2 min, hits the network
elg_cost_spreadsheet.py  cache -> .xlsx + .md              instant, offline
elg_cost_page.py         cache -> ../elg-cost-model.html   instant, offline
page_template.html       the page's layout, copy and styling
```

The cache (`elg_cost_data.json`) and the workbook are **gitignored** — they are
rebuilt, never versioned. The built page goes to the repo root because
`deploy-pages.yml` uploads the whole repo, so there is no copy-to-root step to
forget.

`elg_cost_data.py` needs `JIRA_API_TOKEN` in the environment (it also self-heals
from `HKCU\Environment`). The other two never touch the network.

---

## 2. Adding a game

**The game list lives in `games.json`, not in the Python.** `elg_cost_data.py`
manages it for you — no code edit is needed.

```bash
cd elg-cost-model

python elg_cost_data.py --list                     # what is in the model now
python elg_cost_data.py --find buffalo             # search IG epics by summary
python elg_cost_data.py --add IG-7781 --cat Port   # add one
python elg_cost_data.py --remove IG-7781           # drop one
```

`--find` prints each matching epic with its key, derived name and ELG release,
and marks the ones already in the model:

```
2 epic(s) matching "Buffalo":

  IG-1509   Northern Buffalo Fortune [V]    ELG 4.40       [already in model]
  IG-2821   Northern Buffalo                (no ELG ver)
            fix versions: PFH Games 5.00
```

`--add` reads the display name and the ELG release off the epic itself, so
**category is the only thing you have to supply** — nothing in Jira separates a
Port from a Skin. Valid values: `Port`, `Skin`, `Branded`, `New`.

Override the derived values with `--name` and `--rel`. You will need `--rel` for
an epic that carries no ELG fix version — Flaming Skulls is one, it has only PFH
and Horse Play versions:

```bash
python elg_cost_data.py --add IG-2821 --cat Port --rel "ELG 4.80"
```

### Then rebuild

Adding a game only edits `games.json`. Nothing on the page changes until you
re-pull and rebuild:

```bash
python elg_cost_data.py     # ~2 min, picks up the new epic
python elg_cost_page.py     # instant
```

Commit both `games.json` and the rebuilt `elg-cost-model.html`, then push to
`main`. **Pushing to `main` deploys** — `deploy-pages.yml` triggers on it and the
public page updates within a minute or two.

### Release epics

There is no `--add` for these. Edit the `release_epics` block in `games.json`
directly. A game whose release has no entry there simply gets 0h of release
overhead — see §5.

---

## 3. Scopes: Full game, ELG only, PFH only

Scope is decided per ticket, from that ticket's **own** fix versions and summary.
It does **not** inherit from the game epic. That surprises people: five of the
eight epics carry a PFH fix version, but the tag stays on the epic and does not
reach its children.

| scope | what it includes |
|---|---|
| **Full game** | every ticket under the game epic |
| **ELG only** | carries an `ELG *` fix version **and is not flagged PFH** |
| **PFH only** | flagged PFH |

A ticket is **flagged PFH** if *either*:

- a fix version starts with `PFH` (`PFH_RE`, anchored), **or**
- `PFH` appears anywhere in the summary (`PFH_TITLE_RE`)

The summary rule matters: nine tickets — `PFH Configs`, `PFH Pools`, *"Adjust
splash page 'Win Up to' text for PFH"* — carry **no fix version at all** and
would otherwise be invisible in PFH scope.

**PFH wins over ELG.** Five tickets qualify for both; they count in Full game and
PFH only, never in ELG only. Four are the per-game `- PFH` placeholders (0h
each); the fifth is IG-6921, 5.00h of Server work on Northern Buffalo.

The scopes do **not** partition. 75 tickets (277.53h) carry neither an ELG nor a
PFH marker, so ELG only + PFH only is less than Full game by design, not by a
rounding bug:

```
Full game   981 tickets   4,355.65h
ELG only    827 tickets   3,955.54h
PFH only     79 tickets     122.58h
neither      75 tickets     277.53h
```

> **Known Jira data issue.** Four `Gen2 Game: <name> - PFH` placeholder tickets
> are typed **Math Task**, so they land in the Math column. IG-5825 puts 8h of
> "Math" on Flaming Skulls that is not math work. Retyping them in Jira is
> cheaper than coding around it.

---

## 4. How a ticket gets a department

There is no department field in Jira. `classify()` walks `RULES` top to bottom and
**the first match wins**, so order is load-bearing. Four tiers:

**1. Issue types that *are* a department.** `Bug` and `Live Issue` → Bugs,
`QA Task/Subtask` → QA, `Release*` → Release, `Enhancement` → Enhancement,
`CR` → CR. These sit first and beat everything, so a ticket typed Bug counts as
Bugs whatever its summary says.

**2. Review and Concept text rules.** These must beat the department they
describe, or *"Review - Math"* lands in Math instead of Review and *"Design Doc"*
lands in Math instead of Concept. 8 Math tickets (43.50h) are routed to Review
this way, deliberately — it matches how the 2024 legend prices R&R rows.

**3. Explicit `[bracket]` tags.** `[Server]`, `[GE]`, `[Math]`, `[FE]` — the
current naming convention, authoritative. A Math Task named `[Server] ...` is
Server.

**4. Issue types that *name* a department.** `Math Task/Subtask`,
`Sound Task/Subtask`, `Creative`/`Design Task/Subtask`. **Jira wins over the
loose keyword rules below it.**

Then the older keyword rules, then a fallback to **Dev**.

### Why tier 4 sits above the keywords

It used to sit below them, and that was a real misclassification. A **Math Task**
called *"Gen2 Game: Lost Totem - PFH pools"* was landing in **Server**, because
the keyword rule for `pools` fired before anything looked at the issue type. 34
Math tickets — **108h** — were filed that way, including all of Lost Totem's PFH
math work.

The 2024 legend agrees with the fix: it prices **"Pools/Flares"** and **"Math
Models/weighted outcomes"** under **Math**. The actuals were being classified
against a rule that disagreed with the estimate they are compared to.

### What tier 4 deliberately excludes

`Story`, `Task`, `Dev Task` and `Dev Subtask` are **not** in tier 4. They name no
department, so the keyword rules are what classify them — 126 tickets and
1,309h of Dev-typed work routed to Game Engine, Review, Server and Sound by
summary. That is the design working, not a bug.

### Server is still the softest number

Only the `[Server]` tag is authoritative. The older patterns — `config`, `pools`,
`deploy on/to` — still catch any *generically typed* summary that mentions them,
whoever did the work. Server sits above Game Engine on purpose, so *"deployment
on New Game Engine"* reads as releasing onto the platform rather than building
it.

### Empty columns are intentional

**CR** shows with no hours: the type exists on the instance (2 issues in IG, both
under untracked epics) but none has landed on a modelled game. **Release** is
always empty for a different reason — see §5. A column that disappears when empty
reads as "no such work exists"; one that stays reads as "none yet", which is the
truth.

Every ticket records which rule decided it (the `rule` field), so any cell can be
audited back to the pattern that produced it.

---

## 5. Release overhead

Release work does not sit under a game epic. It lives under its own epics
(`release_epics` in `games.json`), so **no game ticket ever classifies as
Release** and the column would otherwise read `·` for every game.

`release_overhead()` shares each release's hours evenly across the games **this
model tracks** in that release:

| release | overhead | games | per game |
|---|---|---|---|
| ELG 4.20 | 15.83h | 2 | 7.92h |
| ELG 4.30 | 3.25h | 1 | 3.25h |
| ELG 4.40 | 76.42h | 1 | 76.42h |
| ELG 4.50 | 0.50h | 2 | 0.25h |
| ELG 4.70 | 112.50h | 2 | 56.25h |

**That denominator is an assumption.** If a release also shipped games the model
does not track, their share lands on the tracked ones and per-game overhead reads
high. The `counts` map from `collect()` was meant to correct for this, but it
keys on the game epic carrying an ELG fix version and several do not — it returns
0 for ELG 4.30 and would divide by zero.

Presentation:

- shown in the **Release column** and as a separate **Release OH** tile
- kept out of **Actual**, which is the sum of the tickets in view — overhead is
  not one of them
- **Full-game scope only**: ELG and PFH filter on fix versions, and release
  overhead carries none

---

## 6. Reading the numbers

- **Hours are Time Spent**, held as raw seconds and converted once at output.
  The rollup walks epic → children → subtasks and never uses
  `aggregatetimespent`, which double-counts.
- **Cost is hours × a blended rate** set by `hourly_rate` in `games.json`
  (currently $82.50). One knob; every dollar figure follows it.
- **Cells show fractions when they have them** — under 1h to 2dp, under 10h to
  1dp. A 0.25h share rendering as `0` read as nothing at all.
- **`Actual ÷ Est` below 1.0 means over-estimated**, not under-spent. Roughly 0.8
  across the model: the 2024 t-shirt bands were generous against real delivery.
- **The category averages are thin.** Port rests on 5 games, Skin on 2, Branded
  on 1, New on none. The page labels each with its n and a confidence word;
  treat Branded as indicative only.
