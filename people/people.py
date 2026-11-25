"""
people.py
=========
Builds people.html: where everyone works, week by week, on the roadmap's items.

  Rows    Everyone who logs time in Tempo, grouped by the team (board) and
          discipline the Sprint Load page gives them (sprint-load/dashboard.html).
          People Sprint Load doesn't list are classified with Sprint Load's own
          rules (Jira group first, then issue types).
  Weeks   The Roadmap window (config/roadmap.json), Monday-based.
  Past    Tempo worklogs, attributed to the real author, rolled up issue -> epic.
          Pong Internal (PI) time splits into Internal and Time off.
  Future  Open Jira work with a remaining estimate, placed by:
            1. the Game Pipeline velocity forecast, for epics on a pipeline board
               (same rule as the pipeline's applyForecast(): remaining hours / that
               lane's velocity = sprints needed, lanes run in parallel from the
               first future sprint). Each person's remaining on the epic's lane is
               spread from today to that lane's projected end; else
            2. the ticket's active or next dated sprint; else
            3. the person's unscheduled tray (counted, not drawn).
          Every plan window is at least one sprint long: work left at the end of
          a sprint rolls into the next one, it doesn't all happen on the last day.
  Capacity  Sprint Load's 70h per person per sprint (35h a week).

Read-only: Tempo + Jira reads only.
Usage:  python people.py [--today YYYY-MM-DD]
Credentials: JIRA_EMAIL / JIRA_API_TOKEN, TEMPO_API_TOKEN (locally read from the
timesheet scripts' .env when not in the environment).
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import sys
import time
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests
from dotenv import dotenv_values, load_dotenv

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

HERE = Path(__file__).resolve().parent     # people/ — template + page
ROOT = HERE.parent                         # repo root — config/, shared/, other dashboards
load_dotenv(ROOT / ".env")
load_dotenv(ROOT / "game-pipeline" / ".env")
# Local runs: the Tempo token lives with the timesheet scripts. CI: repo secret.
if not os.environ.get("TEMPO_API_TOKEN"):
    _sib = ROOT.parent / "email_updates" / "timesheet_reminders" / ".env"
    if _sib.exists():
        _tok = dotenv_values(_sib).get("TEMPO_API_TOKEN")
        if _tok:
            os.environ["TEMPO_API_TOKEN"] = _tok

sys.path.insert(0, str(ROOT / "shared"))   # shared/jira_client.py
from jira_client import jira_get, jira_jql  # noqa: E402  (needs env loaded)


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# Shared rules, imported rather than copied so the pages can't drift apart.
SL = _load_module("sprint_load", ROOT / "sprint-load" / "sprint_load.py")    # people, capacity
GP = _load_module("build_jira_data", ROOT / "game-pipeline" / "build_jira_data.py")  # lanes, velocity

RM = json.loads((ROOT / "config" / "roadmap.json").read_text(encoding="utf-8"))
TEMPLATE = HERE / "people.template.html"
OUTPUT = HERE / "people.html"
SITE = "https://ponggamestudios.atlassian.net"
TEMPO = "https://api.tempo.io/4"
TODAY = date.today()

CAP_SPRINT = float(SL.CAPACITY_H)       # 70h per person per sprint
CAP_WEEK = CAP_SPRINT / 2
# Jira group -> discipline, as Sprint Load maps it (older sprint_load.py
# copies don't define it, so fall back to the same two groups).
DISCIPLINE_GROUPS = getattr(SL, "DISCIPLINE_GROUPS", {"QA Team": "QA", "Development": "Dev"})
PROJECTS = ("IG", "V2", "CSS")
LINE_OF_PROJECT = {"IG": "ig", "V2": "v2", "CSS": "css"}
SPRINT_FIELDS = ("customfield_10103", "customfield_10020")
FEEDS = ("ig", "igf", "v2")             # Game Pipeline boards: IG games, IG features, V2
INTERNAL_PROJECT = "PI"                 # "Pong Internal": meetings, admin, leave ...
AWAY_RE = re.compile(r"vacation|personal day|time off|in lieu|sick|holiday|leave|bereave", re.I)
ACTIVE_WEEKS = 6                        # logged within this many weeks = active
MIN_PLAN_WORKDAYS = 10                  # one sprint
TEAM_OF_SLUG = {"dams": "DAMS", "ig": "iGaming", "v2": "V2", "css": "Cloud Services"}
TEAM_OF_PROJECT = {"IG": "iGaming", "V2": "V2", "CSS": "Cloud Services"}
TEAM_ORDER = ["DAMS", "iGaming", "V2", "Cloud Services", "Other"]
# Mirrors STATUS_PROGRESSION in game-pipeline/dashboard.js (stale shared-status rule).
STATUS_PROGRESSION = ["Not Started", "On Hold", "In Pre-Prod", "In Progress", "In QA",
                      "Signed Off", "Delivered"]
LINES = {
    "ig":       {"label": "iGaming",        "color": "blue"},
    "v2":       {"label": "V2",             "color": "violet"},
    "css":      {"label": "Cloud Services", "color": "teal"},
    "internal": {"label": "Internal",       "color": "grey"},
    "away":     {"label": "Time off",       "color": "amber"},
    "other":    {"label": "Other projects", "color": "pink"},
}
GAME_RE = {"IG": RM["spaces"]["igaming"]["game_re"], "V2": RM["spaces"]["v2"]["game_re"]}
F_META = ["project", "issuetype", "summary", "parent", "fixVersions", "status"]
F_OPEN = F_META + ["assignee", "timeestimate", "timeoriginalestimate", "timespent",
                   "subtasks", *SPRINT_FIELDS]


# ── small helpers ────────────────────────────────────────────────────────────
def pd(s):
    return date.fromisoformat(str(s)[:10]) if s else None


def monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def month_add(d: date, n: int) -> date:
    y, m = divmod(d.month - 1 + n, 12)
    return date(d.year + y, m + 1, 1)


def r1(x: float) -> float:
    return round(x + 1e-9, 1)


def window(today: date) -> tuple[date, date]:
    """The Roadmap's forward window. History always starts 1 Jan (Jan-Mar: last
    year) — who worked where this year is the point of the page — whether or
    not the Roadmap's own `from_year_start` is on."""
    w = RM["window"]
    first = today.replace(day=1)
    start = min(month_add(first, -w["months_back"]),
                date(today.year - (1 if today.month <= 3 else 0), 1, 1))
    end = month_add(first, w["months_forward"] + 1) - timedelta(days=1)
    return start, end


def workdays(d0: date, d1: date) -> list[date]:
    return [d0 + timedelta(i) for i in range((d1 - d0).days + 1) if (d0 + timedelta(i)).weekday() < 5]


def next_workday(d: date) -> date:
    d = d + timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def plan_days(start: date, end: date) -> list[date]:
    """Working days from start to end, stretched to at least one sprint."""
    days = workdays(start, max(start, end))
    d = days[-1] if days else start
    while len(days) < MIN_PLAN_WORKDAYS:
        d = next_workday(d)
        days.append(d)
    return days


def clean_name(summary: str, proj: str) -> str:
    s = summary or ""
    if proj in GAME_RE:
        s = re.sub(GAME_RE[proj], "", s, flags=re.I).strip()
    s = re.sub(r"\s*\[V\]\s*", " ", s)
    return re.sub(r"\s{2,}", " ", s).strip(" -") or summary


# ── Tempo ────────────────────────────────────────────────────────────────────
def tempo_worklogs(d0: date, d1: date) -> list[dict]:
    tok = os.environ.get("TEMPO_API_TOKEN", "").strip()
    if not tok:
        sys.exit("TEMPO_API_TOKEN is not set (and no local timesheet .env to read it from).")
    hdr = {"Authorization": f"Bearer {tok}", "Accept": "application/json"}
    url, out = f"{TEMPO}/worklogs?from={d0}&to={d1}&limit=1000", []
    while url:
        for attempt in range(4):
            r = requests.get(url, headers=hdr, timeout=90)
            if r.status_code == 429 or r.status_code >= 500:
                time.sleep(2 * (attempt + 1))
                continue
            r.raise_for_status()
            break
        d = r.json()
        out += d.get("results") or []
        url = (d.get("metadata") or {}).get("next")
    return out


# ── Jira ─────────────────────────────────────────────────────────────────────
def jql_in(field: str, vals, fields: list[str]) -> dict:
    """{key: issue} for `field in (vals)`. A deleted or unreadable issue makes
    Jira reject the whole query, so failing chunks are split until the bad
    value is isolated and skipped."""
    out: dict = {}

    def run(chunk):
        try:
            for it in jira_jql(f"{field} in ({','.join(str(v) for v in chunk)})", fields):
                out[it["key"]] = it
        except requests.HTTPError as e:
            if e.response is None or e.response.status_code != 400:
                raise
            if len(chunk) > 1:
                mid = len(chunk) // 2
                run(chunk[:mid])
                run(chunk[mid:])

    vals = sorted({v for v in vals if v})
    for i in range(0, len(vals), 100):
        run(vals[i:i + 100])
    return out


def users(account_ids) -> dict:
    """accountId -> {name, type, active} via /user/bulk."""
    ids, out = sorted(set(account_ids)), {}
    for i in range(0, len(ids), 80):
        d = jira_get("/user/bulk", {"accountId": ids[i:i + 80], "maxResults": 200})
        for u in d.get("values") or []:
            out[u["accountId"]] = {"name": u.get("displayName") or u["accountId"],
                                   "type": u.get("accountType"), "active": u.get("active", True)}
    return out


# ── Game Pipeline forecast (port of applyForecast in game-pipeline/dashboard.js) ──
def pipeline_forecast(today: date) -> dict:
    """epic key -> {feed, name, ship, lanes{lane: end ISO}, hidden}.

    Same inputs the pipeline page uses: the board roster (in_roster or added in
    Edit Plan), shared status overrides (dropped when Jira has moved past them),
    measured velocity per lane over the roster, DEFAULT_VELOCITY fallback."""
    out: dict = {}
    for feed in FEEDS:
        data = json.loads((ROOT / "game-pipeline" / f"data-{feed}.json").read_text(encoding="utf-8"))
        pf = ROOT / "game-pipeline" / f"plan-{feed}.json"
        plan = json.loads(pf.read_text(encoding="utf-8")) if pf.exists() else {}
        added, hidden = set(plan.get("added") or []), set(plan.get("hidden") or [])
        shared = plan.get("status") or {}
        vel_ovr = (plan.get("config") or {}).get("velocities") or {}
        raw = [g for g in data.get("games") or [] if g.get("in_roster") is not False or g.get("jira") in added]
        ladder = sorted([s for s in data.get("sprints") or [] if s.get("start")], key=lambda s: s["start"])
        by_id = {str(s.get("id")): s for s in ladder}

        def past_count(d):
            return sum(1 for sid in d.get("sprints") or []
                       if (s := by_id.get(str(sid))) and pd(s["start"]) <= today)

        acc = {k: [0.0, 0] for k in GP.FORECAST_LANES}
        for g in raw:
            for d in g.get("disciplines") or []:
                if d.get("key") in acc:
                    acc[d["key"]][0] += d.get("spent") or 0.0
                    acc[d["key"]][1] += past_count(d)
        studio = {k: (sp / n if n else None) for k, (sp, n) in acc.items()}

        def base(k):
            return vel_ovr.get(k) or studio.get(k) or GP.DEFAULT_VELOCITY.get(k, 30)

        def eff(d):
            b, past, spent = base(d.get("key")), past_count(d), d.get("spent") or 0.0
            if past >= 2 and spent >= 8:
                return min(max(spent / past, 0.5 * b), 2 * b)
            return b

        def rem_of(d):
            if d.get("remaining") is not None:
                return max(0.0, float(d["remaining"]))
            return max(0.0, float(d.get("est") or 0) - float(d.get("spent") or 0))

        def sidx(s):
            return STATUS_PROGRESSION.index(s) if s in STATUS_PROGRESSION else -1

        first_future = next((i for i, s in enumerate(ladder) if pd(s["start"]) > today), len(ladder))
        needs, max_need = {}, 0
        for g in raw:
            if g.get("delivered"):
                continue
            auto = g.get("workflow_status") or ""
            sh = shared.get(g.get("name"))
            if sh and sh != auto and sidx(auto) >= 0 and sidx(sh) >= 0 and sidx(auto) > sidx(sh):
                sh = None                      # Jira moved past a stale override
            ws = sh or auto
            if ws in GP.STATUS_TERMINAL:
                continue
            by_key = {d.get("key"): d for d in g.get("disciplines") or []}
            prod_open = any(by_key.get(k) and by_key[k].get("phase") not in ("done", "hold")
                            and rem_of(by_key[k]) > 0 for k in GP.PROD_LANES)
            qa_only = ws in GP.STATUS_QA_ONLY and not prod_open
            active = "qa" if qa_only else (g.get("current_stage") or "")
            lanes = {}
            for k in GP.FORECAST_LANES:
                d = by_key.get(k)
                if not d or d.get("phase") == "done" or (qa_only and k != "qa"):
                    continue
                rem = rem_of(d)
                if rem <= 0 and k != active:
                    continue
                lanes[k] = max(1, -(-rem // eff(d))) if rem > 0 else 1
                max_need = max(max_need, lanes[k])
            if lanes:
                needs[g["jira"]] = (g, lanes)

        ladder = [dict(s) for s in ladder]
        synth = max(0, int(max_need) - (len(ladder) - first_future))
        if synth and ladder:
            start = pd(ladder[-1]["start"])
            for i in range(synth):
                start = start + timedelta(days=GP.SPRINT_LEN_DAYS)
                ladder.append({"start": start.isoformat(),
                               "end": (start + timedelta(days=12)).isoformat(), "projected": True})
        if not ladder:
            continue

        def slot_end(n):
            s = ladder[min(first_future + int(n) - 1, len(ladder) - 1)]
            return s.get("end") or s["start"]

        for key, (g, lanes) in needs.items():
            out[key] = {"feed": feed, "name": g.get("name"),
                        "ship": slot_end(max(lanes.values())),
                        "lanes": {k: slot_end(n) for k, n in lanes.items()},
                        "hidden": g.get("name") in hidden}
    return out


def lane_of(issuetype: str, summary: str) -> str:
    """Pipeline lane for a ticket ('exclude' / 'release' / 'unmapped' otherwise)."""
    cat = GP.classify_child(issuetype)
    if cat in GP.REVIEWABLE_DISCIPLINES and GP.REVIEW_RE.search(summary or ""):
        cat = "review"
    return cat


def counts_own_remaining(f: dict) -> bool:
    """Pipeline rule: a parent with classified sub-tasks contributes its own
    remaining only when hours were logged on the parent itself."""
    subs = f.get("subtasks") or []
    has_cls = any(GP.classify_child(((s.get("fields") or {}).get("issuetype") or {}).get("name") or "")
                  not in ("exclude", "unmapped") for s in subs)
    return (not has_cls) or (f.get("timespent") or 0) > 0


def sprint_window(f: dict, today: date):
    """(start, end, label) of the ticket's active sprint, else its next dated
    future sprint. Dateless parking sprints ("Refined Backlog") don't count."""
    act = fut = None
    for fld in SPRINT_FIELDS:
        for s in f.get(fld) or []:
            if not isinstance(s, dict):
                continue
            sd, ed = pd(s.get("startDate")), pd(s.get("endDate"))
            if s.get("state") == "active" and ed:
                act = (next_workday(today) if ed > today else today, ed, s.get("name"))
            elif s.get("state") == "future" and sd and ed and (fut is None or sd < fut[0]):
                fut = (max(sd, next_workday(today)), ed, s.get("name"))
    return act or fut


# ── Sprint Load: team + discipline ───────────────────────────────────────────
def sprint_load_people() -> tuple[dict, str]:
    """accountId -> {team, disc, src, boards[]} from the Sprint Load snapshot.
    A DAMS-roster member is DAMS; anyone else on several boards takes the board
    where they hold the most remaining work (Sprint Load's red rows)."""
    html = (ROOT / "sprint-load" / "dashboard.html").read_text(encoding="utf-8")
    i = html.index("const SNAPSHOTS = ") + len("const SNAPSHOTS = ")
    snaps, _ = json.JSONDecoder().raw_decode(html[i:])
    out, gen = {}, ""
    for slug, b in snaps.items():
        gen = max(gen, b.get("generated_at") or "")
        for p in b.get("people") or []:
            cand = {"team": TEAM_OF_SLUG.get(slug, slug), "slug": slug, "disc": p["discipline"],
                    "src": "Sprint Load · " + (p.get("discipline_source") or "issue types"),
                    "rank": (slug == "dams", p.get("remaining_h") or 0, p.get("tickets") or 0)}
            cur = out.get(p["account"])
            boards = (cur["boards"] if cur else []) + [TEAM_OF_SLUG.get(slug, slug)]
            if cur is None or cand["rank"] > cur["rank"]:
                cur = cand
            cur["boards"] = boards
            out[p["account"]] = cur
    return out, gen


def fallback_class(acct: str, type_votes: Counter, proj_hours: Counter) -> dict:
    """Sprint Load's rules for someone it doesn't list: DAMS roster -> issue-type
    prefixes; else Jira group (QA Team / Development); else issue-type vote."""
    if acct in SL.ROSTER:
        team, slug = "DAMS", "dams"
    else:
        top = max((p for p in proj_hours if p in TEAM_OF_PROJECT), key=lambda p: proj_hours[p], default=None)
        team, slug = (TEAM_OF_PROJECT[top], top.lower()) if top else ("Other", "ig")
    if slug != "dams":
        try:
            groups = [g.get("name") for g in jira_get("/user/groups", {"accountId": acct}) or []]
        except requests.HTTPError:
            groups = []
        hit = sorted({DISCIPLINE_GROUPS[g] for g in groups if g in DISCIPLINE_GROUPS})
        if len(hit) == 1:
            return {"team": team, "disc": hit[0], "src": "Jira group (not on Sprint Load)", "boards": []}
    votes = Counter()
    for t, n in type_votes.items():
        d = SL.discipline_of(slug, t)
        if d:
            votes[d] += n
    disc, _ = SL.pick_discipline(votes)
    return {"team": team, "disc": disc, "src": "ticket types (not on Sprint Load)", "boards": []}


# ── Roadmap cards per epic ───────────────────────────────────────────────────
def roadmap_cards() -> dict:
    """epic key -> [{s, t, d, r, src}] from the generated roadmap pages."""
    out = defaultdict(list)
    for sid, sp in RM["spaces"].items():
        f = ROOT / "roadmap" / sp["file"]
        if not f.exists():
            continue
        html = f.read_text(encoding="utf-8")
        i = html.find("const D = ")
        if i < 0:
            continue
        d, _ = json.JSONDecoder().raw_decode(html[i + len("const D = "):])
        glabel = {g["id"]: g["label"] for g in d.get("groups") or []}
        for c in d.get("cards") or []:
            for it in c.get("items") or []:
                out[it["key"]].append({"s": sp["title"], "g": glabel.get(c["group"], ""), "t": c["title"],
                                       "d": it.get("due") if c.get("source") == "epic-due" and it.get("due") else c["date"],
                                       "r": bool(c.get("released")), "src": c.get("source")})
        for p in d.get("pubs") or []:
            if p.get("key"):
                out[p["key"]].append({"s": sp["title"], "g": glabel.get(p["group"], ""), "t": "Go-live",
                                      "d": p["golive"], "r": False, "src": "golive"})
    for k in out:
        out[k].sort(key=lambda c: c["d"])
    return out


# ── build ────────────────────────────────────────────────────────────────────
def build(today: date) -> dict:
    t0 = time.time()
    win_start, win_end = window(today)
    w0 = monday(win_start)
    weeks = []
    d = w0
    while d <= win_end:
        weeks.append(d)
        d += timedelta(days=7)
    widx = {w: i for i, w in enumerate(weeks)}
    cur_wi = widx.get(monday(today), 0)
    print(f"Window {win_start} -> {win_end} ({len(weeks)} weeks), today {today}")

    # 1. Tempo worklogs ----------------------------------------------------------
    wl = tempo_worklogs(w0, today)
    print(f"  Tempo: {len(wl)} worklogs, {r1(sum(w['timeSpentSeconds'] for w in wl) / 3600)}h "
          f"({time.time() - t0:.0f}s)")

    # 2. Open Jira work (assigned or not: unassigned remaining = unstaffed) -------
    open_iss = jira_jql(f"project in ({', '.join(PROJECTS)}) AND statusCategory != Done", F_OPEN)
    print(f"  Jira: {len(open_iss)} open tickets ({time.time() - t0:.0f}s)")

    # 3. Metadata for every worklog issue, then parents up to the epic ------------
    issues = {it["key"]: it for it in open_iss}
    known_ids = {it["id"] for it in open_iss}
    issues.update(jql_in("id", {str(w["issue"]["id"]) for w in wl} - known_ids, F_META))
    for _ in range(3):
        missing = {(it["fields"].get("parent") or {}).get("key") for it in issues.values()} - {None} - set(issues)
        if not missing:
            break
        issues.update(jql_in("key", missing, F_META))
    by_id = {it["id"]: it for it in issues.values()}
    print(f"  Jira: {len(issues)} issues resolved ({time.time() - t0:.0f}s)")

    root_cache: dict = {}

    def root_of(key):
        if key not in root_cache:
            cur, hops = issues.get(key), 0
            while cur and cur["fields"]["issuetype"]["name"] != "Epic" and hops < 4:
                pk = (cur["fields"].get("parent") or {}).get("key")
                if not pk or pk not in issues:
                    break
                cur, hops = issues[pk], hops + 1
            root_cache[key] = cur["key"] if cur else key
        return root_cache[key]

    cards = roadmap_cards()
    fc = pipeline_forecast(today)
    print(f"  Game Pipeline velocity forecast: {len(fc)} epics "
          f"({sum(1 for v in fc.values() if v['hidden'])} hidden on the board)")

    items: dict = {}

    def item_for(key) -> str:
        it = issues.get(key)
        if not it:
            iid = "unknown"
            items.setdefault(iid, {"n": "Unknown / unreadable issue", "l": "other", "k": "other"})
            return iid
        f = it["fields"]
        proj = f["project"]["key"]
        if proj == INTERNAL_PROJECT:
            away = bool(AWAY_RE.search(f.get("summary") or ""))
            iid = "PI:away" if away else "PI:internal"
            items.setdefault(iid, {"n": "Time off" if away else "Internal · meetings, admin, training",
                                   "l": "away" if away else "internal", "k": "internal", "sub": Counter()})
            items[iid]["sub"][f.get("summary") or "?"] += 1
            return iid
        if proj not in PROJECTS:
            iid = f"other:{proj}"
            items.setdefault(iid, {"n": f"{f['project'].get('name') or proj} ({proj})", "l": "other", "k": "other"})
            return iid
        rk = root_of(key)
        rf = issues[rk]["fields"]
        if rf["issuetype"]["name"] == "Epic":
            if rk not in items:
                summ = rf.get("summary") or rk
                is_game = proj in GAME_RE and re.search(GAME_RE[proj], summ, re.I)
                kind = "game" if is_game else ("release" if re.match(r"^\s*release\b", summ, re.I) else "feature")
                st = rf.get("status") or {}
                items[rk] = {"n": clean_name(summ, proj), "l": LINE_OF_PROJECT[proj], "k": kind, "key": rk,
                             "st": st.get("name"), "cards": cards.get(rk, [])}
                if rk in fc:
                    items[rk]["fc"] = {x: fc[rk][x] for x in ("ship", "lanes", "feed", "hidden")}
            return rk
        fvs = f.get("fixVersions") or rf.get("fixVersions") or []
        if fvs:
            iid = f"{proj}:fv:{fvs[0]['name']}"
            items.setdefault(iid, {"n": f"{fvs[0]['name']} · no epic", "l": LINE_OF_PROJECT[proj], "k": "version",
                                   "jql": f'project = {proj} AND fixVersion = "{fvs[0]["name"]}"'})
        else:
            iid = f"{proj}:none"
            items.setdefault(iid, {"n": f"{proj} · no epic or version", "l": LINE_OF_PROJECT[proj], "k": "other"})
        return iid

    # 4. Actuals -----------------------------------------------------------------
    actual = defaultdict(lambda: defaultdict(Counter))      # acct -> wi -> item -> h
    type_votes = defaultdict(Counter)
    proj_hours = defaultdict(Counter)
    last_log: dict = {}
    recent_cut = today - timedelta(weeks=12)
    unread = 0.0
    for w in wl:
        acct = (w.get("author") or {}).get("accountId")
        day = pd(w.get("startDate"))
        if not acct or not day or day < w0:
            continue
        h = (w.get("timeSpentSeconds") or 0) / 3600
        it = by_id.get(str(w["issue"]["id"]))
        iid = item_for(it["key"]) if it else item_for(None)
        if not it:
            unread += h
        actual[acct][widx[monday(day)]][iid] += h
        last_log[acct] = max(last_log.get(acct, day), day)
        if it and day >= recent_cut:
            proj_hours[acct][it["fields"]["project"]["key"]] += h
            type_votes[acct][it["fields"]["issuetype"]["name"]] += 1

    # 5. Plan ---------------------------------------------------------------------
    plan = defaultdict(lambda: defaultdict(dict))          # acct -> wi -> item -> [h, kind]
    tray = defaultdict(Counter)                            # acct -> item -> h
    unest = Counter()
    names_from_jira = {}
    unstaffed = Counter()
    planned_by = Counter()

    def put(acct, iid, hours, days, kind):
        per = hours / len(days)
        for dd in days:
            wi = widx.get(monday(dd))
            if wi is None:
                continue
            cell = plan[acct][wi].setdefault(iid, [0.0, kind])
            cell[0] += per
            if kind == "f":
                cell[1] = "f"

    start_day = next_workday(today)
    for it in open_iss:
        f = it["fields"]
        a = f.get("assignee") or {}
        acct = a.get("accountId")
        if acct:
            names_from_jira[acct] = a.get("displayName")
        rem_h = (f.get("timeestimate") or 0) / 3600
        iid = item_for(it["key"])
        rk = root_of(it["key"])
        lane = lane_of(f["issuetype"]["name"], f.get("summary") or "")
        fcast = fc.get(rk)
        on_pipeline = bool(fcast and not fcast["hidden"] and lane in fcast["lanes"])
        if on_pipeline and not counts_own_remaining(f):
            continue                                       # the sub-tasks carry this work
        sw = sprint_window(f, today)
        if not acct:
            if rem_h > 0 and (on_pipeline or sw):
                unstaffed[iid] += rem_h
            continue
        if rem_h <= 0:
            if not f.get("timeoriginalestimate") and (on_pipeline or sw):
                unest[acct] += 1
            continue
        if on_pipeline:
            put(acct, iid, rem_h, plan_days(start_day, pd(fcast["lanes"][lane])), "f")
            planned_by["velocity forecast"] += rem_h
        elif sw:
            put(acct, iid, rem_h, plan_days(sw[0], sw[1]), "s")
            planned_by["sprint"] += rem_h
        else:
            tray[acct][iid] += rem_h
            planned_by["unscheduled tray"] += rem_h

    # 6. People --------------------------------------------------------------------
    accts = set(actual) | set(plan) | set(tray)
    info = users(accts)
    sl_people, sl_gen = sprint_load_people()
    people = []
    for acct in accts:
        u = info.get(acct) or {"name": names_from_jira.get(acct) or acct, "type": "atlassian", "active": True}
        if u.get("type") and u["type"] != "atlassian":
            continue                                       # apps / bots
        cls = sl_people.get(acct) or fallback_class(acct, type_votes[acct], proj_hours[acct])
        a_rows = [[wi, iid, r1(h)] for wi, row in sorted(actual[acct].items()) for iid, h in row.items() if h >= 0.05]
        p_rows = [[wi, iid, r1(v[0]), v[1]] for wi, row in sorted(plan[acct].items())
                  for iid, v in row.items() if v[0] >= 0.05]
        last = last_log.get(acct)
        people.append({
            "id": acct, "n": u["name"], "team": cls["team"], "disc": cls["disc"], "src": cls["src"],
            "boards": cls.get("boards") or [],
            "active": bool((last and last >= today - timedelta(weeks=ACTIVE_WEEKS)) or p_rows),
            "jira_active": u.get("active", True),
            "last": last.isoformat() if last else None,
            "a": a_rows, "p": p_rows,
            "tray": sorted(([iid, r1(h)] for iid, h in tray[acct].items() if h >= 0.05), key=lambda x: -x[1]),
            "unest": unest[acct],
        })
    team_rank = {t: i for i, t in enumerate(TEAM_ORDER)}
    disc_rank = {d: i for i, d in enumerate(SL.DISCIPLINE_ORDER)}
    people.sort(key=lambda p: (team_rank.get(p["team"], 99), disc_rank.get(p["disc"], 99), p["n"].lower()))

    used = {r[1] for p in people for r in p["a"] + p["p"]} | {t[0] for p in people for t in p["tray"]}
    for iid, h in unstaffed.items():
        if iid in items:
            items[iid]["unstaffed"] = r1(h)
            used.add(iid)
    out_items = {}
    for iid in used:
        it = dict(items[iid])
        if "sub" in it:
            it["sub"] = [s for s, _ in it["sub"].most_common(8)]
        out_items[iid] = it

    # console summary ---------------------------------------------------------------
    tot_a = sum(r[2] for p in people for r in p["a"])
    epic_a = sum(r[2] for p in people for r in p["a"] if out_items[r[1]].get("key"))
    pi_a = sum(r[2] for p in people for r in p["a"] if r[1].startswith("PI:"))
    print(f"  People: {len(people)} ({sum(p['active'] for p in people)} active), "
          f"{sum(1 for p in people if not p['src'].startswith('Sprint Load'))} not on Sprint Load")
    print(f"  Logged {r1(tot_a)}h: {epic_a / max(tot_a, 1):.0%} on epics, {pi_a / max(tot_a, 1):.0%} internal/time off, "
          f"{unread}h on unreadable issues")
    print("  Remaining hours placed: " + ", ".join(f"{k} {r1(v)}h" for k, v in planned_by.most_common()))
    print(f"  Unstaffed remaining on planned items: {r1(sum(unstaffed.values()))}h; "
          f"assigned tickets without an estimate: {sum(unest.values())}")

    nav = [{"id": k, "title": s["title"], "file": s["file"]} for k, s in RM["spaces"].items()]
    nav += [n for n in RM.get("extra_nav", []) if n.get("id") != "people"]
    nav.append({"id": "people", "title": "People", "file": OUTPUT.name})
    return {
        "generated": datetime.now(timezone.utc).isoformat(timespec="minutes"),
        "today": today.isoformat(), "cur_wi": cur_wi,
        "window": {"start": w0.isoformat(), "end": win_end.isoformat()},
        "weeks": [w.isoformat() for w in weeks],
        "cap_week": CAP_WEEK, "cap_sprint": CAP_SPRINT,
        "nav": nav, "browse": f"{SITE}/browse/", "site": SITE,
        "lines": LINES, "teams": TEAM_ORDER, "disciplines": SL.DISCIPLINE_ORDER,
        "sprint_load_at": sl_gen,
        "items": out_items, "people": people,
        "stats": {"logged_h": r1(tot_a), "epic_share": round(epic_a / max(tot_a, 1), 3),
                  "planned": {k: r1(v) for k, v in planned_by.items()},
                  "unstaffed_h": r1(sum(unstaffed.values())), "unestimated": sum(unest.values()),
                  "forecast_epics": len(fc)},
    }


def main():
    global TODAY
    ap = argparse.ArgumentParser()
    ap.add_argument("--today", help="override today (YYYY-MM-DD) for testing")
    args = ap.parse_args()
    TODAY = pd(args.today) if args.today else date.today()
    t0 = time.time()
    data = build(TODAY)
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    html = TEMPLATE.read_text(encoding="utf-8").replace("/*__PEOPLE_DATA__*/null", payload)
    OUTPUT.write_text(html, encoding="utf-8")
    print(f"Wrote {OUTPUT.name} ({len(html) / 1024:.0f} KB) in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
