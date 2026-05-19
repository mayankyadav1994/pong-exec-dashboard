"""
build_dashboard.py
Pulls live data from Jira (health/progress) and Confluence (scope text),
then generates index.html for GitHub Pages hosting.
Standalone — no Confluence page is written, only read.
"""

import os
import re
import requests
from datetime import date, timedelta
from math import ceil
from dotenv import load_dotenv
from bs4 import BeautifulSoup

from jira_client import jira_get, jira_post, jira_jql, JSON_HEADERS

load_dotenv()

# ── Credentials ───────────────────────────────────────────────────────────────
JIRA_EMAIL           = os.environ["JIRA_EMAIL"]
CONFLUENCE_API_TOKEN = os.environ["CONFLUENCE_API_TOKEN"]

CONFLUENCE_BASE = "https://ponggamestudios.atlassian.net/wiki/rest/api"
CONFLUENCE_AUTH = (JIRA_EMAIL, CONFLUENCE_API_TOKEN)

# ── Project / section config ──────────────────────────────────────────────────
# Each dashboard section is backed by one Jira project. Cloud Services work
# lives in CSS (Scrum); the legacy CS project is no longer tracked.
SECTION_PROJECTS = {
    "v2":  ["V2"],
    "ig":  ["IG"],
    "cs":  ["CSS"],
}

PROJECTS = sorted({p for projs in SECTION_PROJECTS.values() for p in projs})

SECTION_META = {
    "v2":  {"label": "🔵 V2 — Vendor 2",              "card": "card-v2",  "icon": "🎮", "title": "V2 Releases"},
    "ig":  {"label": "🟢 iGaming — ELG & PFH2 Games", "card": "card-ig",  "icon": "🎰", "title": "iGaming — ELG & PFH2 Games"},
    "cs":  {"label": "🩷 CSS — Cloud Services",        "card": "card-cs",  "icon": "☁️", "title": "Cloud Services"},
}

EXCLUDED_FV    = {"Trello", "PFH - Side Projects", "FC Backlog"}
VERSION_RE     = re.compile(r'\d+\.\d+')
QA_STATUSES    = {"In QA", "In QA R1", "In QA R2", "Ready For QA", "QA In Progress"}
GAME_ISSUE_TYPES = ["New Game", "Game"]

# Per-category issue types for unestimated-ticket callouts. Logic for each
# category: if a top-level issue has subtasks, check the subtasks; otherwise
# check the issue itself. Missing = no timeoriginalestimate.
ESTIMATE_CATEGORIES = {
    "QA":       ["QA", "QA Task", "QA Subtask", "Test", "Testing", "QA/Test"],
    "Math":     ["Math Task", "Math Subtask"],
    "Creative": ["Creative Task", "Creative Subtask"],
    "Sound":    ["Sound Task", "Sound Subtask"],
}

TODAY = date.today()


# ── Jira / FV helpers ─────────────────────────────────────────────────────────

def is_valid_fv(name):
    return name not in EXCLUDED_FV and bool(VERSION_RE.search(name))


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
    """
    Fetch every non-archived, valid fix version (released + unreleased).
    A version with the same name can exist in more than one project
    (`PFH2 Services 2.00` is in both PFH and CSS; `CS VGTC 8.00` is in both
    CS and CSS after the Scrum migration). Dedupe globally by name: first-seen
    section wins (iteration order is v2 → ig → cs), and the `projects` list
    expands to every project where the version is defined so issue queries
    hit all of them.

    Classification of shipped vs active happens in build_releases():
        - jira_released=True            → SHIPPED (authoritative)
        - jira_released=False + PRF     → SHIPPED (fallback; flag "Mark Released")
        - otherwise                     → ACTIVE
    """
    seen = {}  # name -> entry

    for section, projs in SECTION_PROJECTS.items():
        for proj in projs:
            data = jira_get(f"/project/{proj}/versions")
            for v in data:
                if v.get("archived"):
                    continue
                name = v.get("name", "")
                if not is_valid_fv(name):
                    continue
                is_released = bool(v.get("released"))
                existing = seen.get(name)
                if existing is None:
                    seen[name] = {
                        "name":          name,
                        "id":            v.get("id"),
                        "projects":      [proj],
                        "section":       section,
                        "releaseDate":   v.get("releaseDate"),
                        "jira_desc":     v.get("description", ""),
                        "jira_released": is_released,
                    }
                else:
                    if proj not in existing["projects"]:
                        existing["projects"].append(proj)
                    if not existing.get("releaseDate") and v.get("releaseDate"):
                        existing["releaseDate"] = v.get("releaseDate")
                    if not existing.get("jira_desc") and v.get("description"):
                        existing["jira_desc"] = v.get("description")
                    existing["jira_released"] = existing["jira_released"] or is_released

    return list(seen.values())


# ── Step 2: PRF override ──────────────────────────────────────────────────────

def fetch_prf_overrides():
    """
    A closed Release issue with a resolutiondate = PRF sent = truly shipped.
    Returns { "V2 C2 5.00": "2026-04-23", ... }
    """
    # The team stamps `resolutiondate` on the Release ticket when the PRF
    # goes out, but often leaves the ticket in "In QA" / "Ready For QA" rather
    # than transitioning it to Closed. So resolution presence (not status) is
    # the actual ship signal.
    issues = jira_jql(
        jql=f'project in ({_projects_clause(PROJECTS)}) AND issuetype = "Release" '
            f'AND resolution is not EMPTY',
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

def _projects_clause(projects):
    return ", ".join(projects)


def fetch_version_stats(fv_name, projects):
    issues = jira_jql(
        jql=f'project in ({_projects_clause(projects)}) AND fixVersion = "{fv_name}"',
        fields=["status", "priority", "subtasks", "issuetype",
                "timeestimate", "timeoriginalestimate", "timespent", "resolutiondate"],
    )

    # Build the set of countable "leaf" issues:
    #   - Skip any subtask already returned (issuetype.subtask=True) — will be
    #     re-fetched below so we get the full status detail from the parent query.
    #   - For parent issues with subtasks, record the key and skip the parent
    #     itself (its status just mirrors children; counting it double-counts).
    #   - For issues with no subtasks, count directly.
    leaf_issues = []
    parent_keys = []
    for issue in issues:
        if issue["fields"].get("issuetype", {}).get("subtask", False):
            continue  # will arrive via parent in (...) below
        subs = issue["fields"].get("subtasks") or []
        if subs:
            parent_keys.append(issue["key"])
        else:
            leaf_issues.append(issue)

    # Fetch full subtask detail in chunks
    chunk = 40
    for i in range(0, len(parent_keys), chunk):
        subs = jira_jql(
            jql=f'parent in ({", ".join(parent_keys[i:i+chunk])})',
            fields=["status", "priority", "issuetype",
                    "timeestimate", "timeoriginalestimate", "timespent", "resolutiondate"],
        )
        leaf_issues.extend(subs)

    done = blockers = qa_count = dev_count = 0
    remaining_secs = 0
    recent_done_secs = 0
    recent_done_count = 0
    open_with_estimate = 0
    open_total = 0
    cutoff = TODAY - timedelta(days=14)

    # Hour-weighted progress accumulators (in seconds):
    #   hours_done_credit = effort credited as "complete"
    #     = sum(timeoriginalestimate || timespent) for done tickets
    #     + sum(timespent) for open tickets (partial credit for work logged so far)
    #   hours_total       = effort that should be done
    #     = sum(max(timeoriginalestimate, timespent + timeestimate)) per ticket
    hours_done_credit = 0
    hours_total       = 0
    hours_spent_open  = 0   # logged time across open tickets (for display)

    # For AI-fill (imputation) of missing estimates: group open-ticket estimates
    # by issue type so we can use the median to fill in unestimated tickets.
    open_estimated_by_type = {}    # itype -> list of timeestimate seconds
    open_unestimated_by_type = {}  # itype -> count of unestimated open tickets

    for issue in leaf_issues:
        fields      = issue["fields"]
        status_cat  = fields["status"]["statusCategory"]["key"]
        status_nm   = fields["status"]["name"]
        priority    = (fields.get("priority") or {}).get("name", "")
        timeest     = fields.get("timeestimate") or 0
        timeorig    = fields.get("timeoriginalestimate") or 0
        timespent   = fields.get("timespent") or 0
        resdate_str = (fields.get("resolutiondate") or "")[:10]
        itype       = (fields.get("issuetype") or {}).get("name", "Unknown")

        # Ticket "scope" = best estimate of total effort for this ticket
        ticket_scope = max(timeorig, timespent + timeest)
        hours_total += ticket_scope

        if status_cat == "done":
            done += 1
            # Done tickets: credit whichever signal is larger — the original
            # estimate (planned effort) or the time actually spent.
            hours_done_credit += max(timeorig, timespent)
            if resdate_str:
                try:
                    if date.fromisoformat(resdate_str) >= cutoff:
                        recent_done_count += 1
                        if timeorig:
                            recent_done_secs += timeorig
                except ValueError:
                    pass
        elif status_nm in QA_STATUSES:
            qa_count += 1
            open_total += 1
            hours_done_credit += timespent
            hours_spent_open  += timespent
            if timeest:
                remaining_secs += timeest
                open_with_estimate += 1
                open_estimated_by_type.setdefault(itype, []).append(timeest)
            else:
                open_unestimated_by_type[itype] = open_unestimated_by_type.get(itype, 0) + 1
        else:
            dev_count += 1
            open_total += 1
            hours_done_credit += timespent
            hours_spent_open  += timespent
            if timeest:
                remaining_secs += timeest
                open_with_estimate += 1
                open_estimated_by_type.setdefault(itype, []).append(timeest)
            else:
                open_unestimated_by_type[itype] = open_unestimated_by_type.get(itype, 0) + 1

        if priority == "Blocker" and status_cat != "done":
            blockers += 1

    # ── AI-fill (statistical imputation) for unestimated tickets ────────────
    # Per-type median from estimated tickets in this FV; fall back to the
    # cross-type median; finally to a sensible default by issue-type name.
    from statistics import median
    type_median   = {it: median(v) for it, v in open_estimated_by_type.items() if v}
    all_estimated = [s for v in open_estimated_by_type.values() for s in v]
    global_median = median(all_estimated) if all_estimated else 0
    DEFAULT_HOURS_BY_NAME = {
        "Sub-task": 4, "Subtask": 4, "Dev Subtask": 4, "QA Subtask": 2,
        "Task": 8, "Dev Task": 8, "Story": 16, "Bug": 4, "QA Task": 4,
    }

    imputed_secs  = 0
    imputed_count = 0
    for itype, count in open_unestimated_by_type.items():
        per_ticket = type_median.get(itype) or global_median \
                     or (DEFAULT_HOURS_BY_NAME.get(itype, 8) * 3600)
        imputed_secs  += per_ticket * count
        imputed_count += count

    total = len(leaf_issues)
    pct   = int((done + qa_count) / total * 100) if total else 0
    # Phase = QA when all remaining work is in QA statuses (dev is done)
    phase = "qa" if qa_count > 0 and dev_count == 0 else "dev"
    estimate_coverage     = open_with_estimate / open_total if open_total else 0
    velocity_secs_per_day = recent_done_secs / 14 if recent_done_secs > 0 else 0
    pct_hours = int(hours_done_credit / hours_total * 100) if hours_total > 0 else 0

    return {
        "done": done, "qa_count": qa_count, "total": total, "pct": pct,
        "blockers": blockers, "phase": phase,
        "remaining_secs": remaining_secs,
        "imputed_secs": imputed_secs,
        "imputed_count": imputed_count,
        "estimate_coverage": estimate_coverage,
        "velocity_secs_per_day": velocity_secs_per_day,
        "recent_done_count": recent_done_count,
        "open_count": open_total,
        # Hour-weighted progress (in seconds for arithmetic; format later)
        "hours_done_credit": hours_done_credit,
        "hours_total":       hours_total,
        "hours_spent_open":  hours_spent_open,
        "pct_hours":         pct_hours,
    }


# ── Step 3b: QA estimate coverage ────────────────────────────────────────────

def fetch_unestimated_status(fv_name, projects):
    """
    Per ESTIMATE_CATEGORIES, count tickets missing timeoriginalestimate.
    Returns { category: {"missing": int, "total": int} }.

    Rule per top-level issue:
      - If the issue has subtasks → check each subtask's timeoriginalestimate.
      - If no subtasks → check the issue itself.
    """
    result = {cat: {"missing": 0, "total": 0} for cat in ESTIMATE_CATEGORIES}

    for category, types in ESTIMATE_CATEGORIES.items():
        types_jql = ", ".join(f'"{t}"' for t in types)
        try:
            issues = jira_jql(
                jql=f'project in ({_projects_clause(projects)}) AND fixVersion = "{fv_name}" '
                    f'AND issuetype in ({types_jql})',
                fields=["subtasks", "timeoriginalestimate"],
            )
        except Exception:
            continue

        if not issues:
            continue

        parent_keys = []
        for issue in issues:
            subtasks = issue["fields"].get("subtasks") or []
            if subtasks:
                parent_keys.append(issue["key"])
            else:
                result[category]["total"] += 1
                if not issue["fields"].get("timeoriginalestimate"):
                    result[category]["missing"] += 1

        if parent_keys:
            try:
                subs = jira_jql(
                    jql=f'parent in ({", ".join(parent_keys)})',
                    fields=["timeoriginalestimate"],
                )
                for sub in subs:
                    result[category]["total"] += 1
                    if not sub["fields"].get("timeoriginalestimate"):
                        result[category]["missing"] += 1
            except Exception:
                pass

    return result


# ── Step 3c: Game issues list ─────────────────────────────────────────────────

def fetch_game_issues(fv_name, projects):
    """
    Returns a list of game names (issue summaries) for the fix version.
    Tries GAME_ISSUE_TYPES; returns [] if none found or on error.
    """
    types_jql = ", ".join(f'"{t}"' for t in GAME_ISSUE_TYPES)
    try:
        issues = jira_jql(
            jql=f'project in ({_projects_clause(projects)}) AND fixVersion = "{fv_name}" '
                f'AND issuetype in ({types_jql})',
            fields=["summary"],
        )
        return [i["fields"]["summary"] for i in issues if i["fields"].get("summary")]
    except Exception:
        return []


# ── Step 4: Health classification ────────────────────────────────────────────

def classify_health(stats, release_date_str, eta_date):
    """
    Health classification — ETA vs. deadline as the primary signal.

    Red    = real, immediate risk:
             • blockers present, OR
             • deadline passed and release isn't nearly done (≥95% any signal),
               OR
             • ETA misses the deadline by more than 2 weeks
    Yellow = potential risk:
             • deadline passed but ≥95% done (trailing edge)
             • ETA slips past the deadline by ≤2 weeks (recoverable)
             • deadline within 7 days and progress < 80%
    Green  = on track:
             • no blockers and ETA fits within deadline
             • no deadline set and no blockers (TBD doesn't mean at risk)
    """
    if stats.get("blockers", 0) > 0:
        return "red"

    # Best progress signal — whichever of count- or hour-weighted is higher.
    best_pct = max(stats.get("pct", 0), stats.get("pct_hours", 0))

    if not release_date_str:
        return "grn"  # No planned deadline → nothing being missed

    try:
        rd = date.fromisoformat(release_date_str)
    except ValueError:
        return "grn"

    if rd < TODAY:
        # Deadline already passed.
        if best_pct >= 95:
            return "yel"
        return "red"

    if eta_date:
        slip_days = (eta_date - rd).days
        if slip_days > 14:
            return "red"
        if slip_days > 0:
            return "yel"
        return "grn"

    # No ETA but deadline approaching.
    days_to = (rd - TODAY).days
    if days_to <= 7 and best_pct < 80:
        return "yel"
    return "grn"


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


# ── Step 4b: ETA calculation ─────────────────────────────────────────────────

def compute_eta(stats, release_date_str):
    """
    Returns (eta_date: date|None, confidence: 'hi'|'med'|'lo'|None).

    Tier 1   hi  — real estimates ≥70% coverage + measured 14-day velocity
    Tier 1b  med — real estimates ≥70% coverage + assumed 12 h/day capacity
    Tier 2   med — real + AI-filled estimates + measured velocity
    Tier 2b  lo  — real + AI-filled estimates + 12 h/day capacity
    Tier 3   med — count velocity (≥3 issues resolved in last 14 days)
    Tier 4   lo  — planned release date or pct extrapolation

    AI-filled (Tier 2 / 2b) uses per-issuetype median imputation for
    unestimated open tickets — see fetch_version_stats() imputation pass.
    Confidence drops to "lo" when 12h/day fallback is used.
    """
    real_secs             = stats.get("remaining_secs", 0)
    imputed_secs          = stats.get("imputed_secs", 0)
    estimate_coverage     = stats.get("estimate_coverage", 0)
    velocity_secs_per_day = stats.get("velocity_secs_per_day", 0)
    recent_done_count     = stats.get("recent_done_count", 0)
    open_count            = stats.get("open_count", 0)
    pct                   = stats.get("pct", 0)

    total_secs = real_secs + imputed_secs

    if open_count == 0 and pct > 0:
        return TODAY, "hi"

    # Tier 1: high real coverage + measured velocity
    if estimate_coverage >= 0.7 and velocity_secs_per_day > 0 and real_secs > 0:
        days = ceil(real_secs / velocity_secs_per_day)
        capped = days > 365
        return TODAY + timedelta(days=min(days, 365)), ("lo" if capped else "hi")

    # Tier 1b: high real coverage + 12 h/day capacity
    if estimate_coverage >= 0.7 and real_secs > 0:
        days = ceil(real_secs / (12 * 3600))
        capped = days > 365
        return TODAY + timedelta(days=min(days, 365)), ("lo" if capped else "med")

    # Tier 2: AI-filled hours + measured velocity
    if total_secs > 0 and velocity_secs_per_day > 0:
        days = ceil(total_secs / velocity_secs_per_day)
        capped = days > 365
        return TODAY + timedelta(days=min(days, 365)), ("lo" if capped else "med")

    # Tier 2b: AI-filled hours + 12 h/day capacity
    if total_secs > 0:
        days = ceil(total_secs / (12 * 3600))
        return TODAY + timedelta(days=min(days, 365)), "lo"

    # Tier 3: count velocity
    if recent_done_count >= 3 and open_count > 0:
        days = ceil(open_count / (recent_done_count / 14))
        capped = days > 365
        return TODAY + timedelta(days=min(days, 365)), ("lo" if capped else "med")

    # Tier 4: planned date or overdue extrapolation
    if release_date_str:
        try:
            rd = date.fromisoformat(release_date_str)
            if rd >= TODAY:
                return rd, "lo"
            od = (TODAY - rd).days
            if pct > 0 and od > 0:
                remaining_days = ceil(od * (100 - pct) / max(pct, 1))
                return TODAY + timedelta(days=min(remaining_days, 365)), "lo"
        except ValueError:
            pass

    return None, None


# ── Step 5: Build release data ────────────────────────────────────────────────

def build_releases():
    print("  Fetching PRF overrides...")
    prf_overrides = fetch_prf_overrides()
    print(f"  Found {len(prf_overrides)} shipped via PRF (Release-ticket resolution)")

    print("  Fetching fix versions from Jira...")
    versions = fetch_fix_versions()
    released_count = sum(1 for v in versions if v.get("jira_released"))
    print(f"  Found {len(versions)} valid fix versions ({released_count} marked Released)")

    active_versions, shipped_versions = [], []
    for v in versions:
        if v.get("jira_released") or v["name"] in prf_overrides:
            shipped_versions.append(v)
        else:
            active_versions.append(v)

    active, shipped = [], []

    for v in shipped_versions:
        # Authoritative date: Jira fix-version releaseDate.
        # Fallback: PRF Release-ticket resolutiondate.
        ship_date = v.get("releaseDate") or prf_overrides.get(v["name"]) or ""
        if not ship_date:
            print(f"  ⚠ {v['name']} flagged as shipped but has no date — skipping")
            continue
        shipped.append({
            "name":          v["name"],
            "id":            v["id"],
            "section":       v["section"],
            "shipped_date":  ship_date,
            "description":   v.get("jira_desc", ""),
            "jira_released": v.get("jira_released", False),
        })

    for v in active_versions:
        name         = v["name"]
        projects     = v["projects"]
        stats        = fetch_version_stats(name, projects)
        if stats["total"] == 0:
            # Fix version exists in Jira but has no associated issues — skip
            # to avoid cluttering the dashboard with 0/0 placeholder rows.
            continue
        unestimated  = fetch_unestimated_status(name, projects)
        release_date = v.get("releaseDate")
        eta_date, eta_confidence = compute_eta(stats, release_date)
        health       = classify_health(stats, release_date, eta_date)
        od           = overdue_days(release_date)
        du           = days_until(release_date)

        # Scope: game names from Jira > fix version description > fallback
        games = fetch_game_issues(name, projects)
        if games:
            scope = " · ".join(games)
        else:
            scope = v.get("jira_desc") or "Scope TBD"

        active.append({
            "name":           name,
            "id":             v["id"],
            "section":        v["section"],
            "description":    scope,
            "health":         health,
            "phase":          stats["phase"],
            "done":           stats["done"],
            "qa_count":       stats["qa_count"],
            "total":          stats["total"],
            "pct":            stats["pct"],
            "blockers":       stats["blockers"],
            "release_date":   release_date,
            "overdue_days":   od,
            "days_until":     du,
            "unestimated":     unestimated,
            "imputed_count":   stats.get("imputed_count", 0),
            "eta_date":        eta_date,
            "eta_confidence":  eta_confidence,
            # Hour-weighted progress (seconds; render as hours in HTML)
            "hours_done":      stats.get("hours_done_credit", 0),
            "hours_total":     stats.get("hours_total", 0),
            "pct_hours":       stats.get("pct_hours", 0),
        })

    # Sort: by section order, then red → yellow → green within each section
    section_order = ["v2", "ig", "cs"]
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

def jira_fv_url(_fv_id, fv_name):
    # Filter by name (string) so the link works across projects when a version
    # is duplicated (e.g. PFH and CSS both have "PFH2 Services 2.00").
    import urllib.parse
    escaped = fv_name.replace('"', '\\"')
    jql = f'fixVersion = "{escaped}" ORDER BY issuetype DESC'
    encoded = urllib.parse.quote(jql)
    return f"https://ponggamestudios.atlassian.net/issues/?jql={encoded}"


def fmt_date(d):
    """Format a date as 'Apr 24'; include year when it differs from today's."""
    try:
        if isinstance(d, date) and d.year != TODAY.year:
            try:
                return d.strftime("%b %-d %Y")
            except ValueError:
                return d.strftime("%b %#d %Y")
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

    # Primary progress = hour-weighted when hour data exists; fall back to count.
    hours_done_h  = round((r.get("hours_done")  or 0) / 3600)
    hours_total_h = round((r.get("hours_total") or 0) / 3600)
    if hours_total_h > 0:
        pct   = r.get("pct_hours", 0)
        label = f'{pct}% by hours · {hours_done_h}h / {hours_total_h}h logged'
    else:
        pct   = r["pct"]
        label = f'{pct}% · {r["done"] + r["qa_count"]}/{r["total"]} tickets'
    pc = prog_class(r["phase"], pct)

    tags = []
    if r["overdue_days"] > 0:
        tags.append(f'<span class="tag t-ovr">⚠️ {r["overdue_days"]}d overdue</span>')
    else:
        tags.append(tag_date(r["release_date"], r.get("days_until")))
    if r["blockers"] > 0:
        word = "blocker" if r["blockers"] == 1 else "blockers"
        tags.append(f'<span class="tag t-blk">🔴 {r["blockers"]} {word}</span>')
    # Per-category unestimated callouts (QA / Math / Creative / Sound).
    # Rule per fetch_unestimated_status: subtasks if present, else parent.
    for cat, counts in (r.get("unestimated") or {}).items():
        n = counts.get("missing", 0)
        if n > 0:
            word = "ticket" if n == 1 else "tickets"
            tags.append(f'<span class="tag t-qa-warn">🔶 {cat}: {n} {word} unestimated</span>')
    if r.get("eta_date"):
        conf = r.get("eta_confidence", "lo")
        cls  = {"hi": "eta-hi", "med": "eta-med", "lo": "eta-lo"}.get(conf, "eta-lo")
        ai_suffix = " · 🤖 AI-filled" if r.get("imputed_count", 0) > 0 else ""
        tags.append(f'<span class="tag {cls}">📅 Est. {fmt_date(r["eta_date"])}{ai_suffix}</span>')

    scope = r.get("description") or "Scope being defined."

    return f"""
    <div class="rel-row">
      <div><div class="rel-name"><a href="{jira_fv_url(r['id'], r['name'])}" target="_blank" rel="noopener" class="rel-link">{r['name']}</a></div></div>
      <div class="hc {r['health']}"><span class="hc-dot"></span>{health_label}</div>
      <span class="ph {phase_cls}">{phase_label}</span>
      <div class="prog-col">
        <div class="prog-bg"><div class="prog-fill {pc}" style="width:{max(pct,1)}%"></div></div>
        <div class="prog-lbl">{label}</div>
      </div>
      <div class="dc">{''.join(tags)}<div class="scope-text">{scope}</div></div>
    </div>"""


def render_shipped_row(r):
    try:
        lbl = fmt_date(date.fromisoformat(r["shipped_date"]))
    except Exception:
        lbl = r["shipped_date"]
    scope = r.get("description", "")
    jira_flag = (
        '<span class="tag t-jira-open">⚠️ Mark Released in Jira</span>'
        if not r.get("jira_released", True) else ""
    )
    return f"""
    <div class="rel-row">
      <div><div class="rel-name"><a href="{jira_fv_url(r['id'], r['name'])}" target="_blank" rel="noopener" class="rel-link">{r['name']}</a></div></div>
      <div class="hc grn"><span class="hc-dot"></span>Shipped</div>
      <span class="ph ph-ship">Shipped</span>
      <div class="dc"><span class="tag t-ship">{lbl}</span>{jira_flag}</div>
      <div class="scope-text">{scope}</div>
    </div>"""


def generate_html(active, shipped, kpis):
    run_date = fmt_date(TODAY) + f", {TODAY.year}"

    month_label   = TODAY.strftime("%B %Y")
    month_prefix  = TODAY.strftime("%Y-%m")
    shipped_month = [r for r in shipped if r["shipped_date"].startswith(month_prefix)]

    sections_html = ""

    # For each team, render: shipped this month (if any) then active releases.
    for sec_key in ["v2", "ig", "cs"]:
        meta        = SECTION_META[sec_key]
        sec_shipped = [r for r in shipped_month if r["section"] == sec_key]
        sec_active  = [r for r in active        if r["section"] == sec_key]
        if not sec_shipped and not sec_active:
            continue

        sections_html += f"""
  <div class="sec-label sec-{sec_key}">{meta['label']}</div>"""

        # Shipped sub-card (this team, current month)
        if sec_shipped:
            shipped_rows = "".join(render_shipped_row(r) for r in sec_shipped)
            sections_html += f"""
  <div class="card card-done">
    <div class="card-head">
      <span style="font-size:16px">🎉</span>
      <span class="card-head-title">{meta['title']} — Shipped {month_label}</span>
      <span class="card-head-count">{len(sec_shipped)} shipped</span>
    </div>
    <div class="col-head">
      <span>Release</span><span>Health</span><span>Phase</span>
      <span>Date</span><span>Scope</span>
    </div>
    {shipped_rows}
  </div>"""

        # Active sub-card (this team, in-progress)
        if sec_active:
            row_html = "".join(render_active_row(r) for r in sec_active)
            sections_html += f"""
  <div class="card {meta['card']}">
    <div class="card-head">
      <span style="font-size:16px">{meta['icon']}</span>
      <span class="card-head-title">{meta['title']} — In Progress</span>
      <span class="card-head-count">{len(sec_active)} active</span>
    </div>
    <div class="col-head">
      <span>Release</span><span>Health</span><span>Phase</span>
      <span>Progress</span><span>Scope &amp; Details</span>
    </div>
    {row_html}
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
.prog-lbl {{ font-size:10px; color:#6B7280; font-variant-numeric: tabular-nums; }}
.dc {{ display:flex; flex-direction:column; gap:3px; padding-top:1px; }}
.tag {{ display:inline-flex; align-items:center; font-size:10px; font-weight:600; padding:2px 8px; border-radius:20px; width:fit-content; white-space:nowrap; }}
.t-apr {{ background:#EDE9FE; color:#5B21B6; }}
.t-may {{ background:#FEF3C7; color:#92400E; }}
.t-tbd {{ background:#F3F4F6; color:#6B7280; }}
.t-ovr {{ background:#FEE2E2; color:#991B1B; }}
.t-blk {{ background:#FECACA; color:#B91C1C; }}
.t-ship {{ background:#A7F3D0; color:#064E3B; }}
.t-imm {{ background:#6D28D9; color:#fff; }}
.t-qa-warn {{ background:#FFF7ED; color:#C2410C; border:1px solid #FED7AA; }}
.t-jira-open {{ background:#FEF9C3; color:#854D0E; border:1px solid #FDE047; }}
.eta-hi  {{ background:#D1FAE5; color:#065F46; border:1px solid #6EE7B7; }}
.eta-med {{ background:#DBEAFE; color:#1E40AF; border:1px solid #93C5FD; }}
.eta-lo  {{ background:#F3F4F6; color:#6B7280; border:1px solid #D1D5DB; }}
.scope-text {{ font-size:10px; color:#374151; line-height:1.55; padding-top:3px; }}
.footer {{ font-size:10px; color:#9CA3AF; margin:8px 2px 0; }}
.rel-link {{ color: inherit; text-decoration: none; border-bottom: 1px dashed rgba(0,0,0,0.25); }}
.rel-link:hover {{ border-bottom-color: currentColor; border-bottom-style: solid; }}
/* Shared tab nav (same markup as v2-timeline.html) */
.site-tabs {{ max-width: 1100px; margin: 0 auto 14px; padding: 0 4px; }}
.site-tabs-inner {{ display: flex; gap: 4px; border-bottom: 1px solid #E5E7EB; }}
.site-tab {{ padding: 9px 16px; font-size: 13px; font-weight: 500; color: #6B7280; text-decoration: none; border-bottom: 2px solid transparent; transition: color .12s, border-color .12s; }}
.site-tab:hover {{ color: #111827; }}
.site-tab.active {{ color: #4F46E5; border-bottom-color: #4F46E5; font-weight: 600; }}
</style>
</head>
<body>
<nav class="site-tabs">
  <div class="site-tabs-inner">
    <a href="index.html" class="site-tab active">Overview</a>
    <a href="v2-timeline.html" class="site-tab">V2 Timeline</a>
  </div>
</nav>
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
