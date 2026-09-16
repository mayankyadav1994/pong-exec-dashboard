#!/usr/bin/env python3
"""
elg_cost_data.py
----------------
Shared layer for the ELG game cost model: pulls the ticket tree out of Jira,
classifies every ticket to a department, and caches the result to JSON.

Run this when you want fresh numbers. The spreadsheet and page builders then
read the cache, so they rebuild instantly with no network and give identical
output from identical input.

    python elg_cost_data.py            # ~2 min, hits Jira, writes the cache
    python elg_cost_spreadsheet.py     # cache -> .xlsx + .md   (rarely changes)
    python elg_cost_page.py            # cache -> .html         (the one we iterate on)

Deps: pip install requests
"""

import os
import re
import sys
import time
import json
import argparse
import io
import statistics as st
from collections import defaultdict, Counter

import requests
from requests.auth import HTTPBasicAuth

# Token lives in User-scope env; self-heal so no per-run bridging is needed.
try:
    import winreg as _winreg
    if not os.environ.get("JIRA_API_TOKEN"):
        with _winreg.OpenKey(_winreg.HKEY_CURRENT_USER, "Environment") as _k:
            os.environ["JIRA_API_TOKEN"] = _winreg.QueryValueEx(_k, "JIRA_API_TOKEN")[0]
except (ImportError, OSError):
    pass

# Anchored to this file, not the working directory, so the cache is found
# no matter where you run the scripts from.
HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_FILE = os.path.join(HERE, "elg_cost_data.json")

# ===========================================================================
#  RUN CONFIG -- games.json
# ===========================================================================
# The model's inputs live in games.json beside this file, not in the source,
# so a game can be added without editing Python:
#
#     python elg_cost_data.py --find buffalo          search Jira for epics
#     python elg_cost_data.py --add IG-7781 --cat Port
#     python elg_cost_data.py --list
#
# Shape:
#   {"hourly_rate": 82.5,
#    "games":         {"IG-1506": {"name": ..., "cat": ..., "rel": ...}, ...},
#    "release_epics": {"IG-5427": "ELG 4.20", ...}}
CONFIG_FILE = os.path.join(HERE, "games.json")


def load_config(path=CONFIG_FILE):
    if not os.path.exists(path):
        sys.exit(f"No {path} -- the game list lives there.")
    with io.open(path, encoding="utf-8") as fh:
        return json.load(fh)


def save_config(cfg, path=CONFIG_FILE):
    """Written with indent=2 so the file stays reviewable in a diff."""
    with io.open(path, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


CONFIG = load_config()

# epic -> (display name, category, ELG release). The tuple shape is kept
# because elg_cost_spreadsheet.py unpacks `for e, (n, c, r) in GAMES.items()`.
GAMES = {k: (v["name"], v["cat"], v["rel"]) for k, v in CONFIG["games"].items()}

# Blended hourly rate used to cost the hours. One knob -- change it in
# games.json and every figure on the page follows.
HOURLY_RATE = CONFIG.get("hourly_rate", 82.5)

# Release-level epics -- overhead, not attributable to any one game.
RELEASE_EPICS = CONFIG.get("release_epics", {})
# ===========================================================================

JIRA_BASE = "https://ponggamestudios.atlassian.net"
JIRA_EMAIL = "mayank.yadav@pongstudios.com"
API_TOKEN = os.environ.get("JIRA_API_TOKEN", "")
AUTH = HTTPBasicAuth(JIRA_EMAIL, API_TOKEN)
HEADERS = {"Accept": "application/json", "Content-Type": "application/json"}

CATEGORIES = ["Port", "Skin", "Branded", "New"]
DEPT_ORDER = ["Concept", "Creative", "Math", "Sound", "Game Engine", "Server",
              "Dev", "Review", "QA", "Bugs", "Release"]
SIZES = ["XS", "S", "M", "L", "XL"]
ELG_RE = re.compile(r"^ELG\b", re.I)
# Some build work on an ELG game is tagged to a PFH release instead, and some
# is only identifiable from the summary -- "PFH Configs", "PFH Pools",
# "Adjust splash page ... for PFH" -- where the ticket carries no fix version
# at all. A ticket counts as PFH if EITHER says so.
#
# PFH also WINS over ELG. Four tickets carry both an ELG and a PFH fix version
# and they belong in the PFH figure, not in the ELG build figure, so the ELG
# scope is (elg AND NOT pfh). The two still do not partition -- plenty of
# tickets carry neither -- so the scopes sum to less than Full game.
PFH_RE = re.compile(r"^PFH\b", re.I)          # fix version, anchored at the start
PFH_TITLE_RE = re.compile(r"\bPFH\b", re.I)   # summary, anywhere in the text

FIELDS = ["summary", "issuetype", "parent", "fixVersions", "status",
          "timeoriginalestimate", "timespent", "timeestimate"]

# ===========================================================================
#  DEPARTMENT RULES -- first match wins, top to bottom.
#  kind "type" matches the issue type; kind "text" matches the summary.
#  Text rules sit ABOVE the type fallbacks on purpose: issue type is wrong
#  often enough to matter (see module docstring).
# ===========================================================================
RULES = [
    # -- reliable issue types ------------------------------------------------
    # Enhancement is deliberately NOT here: it is scoped feature work, not a
    # defect, and lumping it in overstated the Bugs column. With the type
    # dropped it falls through to the text rules and the Dev fallback, so an
    # Enhancement named "[Server] ..." now lands on Server rather than Bugs.
    ("Bugs",        "type", {"Bug", "Live Issue"}),
    ("QA",          "type", {"QA Task", "QA Subtask"}),
    ("Release",     "type", {"Release", "Release Subtask"}),
    # -- review must win before the department it reviews --------------------
    ("Review",      "text", r"review\s*(&|and)\s*refinement"
                            r"|review\s*[-–]\s*(dev|math|art|sound|creative|qa)"
                            r"|code review|game review|art review|review changes"
                            r"|implement review|[-–]\s*review\s*$"),
    ("Concept",     "text", r"design doc|concept layout|\bgdd\b"
                            r"|game info package|\bgip\b|[-–]\s*concept\b"),
    # -- explicit bracket tags (newer naming convention, authoritative) ------
    ("Server",      "text", r"\[server\]"),
    ("Game Engine", "text", r"\[ge\]"),
    ("Math",        "text", r"\[math\]"),
    ("Dev",         "text", r"\[fe\]"),
    # -- older keyword naming ------------------------------------------------
    # Server sits above Game Engine deliberately: "deployment on New Game
    # Engine" and "Configs Deployment >> ... New Game Engine" are releasing
    # onto the engine platform, not building the engine. The [GE] bracket
    # rule above already protects genuine engine work from this.
    ("Server",      "text", r"tickets?\s*(&|and)\s*pools|prizes,\s*tickets"
                            r"|prepare and verify pools|weighted outcome"
                            r"|game config|\bconfigs?\b|\bpools?\b"
                            r"|\bdeploy\w*\s+(on|to)\b"),
    ("Game Engine", "text", r"game engine|simulation|simulator"),
    ("Sound",       "text", r"\bsounds?\b|\bsfx\b|\baudio\b|\bmusic\b"
                            r"|wwise|soundtrack"),
    ("Math",        "text", r"\bmath\b|par sheet|\brtp\b"),
    # -- issue-type fallbacks ------------------------------------------------
    ("Creative",    "type", {"Creative Task", "Creative Subtask",
                             "Design Task", "Design Subtask", "Design Sub-Task"}),
    ("Math",        "type", {"Math Task", "Math Subtask"}),
    ("Sound",       "type", {"Sound Task", "Sound Subtask"}),
    ("Dev",         "type", {"Dev Task", "Dev Subtask", "Story", "Task"}),
]


def classify(issuetype, summary):
    """-> (department, rule that decided it) so the call is auditable."""
    for dept, kind, matcher in RULES:
        if kind == "type" and issuetype in matcher:
            return dept, f"type={issuetype}"
        if kind == "text":
            m = re.search(matcher, summary, re.I)
            if m:
                return dept, f"text~'{m.group(0)[:28]}'"
    return "Dev", "fallback"


# ===========================================================================
#  2024 T-SHIRT LEGEND -- IG_Game_Ticket.md section 2
#  Stored as raw rows so the per-department bands are derived, not asserted.
#  R&R rows are routed to Review to match how actuals are classified.
# ===========================================================================
LEGEND = [
    # (section, subtask, department, [XS, S, M, L, XL])
    ("Creative", "Optimization - Assets",              "Creative", [4, 4, 6, 8, 16]),
    ("Creative", "Optimization - Animations",          "Creative", [4, 8, 12, 16, 24]),
    ("Creative", "In House Prep, additional assets",   "Creative", [4, 8, 12, 16, 24]),
    ("Creative", "Review & Refinement - Artist",       "Review",   [2, 4, 6, 8, 16]),
    ("Creative", "Review & Refinement - Creative Dir", "Review",   [2, 4, 6, 8, 10]),

    ("Math", "GDD - Math",                    "Math",   [3, 2, 2, 2, 2]),
    ("Math", "verification",                  "Math",   [0, 10, 15, 20, 30]),
    ("Math", "Math Models/weighted outcomes", "Math",   [0, 10, 15, 20, 20]),
    ("Math", "Pools/Flares",                  "Math",   [2, 2, 2, 2, 2]),
    ("Math", "Help Pages - Math",             "Math",   [4, 4, 4, 4, 4]),
    ("Math", "Review & Refinement - Math",    "Review", [4, 8, 12, 16, 20]),

    ("Sound-New", "Create Game Soundtracks", "Sound",  [16, 32, 48, 64, 96]),
    ("Sound-New", "Create SFX/Transitions",  "Sound",  [5, 14, 23, 28, 32]),
    ("Sound-New", "Game Bank Mix",           "Sound",  [2, 4, 8, 16, 24]),
    ("Sound-New", "Wwise Implementation",    "Sound",  [2, 2, 2, 4, 6]),
    ("Sound-New", "Review & Refinement",     "Review", [2, 3, 6, 12, 16]),

    ("Sound-Port", "Sound Asset Export",           "Sound",  [2, 4, 6, 8, 10]),
    ("Sound-Port", "Game Bank Remix",              "Sound",  [0, 0, 0, 8, 16]),
    ("Sound-Port", "Wwise Project Cleanup",        "Sound",  [0, 0, 0, 8, 16]),
    ("Sound-Port", "Create Wwise Export Projects", "Sound",  [2, 2, 2, 2, 4]),
    ("Sound-Port", "Review & Refinement",          "Review", [2, 2, 2, 2, 4]),

    ("Dev:Game Clone", "[FE] - Game Clone",                    "Dev",         [1, 1, 2, 4, 8]),
    ("Dev:Game Clone", "[FE] - Assets Replacement",            "Dev",         [2, 4, 6, 8, 12]),
    ("Dev:Game Clone", "[GE] - Sim - XML config & Game Logic", "Game Engine", [0, 0, 0, 2, 4]),
    ("Dev:Game Clone", "[GE] - Sim - New Feature",             "Game Engine", [0, 0, 0, 2, 4]),
    ("Dev:Game Clone", "[Math] - RTP verification",            "Math",        [0, 0, 0, 2, 4]),
    ("Dev:Game Clone", "[Server] - Math Engine & Ticket Resp", "Server",      [0, 0, 0, 2, 4]),
    ("Dev:Game Clone", "[Server] - Tickets & Pools",           "Server",      [0, 0, 0, 2, 4]),

    ("Dev:Base Game", "[GE] - Sim - XML config & Game Logic", "Game Engine", [2, 4, 8, 12, 16]),
    ("Dev:Base Game", "[GE] - Sim - New Feature",             "Game Engine", [2, 4, 8, 12, 16]),
    ("Dev:Base Game", "[Math] - RTP verification",            "Math",        [1, 2, 4, 6, 8]),
    ("Dev:Base Game", "[Server] - Game config",               "Server",      [2, 4, 8, 12, 16]),
    ("Dev:Base Game", "[Server] - Tickets & Pools",           "Server",      [1, 2, 4, 6, 8]),
    ("Dev:Base Game", "[Server] - Math Engine & Ticket Resp", "Server",      [2, 4, 8, 12, 16]),

    ("Dev:Review", "[Dev] - Code Review",    "Review", [1, 2, 4, 6, 8]),
    ("Dev:Review", "[Dev] - Review Changes", "Review", [1, 2, 4, 6, 8]),

    ("Dev:Respin", "[FE] - Feature Clone",                    "Dev",         [2, 4, 4, 8, 12]),
    ("Dev:Respin", "[FE] - Assets Replacement",               "Dev",         [4, 6, 8, 12, 16]),
    ("Dev:Respin", "[GE] - Sim - XML config & Feature Logic", "Game Engine", [4, 6, 8, 12, 16]),
    ("Dev:Respin", "[GE] - Sim - New Feature",                "Game Engine", [2, 4, 4, 8, 12]),
    ("Dev:Respin", "[Math] - RTP verification",               "Math",        [2, 4, 4, 6, 8]),
    ("Dev:Respin", "[Server] - Math Engine & Tickets Resp",   "Server",      [4, 6, 8, 12, 16]),
    ("Dev:Respin", "[Server] - Tickets & Pools",              "Server",      [4, 6, 8, 12, 16]),

    ("Dev:Feature", "[FE] - Feature Clone",                    "Dev",         [2, 4, 4, 8, 12]),
    ("Dev:Feature", "[FE] - Assets Replacement",               "Dev",         [4, 6, 8, 12, 16]),
    ("Dev:Feature", "[GE] - Sim - XML config & Feature Logic", "Game Engine", [4, 6, 8, 12, 16]),
    ("Dev:Feature", "[GE] - Sim - New Feature",                "Game Engine", [2, 4, 4, 8, 12]),
    ("Dev:Feature", "[Math] - RTP verification",               "Math",        [2, 4, 4, 6, 8]),
    ("Dev:Feature", "[Server] - Math Engine & Tickets Resp",   "Server",      [4, 6, 8, 12, 16]),
    ("Dev:Feature", "[Server] - Tickets & Pools",              "Server",      [4, 6, 8, 12, 16]),
]

# Which legend sections make up each category's 2024 estimate.
COMPOSITION = {
    "Port":    ["Creative", "Math", "Sound-Port", "Dev:Game Clone",
                "Dev:Base Game", "Dev:Review"],
    "Skin":    ["Creative", "Math", "Sound-Port", "Dev:Game Clone", "Dev:Review"],
    "Branded": ["Creative", "Math", "Sound-Port", "Dev:Game Clone", "Dev:Review"],
    "New":     ["Creative", "Math", "Sound-New", "Dev:Game Clone", "Dev:Base Game",
                "Dev:Review", "Dev:Respin", "Dev:Feature"],
}
COMPOSITION_NOTE = {
    "Port":    "Creative + Math + Sound(Port) + Dev[Game Clone + Base Game + Review]",
    "Skin":    "Creative + Math + Sound(Port) + Dev[Game Clone + Review]",
    "Branded": "No 2024 Branded variant exists -- Skin composition used as proxy",
    "New":     "Creative + Math + Sound(New) + Dev[Clone + Base + Review + Respin + Feature]",
}


# Departments the 2024 legend never priced -- derived, so it stays true if the
# LEGEND table is ever extended. This is where the hidden cost lives.
UNPRICED_2024 = [d for d in
                 ["Concept", "Creative", "Math", "Sound", "Game Engine", "Server",
                  "Dev", "Review", "QA", "Bugs", "Release"]
                 if d not in {row[2] for row in LEGEND}]


def legend_bands():
    """category -> dept -> [XS..XL] derived from LEGEND + COMPOSITION."""
    out = {}
    for cat, sections in COMPOSITION.items():
        d = defaultdict(lambda: [0] * 5)
        for section, _sub, dept, hrs in LEGEND:
            if section in sections:
                for i in range(5):
                    d[dept][i] += hrs[i]
        out[cat] = dict(d)
    return out


# ===========================================================================
#  JIRA
# ===========================================================================
def search(jql, fields, page_cap=60, tries=4):
    """
    POST /rest/api/3/search/jql -- the only search endpoint that works.

    A full walk is ~140 requests, and Jira will occasionally reset the
    connection or return 429/5xx part way through. Retrying with backoff
    matters here: without it one dropped connection throws away the whole
    two-minute run. Retries are per-page, so a resumed page does not
    re-fetch the pages already collected.
    """
    url = f"{JIRA_BASE}/rest/api/3/search/jql"
    out, token, pages = [], None, 0
    while True:
        body = {"jql": jql, "fields": fields, "maxResults": 100}
        if token:
            body["nextPageToken"] = token

        data = None
        for attempt in range(1, tries + 1):
            try:
                r = requests.post(url, json=body, auth=AUTH, headers=HEADERS,
                                  timeout=120)
            except requests.exceptions.RequestException as exc:
                if attempt == tries:
                    sys.exit(f"\nJira unreachable after {tries} tries on:\n"
                             f"  {jql}\n  {exc}")
                wait = 2 ** attempt
                print(f"    connection dropped, retrying in {wait}s "
                      f"({attempt}/{tries - 1})...", flush=True)
                time.sleep(wait)
                continue

            if r.status_code == 200:
                data = r.json()
                break
            # 429 = rate limited, 5xx = transient. Anything else is a real
            # error (bad JQL, auth) and retrying will not help.
            if r.status_code in (429, 500, 502, 503, 504) and attempt < tries:
                wait = int(r.headers.get("Retry-After", 2 ** attempt))
                print(f"    Jira {r.status_code}, retrying in {wait}s "
                      f"({attempt}/{tries - 1})...", flush=True)
                time.sleep(wait)
                continue
            sys.exit(f"\nJira {r.status_code} on:\n  {jql}\n  {r.text[:400]}")

        out.extend(data.get("issues", []))
        token = data.get("nextPageToken")
        pages += 1
        if data.get("isLast", True) or not token or pages >= page_cap:
            break
    return out


def flatten(issue):
    """Hours kept as raw SECONDS; converted once at output with round(s/3600,2)."""
    f = issue["fields"]
    fvs = [v["name"] for v in (f.get("fixVersions") or [])]
    dept, rule = classify(f["issuetype"]["name"], f.get("summary") or "")
    return {
        "key": issue["key"],
        "parent": (f.get("parent") or {}).get("key"),
        "type": f["issuetype"]["name"],
        "summary": f.get("summary") or "",
        "status": (f.get("status") or {}).get("name", ""),
        "fv": fvs,
        "elg": any(ELG_RE.match(v) for v in fvs),
        # by fix version OR by summary -- see PFH_TITLE_RE above
        "pfh": (any(PFH_RE.match(v) for v in fvs)
                or bool(PFH_TITLE_RE.search(f.get("summary") or ""))),
        "dept": dept,
        "rule": rule,
        "oe_s": f.get("timeoriginalestimate") or 0,
        "ts_s": f.get("timespent") or 0,
        "re_s": f.get("timeestimate") or 0,
    }


def h(seconds):
    return round(seconds / 3600, 2)


def chunks(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def collect():
    """Epic -> children -> subtasks. Chunked at 8 keys; the 100-row cap bites."""
    rows, epic_of = [], {}

    print("Stage 1: epics themselves...")
    for e in search(f"project = IG AND key in ({', '.join(GAMES)})", FIELDS):
        d = flatten(e)
        d["epic"], d["level"] = d["key"], 0
        rows.append(d)
    print(f"  {len(rows)} epics")

    print("Stage 2: epic children...")
    children = []
    for epic in GAMES:
        kids = search(f"project = IG AND parent = {epic}", FIELDS)
        print(f"  {epic} {GAMES[epic][0][:28]:30} {len(kids):4} children")
        for k in kids:
            d = flatten(k)
            d["epic"], d["level"] = epic, 1
            epic_of[d["key"]] = epic
            children.append(d)
    rows.extend(children)

    # Every child is a potential parent -- bugs carry subtasks too (33 of them).
    parents = [d["key"] for d in children]
    total_chunks = (len(parents) + 7) // 8
    print(f"Stage 3: subtasks under all {len(parents)} children...")
    n_sub = 0
    for i, grp in enumerate(chunks(parents, 8), 1):
        subs = search(f"project = IG AND parent in ({', '.join(grp)})", FIELDS)
        if len(subs) >= 100:
            print(f"  WARNING: chunk {i} returned {len(subs)} -- may be truncated")
        for s in subs:
            d = flatten(s)
            d["epic"], d["level"] = epic_of.get(d["parent"]), 2
            if d["epic"]:
                rows.append(d)
                n_sub += 1
        print(f"  chunk {i}/{total_chunks}: +{len(subs)}   ", end="\r")
    print(" " * 60, end="\r")
    print(f"  {n_sub} subtasks -- {len(rows)} tickets total\n")

    print("Stage 4: release overhead...")
    rel = []
    for epic, ver in RELEASE_EPICS.items():
        batch = search(f"project = IG AND (key = {epic} OR parent = {epic})", FIELDS)
        kids = [b["key"] for b in batch if b["key"] != epic]
        for grp in chunks(kids, 8):          # release subtasks, one level deeper
            batch += search(f"project = IG AND parent in ({', '.join(grp)})", FIELDS)
        for b in batch:
            d = flatten(b)
            d["release"] = ver
            rel.append(d)
        print(f"  {epic} {ver}: {len(batch)} tickets")

    # How many game epics shipped in each release (for per-game overhead).
    counts = {}
    for epic, ver in RELEASE_EPICS.items():
        q = (f'project = IG AND issuetype = Epic AND summary ~ "Gen2 Game" '
             f'AND fixVersion = "{ver}"')
        counts[ver] = len(search(q, ["summary"]))
    print()
    return rows, rel, counts


# ===========================================================================
#  AGGREGATION
# ===========================================================================
def build(rows):
    """(epic, dept, scope) -> [orig_s, spent_s, remaining_s]"""
    agg = defaultdict(lambda: [0, 0, 0])
    for d in rows:
        # PFH is carved out of ELG: a PFH-flagged ticket counts in FULL and in
        # PFH, never in the ELG build figure, even carrying both fix versions.
        for scope in ["FULL"] + (["ELG"] if d["elg"] and not d.get("pfh") else []):
            a = agg[(d["epic"], d["dept"], scope)]
            a[0] += d["oe_s"]
            a[1] += d["ts_s"]
            a[2] += d["re_s"]
    return agg


def release_overhead(rel, games=None):
    """
    Release-epic hours, shared out per game.

    Release work does not sit under a game epic -- it lives under the five
    RELEASE_EPICS -- so it cannot simply be summed per game. Each release's
    hours are divided evenly across the games in THIS MODEL that shipped in
    that release.

    Caveat: if a release also shipped games the model does not track, their
    share lands on the tracked ones and per-game overhead reads high. The
    `counts` map from collect() was meant to correct for exactly that, but it
    keys on the game epic carrying an ELG fixVersion and several do not
    (Flaming Skulls carries none), so it returns 0 for ELG 4.30 and would
    divide by zero. Model membership is the honest denominator we actually
    have, and the page states the assumption.

    -> (per_epic {epic: hours}, per_release {version: hours})
    """
    games = GAMES if games is None else games
    per_release = defaultdict(float)
    for d in rel:
        per_release[d.get("release")] += d["ts_s"] / 3600.0

    members = defaultdict(list)
    for epic, (_n, _c, ver) in games.items():
        members[ver].append(epic)

    per_epic = {}
    for ver, epics in members.items():
        share = per_release.get(ver, 0.0) / len(epics)
        for e in epics:
            per_epic[e] = round(share, 2)
    return per_epic, {k: round(v, 2) for k, v in per_release.items()}


# ===========================================================================
#  CACHE
# ===========================================================================
def save(rows, rel, counts, path=CACHE_FILE):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"rows": rows, "rel": rel, "counts": counts}, fh)
    print(f"Saved {path}  ({len(rows)} tickets)")


def load(path=CACHE_FILE):
    """-> (rows, rel, counts, agg). Tells you to refresh if the cache is absent."""
    if not os.path.exists(path):
        sys.exit(f"No {path} found. Run:  python elg_cost_data.py")
    with open(path, encoding="utf-8") as fh:
        d = json.load(fh)
    return d["rows"], d["rel"], d["counts"], build(d["rows"])


# ===========================================================================
#  EPIC PICKER -- managing games.json without editing Python
# ===========================================================================
def derive(issue):
    """-> (display name, ELG release, all fix versions) read off the epic."""
    f = issue["fields"]
    name = re.sub(r"^\s*Gen2 Game\s*:\s*", "", f.get("summary") or "").strip()
    vers = [v["name"] for v in (f.get("fixVersions") or [])]
    return name, next((v for v in vers if ELG_RE.match(v)), ""), vers


def cmd_find(term):
    """Search IG epics by summary so you can find the key to --add."""
    safe = term.replace('"', "")
    hits = search('project = IG AND issuetype = Epic AND summary ~ "%s" '
                  "ORDER BY created DESC" % safe,
                  ["summary", "fixVersions"], page_cap=3)
    if not hits:
        print('No IG epic matches "%s".' % term)
        return
    print('%d epic(s) matching "%s":\n' % (len(hits), term))
    for i in hits:
        name, rel, vers = derive(i)
        mark = "   [already in model]" if i["key"] in GAMES else ""
        print("  %-9s %-46s %-12s%s" % (i["key"], name[:44],
                                        rel or "(no ELG ver)", mark))
        if not rel and vers:
            print("            fix versions: %s" % ", ".join(vers))
    print("\nAdd one with:  python elg_cost_data.py --add <KEY> --cat <%s>"
          % "|".join(CATEGORIES))


def cmd_add(key, cat, name=None, rel=None):
    if cat not in CATEGORIES:
        sys.exit("--cat must be one of: %s" % ", ".join(CATEGORIES))
    cfg = load_config()
    if key in cfg["games"]:
        sys.exit('%s is already in the model as "%s".' % (key, cfg["games"][key]["name"]))

    got = search("project = IG AND key = %s" % key,
                 ["summary", "fixVersions", "issuetype"])
    if not got:
        sys.exit("%s not found in Jira, or the token cannot see it." % key)
    issue = got[0]
    kind = issue["fields"]["issuetype"]["name"]
    if kind != "Epic":
        sys.exit("%s is a %s, not an Epic. Only game epics belong here." % (key, kind))

    dname, drel, vers = derive(issue)
    name, rel = name or dname, rel or drel
    if not rel:
        sys.exit('%s carries no ELG fix version (has: %s).\n'
                 'Pass it explicitly, e.g. --rel "ELG 4.80".'
                 % (key, ", ".join(vers) or "none"))

    cfg["games"][key] = {"name": name, "cat": cat, "rel": rel}
    save_config(cfg)
    print("Added %s  %s  [%s]  %s" % (key, name, cat, rel))
    if rel not in cfg.get("release_epics", {}).values():
        print('  NOTE: no release epic in games.json maps to "%s", so this game '
              "gets 0h\n        release overhead. Add one under "
              '"release_epics" if that release has one.' % rel)
    print("\nNow re-run:  python elg_cost_data.py     (pulls the new epic)")
    print("       then:  python elg_cost_page.py")


def cmd_remove(key):
    cfg = load_config()
    if key not in cfg["games"]:
        sys.exit("%s is not in the model." % key)
    gone = cfg["games"].pop(key)
    save_config(cfg)
    print("Removed %s (%s). Re-run the pull and rebuild." % (key, gone["name"]))


def cmd_list():
    cfg = load_config()
    print("%d games in the model (games.json, rate $%s/h):\n"
          % (len(cfg["games"]), cfg.get("hourly_rate", HOURLY_RATE)))
    for k, v in sorted(cfg["games"].items(),
                       key=lambda kv: (kv[1]["cat"], kv[1]["name"])):
        print("  %-9s %-42s %-8s %s" % (k, v["name"][:40], v["cat"], v["rel"]))
    print("\n%d release epics:" % len(cfg.get("release_epics", {})))
    for k, v in sorted(cfg.get("release_epics", {}).items(), key=lambda kv: kv[1]):
        print("  %-9s %s" % (k, v))


def pull():
    """The default action: walk Jira and refresh the cache."""
    rows, rel, counts = collect()
    save(rows, rel, counts)
    agg = build(rows)
    tot = sum(agg.get((e, d, "FULL"), [0, 0, 0])[1] for e in GAMES for d in DEPT_ORDER)
    _per_epic, per_rel = release_overhead(rel)
    print("  %s h across %d games" % (format(h(tot), ",.2f"), len(GAMES)))
    print("  %s h release overhead across %d releases\n"
          % (format(sum(per_rel.values()), ",.2f"), len(per_rel)))
    print("Now run:  python elg_cost_spreadsheet.py   and/or   python elg_cost_page.py")


def main():
    ap = argparse.ArgumentParser(
        description="Pull the ELG cost model from Jira, or manage the game list.",
        epilog="With no flags: walks Jira and rewrites the cache (~2 min).")
    ap.add_argument("--find", metavar="TEXT", help="search IG epics by summary")
    ap.add_argument("--add", metavar="KEY", help="add a game epic to games.json")
    ap.add_argument("--cat", metavar="CATEGORY",
                    help="category for --add: %s" % ", ".join(CATEGORIES))
    ap.add_argument("--name", metavar="TEXT",
                    help="override the display name derived from the summary")
    ap.add_argument("--rel", metavar="VERSION",
                    help='override the ELG release, e.g. "ELG 4.80"')
    ap.add_argument("--remove", metavar="KEY", help="drop a game from games.json")
    ap.add_argument("--list", action="store_true", help="show the current model")
    a = ap.parse_args()

    # offline commands first -- these never need a token
    if a.list:
        return cmd_list()
    if a.remove:
        return cmd_remove(a.remove)

    if not API_TOKEN:
        sys.exit(r"JIRA_API_TOKEN not set (checked process env and HKCU\Environment).")
    if a.find:
        return cmd_find(a.find)
    if a.add:
        if not a.cat:
            sys.exit("--add needs --cat (%s) -- category cannot be read off "
                     "the epic." % ", ".join(CATEGORIES))
        return cmd_add(a.add, a.cat, a.name, a.rel)
    pull()


if __name__ == "__main__":
    main()
