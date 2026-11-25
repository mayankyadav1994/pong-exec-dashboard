# Pong PMO dashboards

Every PMO dashboard lives in this repo, one folder each. The one exception is the
Confluence release page, which stays in the private `release-dashboard` repo
because this repo, and the site, are public.
Live site: <https://mayankyadav1994.github.io/pong-exec-dashboard/>

| Dashboard | Folder | Public address | Built by | Refresh |
|---|---|---|---|---|
| Exec overview | `exec/` | `/` (`index.html`) | `build_dashboard.py` | daily 06:00 UTC |
| V2 release timeline | `timelines/` | `/v2-timeline.html` | `v2_timeline.py` | daily 06:15 UTC + on plan save |
| iGaming release timeline | `timelines/` | `/igaming-timeline.html` | `timeline.py igaming` | daily 06:30 UTC + on plan save |
| Cloud Services release timeline | `timelines/` | `/css-timeline.html` | `timeline.py css` (same engine as iGaming) | daily 06:30 UTC + on plan save |
| Roadmaps (iGaming, V2, Cloud Services) | `roadmap/` | `/roadmap-igaming.html`, `/roadmap-v2.html`, `/roadmap-css.html` | `roadmap.py all` | daily 06:45 UTC |
| People | `people/` | `/people.html` | `people.py` | daily 07:15 UTC |
| Game & Feature Pipeline + CEO Summary | `game-pipeline/` | `/game-pipeline.html?project=` `v2` · `v2f` · `ig` · `igf` · `cssf` (games / features per space), `/ceo-summary.html` | `build_jira_data.py --project both` (all boards) | daily 06:00 UTC |
| Team Board | `team-board/` | `/team-board.html` | `team_board.py` (Jira writes: `apply_plan.py`) | daily 06:00 UTC |
| Sprint Load | `sprint-load/` | `/sprint-load/dashboard.html` | `sprint_load.py` | by hand (no workflow yet) |
| ELG cost model, Net-new vs port | `elg-cost-model/` | `/elg-cost-model.html`, `/netnew-vs-port.html` | `elg_cost_page.py`, `netnew_page.py` | by hand |
| Weekly Executive Release Dashboard | private repo `release-dashboard` | Confluence page | `release_dashboard_refresh.py` | Mon + Thu 13:00 UTC |

Each refresh runs from `.github/workflows/` and commits the rebuilt page back into the dashboard's folder.

Each space (V2, iGaming, Cloud Services) is three linked pages: **Releases** (its timeline,
with each release's games and features nested under it), **Games** and **Features** (its
boards in the Game & Feature Pipeline). Cloud Services has no games.

## Shared folders

| Folder | What's in it |
|---|---|
| `config/` | PMO-editable settings: the timeline plans saved from the pages (`v2.json`, `igaming.json`), roadmap lanes and PMO dates. |
| `shared/` | Code every dashboard uses: `jira_client.py`, and `site-nav.js` (the navigation drawer and its one list of dashboards). The unified rules (done, release membership, calendar, forecast) go here next. |
| `tools/` | Smoke tests run before each refresh commits, and `build_site.py`. |
| `docs/` | Logic and knowledge docs. |

## How the site is published

Source stays in the folders above, but the public addresses never moved:
bookmarks, emails and `email_updates/release_readiness.py` use them. On every
push, **Deploy Pages** runs `tools/build_site.py`, which copies the repo into
`_site/` and places each dashboard's public files at the site root. To publish a
new page, add it to `SITE_ROOT` in that script. The build fails if a listed file
is missing.

The same step adds the navigation drawer to every published page: the tab on the
left edge opens a list of all dashboards. No template carries it. To add or
rename a dashboard in that list, edit `GROUPS` in `shared/site-nav.js`.

## Running locally

The scripts work from any directory. They find `shared/`, `config/` and each
other's files from their own location, and read credentials from `.env` or the
environment.

```
python timelines/v2_timeline.py
python timelines/timeline.py css      # or igaming
python roadmap/roadmap.py all
python game-pipeline/build_jira_data.py --project both
```

Preview the whole site as it will be published:

```
python tools/build_site.py
python -m http.server 8000 -d _site
```
