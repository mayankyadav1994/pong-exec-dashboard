"""
roadmap.py
Builds the executive Roadmap pages (roadmap-igaming.html, roadmap-v2.html,
roadmap-css.html) from roadmap.template.html. Modelled on Simon's hand-built
"PFH Roadmap": swimlanes, Today line, solid = delivered / dashed = forecast.

Data rules (decided with PMO 2026-11-16):
  * Card = Jira fix version, placed on its real release date.
    Released -> Delivered; unreleased -> Forecast. Items = the epics (or top
    ancestors) of the issues in the version, with status + % done.
  * Bucket versions (Backlog / New Games / Features ...) have no date: their
    open epics WITH a due date become forecast cards on that date; without a
    date they go to the lane's Unscheduled tray.
  * Jira is READ-ONLY here and wins wherever it has a date.
    config/roadmap-pmo.json fills the gaps: PMO forecasts for undated items
    and the game go-live (publishing) schedule, which is not tracked in Jira.
  * Lane rules: config/roadmap.json.
  * Games are market-centric (PMO 2026-11-17): a numbered market version on a
    game epic or any of its stories = the game ships to that market on that
    release date (iGaming markets = ELG / PFH / Horse Play rows, V2 markets =
    P2P / PT / SW / C2 / HHR lanes). The first such release in a market is the
    launch; later ones are updates. A game with a due date but no dated market
    release yet is PMO-dated: shown at its due date in the market row(s) of its
    holding version ("New Games - iGaming" -> ELG, "New Games - PFH" -> PFH,
    V2 "New Games" / none -> "No market assigned"). A game launched on ELG with
    no PFH version gets an amber "awaiting PFH version" flag.

Usage:  python roadmap.py [igaming|v2|css|all] [--today YYYY-MM-DD]
"""

import argparse
import json
import re
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

HERE = Path(__file__).resolve().parent     # roadmap/ — template, pages, slip history
ROOT = HERE.parent                         # repo root — config/, shared/, .env
load_dotenv(ROOT / ".env")

sys.path.insert(0, str(ROOT / "shared"))   # shared/jira_client.py
from jira_client import jira_get, jira_jql  # noqa: E402  (needs env loaded)

CONFIG = json.loads((ROOT / "config" / "roadmap.json").read_text(encoding="utf-8"))
PMO = json.loads((ROOT / "config" / "roadmap-pmo.json").read_text(encoding="utf-8"))
TEMPLATE = HERE / "roadmap.template.html"
HISTORY_FILE = HERE / "roadmap-history.json"
SITE = "https://ponggamestudios.atlassian.net"
TODAY = date.today()  # overridable via --today
FIELDS = ["summary", "issuetype", "status", "parent", "fixVersions", "duedate"]

BUCKET_RE = re.compile(CONFIG["bucket_re"], re.I)
SKIP_ROOT_RE = re.compile(CONFIG["skip_root_re"], re.I)
NO_TRAY_RE = re.compile(r"Backlog|Dev Test|R&D|Game Config|Website", re.I)
MISC_TYPES = {"Bug", "Live Issue", "Enhancement", "CR", "Improvement"}


# ── small helpers ────────────────────────────────────────────────────────────
def pd(s):
    return date.fromisoformat(s[:10]) if s else None


def month_add(d, n):
    y, m = divmod(d.month - 1 + n, 12)
    return date(d.year + y, m + 1, 1)


def status_of(f):
    st = f.get("status") or {}
    return st.get("name", ""), (st.get("statusCategory") or {}).get("key", "")


def clean_name(summary, game_re):
    s = re.sub(game_re, "", summary, flags=re.I).strip() if game_re else summary
    s = re.sub(r"\s*\[V\]\s*", " ", s)
    s = re.sub(r"\s+-\s+(PFH2?|ELG|C2|P2P|PT|SW|HHR)\s*$", "", s)
    return re.sub(r"\s{2,}", " ", s).strip(" -")


_versions_cache = {}


def project_versions(proj):
    if proj not in _versions_cache:
        _versions_cache[proj] = [v for v in jira_get(f"/project/{proj}/versions")
                                 if not v.get("archived")]
    return _versions_cache[proj]


def version_url(proj, vid):
    return f"{SITE}/projects/{proj}/versions/{vid}/tab/release-report-all-issues"


# ── lane classification ──────────────────────────────────────────────────────
SERIES_RE = re.compile(r"\d+\.\d+")


def market_of(vname, proj, space):
    """Market a NUMBERED release version belongs to: the group id where groups
    have their own match (iGaming: elg / pfh / hp), else the lane id (V2: p2p /
    pt / ...). None for buckets, unnumbered versions and non-markets."""
    if BUCKET_RE.search(vname) or not SERIES_RE.search(vname):
        return None
    g, lanes = classify(vname, proj, space)
    if not g:
        return None
    m = g["id"] if g.get("match") else (lanes[0]["id"] if lanes else None)
    return None if m in space.get("non_markets", []) else m


def classify(vname, proj, space):
    """Return (group, [lanes]) a version contributes cards to, or (None, [])."""
    for g in space["groups"]:
        if proj not in g.get("projects", space["projects"]):
            continue
        card_lanes = [ln for ln in g["lanes"] if ln.get("kind") != "publishing"]
        if g.get("match"):
            if re.search(g["match"], vname, re.I):
                return g, card_lanes
            continue
        for ln in card_lanes:
            if ln.get("match") and re.search(ln["match"], vname, re.I):
                return g, [ln]
    return None, []


def lane_for_category(lanes, cat):
    """Pick the lane an item of category 'games'/'gamefix'/'features' belongs to.
    Updates to existing games are game releases too, so they share the Games lane."""
    if cat == "gamefix":
        cat = "games"
    for ln in lanes:
        if ln.get("filter") == cat:
            return ln
    for ln in lanes:
        if ln.get("filter") == "all":
            return ln
    return lanes[0] if lanes else None


# ── history (slip markers) ───────────────────────────────────────────────────
def load_history(space):
    hist = json.loads(HISTORY_FILE.read_text(encoding="utf-8")) if HISTORY_FILE.exists() else {}
    seed = {}
    for fn in space.get("history", []):
        p = ROOT / "game-pipeline" / fn   # release-history-*.json (Game Pipeline)
        if p.exists():
            d = json.loads(p.read_text(encoding="utf-8"))
            for name, rec in (d.get("releases") or {}).items():
                if rec.get("original_date"):
                    seed.setdefault(name, rec["original_date"])
    return hist, seed


def slip_for(hist, seed, proj, name, rd):
    if not rd:
        return None
    ph = hist.setdefault(proj, {})
    rec = ph.get(name)
    if rec is None:
        rec = ph[name] = {"original": seed.get(name) or rd.isoformat(),
                          "first_seen": TODAY.isoformat()}
    rec["current"] = rd.isoformat()
    orig = pd(rec["original"])
    if orig and orig != rd:
        return {"from": orig.isoformat(), "days": (rd - orig).days}
    return None


# ── issue roll-up ────────────────────────────────────────────────────────────
def root_of(issue, allmap):
    """Top-level grouping for an issue: nearest Epic ancestor, else top known ancestor."""
    f = issue["fields"]
    if f["issuetype"]["name"] == "Epic":
        return issue["key"], f
    cur = issue
    for _ in range(6):
        p = cur["fields"].get("parent")
        if not p:
            return cur["key"], cur["fields"]
        pf = p.get("fields") or {}
        if (pf.get("issuetype") or {}).get("name") == "Epic":
            return p["key"], pf
        if p["key"] in allmap:
            cur = allmap[p["key"]]
            continue
        return p["key"], pf
    return cur["key"], cur["fields"]


def rollup(issues, allmap, game_re, vid, launch=None):
    """Group a version's issues into items. Returns (items, misc_counts).

    Item categories: 'games' = a game launching in this release's market (its
    first release there, per `launch(epic_key, version_id)`); 'gamefix' = a game
    already in that market getting an update; 'features' = everything else.
    """
    items = {}
    misc = {"total": 0, "done": 0}
    for it in issues:
        f = it["fields"]
        rk, rf = root_of(it, allmap)
        rsum = rf.get("summary", "")
        rtype = (rf.get("issuetype") or {}).get("name", "")
        if rtype == "Release" or SKIP_ROOT_RE.search(rsum):
            continue
        _, cat_key = status_of(f)
        if rk == it["key"] and rtype != "Epic" and rtype in MISC_TYPES:
            misc["total"] += 1
            misc["done"] += cat_key == "done"
            continue
        sname, scat = status_of(rf)
        if rk not in items:
            is_game = bool(re.search(game_re, rsum, re.I))
            if launch is not None:
                new = launch(rk, vid)
            else:
                new = rk in allmap and any(
                    fv["id"] == vid for fv in allmap[rk]["fields"].get("fixVersions") or [])
            cat = ("games" if new else "gamefix") if is_game else "features"
            tagged = rk in allmap and any(
                fv["id"] == vid for fv in allmap[rk]["fields"].get("fixVersions") or [])
            items[rk] = {"key": rk, "summary": rsum, "type": rtype, "status": sname,
                         "statusCat": scat, "cat": cat, "total": 0, "done": 0,
                         "work": 0, "tagged": tagged}
        item = items[rk]
        if it["key"] != rk:
            item["total"] += 1
            item["done"] += cat_key == "done"
            t = f["issuetype"]["name"]
            if t not in MISC_TYPES and "ubtask" not in t and t != "Epic":
                item["work"] += 1  # new stories/tasks, not bugs/CRs
    out = []
    for item in items.values():
        item["name"] = clean_name(item["summary"], game_re if item["cat"] != "features" else None)
        item["pct"] = round(100 * item["done"] / item["total"]) if item["total"] else (
            100 if item["statusCat"] == "done" else None)
        out.append(item)
    out.sort(key=lambda x: (-x["total"], x["name"]))
    # Same game under two epics (e.g. an IG and a V2 "Mayan Madness"): list once.
    # A launch beats an update when both epics land in the same release.
    seen = {i["name"].lower() for i in out if i["cat"] == "games"}
    dedup = []
    for item in out:
        if item["cat"] == "gamefix" and item["name"].lower() in seen:
            continue
        if item["cat"] != "features":
            if any(d is not item and d["cat"] == item["cat"] and d["name"].lower() == item["name"].lower()
                   for d in dedup):
                continue
            seen.add(item["name"].lower())
        dedup.append(item)
    return dedup, misc


def desc_lane(desc, per_lane, lanes):
    """A version split across lanes keeps its Jira description on one card only:
    the lane whose item names the description mentions most (e.g. "Viking Voyage
    and Lanterns Rising" -> Games). Ties go to the Games lane if the text says
    "game", else the Features/first lane."""
    if len(per_lane) < 2 or not desc:
        return next(iter(per_lane))
    norm = lambda t: re.sub(r"[^a-z0-9 ]", "", t.lower().replace("’", "'").replace("'", ""))
    words = [w for w in norm(desc).split() if len(w) >= 4 and w not in ("with", "and", "fixes", "new", "games")]
    lane_filter = {ln["id"]: ln.get("filter") for ln in lanes}
    def score(lid):
        names = " ".join(norm(i["name"]) for i in per_lane[lid])
        sc = sum(w in names for w in words)
        if lane_filter.get(lid) == "games" and re.search(r"games?", desc, re.I):
            sc += 1
        return sc
    order = sorted(per_lane, key=lambda lid: (-score(lid), lane_filter.get(lid) != "features"))
    return order[0]


def card_status(released, rd, done, total, any_wip):
    if released:
        return "Delivered"
    if rd and rd < TODAY:
        return "Overdue"
    if total and done == total:
        return "Ready"
    if done or any_wip:
        return "In progress"
    return "Planned"


# ── main build per space ─────────────────────────────────────────────────────
def build_space(sid, space, win_start, win_end):
    game_re = space["game_re"]
    hist, seed = load_history(space)
    cards, unscheduled, pubs, parked = [], defaultdict(list), [], []

    # 1. Sort every version of every project into card / bucket / unscheduled.
    card_versions, bucket_versions = [], []
    projects = sorted({p for g in space["groups"] for p in g.get("projects", space["projects"])})
    for proj in projects:
        for v in project_versions(proj):
            g, lanes = classify(v["name"], proj, space)
            if not g:
                continue
            rd = pd(v.get("releaseDate"))
            rec = {"v": v, "proj": proj, "group": g, "lanes": lanes, "rd": rd}
            if rd:
                if win_start <= rd <= win_end or (not v.get("released") and rd < win_start):
                    card_versions.append(rec)
            elif v.get("released"):
                continue
            elif BUCKET_RE.search(v["name"]):
                bucket_versions.append(rec)
            else:
                unscheduled[(g["id"], lanes[0]["id"])].append({
                    "kind": "version", "name": v["name"], "desc": v.get("description") or "",
                    "url": version_url(proj, v["id"])})

    # 2. Issues in the dated versions (one JQL per project).
    by_version = defaultdict(list)
    allmap = {}
    for proj in projects:
        ids = [r["v"]["id"] for r in card_versions if r["proj"] == proj]
        if not ids:
            continue
        idset = set(ids)
        for i in range(0, len(ids), 40):
            chunk = ids[i:i + 40]
            for it in jira_jql(f"project = {proj} AND fixVersion in ({','.join(chunk)})", FIELDS):
                allmap[it["key"]] = it
                for fv in it["fields"].get("fixVersions") or []:
                    if fv["id"] in idset:
                        by_version[fv["id"]].append(it)
    print(f"  [{sid}] {len(card_versions)} dated versions, {len(allmap)} issues, "
          f"{len(bucket_versions)} buckets")

    dated_epics = {k for k, it in allmap.items() if it["fields"]["issuetype"]["name"] == "Epic"}

    # 2a. Epic age. Long-running container epics (Game Menu, Server, System,
    # Website, VGTC, Task Handler ...) are years old and never carry a release
    # fix version themselves; real requirements are either tagged or recent.
    roots = {root_of(it, allmap)[0] for it in allmap.values()}
    epic_created = {}
    rk_list = sorted(roots)
    for i in range(0, len(rk_list), 50):
        for it in jira_jql(f"key in ({','.join(rk_list[i:i + 50])})", ["created"]):
            epic_created[it["key"]] = pd(it["fields"].get("created"))
    max_age = CONFIG.get("scope", {}).get("container_age_months", 12)
    recent_cut = month_add(TODAY.replace(day=1), -max_age)

    def in_scope(it):
        """Card scope (agreed with PMO 2026-11-23): games launching in this
        market; features whose epic carries this release, or that have new
        stories here and are recent (created within container_age_months).
        Everything else (bugs/CRs only, game updates, old container epics) is
        counted on the card, not listed."""
        if it["cat"] == "games":
            return True
        if it["cat"] == "gamefix":
            return False
        created = epic_created.get(it["key"])
        return it["tagged"] or (it["work"] > 0 and created is not None and created >= recent_cut)

    # 2b. Game market index: every version on each game epic and its stories.
    vindex = {v["id"]: (v, proj) for proj in projects for v in project_versions(proj)}
    game_projects = space.get("game_projects", [])
    game_keys = set()
    for it in allmap.values():
        rk, rf = root_of(it, allmap)
        if re.search(game_re, rf.get("summary", ""), re.I):
            game_keys.add(rk)
    pmo_games = []
    if game_projects:
        pmo_games = [e for e in jira_jql(
            f'project in ({",".join(game_projects)}) AND issuetype = Epic '
            f'AND statusCategory != Done AND duedate is not EMPTY', FIELDS)
            if re.search(game_re, e["fields"]["summary"], re.I)]
        game_keys |= {e["key"] for e in pmo_games}
    game_vids = defaultdict(set)
    keys = sorted(game_keys)
    for i in range(0, len(keys), 40):
        chunk = keys[i:i + 40]
        for it in jira_jql(f"key in ({','.join(chunk)}) OR parent in ({','.join(chunk)})",
                           ["fixVersions", "parent", "issuetype"]):
            owner = it["key"] if it["key"] in game_keys else (it["fields"].get("parent") or {}).get("key")
            if owner in game_keys:
                game_vids[owner] |= {fv["id"] for fv in it["fields"].get("fixVersions") or []}

    def markets(gkey):
        """market -> sorted [(date|None, version name, released)] of numbered versions."""
        out = defaultdict(list)
        for vid in game_vids.get(gkey, ()):
            if vid not in vindex:
                continue
            v, proj = vindex[vid]
            m = market_of(v["name"], proj, space)
            if m:
                out[m].append((pd(v.get("releaseDate")), v["name"], bool(v.get("released"))))
        for m in out:
            out[m].sort(key=lambda t: (t[0] is None, t[0] or date.max))
        return out

    def launch(gkey, vid):
        if gkey not in game_vids or vid not in vindex:
            return False
        v, proj = vindex[vid]
        m = market_of(v["name"], proj, space)
        rd = pd(v.get("releaseDate"))
        firsts = [d for d, _, _ in markets(gkey).get(m, []) if d]
        return bool(m and rd and firsts and rd <= min(firsts))

    # 3. Version cards.
    for r in card_versions:
        v, rd = r["v"], r["rd"]
        items, misc = rollup(by_version.get(v["id"], []), allmap, game_re, v["id"], launch)
        touched = sorted({i["name"] for i in items if i["cat"] != "features"})
        kept = []
        for it in items:
            if in_scope(it):
                kept.append(it)
            else:
                misc["total"] += max(it["total"], 1)
                misc["done"] += it["done"]
        items = kept
        slip = slip_for(hist, seed, r["proj"], v["name"], rd)
        per_lane = defaultdict(list)
        for it in items:
            ln = lane_for_category(r["lanes"], it["cat"])
            per_lane[ln["id"]].append(it)
        if not per_lane:
            per_lane[lane_for_category(r["lanes"], "features")["id"]] = []
        # Misc bugs/fixes go to the features (or only) card of this version.
        misc_lane = next((ln["id"] for ln in r["lanes"] if ln["id"] in per_lane
                          and ln.get("filter") in ("features", "all")), next(iter(per_lane)))
        dlane = desc_lane(v.get("description") or "", per_lane, r["lanes"])
        for lane_id, its in per_lane.items():
            m = misc if lane_id == misc_lane else {"total": 0, "done": 0}
            total = sum(i["total"] for i in its) + m["total"]
            done = sum(i["done"] for i in its) + m["done"]
            wip = any(i["statusCat"] == "indeterminate" for i in its)
            cards.append({
                "group": r["group"]["id"], "lane": lane_id, "source": "version",
                "title": v["name"], "desc": (v.get("description") or "") if lane_id == dlane else "",
                "date": rd.isoformat(), "start": v.get("startDate"),
                "released": bool(v.get("released")),
                "status": card_status(v.get("released"), rd, done, total, wip),
                "done": done, "total": total,
                "pct": round(100 * done / total) if total else None,
                "slip": slip, "items": its, "misc": m["total"], "touched": touched,
                "url": version_url(r["proj"], v["id"]),
            })

    # 4. Bucket epics: dated -> forecast cards, undated -> Unscheduled tray.
    pmo_fc = [f for f in PMO.get("forecasts", []) if f["space"] == sid]
    pmo_keys = {f["key"] for f in pmo_fc}
    bucket_epic_due = {}
    groups_of = defaultdict(list)

    # 4a. PMO-dated games: due date, but no dated market release yet. Placed in
    # the row(s) of their holding versions; none -> the "no market" lane.
    unassigned = space.get("unassigned_games")
    for e in pmo_games:
        mk = markets(e["key"])
        if any(d for rows in mk.values() for d, _, _ in rows):
            continue  # already in a dated market release -> shown there
        f = e["fields"]
        due = pd(f["duedate"])
        rows = {}
        for vid in game_vids.get(e["key"], ()):
            if vid not in vindex:
                continue
            v, proj = vindex[vid]
            g, lanes = classify(v["name"], proj, space)
            if not g or not (BUCKET_RE.search(v["name"]) or market_of(v["name"], proj, space)):
                continue
            ln = lane_for_category(lanes, "games")
            if ln["id"] in space.get("non_markets", []) and ln["id"] != (unassigned or {}).get("lane"):
                continue
            rows[(g["id"], ln["id"])] = v["name"]
        if not rows and unassigned:
            rows[(unassigned["group"], unassigned["lane"])] = "No market assigned"
        if not rows:
            print(f"  [{sid}] PMO game {e['key']} has no market row — skipped")
            continue
        sname, scat = status_of(f)
        bucket_epic_due[e["key"]] = due
        for (gid, lid), bname in rows.items():
            item = {"key": e["key"], "summary": f["summary"], "type": "Epic",
                    "name": clean_name(f["summary"], game_re), "status": sname,
                    "statusCat": scat, "cat": "games", "total": 0, "done": 0,
                    "pct": None, "due": due.isoformat()}
            month = "overdue" if due < TODAY else due.strftime("%Y-%m")
            groups_of[(gid, lid, month)].append((bname, item))

    for r in bucket_versions:
        epics = jira_jql(f'project = {r["proj"]} AND fixVersion = {r["v"]["id"]} '
                         f'AND issuetype = Epic AND statusCategory != Done', FIELDS)
        for e in epics:
            if e["key"] in dated_epics:
                continue
            f = e["fields"]
            cat = "games" if re.search(game_re, f["summary"], re.I) else "features"
            if cat == "games" and f.get("duedate"):
                continue  # PMO-dated game: placed by step 4a
            ln = lane_for_category(r["lanes"], cat)
            due = pd(f.get("duedate"))
            sname, scat = status_of(f)
            item = {"key": e["key"], "summary": f["summary"], "type": "Epic",
                    "name": clean_name(f["summary"], game_re if cat == "games" else None),
                    "status": sname, "statusCat": scat, "cat": cat,
                    "total": 0, "done": 0, "pct": None}
            if due:
                bucket_epic_due[e["key"]] = due
                item["due"] = due.isoformat()
                month = "overdue" if due < TODAY else due.strftime("%Y-%m")
                groups_of[(r["group"]["id"], ln["id"], month)].append((r["v"]["name"], item))
            elif e["key"] not in pmo_keys and not NO_TRAY_RE.search(r["v"]["name"]):
                unscheduled[(r["group"]["id"], ln["id"])].append({
                    "kind": "epic", "key": e["key"], "name": item["name"],
                    "status": sname, "bucket": r["v"]["name"],
                    "url": f"{SITE}/browse/{e['key']}"})
    # One card per lane per month (like Simon's "December Releases"), plus one
    # "past due" card at today for epics whose due date passed with no release.
    for (gid, lid, month), pairs in sorted(groups_of.items(), key=lambda kv: kv[0][2]):
        its = sorted((it for _, it in pairs), key=lambda i: (i["due"], i["name"]))
        overdue = month == "overdue"
        buckets = " · ".join(sorted({b for b, _ in pairs}))
        cards.append({
            "group": gid, "lane": lid, "source": "epic-due",
            "title": "Past due · no release yet" if overdue else buckets,
            "desc": (f"Due date passed, not in a dated release ({buckets})" if overdue
                     else "PMO dates (epic due dates) — no market release yet"),
            "date": TODAY.isoformat() if overdue else max(i["due"] for i in its),
            "start": None, "released": False,
            "status": "Overdue" if overdue else "Forecast",
            "done": 0, "total": 0, "pct": None, "slip": None,
            "items": its, "misc": 0, "url": None,
        })

    # 5. PMO forecasts — only where Jira has no date.
    for fc in pmo_fc:
        jira_dated = fc["key"] in bucket_epic_due or fc["key"] in dated_epics
        if fc.get("version"):
            jira_dated = jira_dated or any(
                r["v"]["name"] == fc["version"] for r in card_versions)
        if jira_dated:
            print(f"  [{sid}] PMO forecast {fc['key']} skipped — Jira has a date (Jira wins)")
            continue
        try:
            jf = jira_get(f"/issue/{fc['key']}", {"fields": "summary,status"})["fields"]
            sname, scat = status_of(jf)
        except Exception:
            sname, scat = "", ""
        g_lanes = next((g["lanes"] for g in space["groups"] if g["id"] == fc["group"]), [])
        if fc["lane"] not in {ln["id"] for ln in g_lanes} and g_lanes:
            fc = {**fc, "lane": lane_for_category([ln for ln in g_lanes if ln.get("kind") != "publishing"],
                                                  fc["lane"])["id"]}
        key = (fc["group"], fc["lane"])
        unscheduled[key] = [u for u in unscheduled[key]
                            if u.get("key") != fc["key"] and u.get("name") != fc.get("version")]
        cards.append({
            "group": fc["group"], "lane": fc["lane"], "source": "pmo",
            "title": fc["title"], "label": fc.get("label"), "desc": "",
            "date": fc["date"], "start": None, "released": False,
            "status": sname or "Forecast", "done": 0, "total": 0, "pct": None, "slip": None,
            "items": [], "notes": fc.get("notes", []), "misc": 0,
            "key": fc["key"], "url": f"{SITE}/browse/{fc['key']}",
        })
    for pk in PMO.get("parked", []):
        if pk["space"] == sid:
            parked.append(pk)

    # 5b. "Awaiting market version": a game launched in one market (e.g. ELG)
    # with no dated release in its sister market (PFH) yet.
    awaits, seen_aw = [], set()

    def game_norm(name):
        # Same game, per-market epics: "Kandy Land Keno - PFH", "Northern Buffalo HD".
        n = re.sub(r"\s+(HD|-\s*(PFH2?|ELG|Vertical))\s*$", "", name.strip(), flags=re.I)
        return re.sub(r"[^a-z0-9]", "", n.lower())

    for rule in space.get("awaiting", []):
        to_g = next(g for g in space["groups"] if g["id"] == rule["to"])
        in_target = {game_norm(n) for c in cards
                     if c["group"] == rule["to"] and c["source"] == "version"
                     for n in c.get("touched", [])}
        to_lane = lane_for_category([ln for ln in to_g["lanes"] if ln.get("kind") != "publishing"], "games")
        for c in sorted((c for c in cards if c["group"] == rule["from"] and c["source"] == "version"),
                        key=lambda c: c["date"]):
            for it in c["items"]:
                # Only the space's own game epics: legacy PFH-/V1- epics keep their
                # PFH history in other projects' versions, which we can't see here.
                if it["type"] != "Epic" or it["key"].split("-")[0] not in game_projects:
                    continue
                if it["cat"] != "games" or it["key"] in seen_aw or \
                        re.search(rule.get("skip_re", r"^\b$"), it["summary"], re.I):
                    continue
                target = markets(it["key"]).get(rule["to"], [])
                if any(d for d, _, _ in target) or game_norm(it["name"]) in in_target:
                    continue
                seen_aw.add(it["key"])
                undated = next((n for d, n, _ in target if not d), None)
                awaits.append({"group": rule["to"], "lane": to_lane["id"], "date": c["date"],
                               "game": it["name"], "key": it["key"], "from": c["title"],
                               "text": (f"{undated} has no release date" if undated
                                        else f"No {to_g['label']} version yet")})

    # 6. Publishing (go-live) — PMO schedule + "assumed = release date" fallback.
    for g in space["groups"]:
        if not any(ln.get("kind") == "publishing" for ln in g["lanes"]):
            continue
        entries = (PMO.get("publishing", {}).get(sid, {}) or {}).get(g["id"], [])
        keys = [e["key"] for e in entries if e.get("key")]
        epic_info = {}
        if keys:
            for it in jira_jql(f"key in ({','.join(keys)})", FIELDS):
                epic_info[it["key"]] = it["fields"]
        covered = set(keys)
        for e in entries:
            f = epic_info.get(e.get("key"), {})
            rel = None
            for fv in f.get("fixVersions") or []:
                vv = next((v for p in g.get("projects", []) for v in project_versions(p)
                           if v["id"] == fv["id"]), None)
                if not vv or not re.search(g["match"], vv["name"], re.I) or BUCKET_RE.search(vv["name"]):
                    continue
                vd = pd(vv.get("releaseDate"))
                if vd and (rel is None or vd < pd(rel["date"])):
                    rel = {"name": vv["name"], "date": vd.isoformat(), "released": bool(vv.get("released"))}
            if rel is None and f.get("duedate"):
                rel = {"name": "epic due date", "date": f["duedate"], "released": False, "estimate": True}
            golive = pd(e["golive"])
            flag, flag_text = None, None
            if rel is None:
                flag, flag_text = "amber", f"No {g['label']} release date in Jira"
            elif golive < pd(rel["date"]) and not rel["released"]:
                flag, flag_text = "red", f"Go-live before release ({rel['name']} · {rel['date']})"
            pubs.append({"group": g["id"], "game": e["game"], "key": e.get("key"),
                         "golive": e["golive"], "until": e.get("until"), "note": e.get("note"),
                         "assumed": False, "release": rel, "flag": flag, "flagText": flag_text,
                         "status": status_of(f)[0] if f else ""})
        # Markets whose go-live IS the release date (ELG): every game launch.
        if g.get("golive_from_release"):
            for c in sorted((c for c in cards if c["group"] == g["id"] and c["source"] == "version"),
                            key=lambda c: c["date"]):
                for it in c["items"]:
                    if it["cat"] != "games" or it["key"] in covered:
                        continue
                    covered.add(it["key"])
                    pubs.append({"group": g["id"], "game": it["name"], "key": it["key"],
                                 "golive": c["date"], "until": None,
                                 "note": "Go-live = release date",
                                 "assumed": False,
                                 "release": {"name": c["title"], "date": c["date"],
                                             "released": c["released"]},
                                 "flag": None, "flagText": None, "status": it["status"]})
        # Fallback: UPCOMING new games (unreleased release or future epic due
        # date) with no PMO go-live yet -> assumed go-live = release date.
        # Already-released games without a PMO entry are assumed live; skipped.
        upcoming = sorted((c for c in cards if c["group"] == g["id"] and not c["released"]
                           and c["source"] in ("version", "epic-due") and c["status"] != "Overdue"),
                          key=lambda c: c["date"])
        for c in upcoming:
            for it in c["items"]:
                if it["cat"] != "games" or it["key"] in covered:
                    continue
                covered.add(it["key"])
                when = it.get("due") or c["date"]
                pubs.append({"group": g["id"], "game": it["name"], "key": it["key"],
                             "golive": when, "until": None,
                             "note": "Go-live not scheduled · assumed = release date",
                             "assumed": True,
                             "release": {"name": c["title"] if c["source"] == "version" else "epic due date",
                                         "date": when, "released": False},
                             "flag": None, "flagText": None, "status": it["status"]})

    HISTORY_FILE.write_text(json.dumps(hist, indent=1, sort_keys=True), encoding="utf-8")

    groups_out = []
    for g in space["groups"]:
        lanes_out = []
        for ln in g["lanes"]:
            n = sum(1 for c in cards if c["group"] == g["id"] and c["lane"] == ln["id"]) + \
                (sum(1 for p in pubs if p["group"] == g["id"]) if ln.get("kind") == "publishing" else 0) + \
                len(unscheduled.get((g["id"], ln["id"]), []))
            lanes_out.append({"id": ln["id"], "label": ln["label"], "color": ln["color"],
                              "kind": ln.get("kind", "cards"), "count": n,
                              "unscheduled": unscheduled.get((g["id"], ln["id"]), [])})
        if g.get("hide_if_empty") and not any(ln["count"] for ln in lanes_out):
            continue
        groups_out.append({"id": g["id"], "label": g["label"], "lanes": lanes_out})

    return {
        "space": sid, "title": space["title"], "pipeline": space.get("pipeline"),
        "nav": [{"id": k, "title": s["title"], "file": s["file"]} for k, s in CONFIG["spaces"].items()]
               + CONFIG.get("extra_nav", []),
        "generated": datetime.now(timezone.utc).isoformat(timespec="minutes"),
        "today": TODAY.isoformat(),
        "window": {"start": win_start.isoformat(), "end": win_end.isoformat()},
        "groups": groups_out, "cards": cards, "pubs": pubs, "parked": parked, "awaits": awaits,
        "browse": f"{SITE}/browse/",
    }


def main():
    global TODAY
    ap = argparse.ArgumentParser()
    ap.add_argument("space", nargs="?", default="all", choices=["all", *CONFIG["spaces"].keys()])
    ap.add_argument("--today", help="override today (YYYY-MM-DD) for testing")
    args = ap.parse_args()
    TODAY = pd(args.today) if args.today else date.today()

    w = CONFIG["window"]
    first = TODAY.replace(day=1)
    win_start = month_add(first, -w["months_back"])
    if w.get("from_year_start"):
        # Scroll back to January: this year's, or last year's while in Q1.
        win_start = min(win_start, date(TODAY.year - (1 if TODAY.month <= 3 else 0), 1, 1))
    win_end = month_add(first, w["months_forward"] + 1) - timedelta(days=1)
    tpl = TEMPLATE.read_text(encoding="utf-8")

    targets = list(CONFIG["spaces"]) if args.space == "all" else [args.space]
    for sid in targets:
        space = CONFIG["spaces"][sid]
        data = build_space(sid, space, win_start, win_end)
        payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
        html = tpl.replace("/*__ROADMAP_DATA__*/null", payload)
        (HERE / space["file"]).write_text(html, encoding="utf-8")
        print(f"  [{sid}] wrote {space['file']}: {len(data['cards'])} cards, "
              f"{len(data['pubs'])} go-lives, "
              f"{sum(len(l['unscheduled']) for g in data['groups'] for l in g['lanes'])} unscheduled")


if __name__ == "__main__":
    main()
