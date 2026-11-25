"""
Games & features layer under each release, shared by every release timeline:
timeline.py (iGaming, Cloud Services) and v2_timeline.py (V2).

Python only labels each release's Scope cards (its epics, with Stories rolled
up): game / feature / release prep / other, the epic's Jira due date, and the
Game & Feature Pipeline board it links to. The page forecasts each card with
the release bar's own rule (pipeline_layer.js), so the layer always adds up to
the release. The CSS and JS are inlined into the page by inline().
"""
import json
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent          # timelines/
REPO = HERE.parent

_RELEASE_EPIC_RE = re.compile(r"^\s*release\b", re.I)


def inline(template: str) -> str:
    """Put the shared layer CSS/JS into a timeline template's placeholders."""
    css = (HERE / "pipeline_layer.css").read_text(encoding="utf-8")
    js = (HERE / "pipeline_layer.js").read_text(encoding="utf-8")
    for ph in ("/*__PIPELINE_CSS__*/", "/*__PIPELINE_JS__*/"):
        if template.count(ph) != 1:
            raise SystemExit(f"timeline template must contain {ph} exactly once")
    return template.replace("/*__PIPELINE_CSS__*/", css).replace("/*__PIPELINE_JS__*/", js)


def annotate(fv_list, jira_jql, game_re=None, games_feed=None, features_feed=None):
    """Tag every scope card with kind (game / feature / release / other), the
    epic's Jira due date, a display label and its pipeline board (`pipe`).
    Returns (games, features) card counts."""
    keys = sorted({s["key"] for fv in fv_list for s in fv.get("scope") or [] if s.get("key")})
    info = {}
    for i in range(0, len(keys), 80):
        for e in jira_jql(f"key in ({','.join(keys[i:i + 80])})", fields=["duedate", "issuetype"]):
            f = e.get("fields") or {}
            info[e["key"]] = {"due": f.get("duedate"),
                              "type": (f.get("issuetype") or {}).get("name")}
    game_rx = re.compile(game_re, re.I) if game_re else None
    boards = {"game": _board(games_feed), "feature": _board(features_feed)}
    n_games = n_feats = 0
    for fv in fv_list:
        for s in fv.get("scope") or []:
            name = s.get("name") or ""
            meta = info.get(s.get("key")) or {}
            if not s.get("key"):
                kind = "other"
            elif _RELEASE_EPIC_RE.search(name):
                kind = "release"
            elif game_rx and game_rx.search(name):
                kind = "game"
            else:
                kind = "feature"
            s["kind"] = kind
            s["due"] = meta.get("due")
            s["itype"] = meta.get("type")
            s["label"] = (game_rx.sub("", name).strip(" :-") or name) if kind == "game" else name
            feed, on_board = boards.get(kind, (None, set()))
            if feed and s.get("key") in on_board:
                s["pipe"] = feed          # link: game-pipeline.html?project=<feed>&focus=<key>
            n_games += kind == "game"
            n_feats += kind == "feature"
    return n_games, n_feats


def _board(feed):
    """(feed, epic keys on that Game & Feature Pipeline board): its default roster
    plus anything the PMO added. Read from the committed pipeline data, so the
    links match what the board shows."""
    if not feed:
        return None, set()
    gp = REPO / "game-pipeline"
    try:
        games = json.loads((gp / f"data-{feed}.json").read_text(encoding="utf-8")).get("games") or []
    except (OSError, json.JSONDecodeError):
        return feed, set()
    keys = {g.get("jira") for g in games if g.get("in_roster") is not False}
    try:
        keys |= set(json.loads((gp / f"plan-{feed}.json").read_text(encoding="utf-8")).get("added") or [])
    except (OSError, json.JSONDecodeError):
        pass
    return feed, keys
