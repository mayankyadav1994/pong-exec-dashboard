"""
build_dashboard.py
Pulls live data from Jira (health/progress) and Confluence (scope text),
then generates index.html for GitHub Pages hosting.
Standalone — no Confluence page is written, only read.
"""

import os
import re
import requests
from datetime import date
from dotenv import load_dotenv
from bs4 import BeautifulSoup

load_dotenv()

# ── Credentials ───────────────────────────────────────────────────────────────
JIRA_EMAIL           = os.environ["JIRA_EMAIL"]
JIRA_API_TOKEN       = os.environ["JIRA_API_TOKEN"]
CONFLUENCE_API_TOKEN = os.environ["CONFLUENCE_API_TOKEN"]

JIRA_BASE      = "https://api.atlassian.com/ex/jira/85005dc7-cde3-4a2c-8e65-2d746db228ed/rest/api/3"
CONFLUENCE_BASE = "https://ponggamestudios.atlassian.net/wiki/rest/api"

JIRA_AUTH       = (JIRA_EMAIL, JIRA_API_TOKEN)
CONFLUENCE_AUTH = (JIRA_EMAIL, CONFLUENCE_API_TOKEN)
JSON_HEADERS    = {"Accept": "application/json"}

# ── Project / section config ──────────────────────────────────────────────────
PROJECTS = ["V2", "IG", "CS", "PFH"]

SECTION_MAP = {"V2": "v2", "IG": "ig", "CS": "cs", "PFH": "pfh"}

SECTION_META = {
    "v2":  {"label": "🔵 V2 — Vendor 2",              "card": "card-v2",  "icon": "🎮", "title": "V2 Releases"},
    "ig":  {"label": "🟢 iGaming — ELG & PFH2 Games", "card": "card-ig",  "icon": "🎰", "title": "iGaming — ELG & PFH2 Games"},
    "pfh": {"label": "🟡 PFH — Services",              "card": "card-pfh", "icon": "⚙️", "title": "PFH Services"},
    "cs":  {"label": "🩷 CS — Cloud Services",         "card": "card-cs",  "icon": "☁️", "title": "Cloud Services"},
}

EXCLUDED_FV  = {"Trello", "PFH - Side Projects", "FC Backlog"}
VERSION_RE   = re.compile(r'^\d+\.\d+|^\d{4}$')
QA_STATUSES  = {"In QA", "In QA R1", "In QA R2", "Ready For QA", "QA In Progress"}

TODAY = date.today()


# ── Jira helpers ──────────────────────────────────────────────────────────────

def jira_get(path, params=None):
    r = requests.get(f"{JIRA_BASE}{path}", auth=JIRA_AUTH,
                     headers=JSON_HEADERS, params=params)
    r.raise_for_status()
    return r.json()


def jira_post(path, body):
    r = requests.post(f"{JIRA_BASE}{path}", auth=JIRA_AUTH,
                      headers={**JSON_HEADERS, "Content-Type": "application/json"},
                      json=body)
    r.raise_for_status()
    return r.json()


def jira_jql(jql, fields, max_results=100):
    """Paginate all JQL results using POST /search/jql (GET /search is 410 Gone)."""
    issues, token = [], None
    while True:
        body = {"jql": jql, "fields": fields, "maxResults": max_results}
        if token:
            body["nextPageToken"] = token
        data = jira_post("/search/jql", body)
        issues.extend(data.get("issues", []))
        if data.get("isLast", True):
            break
        token = data.get("nextPageToken")
        if not token:
            break
    return issues


def is_valid_fv(name):
    return name not in EXCLUDED_FV and bool(VERSION_RE.match(name))


# ── Confluence helpers ────────────────────────────────────────────────────────

def confluence_search(cql, limit=5):
    """Search Confluence pages via CQL, return results with body.view."""
    r = requests.get(
        f"{CONFLUENCE_BASE}/content/search",
        auth=CONFLUENCE_AUTH,
        headers=JSON_HEADERS,
        params={"cql": cql, "expand": "body.view", "limit": limit},
    )
    if r.status_code != 200:
        return []
    return r.json().get("results", [])


def extract_text(html_body):
    """Strip HTML tags and collapse whitespace to plain text."""
    soup = BeautifulSoup(html_body, "html.parser")
    return re.sub(r'\s+', ' ', soup.get_text(separator=' ')).strip()


def extract_scope_for_version(version_name, full_text):
    """
    Find the paragraph in a Confluence page that mentions the version name
    and return a clean 250-char scope snippet.
    """
    # Try to find the section around the version name
    idx = full_text.find(version_name)
    if idx == -1:
        return ""
    # Grab up to 300 chars after the name, trim at sentence boundary if possible
    snippet = full_text[idx + len(version_name):idx + len(version_name) + 300].strip()
    # Clean leading punctuation/separators
    snippet = re.sub(r'^[\s:–—\-]+', '', snippet)
    # Trim to last complete word under 250 chars
    if len(snippet) > 250:
        snippet = snippet[:250].rsplit(' ', 1)[0] + '…'
    return snippet


def fetch_confluence_scope(version_names):
    """
    Search Confluence for each fix version name.
    Returns dict: { "V2 HHR 2.00": "Server restructure, 9 new games...", ... }
    Falls back to empty string if nothing found — Jira description used instead.
    """
    scope_map = {}
    for name in version_names:
        try:
            # Search for pages mentioning this exact version name
            results = confluence_search(
                cql=f'type = page AND text ~ "{name}"',
                limit=3,
            )
            if not results:
                continue

            # Try each result, use the first that has a meaningful snippet
            for page in results:
                body_html = page.get("body", {}).get("view", {}).get("value", "")
                if not body_html:
                    continue
                full_text = extract_text(body_html)
                snippet = extract_scope_for_version(name, full_text)
                if snippet and len(snippet) > 20:
                    scope_map[name] = snippet
                    break

        except Exception as e:
            print(f"  ⚠ Confluence scope fetch failed for '{name}': {e}")

    return scope_map


# ── Step 1: Fix versions ──────────────────────────────────────────────────────

def fetch_fix_versions():
    versions = []
    for proj in PROJECTS:
        data = jira_get(f"/project/{proj}/versions")
        unarchived = [v for v in data if not v.get("archived") and not v.get("released")]
        print(f"  [{proj}] total={len(data)} unarchived/unreleased={len(unarchived)} names={[v.get('name','') for v in unarchived]}")
        for v in unarchived:
            name = v.get("name", "")
            if not is_valid_fv(name):
                print(f"  [{proj}] SKIPPED by filter: '{name}'")
                continue
            versions.append({
                "name":        name,
                "project":     proj,
                "section":     SECTION_MAP[proj],
                "releaseDate": v.get("releaseDate"),
                "jira_desc":   v.get("description", ""),
            })
    return versions


# ── Step 2: PRF override ──────────────────────────────────────────────────────

def fetch_prf_overrides():
    """
    A closed Release issue with a resolutiondate = PRF sent = truly shipped.
    Returns { "V2 C2 5.00": "2026-04-23", ... }
    """
    issues = jira_jql(
        jql='project in (V2, IG, CS, PFH) AND issuetype = "Release" '
            'AND status = "Closed" AND resolution is not EMPTY',
        fields=["summary", "resolutiondate", "fixVersions"],
    )
    overrides = {}
    for issue in issues:
        rd = issue["fields"].get("resolutiondate", "")[:10]
        if not rd:
            continue
        for fv in issue["fields"].get("fixVersions", []):
            overrides[fv["name"]] = rd
    return overrides


# ── Step 3: Issue stats per version ──────────────────────────────────────────

def fetch_version_stats(fv_name, project):
    issues = jira_jql(
        jql=f'project = {project} AND fixVersion = "{fv_name}"',
        fields=["status", "priority"],
    )
    done = blockers = qa_count = dev_count = 0
    for issue in issues:
        fields     = issue["fields"]
        status_cat = fields["status"]["statusCategory"]["key"]
        status_nm  = fields["status"]["name"]
        priority   = (fields.get("priority") or {}).get("name", "")
        if status_cat == "done":
            done += 1
        elif status_nm in QA_STATUSES:
            qa_count += 1
        else:
            dev_count += 1
        if priority == "Blocker" and status_cat != "done":
            blockers += 1

    total = len(issues)
    pct   = int(done / total * 100) if total else 0
    phase = "qa" if qa_count >= dev_count else "dev"
    return {"done": done, "total": total, "pct": pct, "blockers": blockers, "phase": phase}


# ── Step 4: Health classification ────────────────────────────────────────────

def classify_health(stats, release_date_str):
    if stats["blockers"] > 0:
        return "red"
    if release_date_str:
        try:
            if date.fromisoformat(release_date_str) < TODAY:
                return "red"
        except ValueError:
            pass
    return "grn" if stats["pct"] >= 90 else "yel"


def overdue_days(release_date_str):
    if not release_date_str:
        return 0
    try:
        return max(0, (TODAY - date.fromisoformat(release_date_str)).days)
    except ValueError:
        return 0


def days_until(release_date_str):
    if not release_date_str:
        return None
    try:
        return (date.fromisoformat(release_date_str) - TODAY).days
    except ValueError:
        return None


# ── Step 5: Build release data ────────────────────────────────────────────────

def build_releases():
    print("  Fetching fix versions from Jira...")
    versions     = fetch_fix_versions()
    print(f"  Found {len(versions)} valid fix versions")

    print("  Fetching PRF overrides...")
    prf_overrides = fetch_prf_overrides()
    print(f"  Found {len(prf_overrides)} shipped via PRF")

    # Separate active from shipped first (to avoid Confluence calls on shipped)
    active_versions  = [v for v in versions if v["name"] not in prf_overrides]
    shipped_versions = [v for v in versions if v["name"] in prf_overrides]

    # Fetch Confluence scope for active versions only
    print(f"  Fetching Confluence scope for {len(active_versions)} active releases...")
    active_names  = [v["name"] for v in active_versions]
    scope_map     = fetch_confluence_scope(active_names)
    print(f"  Got Confluence scope for {len(scope_map)}/{len(active_versions)} releases")

    active  = []
    shipped = []

    for v in shipped_versions:
        shipped.append({
            "name":         v["name"],
            "section":      v["section"],
            "shipped_date": prf_overrides[v["name"]],
            "description":  v.get("jira_desc", ""),
        })

    for v in active_versions:
        name         = v["name"]
        stats        = fetch_version_stats(name, v["project"])
        release_date = v.get("releaseDate")
        health       = classify_health(stats, release_date)
        od           = overdue_days(release_date)
        du           = days_until(release_date)

        # Scope priority: Confluence text > Jira fix version description > fallback
        scope = (
            scope_map.get(name)
            or v.get("jira_desc")
            or "Scope being defined."
        )

        active.append({
            "name":         name,
            "section":      v["section"],
            "description":  scope,
            "health":       health,
            "phase":        stats["phase"],
            "done":         stats["done"],
            "total":        stats["total"],
            "pct":          stats["pct"],
            "blockers":     stats["blockers"],
            "release_date": release_date,
            "overdue_days": od,
            "days_until":   du,
        })

    # Sort: by section order, then red → yellow → green within each section
    section_order = ["v2", "ig", "pfh", "cs"]
    health_order  = {"red": 0, "yel": 1, "grn": 2}
    active.sort(key=lambda r: (
        section_order.index(r["section"]),
        health_order.get(r["health"], 9),
    ))
    shipped.sort(key=lambda r: r["shipped_date"], reverse=True)

    return active, shipped


# ── Step 6: KPI counts ────────────────────────────────────────────────────────

def compute_kpis(active, shipped):
    month_str = TODAY.strftime("%b")
    return {
        "red":     sum(1 for r in active if r["health"] == "red"),
        "yel":     sum(1 for r in active if r["health"] == "yel"),
        "qa":      sum(1 for r in active if r["phase"] == "qa"),
        "soon":    sum(1 for r in active
                       if r.get("days_until") is not None and 0 <= r["days_until"] <= 7),
        "shipped": sum(1 for r in shipped
                       if r["shipped_date"].startswith(TODAY.strftime("%Y-%m"))),
        "month":   month_str,
    }


# ── Step 7: HTML rendering ────────────────────────────────────────────────────

def fmt_date(d):
    """Format a date object to 'Apr 24' (cross-platform)."""
    try:
        return d.strftime("%b %-d")   # Linux/Mac
    except ValueError:
        return d.strftime("%b %#d")   # Windows


def tag_date(release_date, du):
    if not release_date:
        return '<span class="tag t-tbd">TBD</span>'
    try:
        rd  = date.fromisoformat(release_date)
        lbl = fmt_date(rd)
    except ValueError:
        return '<span class="tag t-tbd">TBD</span>'

    if du is not None and 0 <= du <= 7:
        suffix = f"{du}d" if du > 0 else "Today"
        return f'<span class="tag t-imm">🚀 {lbl} — {suffix}</span>'
    if rd.month == TODAY.month:
        return f'<span class="tag t-apr">🚀 {lbl}</span>'
    return f'<span class="tag t-may">{lbl}</span>'


def prog_class(phase, pct):
    if pct == 0:
        return "pf-zero"
    return "pf-qa" if phase == "qa" else "pf-dev"


def render_active_row(r):
    health_label = {"red": "Red Flag", "yel": "At Risk", "grn": "On Track"}[r["health"]]
    phase_label  = "In QA" if r["phase"] == "qa" else "In Dev"
    phase_cls    = "ph-qa" if r["phase"] == "qa" else "ph-dev"
    pct          = r["pct"]
    pc           = prog_class(r["phase"], pct)

    tags = []
    if r["overdue_days"] > 0:
        tags.append(f'<span class="tag t-ovr">⚠️ {r["overdue_days"]}d overdue</span>')
    else:
        tags.append(tag_date(r["release_date"], r.get("days_until")))
    if r["blockers"] > 0:
        word = "blocker" if r["blockers"] == 1 else "blockers"
        tags.append(f'<span class="tag t-blk">🔴 {r["blockers"]} {word}</span>')

    scope = r.get("description") or "Scope being defined."

    return f"""
    <div class="rel-row">
      <div><div class="rel-name">{r['name']}</div></div>
      <div class="hc {r['health']}"><span class="hc-dot"></span>{health_label}</div>
      <span class="ph {phase_cls}">{phase_label}</span>
      <div class="prog-col">
        <div class="prog-bg"><div class="prog-fill {pc}" style="width:{max(pct,1)}%"></div></div>
        <div class="prog-lbl">{pct}% · {r['done']}/{r['total']}</div>
      </div>
      <div class="dc">{''.join(tags)}<div class="scope-text">{scope}</div></div>
    </div>"""


def render_shipped_row(r):
    try:
        lbl = fmt_date(date.fromisoformat(r["shipped_date"]))
    except Exception:
        lbl = r["shipped_date"]
    scope = r.get("description", "")
    return f"""
    <div class="rel-row">
      <div><div class="rel-name">{r['name']}</div></div>
      <div class="hc grn"><span class="hc-dot"></span>Shipped</div>
      <span class="ph ph-ship">Shipped</span>
      <div class="dc"><span class="tag t-ship">{lbl}</span></div>
      <div class="scope-text">{scope}</div>
    </div>"""


def generate_html(active, shipped, kpis):
    run_date = fmt_date(TODAY) + f", {TODAY.year}"

    sections_html = ""
    for sec_key in ["v2", "ig", "pfh", "cs"]:
        meta = SECTION_META[sec_key]
        rows = [r for r in active if r["section"] == sec_key]
        if not rows:
            continue
        row_html = "".join(render_active_row(r) for r in rows)
        sections_html += f"""
  <div class="sec-label sec-{sec_key}">{meta['label']}</div>
  <div class="card {meta['card']}">
    <div class="card-head">
      <span style="font-size:16px">{meta['icon']}</span>
      <span class="card-head-title">{meta['title']}</span>
      <span class="card-head-count">{len(rows)} active</span>
    </div>
    <div class="col-head">
      <span>Release</span><span>Health</span><span>Phase</span>
      <span>Progress</span><span>Scope &amp; Details</span>
    </div>
    {row_html}
  </div>"""

    month_label  = TODAY.strftime("%B %Y")
    shipped_rows = "".join(render_shipped_row(r) for r in shipped)
    if shipped:
        sections_html += f"""
  <div class="sec-label sec-done">✅ Shipped — {month_label}</div>
  <div class="card card-done">
    <div class="card-head">
      <span style="font-size:16px">🎉</span>
      <span class="card-head-title">Shipped this month</span>
      <span class="card-head-count">{kpis['shipped']} releases</span>
    </div>
    <div class="col-head">
      <span>Release</span><span>Health</span><span>Phase</span>
      <span>Date</span><span>Scope</span>
    </div>
    {shipped_rows}
  </div>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Pong Game Studios — Release Dashboard</title>
<style>
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #F9FAFB; padding: 1.5rem; }}
.db {{ max-width: 1100px; margin: 0 auto; }}
.db-header {{ background: linear-gradient(135deg, #4F46E5 0%, #7C3AED 50%, #DB2777 100%); border-radius: 14px; padding: 20px 24px 18px; margin-bottom: 16px; }}
.db-header h1 {{ font-size: 20px; font-weight: 600; color: #fff; margin-bottom: 4px; }}
.db-header p {{ font-size: 12px; color: rgba(255,255,255,0.78); }}
.kpi-grid {{ display: grid; grid-template-columns: repeat(5, 1fr); gap: 8px; margin-bottom: 16px; }}
.kpi {{ border-radius: 12px; padding: 14px 8px 12px; text-align: center; }}
.kpi-num {{ font-size: 30px; font-weight: 600; line-height: 1; }}
.kpi-lbl {{ font-size: 10px; font-weight: 600; text-transform: uppercase; letter-spacing: .05em; margin-top: 5px; }}
.kpi-r {{ background:#FEE2E2; }} .kpi-r .kpi-num {{ color:#B91C1C; }} .kpi-r .kpi-lbl {{ color:#991B1B; }}
.kpi-y {{ background:#FEF3C7; }} .kpi-y .kpi-num {{ color:#B45309; }} .kpi-y .kpi-lbl {{ color:#92400E; }}
.kpi-g {{ background:#D1FAE5; }} .kpi-g .kpi-num {{ color:#065F46; }} .kpi-g .kpi-lbl {{ color:#064E3B; }}
.kpi-b {{ background:#DBEAFE; }} .kpi-b .kpi-num {{ color:#1D4ED8; }} .kpi-b .kpi-lbl {{ color:#1E40AF; }}
.kpi-p {{ background:#EDE9FE; }} .kpi-p .kpi-num {{ color:#6D28D9; }} .kpi-p .kpi-lbl {{ color:#5B21B6; }}
.sec-label {{ font-size: 10px; font-weight: 700; text-transform: uppercase; letter-spacing: .09em; margin: 16px 0 7px 2px; }}
.sec-v2 {{ color:#1D4ED8; }} .sec-ig {{ color:#065F46; }} .sec-pfh {{ color:#B45309; }} .sec-cs {{ color:#9D174D; }} .sec-done {{ color:#064E3B; }}
.card {{ border-radius: 14px; overflow: hidden; margin-bottom: 12px; box-shadow: 0 1px 4px rgba(0,0,0,0.08); }}
.card-v2   {{ background:#EFF6FF; border:1.5px solid #BFDBFE; }}
.card-ig   {{ background:#ECFDF5; border:1.5px solid #6EE7B7; }}
.card-pfh  {{ background:#FFFBEB; border:1.5px solid #FCD34D; }}
.card-cs   {{ background:#FDF2F8; border:1.5px solid #F9A8D4; }}
.card-done {{ background:#F0FDF4; border:1.5px solid #86EFAC; }}
.card-head {{ display:flex; align-items:center; gap:8px; padding:11px 16px 9px; border-bottom:1px solid rgba(0,0,0,0.07); }}
.card-v2  .card-head {{ background:#DBEAFE; }} .card-ig .card-head {{ background:#D1FAE5; }}
.card-pfh .card-head {{ background:#FEF3C7; }} .card-cs .card-head {{ background:#FCE7F3; }}
.card-done .card-head {{ background:#DCFCE7; }}
.card-head-title {{ font-size:13px; font-weight:600; color:#111; flex:1; }}
.card-head-count {{ font-size:11px; background:rgba(0,0,0,0.09); color:#333; padding:2px 10px; border-radius:20px; font-weight:600; }}
.col-head {{ display:grid; grid-template-columns:155px 76px 68px 82px 1fr; gap:6px; padding:6px 16px; font-size:10px; color:#6B7280; font-weight:700; text-transform:uppercase; letter-spacing:.04em; border-bottom:1px solid rgba(0,0,0,0.05); background:rgba(255,255,255,0.4); }}
.rel-row {{ display:grid; grid-template-columns:155px 76px 68px 82px 1fr; align-items:start; gap:6px; padding:10px 16px; border-bottom:1px solid rgba(0,0,0,0.04); }}
.rel-row:last-child {{ border-bottom:none; }}
.rel-row:hover {{ background:rgba(255,255,255,0.6); }}
.rel-name {{ font-weight:600; color:#111; font-size:12px; line-height:1.3; }}
.hc {{ display:flex; align-items:center; gap:5px; font-size:11px; font-weight:600; padding-top:1px; }}
.hc-dot {{ width:9px; height:9px; border-radius:50%; flex-shrink:0; }}
.hc.red {{ color:#B91C1C; }} .hc.red .hc-dot {{ background:#EF4444; }}
.hc.yel {{ color:#B45309; }} .hc.yel .hc-dot {{ background:#F59E0B; }}
.hc.grn {{ color:#065F46; }} .hc.grn .hc-dot {{ background:#10B981; }}
.ph {{ display:inline-flex; align-items:center; font-size:10px; font-weight:600; padding:3px 9px; border-radius:20px; white-space:nowrap; margin-top:1px; }}
.ph-dev  {{ background:#DBEAFE; color:#1E40AF; }}
.ph-qa   {{ background:#D1FAE5; color:#065F46; }}
.ph-ship {{ background:#A7F3D0; color:#064E3B; }}
.prog-col {{ padding-top:2px; }}
.prog-bg  {{ height:8px; border-radius:6px; background:rgba(0,0,0,0.09); overflow:hidden; margin-bottom:3px; }}
.prog-fill {{ height:100%; border-radius:6px; }}
.pf-dev  {{ background:linear-gradient(90deg,#3B82F6,#1D4ED8); }}
.pf-qa   {{ background:linear-gradient(90deg,#10B981,#065F46); }}
.pf-ship {{ background:linear-gradient(90deg,#34D399,#059669); }}
.pf-zero {{ background:#D1D5DB; }}
.prog-lbl {{ font-size:10px; color:#6B7280; }}
.dc {{ display:flex; flex-direction:column; gap:3px; padding-top:1px; }}
.tag {{ display:inline-flex; align-items:center; font-size:10px; font-weight:600; padding:2px 8px; border-radius:20px; width:fit-content; white-space:nowrap; }}
.t-apr {{ background:#EDE9FE; color:#5B21B6; }}
.t-may {{ background:#FEF3C7; color:#92400E; }}
.t-tbd {{ background:#F3F4F6; color:#6B7280; }}
.t-ovr {{ background:#FEE2E2; color:#991B1B; }}
.t-blk {{ background:#FECACA; color:#B91C1C; }}
.t-ship {{ background:#A7F3D0; color:#064E3B; }}
.t-imm {{ background:#6D28D9; color:#fff; }}
.scope-text {{ font-size:10px; color:#374151; line-height:1.55; padding-top:3px; }}
.footer {{ font-size:10px; color:#9CA3AF; margin:8px 2px 0; }}
</style>
</head>
<body>
<div class="db">
  <div class="db-header">
    <h1>🎮 Pong Game Studios — Release Dashboard</h1>
    <p>Live Jira + Confluence data &nbsp;·&nbsp; {run_date} &nbsp;·&nbsp; Auto-refreshes daily &nbsp;·&nbsp; PRF override active</p>
  </div>
  <div class="kpi-grid">
    <div class="kpi kpi-r"><div class="kpi-num">{kpis['red']}</div><div class="kpi-lbl">🔴 Red Flag</div></div>
    <div class="kpi kpi-y"><div class="kpi-num">{kpis['yel']}</div><div class="kpi-lbl">🟡 At Risk</div></div>
    <div class="kpi kpi-g"><div class="kpi-num">{kpis['qa']}</div><div class="kpi-lbl">🟢 In QA</div></div>
    <div class="kpi kpi-b"><div class="kpi-num">{kpis['soon']}</div><div class="kpi-lbl">🚀 Ships Soon</div></div>
    <div class="kpi kpi-p"><div class="kpi-num">{kpis['shipped']}</div><div class="kpi-lbl">✅ Shipped {kpis['month']}</div></div>
  </div>
  {sections_html}
  <p class="footer">Source: Live Jira + Confluence PMO · ponggamestudios.atlassian.net · PRF shipped signal via Release issue resolutiondate · Generated {run_date}</p>
</div>
</body>
</html>"""


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print(f"▶ Building dashboard — {TODAY}")
    active, shipped = build_releases()
    kpis = compute_kpis(active, shipped)
    print(f"  KPIs → 🔴{kpis['red']} 🟡{kpis['yel']} 🟢QA:{kpis['qa']} 🚀{kpis['soon']} ✅{kpis['shipped']}")
    html = generate_html(active, shipped, kpis)
    with open("index.html", "w", encoding="utf-8") as f:
        f.write(html)
    print("✅ index.html written")


if __name__ == "__main__":
    main()
