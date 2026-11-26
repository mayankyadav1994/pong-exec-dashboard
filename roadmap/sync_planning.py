#!/usr/bin/env python3
"""
sync_planning.py — daily import of the PMO plan for the Planning sub-pages.

Downloads the PMO sheet (SharePoint) through Microsoft Graph, parses the
planning tab and rewrites config/roadmap-planning.json, which roadmap.py turns
into roadmap-<space>-planning.html. The sheet stays the source of truth for
initiatives, months, status, weeks and developers; config/roadmap-planning-map.json
says which space each product block belongs to and which Jira epics each
initiative maps to (those can't come from the sheet).

Sheet layout it understands (one block per product line, e.g. PoP / PFH X / PFH):
    A: product label            B..: month headers (Dec, Jan, March, ?, or real dates)
    below each month column:    initiative name ("DAC [Dynamic Account creation]")
                                scope lines, a status ("To Do"), a length ("2 Weeks"),
                                "Developers" followed by one developer per row
    A, inside the block:        lane notes ("1 month: Prototype" ...)

Auth (app-only, same Azure app as the PRF checker; needs Graph Sites.Read.All):
    SP_TENANT_ID, SP_CLIENT_ID, SP_CLIENT_SECRET
If they are missing, or the download/parse fails, the current plan is kept and
the script exits 0 so the roadmap still builds.

Usage:
    python roadmap/sync_planning.py                # Graph download
    python roadmap/sync_planning.py --file x.xlsx  # parse a local copy
    python roadmap/sync_planning.py --dry-run      # print, don't write
"""
import argparse
import io
import json
import os
import re
import sys
from datetime import date, datetime
from pathlib import Path

from dotenv import load_dotenv

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
load_dotenv(ROOT / ".env")
MAP_FILE = ROOT / "config" / "roadmap-planning-map.json"
OUT_FILE = ROOT / "config" / "roadmap-planning.json"

MONTHS = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}
STATUS_RE = re.compile(r"^(to ?do|in ?progress|done|planned|blocked|on ?hold|parked|complete[d]?)$", re.I)
LENGTH_RE = re.compile(r"\b\d+(\.\d+)?\s*(-\s*\d+\s*)?(weeks?|wks?|days?|months?)\b", re.I)


def log(msg):
    print(f"[sync_planning] {msg}", file=sys.stderr, flush=True)


# ── download ────────────────────────────────────────────────────────────────
def graph_download(src):
    import requests
    tenant, cid, secret = (os.getenv(k) for k in ("SP_TENANT_ID", "SP_CLIENT_ID", "SP_CLIENT_SECRET"))
    if not (tenant and cid and secret):
        raise RuntimeError("SP_TENANT_ID / SP_CLIENT_ID / SP_CLIENT_SECRET not set")
    tok = requests.post(
        f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token",
        data={"client_id": cid, "client_secret": secret, "grant_type": "client_credentials",
              "scope": "https://graph.microsoft.com/.default"}, timeout=30)
    tok.raise_for_status()
    h = {"Authorization": f"Bearer {tok.json()['access_token']}"}
    base = f"https://graph.microsoft.com/v1.0/drives/{src['drive_id']}/items/{src['item_id']}"
    meta = requests.get(base, params={"$select": "name,lastModifiedDateTime,webUrl"}, headers=h, timeout=30)
    meta.raise_for_status()
    body = requests.get(base + "/content", headers=h, timeout=60)
    body.raise_for_status()
    return body.content, meta.json()


# ── parse ───────────────────────────────────────────────────────────────────
def cell_text(v):
    if v is None:
        return ""
    if isinstance(v, (datetime, date)):
        return v
    return re.sub(r"\s+", " ", str(v)).strip()


def month_of(v):
    """(year|None, month) for a header cell, '?' for an unknown month, None if not a month."""
    if isinstance(v, (datetime, date)):
        return (v.year, v.month)
    s = (v or "").strip().lower()
    if s == "?":
        return "?"
    if s[:3] in MONTHS and re.fullmatch(r"[a-z]+\.?", s):
        return (None, MONTHS[s[:3]])
    return None


def resolve_years(months, today):
    """Give bare month names a year: the first one lands within ±6 months of
    today, the rest run left-to-right (Dec, Jan -> next year)."""
    first = date(today.year, today.month, 1)
    lo = (first.year * 12 + first.month - 1) - 6
    out, prev = [], None
    for mo in months:
        if mo in (None, "?"):
            out.append(mo)
            continue
        y, m = mo
        if y is None:
            if prev is None:
                y = next(y for y in (today.year - 1, today.year, today.year + 1)
                         if lo <= y * 12 + m - 1 < lo + 12)
            else:
                py, pm = prev
                y = py + (1 if m < pm else 0)
        prev = (y, m)
        out.append((y, m))
    return out


def parse_sheet(ws, today):
    grid = [[cell_text(c) for c in row] for row in ws.iter_rows(values_only=True)]
    width = max((len(r) for r in grid), default=0)
    grid = [r + [""] * (width - len(r)) for r in grid]

    def has_months(r):
        cells = [c for c in grid[r][1:] if c != ""]
        return bool(cells) and all(month_of(c) is not None for c in cells)

    # Block starts: label in A, months on the same row or the next (with empty A).
    starts = []
    for r, row in enumerate(grid):
        if not row[0] or isinstance(row[0], (datetime, date)):
            continue
        if has_months(r):
            starts.append((r, r))
        elif not any(c != "" for c in row[1:]) and r + 1 < len(grid) \
                and not grid[r + 1][0] and has_months(r + 1):
            starts.append((r, r + 1))

    lanes = []
    for bi, (lr, hr) in enumerate(starts):
        end = starts[bi + 1][0] if bi + 1 < len(starts) else len(grid)
        label = grid[lr][0]
        months = resolve_years([month_of(grid[hr][c]) if grid[hr][c] != "" else None
                                for c in range(1, width)], today)
        notes = [grid[r][0] for r in range(hr + 1, end) if grid[r][0] and not isinstance(grid[r][0], date)]
        inits = []
        for ci in range(1, width):
            vals = [grid[r][ci] for r in range(hr + 1, end) if grid[r][ci] != ""]
            name = status = length = None
            items, devs, in_devs = [], [], False
            for v in vals:
                v = v.isoformat() if isinstance(v, (datetime, date)) else v
                if v.lower() == "developers":
                    in_devs = True
                elif in_devs:
                    devs.append(v)
                elif STATUS_RE.match(v):
                    status = v.title().replace("Todo", "To Do")
                elif LENGTH_RE.search(v) and name is not None:
                    length = v.lower()
                elif name is None:
                    name = v
                else:
                    items.append(v)
            if not name:
                continue  # a status/devs column with no initiative named
            desc = None
            m = re.match(r"^(.*?)\s*\[(.+)\]\s*$", name)
            if m:
                name, desc = m.group(1).strip(), m.group(2).strip()
            mo = months[ci - 1]
            init = {"name": name}
            if desc:
                init["desc"] = desc
            init["month"] = f"{mo[0]}-{mo[1]:02d}" if isinstance(mo, tuple) else None
            if length:
                init["weeks"] = length
            if status:
                init["status"] = status
            if items:
                init["items"] = items
            if devs:
                init["developers"] = devs
            inits.append(init)
        lanes.append({"label": label, "note": " · ".join(notes) or None, "initiatives": inits})
    return lanes


# ── merge with the mapping and write ────────────────────────────────────────
def slug(s):
    return re.sub(r"[^a-z0-9]+", "", s.lower())


def build_plan(parsed, mp, source):
    lane_map = {slug(k): v for k, v in mp.get("lanes", {}).items()}
    jira = {k.lower(): v for k, v in mp.get("jira", {}).items()}
    out = []
    for ln in parsed:
        lm = lane_map.get(slug(ln["label"]))
        if not lm:
            log(f"WARNING: product block '{ln['label']}' has no space in {MAP_FILE.name} — skipped")
            continue
        lane = {"id": lm["id"], "label": ln["label"], "space": lm["space"]}
        if ln["note"]:
            lane["note"] = ln["note"]
        for ini in ln["initiatives"]:
            keys = jira.get(f"{lm['id']}/{ini['name']}".lower())
            if keys:
                ini["jira"] = keys
            elif keys is None:
                log(f"note: {lm['id']}/{ini['name']} has no Jira mapping yet")
        lane["initiatives"] = ln["initiatives"]
        out.append(lane)
    return {
        "_comment": "GENERATED daily by roadmap/sync_planning.py from the PMO sheet below — edit the sheet, "
                    "not this file. Space per product block and Jira epics per initiative live in "
                    "config/roadmap-planning-map.json.",
        "source": source,
        "lanes": out,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", help="parse a local .xlsx instead of downloading")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--today", help="YYYY-MM-DD (testing)")
    args = ap.parse_args()
    today = date.fromisoformat(args.today) if args.today else date.today()
    mp = json.loads(MAP_FILE.read_text(encoding="utf-8"))
    src = mp["source"]

    try:
        from openpyxl import load_workbook
        if args.file:
            data, meta = Path(args.file).read_bytes(), {}
        else:
            data, meta = graph_download(src)
        wb = load_workbook(io.BytesIO(data), data_only=True, read_only=True)
        ws = wb[src["sheet"]] if src.get("sheet") in wb.sheetnames else None
        if ws is None:
            raise RuntimeError(f"sheet '{src.get('sheet')}' not found (have {wb.sheetnames})")
        parsed = parse_sheet(ws, today)
        if not parsed or not any(l["initiatives"] for l in parsed):
            raise RuntimeError("no product blocks / initiatives recognised — layout changed?")
    except Exception as e:  # keep the last good plan; never break the roadmap build
        log(f"kept the current plan — could not import the sheet: {e}")
        return 0

    source = {"name": meta.get("name") or src["name"], "sheet": src["sheet"],
              "url": meta.get("webUrl") or src.get("url"),
              "modified": (meta.get("lastModifiedDateTime") or "")[:10] or None,
              "imported": today.isoformat()}
    plan = build_plan(parsed, mp, source)
    n = sum(len(l["initiatives"]) for l in plan["lanes"])
    if args.dry_run:
        print(json.dumps(plan, indent=2, ensure_ascii=False))
        return 0
    old = json.loads(OUT_FILE.read_text(encoding="utf-8")) if OUT_FILE.exists() else {}
    if old.get("lanes") == plan["lanes"]:
        log(f"no change in the plan ({n} initiatives)")
        return 0
    OUT_FILE.write_text(json.dumps(plan, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    log(f"updated {OUT_FILE.name}: {len(plan['lanes'])} product lines, {n} initiatives")
    return 0


if __name__ == "__main__":
    sys.exit(main())
