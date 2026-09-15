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
#  RUN CONFIG
# ===========================================================================
# epic -> (display name, category, ELG release)
GAMES = {
    "IG-1506": ("Flaming Skulls",               "Port",    "ELG 4.30"),
    "IG-1509": ("Northern Buffalo Fortune [V]", "Port",    "ELG 4.40"),
    "IG-1508": ("Lost Totem",                   "Port",    "ELG 4.50"),
    "IG-3234": ("Viking's Voyage of Fortune",   "Port",    "ELG 4.70"),
    "IG-3304": ("Lantern's Rising [V]",         "Port",    "ELG 4.70"),
    "IG-4555": ("Luck Party Blue Bird Bonanza", "Branded", "ELG 4.50"),
    "IG-3657": ("American Wins",                "Skin",    "ELG 4.20"),
    "IG-3689": ("Fortune Diamond 10X",          "Skin",    "ELG 4.20"),
}

# Release-level epics -- overhead, not attributable to any one game.
RELEASE_EPICS = {
    "IG-5427": "ELG 4.20",
    "IG-5442": "ELG 4.30",
    "IG-5445": "ELG 4.40",
    "IG-6153": "ELG 4.50",
    "IG-6925": "ELG 4.70",
}
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
# Some build work on an ELG game is tagged to a PFH release instead. The two
# are not mutually exclusive -- a handful of tickets carry both -- so these
# scopes overlap rather than partition, and neither covers every ticket.
PFH_RE = re.compile(r"^PFH\b", re.I)

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
    ("Bugs",        "type", {"Bug", "Live Issue", "Enhancement"}),
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
        for scope in ["FULL"] + (["ELG"] if d["elg"] else []):
            a = agg[(d["epic"], d["dept"], scope)]
            a[0] += d["oe_s"]
            a[1] += d["ts_s"]
            a[2] += d["re_s"]
    return agg


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


def main():
    if not API_TOKEN:
        sys.exit(r"JIRA_API_TOKEN not set (checked process env and HKCU\Environment).")
    rows, rel, counts = collect()
    save(rows, rel, counts)
    agg = build(rows)
    tot = sum(agg.get((e, d, "FULL"), [0, 0, 0])[1] for e in GAMES for d in DEPT_ORDER)
    print(f"  {h(tot):,.2f}h across {len(GAMES)} games\n")
    print("Now run:  python elg_cost_spreadsheet.py   and/or   python elg_cost_page.py")


if __name__ == "__main__":
    main()
