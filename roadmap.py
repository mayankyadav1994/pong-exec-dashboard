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

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

from jira_client import jira_get, jira_jql  # noqa: E402  (needs env loaded)

CONFIG = json.loads((ROOT / "config" / "roadmap.json").read_text(encoding="utf-8"))
PMO = json.loads((ROOT / "config" / "roadmap-pmo.json").read_text(encoding="utf-8"))
TEMPLATE = ROOT / "roadmap.template.html"
HISTORY_FILE = ROOT / "roadmap-history.json"
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
        p = ROOT / fn
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


def rollup(issues, allmap, game_re, vid):
    """Group a version's issues into items. Returns (items, misc_counts).

    Item categories: 'games' = a game whose epic is itself in this release (a
    new game shipping); 'gamefix' = an existing game that only gets fixes in
    this release; 'features' = everything else.
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
            in_version = rk in allmap and any(
                fv["id"] == vid for fv in allmap[rk]["fields"].get("fixVersions") or [])
            cat = ("games" if in_version else "gamefix") if is_game else "features"
            items[rk] = {"key": rk, "summary": rsum, "type": rtype, "status": sname,
                         "statusCat": scat, "cat": cat, "total": 0, "done": 0}
        item = items[rk]
        # A game-named story inside the release (e.g. "Game: Blue Bird Bonus - C2"
        # under an older game epic) means that game IS shipping here (a port).
        if item["cat"] == "gamefix" and it["key"] != rk and re.search(game_re, f.get("summary", ""), re.I) \
                and f["issuetype"]["name"] not in MISC_TYPES and "Subtask" not in f["issuetype"]["name"]:
            item["cat"] = "games"
        if it["key"] != rk:
            item["total"] += 1
            item["done"] += cat_key == "done"
    out = []
    for item in items.values():
        item["name"] = clean_name(item["summary"], game_re if item["cat"] != "features" else None)
        item["pct"] = round(100 * item["done"] / item["total"]) if item["total"] else (
            100 if item["statusCat"] == "done" else None)
        out.append(item)
    out.sort(key=lambda x: (-x["total"], x["name"]))
    return out, misc


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

    # 3. Version cards.
    for r in card_versions:
        v, rd = r["v"], r["rd"]
        items, misc = rollup(by_version.get(v["id"], []), allmap, game_re, v["id"])
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
                "slip": slip, "items": its, "misc": m["total"],
                "url": version_url(r["proj"], v["id"]),
            })

    # 4. Bucket epics: dated -> forecast cards, undated -> Unscheduled tray.
    pmo_fc = [f for f in PMO.get("forecasts", []) if f["space"] == sid]
    pmo_keys = {f["key"] for f in pmo_fc}
    bucket_epic_due = {}
    groups_of = defaultdict(list)
    for r in bucket_versions:
        epics = jira_jql(f'project = {r["proj"]} AND fixVersion = {r["v"]["id"]} '
                         f'AND issuetype = Epic AND statusCategory != Done', FIELDS)
        for e in epics:
            if e["key"] in dated_epics:
                continue
            f = e["fields"]
            cat = "games" if re.search(game_re, f["summary"], re.I) else "features"
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
                     else "Epic due dates — no release date yet"),
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
        "space": sid, "title": space["title"],
        "nav": [{"id": k, "title": s["title"], "file": s["file"]} for k, s in CONFIG["spaces"].items()],
        "generated": datetime.now(timezone.utc).isoformat(timespec="minutes"),
        "today": TODAY.isoformat(),
        "window": {"start": win_start.isoformat(), "end": win_end.isoformat()},
        "groups": groups_out, "cards": cards, "pubs": pubs, "parked": parked,
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
    win_end = month_add(first, w["months_forward"] + 1) - timedelta(days=1)
    tpl = TEMPLATE.read_text(encoding="utf-8")

    targets = list(CONFIG["spaces"]) if args.space == "all" else [args.space]
    for sid in targets:
        space = CONFIG["spaces"][sid]
        data = build_space(sid, space, win_start, win_end)
        payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
        html = tpl.replace("/*__ROADMAP_DATA__*/null", payload)
        (ROOT / space["file"]).write_text(html, encoding="utf-8")
        print(f"  [{sid}] wrote {space['file']}: {len(data['cards'])} cards, "
              f"{len(data['pubs'])} go-lives, "
              f"{sum(len(l['unscheduled']) for g in data['groups'] for l in g['lanes'])} unscheduled")


if __name__ == "__main__":
    main()
