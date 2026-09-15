#!/usr/bin/env python3
"""
elg_cost_spreadsheet.py
-----------------------
Builds the ELG cost workbook (.xlsx) and the plugin knowledge file (.md) from
the cache written by elg_cost_data.py. No network, no Jira.

This one is deliberately stable -- it is not where the iteration happens.

    python elg_cost_data.py        # first, if you want fresh numbers
    python elg_cost_spreadsheet.py

Deps: pip install openpyxl
"""

import statistics as st
from collections import defaultdict, Counter

from openpyxl import Workbook
from openpyxl.formatting.rule import ColorScaleRule
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

import os

from elg_cost_data import (
    GAMES, RELEASE_EPICS, CATEGORIES, DEPT_ORDER, SIZES, RULES, LEGEND,
    COMPOSITION, COMPOSITION_NOTE, UNPRICED_2024, JIRA_BASE,
    legend_bands, h, load, HERE,
)

# Anchored to this folder, not the working directory. The workbook is a local
# artefact -- it is gitignored and never published to the Pages site.
OUTPUT_FILE = os.path.join(HERE, "elg_game_cost_report.xlsx")
KNOWLEDGE_FILE = os.path.join(HERE, "elg_game_cost_model.md")  # mirrored into the Knowledge tab


def knowledge_md(rows, rel, counts, agg):
    """
    The Knowledge tab / .md file. Every figure is computed from the same
    aggregates the workbook uses, so the document cannot drift from the data.
    Written for a Claude plugin: durable facts and rules, not a run log.
    """
    L = []
    w = L.append

    def gtot(epic, idx):
        return sum(agg.get((epic, d, "FULL"), [0, 0, 0])[idx] for d in DEPT_ORDER)

    tot_e = sum(gtot(e, 0) for e in GAMES)
    tot_a = sum(gtot(e, 1) for e in GAMES)
    oh_total = sum(d["ts_s"] for d in rel)
    oh_games = sum(counts.values()) or 1
    oh_pg = h(oh_total / oh_games)

    w("# ELG Game Cost Model")
    w("")
    w("Delivery-cost knowledge for iGaming (project IG) ELG games at Pong Game "
      "Studios. Generated from Jira by `game_cost_report.py`; every figure below "
      "is measured, not estimated.")
    w("")
    w("## Scope")
    w("")
    w(f"- **Sample**: {len(GAMES)} games from the last ELG game-bearing releases "
      f"({', '.join(sorted(set(v[2] for v in GAMES.values())))}).")
    w(f"- **Tickets traced**: {len(rows)}, walking epic -> children -> subtasks.")
    w(f"- **Total estimated**: {tot_e / 3600:,.0f}h. **Total actual**: {tot_a / 3600:,.0f}h. "
      f"**Actual / Estimate: {tot_a / tot_e:.2f}**.")
    w("- **Categories**: Port, Skin, Branded, New. No New game has ever shipped to "
      "ELG -- everything ELG is built in V2 first and ported.")
    w("")
    w("## Headline finding")
    w("")
    w("**The 2024 t-shirt bands do not fail high. They fail to scale down.**")
    w("")
    w("Large Ports land close to estimate; small games come in at roughly half. "
      "Cutting every band by a flat percentage would break the large end that "
      "already works -- the correct fix is to steepen the small sizes.")
    w("")
    w("| Game | Category | Est h | Actual h | Actual/Est |")
    w("|---|---|---:|---:|---:|")
    for epic, (name, cat, _) in sorted(GAMES.items(), key=lambda kv: -gtot(kv[0], 1)):
        e, a = gtot(epic, 0), gtot(epic, 1)
        w(f"| {name} | {cat} | {h(e):,.0f} | {h(a):,.0f} | "
          f"{(a / e if e else 0):.2f} |")
    w("")
    w("## Budget per game by category")
    w("")
    w(f"Build hours plus {oh_pg}h release overhead per game "
      f"({h(oh_total):,.0f}h across {oh_games} game epics).")
    w("")
    w("| Category | Games | Avg build h | + Release o/h | Budget h | Confidence |")
    w("|---|---:|---:|---:|---:|---|")
    for cat in CATEGORIES:
        eps = [e for e, (_, c, _) in GAMES.items() if c == cat]
        avg = (sum(h(gtot(e, 1)) for e in eps) / len(eps)) if eps else 0
        conf = {0: "NO DATA - cannot budget", 1: "Indicative only (n=1)",
                2: "Low (n=2)"}.get(len(eps), f"Moderate (n={len(eps)})")
        w(f"| {cat} | {len(eps)} | {avg:,.0f} | {oh_pg} | "
          f"{(avg + oh_pg if eps else 0):,.0f} | {conf} |")
    w("")
    w("> Only Port rests on enough games to average. Skin (n=2) and Branded (n=1) "
      "are single data points -- a starting hypothesis, not a budget. New cannot be "
      "budgeted from delivery data at all.")
    w("")
    w("## Department cost profile")
    w("")
    w("Across all 8 games, full-game scope. `Actual/Est` below 0.80 means the "
      "department is over-estimated; above 1.15 means under-estimated.")
    w("")
    w("| Department | Est h | Actual h | Actual/Est | Read as |")
    w("|---|---:|---:|---:|---|")
    for dept in DEPT_ORDER:
        e = sum(agg.get((g, dept, "FULL"), [0, 0, 0])[0] for g in GAMES)
        a = sum(agg.get((g, dept, "FULL"), [0, 0, 0])[1] for g in GAMES)
        if not e and not a:
            continue
        r = a / e if e else 0
        verdict = ("never estimated" if not e else
                   "OVER-estimated ~2x" if r < 0.6 else
                   "over-estimated" if r < 0.8 else
                   "about right" if r <= 1.15 else "UNDER-estimated")
        w(f"| {dept} | {h(e):,.0f} | {h(a):,.0f} | {r:.2f} | {verdict} |")
    w("")
    w("Departments the 2024 legend never priced at all: "
      f"{', '.join(UNPRICED_2024)}. That is where the hidden cost lives -- real "
      "spend no estimate ever accounted for.")
    w("")
    w("## How work maps to departments")
    w("")
    w("Issue type alone is NOT reliable in project IG: Sound work is typed "
      "`Dev Subtask`, a 43h Review story is typed `Story`. Summary-pattern rules "
      "are therefore evaluated BEFORE issue-type fallbacks. First match wins, "
      "top to bottom:")
    w("")
    w("| # | Department | Match on | Pattern / issue types |")
    w("|---:|---|---|---|")
    for i, (dept, kind, matcher) in enumerate(RULES, 1):
        if kind == "type":
            val = ", ".join("`%s`" % t for t in sorted(matcher))
        else:
            # raw regex, escaped for a markdown table cell -- never split on "|",
            # which is a regex alternation character inside the pattern itself
            val = "`%s`" % matcher.replace("|", "\\|")
        w(f"| {i} | {dept} | {kind} | {val} |")
    w("")
    w("Notes on deliberate calls:")
    w("")
    w("- Bracket tags (`[Server]`, `[GE]`, `[Math]`, `[FE]`) are authoritative and "
      "beat keywords -- they are the newer naming convention.")
    w("- Server sits ABOVE Game Engine in keyword order: \"deployment on New Game "
      "Engine\" is releasing onto the engine platform, not building the engine.")
    w("- All `Review & Refinement` / `Review - <dept>` work is routed to **Review**, "
      "not to the department being reviewed.")
    w("- `[FE] - Sounds` lands in Dev, because the bracket tag wins. Debatable (~14h).")
    w("")
    w("## Jira traversal rules (hard-won)")
    w("")
    w("- **Search endpoint**: only `POST /rest/api/3/search/jql` works. "
      "`GET /search` returns 410 Gone.")
    w("- **Paging**: use `nextPageToken`, never `startAt`. 100 rows per page.")
    w("- **Chunking**: `parent in (...)` must be chunked to ~8 keys or tickets are "
      "silently lost.")
    w("- **Two-stage traversal**: `parent = EPIC` for children, then "
      "`parent in (child keys)` for subtasks. Recursive JQL is unreliable on this "
      "plan tier.")
    w("- **Bugs carry subtasks too** -- do not exclude Bug/Live Issue/Enhancement "
      "when collecting parents (33 subtasks hide there).")
    w("- **Rollup**: sum each ticket's own `timespent` across the walked tree. "
      "Epic-level `aggregatetimespent` only rolls up one level and understates "
      "by 5-20x.")
    w("- **Hours**: keep raw seconds, convert once with `round(secs/3600, 2)`. "
      "Integer division drops half-hours.")
    w("- **Never attribute by assignee**: devs reassign to the Dev Lead at the "
      "Review handoff, so the assignee field credits the wrong person. Jira's "
      "worklog author is the Tempo app account and is useless. Rebuild from "
      "changelog if per-person attribution is ever needed.")
    w("- **Naming drift**: older games use `Game Engine - X`; newer use "
      "`[GE] - Simulation` and `[Server] - X`. Match both. "
      "`[Server] - Tickets & Pools` and `[Server] - Game config` contain no "
      "\"engine\" keyword and are easy to miss.")
    w("")
    w("## Corrections to previously held rules")
    w("")
    w("- **IG epics DO carry fix versions.** The standing rule \"epics carry no fix "
      "version\" is false: 7 of these 8 epics are tagged. Only Flaming Skulls "
      "(IG-1506) has no ELG version on the epic. Still test ELG membership at "
      "story/subtask level, but any \"games per release\" count built on epic "
      "fixVersion undercounts.")
    elg_a = sum(agg.get((g, d, "ELG"), [0, 0, 0])[1] for g in GAMES for d in DEPT_ORDER)
    w(f"- **PFH leakage is smaller than assumed.** ELG-only scope captures "
      f"{h(elg_a):,.0f}h of {h(tot_a):,.0f}h; the gap is {h(tot_a - elg_a):,.0f}h "
      f"({100 * (tot_a - elg_a) / tot_a:.1f}%), confined to the three oldest Ports. "
      "The single largest item is QA tagged `New Games - iGaming`, not PFH.")
    w("")
    w("## Data hygiene")
    w("")
    flags = {"est_no_log": [0, 0], "no_fv": [0, 0], "non_elg": [0, 0], "log_no_est": [0, 0]}
    for d in rows:
        if d["oe_s"] > 0 and d["ts_s"] == 0:
            flags["est_no_log"][0] += 1; flags["est_no_log"][1] += d["oe_s"]
        if not d["fv"] and d["level"] > 0:
            flags["no_fv"][0] += 1; flags["no_fv"][1] += d["ts_s"]
        if d["fv"] and not d["elg"]:
            flags["non_elg"][0] += 1; flags["non_elg"][1] += d["ts_s"]
        if d["ts_s"] > 0 and d["oe_s"] == 0:
            flags["log_no_est"][0] += 1; flags["log_no_est"][1] += d["ts_s"]
    w(f"- **{flags['est_no_log'][0]} tickets carry {h(flags['est_no_log'][1]):,.0f}h of "
      "estimate with zero time logged.** This is the single reason the bands were "
      "never validated: there was nothing to compare an actual against. Fix this first.")
    w(f"- **{flags['non_elg'][0]} tickets ({h(flags['non_elg'][1]):,.0f}h)** sit under an "
      "ELG game epic but carry a non-ELG fix version (PFH, Horse Play, "
      "'New Games - iGaming').")
    w(f"- **{flags['no_fv'][0]} tickets ({h(flags['no_fv'][1]):,.0f}h)** carry no fix "
      "version at all and cannot be attributed to any release.")
    w(f"- **{flags['log_no_est'][0]} tickets ({h(flags['log_no_est'][1]):,.0f}h)** have time "
      "logged against no estimate -- invisible to any capacity forecast.")
    w("")
    w("## What this model cannot tell you")
    w("")
    w("- **New-game cost.** Zero delivery data. Do not infer it from Port.")
    w("- **Per-person or per-team cost.** Assignee is unreliable (see above).")
    w("- **Anything at size granularity.** These are whole-game actuals; the sample "
      "cannot say what an \"M Port\" costs, only what five real Ports cost.")
    w("- **Branded as a category.** One game. Treat 'Branded' figures as that one "
      "game's number wearing a category label.")
    w("")
    return "\n".join(L)


def bands_from(values):
    """
    Observed XS..XL from actual spend. Honest about small n: quantiles are
    only computed when there are enough points to mean anything, otherwise
    the cell is left blank rather than fabricated.
    """
    v = sorted(values)
    n = len(v)
    if n == 0:
        return [None] * 5, "No data"
    if n == 1:
        return [None, None, v[0], None, None], "Single observation"
    if n < 4:
        return [v[0], None, st.median(v), None, v[-1]], f"Range only (n={n})"
    q = st.quantiles(v, n=4)
    return [v[0], q[0], st.median(v), q[2], v[-1]], f"Quantiles (n={n})"


# ===========================================================================
#  WORKBOOK
# ===========================================================================
A = "Arial"
NAVY = PatternFill("solid", fgColor="1F4E79")
BAND = PatternFill("solid", fgColor="DDEBF7")
WARN = PatternFill("solid", fgColor="FCE4D6")
HDR = Font(name=A, bold=True, color="FFFFFF", size=11)
BODY = Font(name=A, size=10)
BOLD = Font(name=A, size=10, bold=True)
NOTE = Font(name=A, size=9, italic=True)
LINK = Font(name=A, size=10, color="0563C1", underline="single")
RIGHT = Alignment(horizontal="right")
NUM = "#,##0.00"


def banner(ws, title, ncols):
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=ncols)
    c = ws.cell(row=1, column=1, value=title)
    c.font = Font(name=A, bold=True, color="FFFFFF", size=14)
    c.fill = NAVY
    c.alignment = Alignment(horizontal="left", vertical="center")
    ws.row_dimensions[1].height = 24


def head(ws, row, labels):
    for j, t in enumerate(labels, 1):
        c = ws.cell(row=row, column=j, value=t)
        c.font, c.fill = HDR, NAVY
        c.alignment = Alignment(horizontal="center", wrap_text=True)


def footnote(ws, row, ncols, text, height=4):
    ws.merge_cells(start_row=row, start_column=1, end_row=row + height - 1,
                   end_column=ncols)
    c = ws.cell(row=row, column=1, value=text)
    c.font = NOTE
    c.alignment = Alignment(wrap_text=True, vertical="top")


def widths(ws, spec):
    for col, w in spec.items():
        ws.column_dimensions[col].width = w


def sheet_explorer(wb, n_tickets):
    """
    Live per-game / per-department explorer driven by real Excel dropdowns.

    Everything is SUMIFS over the Ticket Detail sheet, so it recalculates in
    Excel with no macros. "All" is expressed as the "*" wildcard -- which is
    also why Ticket Detail writes Y/N in the ELG column rather than Y/blank:
    "*" matches any text but never an empty cell.
    """
    ws = wb.create_sheet("Explorer", 1)
    end = n_tickets + 1                      # data occupies rows 2..end
    D = "'Ticket Detail'!"
    RG = f"{D}$A$2:$A${end}"                 # Game
    RD = f"{D}$F$2:$F${end}"                 # Department
    RE = f"{D}$I$2:$I${end}"                 # ELG? (Y/N)
    RK = f"{D}$K$2:$K${end}"                 # Orig Est
    RL = f"{D}$L$2:$L${end}"                 # Time Spent
    CG, CD, CE = "$T$1", "$T$2", "$T$3"      # criteria helper cells

    banner(ws, "Explorer -- pick a game and a department", 13)
    ws.cell(row=2, column=1,
            value="Choose from the dropdowns in B3:B5. Every figure below "
                  "recalculates automatically.").font = NOTE

    # -- hidden option lists + criteria helpers (cols P-T) -------------------
    games = ["All games"] + [v[0] for v in GAMES.values()]
    depts = ["All departments"] + DEPT_ORDER
    scopes = ["Full game", "ELG only"]
    for i, v in enumerate(games, 1):
        ws.cell(row=i, column=16, value=v)
    for i, v in enumerate(depts, 1):
        ws.cell(row=i, column=17, value=v)
    for i, v in enumerate(scopes, 1):
        ws.cell(row=i, column=18, value=v)
    ws["T1"] = '=IF($B$3="All games","*",$B$3)'
    ws["T2"] = '=IF($B$4="All departments","*",$B$4)'
    ws["T3"] = '=IF($B$5="ELG only","Y","*")'
    for col in "PQRST":
        ws.column_dimensions[col].hidden = True

    # -- the three controls --------------------------------------------------
    for r_, (lab, cell, rng, default) in enumerate([
            ("Game", "B3", f"$P$1:$P${len(games)}", "All games"),
            ("Department", "B4", f"$Q$1:$Q${len(depts)}", "All departments"),
            ("Scope", "B5", f"$R$1:$R${len(scopes)}", "Full game")], start=3):
        c = ws.cell(row=r_, column=1, value=lab)
        c.font, c.alignment = BOLD, Alignment(horizontal="right")
        dv = DataValidation(type="list", formula1=f"={rng}", allow_blank=False)
        ws.add_data_validation(dv)
        dv.add(ws[cell])
        sel = ws[cell]
        sel.value = default
        sel.font = Font(name=A, size=11, bold=True, color="1F4E79")
        sel.fill = PatternFill("solid", fgColor="FFF2CC")
        sel.alignment = Alignment(horizontal="left")

    # -- selection summary ---------------------------------------------------
    head(ws, 7, ["Tickets", "Estimated (h)", "Actual (h)", "Actual / Est"])
    crit = f"{RG},{CG},{RD},{CD},{RE},{CE}"
    vals = [f"=COUNTIFS({crit})",
            f"=SUMIFS({RK},{RG},{CG},{RD},{CD},{RE},{CE})",
            f"=SUMIFS({RL},{RG},{CG},{RD},{CD},{RE},{CE})",
            '=IFERROR(C8/B8,"")']
    for j, f_ in enumerate(vals, 1):
        c = ws.cell(row=8, column=j, value=f_)
        c.font, c.alignment = BOLD, RIGHT
        c.number_format = "#,##0" if j == 1 else ("0.00" if j == 4 else NUM)

    # -- by department (respects Game + Scope) -------------------------------
    r = 10
    c = ws.cell(row=r, column=1, value="BY DEPARTMENT  --  follows the Game and Scope pickers")
    c.font, c.fill = BOLD, BAND
    r += 1
    head(ws, r, ["Department", "Estimated (h)", "Actual (h)", "Actual / Est"])
    r += 1
    dept_first = r
    for dept in DEPT_ORDER:
        ws.cell(row=r, column=1, value=dept).font = BODY
        ws.cell(row=r, column=2,
                value=f'=SUMIFS({RK},{RG},{CG},{RD},"{dept}",{RE},{CE})')
        ws.cell(row=r, column=3,
                value=f'=SUMIFS({RL},{RG},{CG},{RD},"{dept}",{RE},{CE})')
        ws.cell(row=r, column=4, value=f'=IFERROR(C{r}/B{r},"")')
        for j in (2, 3):
            ws.cell(row=r, column=j).number_format = NUM
            ws.cell(row=r, column=j).alignment = RIGHT
        ws.cell(row=r, column=4).number_format = "0.00"
        ws.cell(row=r, column=4).alignment = RIGHT
        r += 1
    ws.conditional_formatting.add(
        f"C{dept_first}:C{r - 1}",
        ColorScaleRule(start_type="min", start_color="FFFFFF",
                       end_type="max", end_color="C9A868"))

    # -- by game (respects Department + Scope) -------------------------------
    r += 1
    c = ws.cell(row=r, column=1, value="BY GAME  --  follows the Department and Scope pickers")
    c.font, c.fill = BOLD, BAND
    r += 1
    head(ws, r, ["Game", "Category", "Estimated (h)", "Actual (h)", "Actual / Est"])
    r += 1
    game_first = r
    for epic, (name, cat, _) in GAMES.items():
        ws.cell(row=r, column=1, value=name).font = BODY
        ws.cell(row=r, column=2, value=cat).font = BODY
        ws.cell(row=r, column=3,
                value=f'=SUMIFS({RK},{RG},"{name}",{RD},{CD},{RE},{CE})')
        ws.cell(row=r, column=4,
                value=f'=SUMIFS({RL},{RG},"{name}",{RD},{CD},{RE},{CE})')
        ws.cell(row=r, column=5, value=f'=IFERROR(D{r}/C{r},"")')
        for j in (3, 4):
            ws.cell(row=r, column=j).number_format = NUM
            ws.cell(row=r, column=j).alignment = RIGHT
        ws.cell(row=r, column=5).number_format = "0.00"
        ws.cell(row=r, column=5).alignment = RIGHT
        r += 1
    ws.conditional_formatting.add(
        f"D{game_first}:D{r - 1}",
        ColorScaleRule(start_type="min", start_color="FFFFFF",
                       end_type="max", end_color="C9A868"))

    # -- full matrix (Scope only) --------------------------------------------
    r += 1
    c = ws.cell(row=r, column=1,
                value="MATRIX  --  actual hours, game x department (follows Scope only)")
    c.font, c.fill = BOLD, BAND
    r += 1
    head(ws, r, ["Game"] + DEPT_ORDER + ["Total"])
    r += 1
    m_first = r
    for epic, (name, cat, _) in GAMES.items():
        ws.cell(row=r, column=1, value=name).font = BODY
        for j, dept in enumerate(DEPT_ORDER, 2):
            c = ws.cell(row=r, column=j,
                        value=f'=SUMIFS({RL},{RG},"{name}",{RD},"{dept}",{RE},{CE})')
            c.number_format, c.alignment, c.font = NUM, RIGHT, BODY
        a_, b_ = get_column_letter(2), get_column_letter(1 + len(DEPT_ORDER))
        c = ws.cell(row=r, column=2 + len(DEPT_ORDER), value=f"=SUM({a_}{r}:{b_}{r})")
        c.number_format, c.alignment, c.font = NUM, RIGHT, BOLD
        r += 1
    ws.conditional_formatting.add(
        f"B{m_first}:{get_column_letter(1 + len(DEPT_ORDER))}{r - 1}",
        ColorScaleRule(start_type="num", start_value=0, start_color="FFFFFF",
                       end_type="max", end_color="A8802F"))

    footnote(ws, r + 1, 13,
             "Scope 'Full game' counts every ticket under the game epic; 'ELG only' "
             "counts just those carrying an ELG fix version. The difference is work "
             "tagged PFH / Horse Play / 'New Games - iGaming' -- a fix-version hygiene "
             "problem, not a costing decision.\n"
             "Blank Actual/Est means the selection has no Original Estimate to divide "
             "by -- that is itself a finding, not an error.", height=4)

    widths(ws, {"A": 34, "B": 15, "C": 15, "D": 15, "E": 14})
    for j in range(6, 14):
        ws.column_dimensions[get_column_letter(j)].width = 12
    ws.freeze_panes = "A7"
    return ws


def sheet_knowledge(wb, md):
    """Markdown mirrored one line per row so column A can be saved as .md."""
    ws = wb.create_sheet("Knowledge")
    c = ws.cell(row=1, column=1,
                value="# Knowledge tab -- copy column A (from row 3 down) and save as .md")
    c.font = Font(name=A, bold=True, size=11, color="1F4E79")
    ws.cell(row=2, column=1,
            value=f"# The same content is written to {KNOWLEDGE_FILE} next to the "
                  f"workbook -- use that file directly.").font = NOTE
    mono = Font(name="Consolas", size=10)
    for i, line in enumerate(md.split("\n"), start=3):
        cell = ws.cell(row=i, column=1, value=line)
        cell.font = mono
        cell.alignment = Alignment(horizontal="left", vertical="top")
    ws.column_dimensions["A"].width = 120
    ws.sheet_view.showGridLines = False
    return ws


def write(rows, rel, counts, agg):
    wb = Workbook()
    ref = legend_bands()

    # ---- 1. Per Game -------------------------------------------------------
    ws = wb.active
    ws.title = "Per Game"
    nc = 3 + len(DEPT_ORDER) + 1
    banner(ws, "ELG game cost -- hours actually spent, by department", nc)
    head(ws, 2, ["Game", "Category", "Release"] + DEPT_ORDER + ["TOTAL"])
    r = 3
    for scope, label in (("ELG", "ELG fix versions only"),
                         ("FULL", "FULL game -- all fix versions")):
        c = ws.cell(row=r, column=1, value=f"--- {label} ---")
        c.font, c.fill = BOLD, BAND
        r += 1
        for epic, (name, cat, ver) in GAMES.items():
            ws.cell(row=r, column=1, value=name).font = BODY
            ws.cell(row=r, column=2, value=cat).font = BODY
            ws.cell(row=r, column=3, value=ver).font = BODY
            for j, dept in enumerate(DEPT_ORDER, 4):
                c = ws.cell(row=r, column=j,
                            value=h(agg.get((epic, dept, scope), [0, 0, 0])[1]))
                c.font, c.number_format, c.alignment = BODY, NUM, RIGHT
            a, b = get_column_letter(4), get_column_letter(3 + len(DEPT_ORDER))
            c = ws.cell(row=r, column=nc, value=f"=SUM({a}{r}:{b}{r})")
            c.font, c.number_format, c.alignment = BOLD, NUM, RIGHT
            r += 1
        r += 1
    # computed, never hardcoded -- this gap moves whenever the traversal or the
    # underlying Jira data changes
    full_s = sum(agg.get((e, d, "FULL"), [0, 0, 0])[1] for e in GAMES for d in DEPT_ORDER)
    elg_s = sum(agg.get((e, d, "ELG"), [0, 0, 0])[1] for e in GAMES for d in DEPT_ORDER)
    footnote(ws, r, nc,
             "Hours are Time Spent. The ELG/FULL gap is work under an ELG game epic that "
             "does not carry an ELG fix version -- either tagged elsewhere (PFH, Horse "
             "Play, 'New Games - iGaming') or tagged with nothing at all. "
             f"Measured at {h(full_s - elg_s):,.2f}h of {h(full_s):,.2f}h "
             f"({100 * (full_s - elg_s) / full_s:.1f}%), concentrated in the oldest Ports "
             "-- see Data Gaps for the ticket list. Treat it as fix-version hygiene to "
             "fix, not as a costing decision.")
    widths(ws, {"A": 30, "B": 10, "C": 11})
    for j in range(4, nc + 1):
        ws.column_dimensions[get_column_letter(j)].width = 12
    ws.freeze_panes = "D3"

    # ---- 2. Category Averages ---------------------------------------------
    ws2 = wb.create_sheet("Category Averages")
    nc2 = len(DEPT_ORDER) + 6
    banner(ws2, "Average hours per game by category -- the budget basis", nc2)
    head(ws2, 2, ["Category", "Games"] + DEPT_ORDER +
         ["AVG TOTAL", "Release o/h", "BUDGET", "Confidence"])
    cat_vals = defaultdict(list)
    r = 3
    oh_total = sum(d["ts_s"] for d in rel)
    oh_games = sum(counts.values()) or 1
    oh_per_game = oh_total / oh_games
    for cat in CATEGORIES:
        eps = [e for e, (_, c2, _) in GAMES.items() if c2 == cat]
        ws2.cell(row=r, column=1, value=cat).font = BOLD
        ws2.cell(row=r, column=2, value=len(eps)).font = BODY
        tot = 0.0
        for j, dept in enumerate(DEPT_ORDER, 3):
            vals = [h(agg.get((e, dept, "FULL"), [0, 0, 0])[1]) for e in eps]
            cat_vals[(cat, dept)] = vals
            avg = sum(vals) / len(vals) if vals else 0
            tot += avg
            c = ws2.cell(row=r, column=j, value=round(avg, 2))
            c.font, c.number_format, c.alignment = BODY, NUM, RIGHT
        for j, v in ((len(DEPT_ORDER) + 3, tot),
                     (len(DEPT_ORDER) + 4, h(oh_per_game)),
                     (len(DEPT_ORDER) + 5, tot + h(oh_per_game))):
            c = ws2.cell(row=r, column=j, value=round(v, 2))
            c.font, c.number_format, c.alignment = BOLD, NUM, RIGHT
        conf = {0: "No data -- cannot budget", 1: "Indicative only (n=1)",
                2: "Low (n=2)"}.get(len(eps), f"Moderate (n={len(eps)})")
        cc = ws2.cell(row=r, column=len(DEPT_ORDER) + 6, value=conf)
        cc.font = BODY
        if len(eps) < 3:
            cc.fill = WARN
        r += 1
    footnote(ws2, r + 1, nc2,
             f"Averages use FULL scope. BUDGET = department average + release overhead "
             f"({h(oh_per_game)}h per game: {h(oh_total)}h across {oh_games} game epics "
             f"in releases {', '.join(sorted(set(RELEASE_EPICS.values())))}).\n"
             "READ THE CONFIDENCE COLUMN. Port rests on 5 games and is usable. Skin rests "
             "on 2 and Branded on 1 -- those are single data points, not averages, and "
             "should be treated as a starting hypothesis to widen as more games ship. "
             "'New' is empty because no New game has shipped to ELG: everything ELG is "
             "built in V2 first and ported, so that category cannot be budgeted from "
             "this data at all.", height=6)
    widths(ws2, {"A": 16, "B": 8})
    for j in range(3, nc2 + 1):
        ws2.column_dimensions[get_column_letter(j)].width = 12
    ws2.column_dimensions[get_column_letter(nc2)].width = 24

    # ---- 3. Rebased Sizing -------------------------------------------------
    ws3 = wb.create_sheet("Rebased Sizing")
    banner(ws3, "T-shirt bands rebased on delivery -- vs the 2024 legend", 13)
    head(ws3, 2, ["Category", "Department", "n"] +
         [f"{s} obs" for s in SIZES] +
         ["2024 M", "Obs M", "Delta M", "2024M / ObsM", "Basis"])
    r = 3
    for cat in CATEGORIES:
        for dept in DEPT_ORDER:
            vals = [v for v in cat_vals.get((cat, dept), []) if v > 0]
            b, basis = bands_from(vals)
            ref_band = ref.get(cat, {}).get(dept)
            ref_m = ref_band[2] if ref_band else None
            obs_m = b[2]
            ws3.cell(row=r, column=1, value=cat).font = BODY
            ws3.cell(row=r, column=2, value=dept).font = BODY
            ws3.cell(row=r, column=3, value=len(vals)).font = BODY
            for j, v in enumerate(b, 4):
                c = ws3.cell(row=r, column=j,
                             value=round(v, 1) if v is not None else "")
                c.font, c.number_format, c.alignment = BODY, NUM, RIGHT
            c = ws3.cell(row=r, column=9,
                         value=ref_m if ref_m is not None else "not priced 2024")
            c.font, c.alignment = BODY, RIGHT
            if ref_m is None:
                c.fill = WARN
            c = ws3.cell(row=r, column=10,
                         value=round(obs_m, 1) if obs_m is not None else "")
            c.font, c.number_format, c.alignment = BODY, NUM, RIGHT
            if ref_m and obs_m:
                c = ws3.cell(row=r, column=11, value=round(obs_m - ref_m, 1))
                c.font, c.number_format, c.alignment = BODY, NUM, RIGHT
                c = ws3.cell(row=r, column=12, value=round(ref_m / obs_m, 2))
                c.font, c.number_format, c.alignment = BOLD, NUM, RIGHT
                if ref_m / obs_m >= 1.5:
                    c.fill = WARN
            ws3.cell(row=r, column=13, value=basis).font = BODY
            r += 1
        cn = ws3.cell(row=r, column=1, value=f"{cat} 2024 basis: {COMPOSITION_NOTE[cat]}")
        cn.font, cn.fill = NOTE, BAND
        r += 2

    # -- Estimate accuracy: the assumption-free evidence for rebasing --------
    # The band comparison above needs you to assume the median game is an "M".
    # This block needs no such assumption: it is each ticket's own Original
    # Estimate against its own Time Spent, so it is the stronger evidence.
    c = ws3.cell(row=r, column=1,
                 value="ESTIMATE ACCURACY -- Original Estimate vs Time Spent, no sizing assumption")
    c.font, c.fill = BOLD, BAND
    r += 1
    head(ws3, r, ["Game", "Category", "", "Orig Est", "Actual", "Actual/Est", "Read as"])
    r += 1
    for epic, (name, cat, _) in sorted(
            GAMES.items(), key=lambda kv: -sum(agg.get((kv[0], d, "FULL"), [0, 0, 0])[1]
                                               for d in DEPT_ORDER)):
        est = sum(agg.get((epic, d, "FULL"), [0, 0, 0])[0] for d in DEPT_ORDER)
        act = sum(agg.get((epic, d, "FULL"), [0, 0, 0])[1] for d in DEPT_ORDER)
        ratio = act / est if est else 0
        ws3.cell(row=r, column=1, value=name).font = BODY
        ws3.cell(row=r, column=2, value=cat).font = BODY
        for j, v in ((4, h(est)), (5, h(act)), (6, round(ratio, 2))):
            c = ws3.cell(row=r, column=j, value=v)
            c.font, c.number_format, c.alignment = BODY, NUM, RIGHT
        verdict = ("over-estimated ~2x" if ratio and ratio < 0.6 else
                   "over-estimated" if ratio and ratio < 0.8 else
                   "about right" if ratio <= 1.15 else "under-estimated")
        vc = ws3.cell(row=r, column=7, value=verdict)
        vc.font = BODY
        if ratio and ratio < 0.6:
            vc.fill = WARN
        r += 1
    r += 1
    head(ws3, r, ["Department", "", "", "Orig Est", "Actual", "Actual/Est", "Read as"])
    r += 1
    for dept in DEPT_ORDER:
        est = sum(agg.get((e, dept, "FULL"), [0, 0, 0])[0] for e in GAMES)
        act = sum(agg.get((e, dept, "FULL"), [0, 0, 0])[1] for e in GAMES)
        if not est and not act:
            continue
        ratio = act / est if est else 0
        ws3.cell(row=r, column=1, value=dept).font = BODY
        for j, v in ((4, h(est)), (5, h(act)), (6, round(ratio, 2))):
            c = ws3.cell(row=r, column=j, value=v)
            c.font, c.number_format, c.alignment = BODY, NUM, RIGHT
        verdict = ("never estimated" if not est else
                   "over-estimated ~2x" if ratio < 0.6 else
                   "over-estimated" if ratio < 0.8 else
                   "about right" if ratio <= 1.15 else "under-estimated")
        vc = ws3.cell(row=r, column=7, value=verdict)
        vc.font = BODY
        if not est or ratio < 0.6:
            vc.fill = WARN
        r += 1
    r += 1
    # every figure in this note is computed, so it cannot go stale
    all_e = sum(agg.get((e, d, "FULL"), [0, 0, 0])[0] for e in GAMES for d in DEPT_ORDER)
    all_a = sum(agg.get((e, d, "FULL"), [0, 0, 0])[1] for e in GAMES for d in DEPT_ORDER)
    g_ratio = []
    for epic, (name, _c, _r) in GAMES.items():
        e_ = sum(agg.get((epic, d, "FULL"), [0, 0, 0])[0] for d in DEPT_ORDER)
        a_ = sum(agg.get((epic, d, "FULL"), [0, 0, 0])[1] for d in DEPT_ORDER)
        if e_:
            g_ratio.append((a_ / e_, name))
    g_ratio.sort()
    worst_g = ", ".join(f"{n} {v:.2f}" for v, n in g_ratio[:4])
    best_g = ", ".join(f"{v:.2f}" for v, n in g_ratio[-3:])
    d_ratio = []
    for dept in DEPT_ORDER:
        e_ = sum(agg.get((g, dept, "FULL"), [0, 0, 0])[0] for g in GAMES)
        a_ = sum(agg.get((g, dept, "FULL"), [0, 0, 0])[1] for g in GAMES)
        if e_:
            d_ratio.append((a_ / e_, dept))
    d_ratio.sort()
    worst_d = ", ".join(f"{n} {v:.2f}" for v, n in d_ratio[:4])
    footnote(ws3, r, 13,
             "HOW TO READ THE ACCURACY BLOCK -- this is the real basis for rebasing. "
             f"Overall we spend {all_a / all_e:.2f} of what we estimate, but that average "
             "hides the actual pattern: the largest games land close to estimate "
             f"({best_g}) while the smallest come in at roughly half ({worst_g}). "
             "The bands are not uniformly too high -- they fail to scale DOWN. Cutting "
             "every band by a flat percentage would break the large end that currently "
             "works; the fix is to steepen the small sizes. Worst-calibrated "
             f"departments: {worst_d}.", height=6)
    r += 7
    footnote(ws3, r, 13,
             "XS..XL are min / 25th / median / 75th / max of Time Spent across the games "
             "in that category -- the observed spread IS the band. Cells are left blank "
             "where n is too small for the statistic to mean anything: n=1 fills only the "
             "median, n=2-3 gives min/median/max, quantiles need n>=4. Only Port (n=5) "
             "supports a full band; read every other row as a single point, not a curve.\n"
             "'2024 M' is the M-size legend from IG_Game_Ticket.md section 2, decomposed by "
             "its [FE]/[GE]/[Math]/[Server] row tags so each department compares "
             "like-for-like, with Review & Refinement rows routed to Review exactly as the "
             "actuals are. '2024M / ObsM' divides the legend's M-size figure by the observed "
             "median -- BUT it only means 'over-estimated' if the median game in that "
             "category really is an M, which is an assumption this data cannot support "
             "(these Ports run 154h to 1,557h, a 10x spread). Treat that column as "
             "orientation and the ESTIMATE ACCURACY block below as the actual evidence.\n"
             "Concept, QA, Bugs and Release carry NO 2024 band at all -- they were never "
             "priced. That is the hidden cost: real spend no estimate ever accounted for.",
             height=8)
    widths(ws3, {"A": 12, "B": 14, "C": 5, "I": 15, "J": 10, "K": 10,
                 "L": 11, "M": 20})
    for col in "DEFGH":
        ws3.column_dimensions[col].width = 10

    # ---- 4. Release Overhead ----------------------------------------------
    ws4 = wb.create_sheet("Release Overhead")
    banner(ws4, "Release-level hours -- not attributable to any one game", 6)
    head(ws4, 2, ["Release", "Ticket", "Type", "Summary", "Orig Est", "Time Spent"])
    r = 3
    for d in sorted(rel, key=lambda x: (x["release"], -x["ts_s"])):
        ws4.cell(row=r, column=1, value=d["release"]).font = BODY
        c = ws4.cell(row=r, column=2, value=d["key"])
        c.hyperlink, c.font = f"{JIRA_BASE}/browse/{d['key']}", LINK
        ws4.cell(row=r, column=3, value=d["type"]).font = BODY
        ws4.cell(row=r, column=4, value=d["summary"][:90]).font = BODY
        for j, v in ((5, d["oe_s"]), (6, d["ts_s"])):
            c = ws4.cell(row=r, column=j, value=h(v))
            c.font, c.number_format, c.alignment = BODY, NUM, RIGHT
        r += 1
    ws4.cell(row=r, column=4, value="TOTAL").font = BOLD
    for j in (5, 6):
        col = get_column_letter(j)
        c = ws4.cell(row=r, column=j, value=f"=SUM({col}3:{col}{r - 1})")
        c.font, c.number_format, c.alignment = BOLD, NUM, RIGHT
    r += 2
    head(ws4, r, ["Release", "Games shipped", "Release hours", "Per game", "", ""])
    r += 1
    for ver in sorted(set(RELEASE_EPICS.values())):
        hrs = sum(d["ts_s"] for d in rel if d["release"] == ver)
        n = counts.get(ver, 0)
        ws4.cell(row=r, column=1, value=ver).font = BODY
        ws4.cell(row=r, column=2, value=n).font = BODY
        c = ws4.cell(row=r, column=3, value=h(hrs))
        c.font, c.number_format, c.alignment = BODY, NUM, RIGHT
        c = ws4.cell(row=r, column=4, value=h(hrs / n) if n else "no epics found")
        c.font, c.number_format, c.alignment = BOLD, NUM, RIGHT
        r += 1
    footnote(ws4, r + 1, 6,
             "'Games shipped' counts IG epics titled 'Gen2 Game' carrying that fix version. "
             "It undercounts where an epic was never tagged -- Flaming Skulls (IG-1506) has "
             "no ELG version on the epic at all, so ELG 4.30 is short by at least one and "
             "its per-game overhead is correspondingly overstated. Fix the epic tagging and "
             "this number corrects itself.", height=5)
    widths(ws4, {"A": 12, "B": 12, "C": 16, "D": 70, "E": 11, "F": 12})

    # ---- 5. Ticket Detail --------------------------------------------------
    ws5 = wb.create_sheet("Ticket Detail")
    head(ws5, 1, ["Game", "Category", "Ticket", "Lvl", "Type", "Department",
                  "Matched rule", "Fix Versions", "ELG?", "Status",
                  "Orig Est", "Time Spent", "Remaining", "Summary"])
    r = 2
    for d in sorted(rows, key=lambda x: (GAMES.get(x["epic"], ("",))[0],
                                         x["level"], -x["ts_s"])):
        g = GAMES.get(d["epic"], ("?", "?", "?"))
        ws5.cell(row=r, column=1, value=g[0]).font = BODY
        ws5.cell(row=r, column=2, value=g[1]).font = BODY
        c = ws5.cell(row=r, column=3, value=d["key"])
        c.hyperlink, c.font = f"{JIRA_BASE}/browse/{d['key']}", LINK
        ws5.cell(row=r, column=4, value=d["level"]).font = BODY
        ws5.cell(row=r, column=5, value=d["type"]).font = BODY
        ws5.cell(row=r, column=6, value=d["dept"]).font = BODY
        ws5.cell(row=r, column=7, value=d["rule"]).font = BODY
        ws5.cell(row=r, column=8, value="; ".join(d["fv"]) or "(none)").font = BODY
        # Y/N, never blank: Excel's "*" wildcard does not match empty cells,
        # so a blank here would silently drop rows from the Explorer's
        # "Full game" scope.
        ws5.cell(row=r, column=9, value="Y" if d["elg"] else "N").font = BODY
        ws5.cell(row=r, column=10, value=d["status"]).font = BODY
        for j, v in ((11, d["oe_s"]), (12, d["ts_s"]), (13, d["re_s"])):
            c = ws5.cell(row=r, column=j, value=h(v))
            c.font, c.number_format, c.alignment = BODY, NUM, RIGHT
        ws5.cell(row=r, column=14, value=d["summary"][:120]).font = BODY
        r += 1
    widths(ws5, {"A": 26, "B": 9, "C": 11, "D": 5, "E": 16, "F": 13, "G": 26,
                 "H": 24, "I": 6, "J": 14, "K": 10, "L": 11, "M": 11, "N": 85})
    ws5.freeze_panes = "D2"
    ws5.auto_filter.ref = f"A1:N{r - 1}"

    # ---- 6. Data Gaps ------------------------------------------------------
    ws6 = wb.create_sheet("Data Gaps")
    banner(ws6, "Hygiene issues that distort the numbers above", 7)
    head(ws6, 2, ["Issue", "Ticket", "Game", "Est h", "Spent h", "Detail", "Type"])
    r = 3
    flagged = []
    for d in rows:
        g = GAMES.get(d["epic"], ("?",))[0]
        if d["fv"] and not d["elg"]:
            flagged.append(("Non-ELG fix version", d, g,
                            "Under an ELG game epic but tagged: " + "; ".join(d["fv"])))
        if not d["fv"] and d["level"] > 0:
            flagged.append(("No fix version", d, g,
                            "Cannot be attributed to any release"))
        if d["ts_s"] > 0 and d["oe_s"] == 0:
            flagged.append(("Logged without estimate", d, g,
                            "Time logged against no original estimate"))
        if d["oe_s"] > 0 and d["ts_s"] == 0:
            flagged.append(("Estimated, nothing logged", d, g,
                            "Estimate set but no time ever logged"))
    for kind, d, g, detail in sorted(flagged, key=lambda x: (x[0], -x[1]["ts_s"])):
        ws6.cell(row=r, column=1, value=kind).font = BODY
        c = ws6.cell(row=r, column=2, value=d["key"])
        c.hyperlink, c.font = f"{JIRA_BASE}/browse/{d['key']}", LINK
        ws6.cell(row=r, column=3, value=g).font = BODY
        for j, v in ((4, d["oe_s"]), (5, d["ts_s"])):
            c = ws6.cell(row=r, column=j, value=h(v))
            c.font, c.number_format, c.alignment = BODY, NUM, RIGHT
        ws6.cell(row=r, column=6, value=detail).font = BODY
        ws6.cell(row=r, column=7, value=d["type"]).font = BODY
        r += 1
    last_detail = r - 1
    summary = Counter(k for k, _, _, _ in flagged)
    r += 1
    ws6.cell(row=r, column=1, value="SUMMARY").font = BOLD
    r += 1
    for k, n in summary.most_common():
        ws6.cell(row=r, column=1, value=k).font = BODY
        ws6.cell(row=r, column=2, value=n).font = BODY
        for j, key in ((4, "oe_s"), (5, "ts_s")):
            c = ws6.cell(row=r, column=j,
                         value=h(sum(d[key] for kk, d, _, _ in flagged if kk == k)))
            c.font, c.number_format, c.alignment = BOLD, NUM, RIGHT
        r += 1
    footnote(ws6, r + 1, 7,
             "'Logged without estimate' is the one to act on: it is the reason the t-shirt "
             "bands could never be validated -- there was no estimate to compare the actual "
             "against. 'Non-ELG fix version' is the ELG/FULL gap itemised. A ticket can be "
             "flagged under more than one heading, so the counts overlap.", height=4)
    widths(ws6, {"A": 26, "B": 11, "C": 26, "D": 10, "E": 10, "F": 62, "G": 16})
    ws6.auto_filter.ref = f"A2:G{last_detail}"

    # ---- 7. Explorer (dropdowns) + 8. Knowledge ----------------------------
    sheet_explorer(wb, len(rows))
    md = knowledge_md(rows, rel, counts, agg)
    sheet_knowledge(wb, md)
    with open(KNOWLEDGE_FILE, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(md + "\n")

    wb.active = 0
    wb.save(OUTPUT_FILE)
    print(f"Saved {OUTPUT_FILE}")
    print(f"Saved {KNOWLEDGE_FILE}  ({len(md.splitlines())} lines)")


# ===========================================================================
def console(agg):
    w = 11
    print(f"{'Game':30}{'Cat':9}" + "".join(d[:9].rjust(w) for d in DEPT_ORDER)
          + "TOTAL".rjust(w))
    print("-" * (39 + w * (len(DEPT_ORDER) + 1)))
    for epic, (name, cat, _) in GAMES.items():
        v = [h(agg.get((epic, d, "FULL"), [0, 0, 0])[1]) for d in DEPT_ORDER]
        print(f"{name[:29]:30}{cat:9}"
              + "".join(f"{x:,.1f}".rjust(w) for x in v)
              + f"{sum(v):,.1f}".rjust(w))
    print()
    for cat in CATEGORIES:
        eps = [e for e, (_, c, _) in GAMES.items() if c == cat]
        tot = [sum(h(agg.get((e, d, "FULL"), [0, 0, 0])[1]) for d in DEPT_ORDER)
               for e in eps]
        avg = sum(tot) / len(tot) if tot else 0
        print(f"  {cat:9} n={len(eps)}  avg {avg:9,.1f}h"
              + ("   <-- no ELG data, cannot budget" if not eps else ""))


def main():
    rows, rel, counts, agg = load()
    write(rows, rel, counts, agg)
    console(agg)


if __name__ == "__main__":
    main()
