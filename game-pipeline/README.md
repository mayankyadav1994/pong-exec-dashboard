# Game Pipeline Dashboard — V2 + iGaming

A **two-page** dashboard system for Pong Game Studios that visualizes every game
epic across its lifecycle, sourced **entirely from Jira**. One page covers **V2**
games, one covers **iGaming** games; both are thin HTML shells over a single
shared engine (`dashboard.js` + `dashboard.css`), each loading its own
Jira-built data file. It mirrors the architecture of the sibling release-timeline
dashboards in the `pong-exec-dashboard` repo and deploys to the same GitHub Pages
site.

---

## Folder structure

```
pong_game_pipeline\
├── game-pipeline.html             PRIMARY combined page (site nav + V2/iGaming sub-tabs)
├── v2-game-pipeline.html          V2 standalone shell (sets window.PROJECT)
├── igaming-game-pipeline.html     iGaming standalone shell
├── dashboard.css                  Shared styling (incl. sprint axis)
├── dashboard.js                   Shared engine, parameterized by window.PROJECT
├── dashboard-data-v2.js           Built V2 data (GAMES / SPRINTS / REFRESHED_AT)
├── dashboard-data-ig.js           Built iGaming data
├── build_jira_data.py             Jira → data-file builder
├── refresh.bat                    Local refresh: build both + open both pages
├── requirements.txt               requests, python-dotenv
├── .env / .env.example            Jira credentials (.env is gitignored)
├── .github-workflow.yml           CI template to copy into pong-exec-dashboard
├── GAME_PIPELINE_KNOWLEDGE.md     Architectural reference — the WHAT
├── GAME_PIPELINE_LOGIC.md         Decisions ledger — the WHY
├── README.md                      This file
├── archive\                       Timestamped JSON snapshots of past refreshes
└── source\                        (legacy) Excel reference — NOT read by the build
```

---

## How to refresh the data

1. Make sure `.env` is filled in (copy `.env.example` → `.env`). Required keys:
   `JIRA_BASE_URL`, `JIRA_EMAIL`, `JIRA_API_TOKEN`, `JIRA_BOARD_ID_V2`,
   `JIRA_BOARD_ID_IG`.
2. Run the builder (or double-click **`refresh.bat`**):

   ```bat
   python build_jira_data.py --project both          REM default
   python build_jira_data.py --project v2 --verbose
   python build_jira_data.py --project ig
   ```

   It rewrites `dashboard-data-v2.js` / `dashboard-data-ig.js` and snapshots a
   JSON copy into `archive\`.
3. Open `v2-game-pipeline.html` / `igaming-game-pipeline.html` in a browser
   (`refresh.bat` opens both).

First-time setup (optional venv):

```bat
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
```

In production, the **GitHub Actions** workflow rebuilds both data files from Jira
on a schedule (and on manual dispatch) and commits the refreshed runtime files to
the `pong-exec-dashboard` repo root for GitHub Pages. See `.github-workflow.yml`
and Decision #28.

---

## How Plan Mode works

Click **✎ Plan Mode** in the header. While on:

- **Reorder games** by dragging the row handle (⠿).
- **Set workflow status** via the dashed-amber dropdown on each row (default is
  `Not Started` — status is never inferred from Jira; Decision #24).
- **Set sizes** (XS/S/M/L/XL per discipline: Art/Math/Dev/Sound) in the config
  panel — these are manual overrides, **not** from Jira (Decision #25). The size
  column on a row stays hidden until you enter a size.
- **Configure enums** (statuses, stages, capacities).

All edits autosave to `localStorage`, **namespaced per project** (`gp_v2_*` vs
`gp_ig_*`, Decision #22), so the two pages never interfere and your curation
survives every Jira refresh. The **Save plan** button is a confirmation
affordance.

> Lifecycle **Stage** chips are auto-derived from the latest active sprint and
> cannot be edited. Workflow **Status** is the manual one. See Decision #2.

---

## Where to read for context

| File | Read it for |
|---|---|
| `GAME_PIPELINE_KNOWLEDGE.md` | Jira data model, two-page architecture, sprint axis, heatmap math, build phases. **The WHAT.** |
| `GAME_PIPELINE_LOGIC.md` | The numbered decisions ledger with rationale + dates. **The WHY.** |

---

## Deployment / related repo

Deployed through the existing **pong-exec-dashboard** repo (same GitHub Pages
site as the release-timeline dashboards):

- Repo: https://github.com/mayankyadav1994/pong-exec-dashboard
- Live (after first CI run):
  - https://mayankyadav1994.github.io/pong-exec-dashboard/v2-game-pipeline.html
  - https://mayankyadav1994.github.io/pong-exec-dashboard/igaming-game-pipeline.html

Source files are committed into that repo under a `game-pipeline/` subfolder; CI
copies the runtime files to the repo root. See Decision #28 and
`.github-workflow.yml`.

---

## Phases

- **Phase 1 (this build):** Jira-sourced data, two pages, shared engine, sprint
  axis + markers, Plan Mode (status / sizes / reorder), GitHub Pages deploy.
- **Phase 2:** game-to-game dependencies (Jira "is blocked by"), hide/restore.
- **Phase 3:** drag-to-replan stage bars with live heatmap.

See `GAME_PIPELINE_KNOWLEDGE.md` §4 and Decisions #15 / #16.

---

*Pong Game Studios PMO · Game Pipeline dashboard (V2 + iGaming)*
