/* ============================================================
   Site navigation: one slide-out list of every PMO dashboard, on every page.

   Deploy Pages adds <script src="site-nav.js" defer> to each published page
   (tools/build_site.py), so no page template carries its own copy. To add,
   rename or regroup a dashboard, edit GROUPS below. That is the only list.

   The drawer renders in a shadow root, so page CSS can't restyle it and its
   CSS can't leak into the page. It is hidden until the left-edge tab is
   clicked. Esc, the x button or a click outside close it.
   ============================================================ */
(function () {
  "use strict";
  if (window.__pongSiteNav) return;
  window.__pongSiteNav = true;

  var GROUPS = [
    { title: "Overview", items: [
      { href: "index.html",          label: "Exec overview",   desc: "Every release's health and progress" },
      { href: "ceo-summary.html",    label: "CEO summary",     desc: "Game reviews and decisions for the CEO" },
    ]},
    // One group per space: its releases, then the games and features inside them.
    { title: "V2", items: [
      { href: "v2-timeline.html",                 label: "Releases",  desc: "Dev and QA forecast per release" },
      { href: "game-pipeline.html?project=v2",    label: "Games",     desc: "Every game's lifecycle" },
      { href: "game-pipeline.html?project=v2f",   label: "Features",  desc: "Every feature's lifecycle" },
      { href: "roadmap-v2.html",                  label: "Roadmap",   desc: "Dated releases and go-lives" },
    ]},
    { title: "iGaming", items: [
      { href: "igaming-timeline.html",            label: "Releases",  desc: "Dev and QA forecast per release" },
      { href: "game-pipeline.html?project=ig",    label: "Games",     desc: "Every game's lifecycle" },
      { href: "game-pipeline.html?project=igf",   label: "Features",  desc: "Every feature's lifecycle" },
      { href: "roadmap-igaming.html",             label: "Roadmap",   desc: "Dated releases and go-lives" },
    ]},
    { title: "Cloud Services", items: [
      { href: "css-timeline.html",                label: "Releases",  desc: "Dev and QA forecast per release" },
      { href: "game-pipeline.html?project=cssf",  label: "Features",  desc: "Every feature's lifecycle" },
      { href: "roadmap-css.html",                 label: "Roadmap",   desc: "Dated releases" },
    ]},
    { title: "Costs", items: [
      { href: "elg-cost-model.html", label: "ELG cost model",  desc: "Cost per ELG game from Jira hours" },
      { href: "netnew-vs-port.html", label: "Net-new vs port", desc: "What new games cost against ports" },
    ]},
    { title: "Teams", items: [
      { href: "people.html",                label: "People",      desc: "Who works on what, week by week" },
      { href: "team-board.html",            label: "Team board",  desc: "Sprint planning calendar, Math and Art" },
      { href: "sprint-load/dashboard.html", label: "Sprint load", desc: "Remaining work against capacity" },
    ]},
  ];

  // Links resolve against this script's own folder (the site root), so the
  // drawer works from pages at any depth (e.g. sprint-load/dashboard.html).
  var script = document.currentScript;
  var BASE = script && script.src ? script.src.replace(/[^/]*$/, "") : "";

  var CSS = [
    ":host{all:initial}",
    "*{box-sizing:border-box}",
    ".wrap{font-family:'IBM Plex Sans',system-ui,-apple-system,'Segoe UI',Roboto,sans-serif;",
    "  color:#1a2035;font-size:14px;line-height:1.35;-webkit-font-smoothing:antialiased}",
    // left-edge tab
    ".tab{position:fixed;left:0;top:14px;z-index:9000;width:22px;height:46px;padding:0;",
    "  display:flex;align-items:center;justify-content:center;cursor:pointer;",
    "  background:#fff;border:1px solid #c8cedf;border-left:0;border-radius:0 10px 10px 0;",
    "  box-shadow:0 2px 8px rgba(26,32,53,.10);color:#6b7a99;transition:width .15s,color .15s,background .15s}",
    ".tab:hover,.tab:focus-visible{width:28px;color:#1a2035;background:#f0f3f9;outline:none}",
    ".tab svg{width:14px;height:14px;display:block}",
    // scrim + drawer
    ".scrim{position:fixed;inset:0;z-index:9001;background:rgba(26,32,53,.28);opacity:0;",
    "  pointer-events:none;transition:opacity .18s}",
    ".drawer{position:fixed;top:0;bottom:0;left:0;z-index:9002;width:288px;max-width:86vw;",
    "  display:flex;flex-direction:column;background:#fff;border-right:1px solid #dde2ee;",
    "  box-shadow:6px 0 24px rgba(26,32,53,.14);transform:translateX(-102%);",
    "  transition:transform .2s ease;visibility:hidden}",
    ".open .scrim{opacity:1;pointer-events:auto}",
    ".open .drawer{transform:none;visibility:visible}",
    ".open .tab{visibility:hidden}",
    ".head{display:flex;align-items:center;gap:10px;padding:16px 14px 12px 18px;",
    "  border-bottom:1px solid #e8ecf4}",
    ".brand{flex:1;min-width:0}",
    ".brand b{display:block;font-size:15px;font-weight:600;letter-spacing:-.01em}",
    ".brand span{display:block;font-size:11.5px;color:#6b7a99;margin-top:2px}",
    ".close{width:30px;height:30px;border:1px solid #dde2ee;border-radius:8px;background:#fff;",
    "  color:#6b7a99;font-size:18px;line-height:1;cursor:pointer;display:flex;align-items:center;justify-content:center}",
    ".close:hover,.close:focus-visible{background:#f0f3f9;color:#1a2035;outline:none}",
    ".list{flex:1;overflow-y:auto;padding:6px 10px 16px}",
    ".group{margin-top:12px}",
    ".gtitle{font-size:10.5px;font-weight:600;letter-spacing:.08em;text-transform:uppercase;",
    "  color:#8a94ad;padding:0 10px 4px}",
    "a{display:block;text-decoration:none;color:inherit;padding:7px 10px 7px 12px;margin:1px 0;",
    "  border-radius:8px;border-left:3px solid transparent}",
    "a:hover,a:focus-visible{background:#f0f3f9;outline:none}",
    "a .l{display:block;font-size:13.5px;font-weight:500}",
    "a .d{display:block;font-size:11.5px;color:#6b7a99;margin-top:1px}",
    "a.cur{background:#eef3ff;border-left-color:#2563eb}",
    "a.cur .l{font-weight:600;color:#1d4ed8}",
    ".foot{padding:10px 18px 14px;border-top:1px solid #e8ecf4;font-size:11px;color:#8a94ad}",
    "@media print{.wrap{display:none}}",
    "@media (prefers-reduced-motion:reduce){.drawer,.scrim,.tab{transition:none}}",
  ].join("\n");

  function esc(s) {
    return String(s).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }

  // "…/pong-exec-dashboard/" and "…/index.html" are the same page, and a
  // Roadmap's Planning sub-page (roadmap-css-planning.html) counts as that Roadmap.
  function norm(path) {
    return path.replace(/\/index\.html$/, "/").replace(/(\/roadmap-[a-z0-9]+)-planning\.html$/, "$1.html");
  }
  function isCurrent(href) {
    var u = new URL(href, location.href);
    if (norm(u.pathname) !== norm(location.pathname)) return false;
    var want = u.searchParams.get("project");
    if (!want) return true;
    var have = new URLSearchParams(location.search).get("project");
    if (!have) { try { have = localStorage.getItem("gp_active_project"); } catch (e) {} }
    return want === (have || "v2");
  }

  function mount() {
    var here = norm(location.pathname);
    var host = document.createElement("div");
    host.id = "pong-site-nav";
    var root = host.attachShadow ? host.attachShadow({ mode: "open" }) : host;

    var groups = GROUPS.map(function (g) {
      var links = g.items.map(function (it) {
        var url = new URL(it.href, BASE || location.href);
        var cur = isCurrent(url.href);
        return '<a href="' + esc(url.href) + '"' + (cur ? ' class="cur" aria-current="page"' : "") + ">" +
          '<span class="l">' + esc(it.label) + '</span><span class="d">' + esc(it.desc) + "</span></a>";
      }).join("");
      return '<div class="group"><div class="gtitle">' + esc(g.title) + "</div>" + links + "</div>";
    }).join("");

    root.innerHTML =
      "<style>" + CSS + "</style>" +
      '<div class="wrap">' +
      '<button class="tab" type="button" aria-label="All dashboards" title="All dashboards"' +
      ' aria-expanded="false" aria-controls="drawer">' +
      '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" aria-hidden="true">' +
      '<path d="M2.5 4h11M2.5 8h11M2.5 12h11"/></svg></button>' +
      '<div class="scrim"></div>' +
      '<nav class="drawer" id="drawer" aria-label="All dashboards">' +
      '<div class="head"><div class="brand"><b>Pong PMO dashboards</b><span>Live from Jira, refreshed daily</span></div>' +
      '<button class="close" type="button" aria-label="Close">&times;</button></div>' +
      '<div class="list">' + groups + "</div>" +
      '<div class="foot">Pong Game Studios</div>' +
      "</nav></div>";

    var wrap = root.querySelector(".wrap");
    var tab = root.querySelector(".tab");
    var drawer = root.querySelector(".drawer");

    function markCurrent() {
      root.querySelectorAll("a").forEach(function (a) {
        var cur = isCurrent(a.href);
        a.classList.toggle("cur", cur);
        if (cur) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
      });
    }
    function setOpen(open) {
      if (open) markCurrent();
      wrap.classList.toggle("open", open);
      tab.setAttribute("aria-expanded", open ? "true" : "false");
      if (open) {
        var first = root.querySelector("a.cur") || root.querySelector("a");
        setTimeout(function () { first && first.focus(); }, 50);
      } else {
        tab.focus();
      }
    }
    tab.addEventListener("click", function () { setOpen(true); });
    root.querySelector(".close").addEventListener("click", function () { setOpen(false); });
    root.querySelector(".scrim").addEventListener("click", function () { setOpen(false); });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && wrap.classList.contains("open")) setOpen(false);
    });
    drawer.addEventListener("keydown", function (e) {
      if (e.key === "Escape") setOpen(false);
    });

    document.body.appendChild(host);
  }

  if (document.body) mount();
  else document.addEventListener("DOMContentLoaded", mount);
})();
