#!/usr/bin/env node
// Smoke test for a rendered dashboard HTML file. Used by the V2 + iGaming
// workflows after the Python builder writes the HTML and BEFORE the commit
// step, so a broken JS payload can't land on main.
//
// Catches the bug classes that have actually shipped:
//   • script-init ReferenceError (the page is blank below the header)
//   • mid-buildRows() throw (only the first N rows render, later code fails)
//   • silently broken event handlers (rows render, but Edit Plan / What-If
//     button clicks are no-ops because the listener never attached)
//
// Usage:  node tools/smoke-test.js <file1.html> [<file2.html> ...]
// Exits 0 on success, non-zero on any failure.

const fs = require('fs');
const { JSDOM, VirtualConsole } = require('jsdom');

const MIN_ROWS = 5;       // far below 14 (V2) / 18 (iGaming) — flags any major data loss
const SETTLE_MS = 800;    // give the script's setTimeout-driven init a beat
const CLICK_MS  = 200;    // small wait after each click for sync DOM updates

async function check(file) {
  const errs = [];
  const vc = new VirtualConsole();
  vc.on('jsdomError', e => {
    const msg = (e?.detail?.message || e?.detail?.stack || String(e)).slice(0, 300);
    errs.push(msg);
  });

  const dom = new JSDOM(fs.readFileSync(file, 'utf-8'), {
    // Real URL so localStorage works (opaque-origin throws SecurityError).
    url: 'https://mayankyadav1994.github.io/pong-exec-dashboard/',
    runScripts: 'dangerously',
    pretendToBeVisual: true,
    virtualConsole: vc,
  });

  await new Promise(r => setTimeout(r, SETTLE_MS));
  const d = dom.window.document;
  const failures = [];

  // (1) initial render produced rows
  const rows = d.getElementById('rows');
  const rowCount = rows?.children?.length || 0;
  if (rowCount < MIN_ROWS) {
    failures.push(`expected ≥${MIN_ROWS} rows in #rows, got ${rowCount}`);
  }

  // (2) Edit Plan button opens the scenario panel
  const editBtn = d.getElementById('editPlanBtn');
  if (!editBtn) failures.push(`#editPlanBtn not found in DOM`);
  else {
    editBtn.click();
    await new Promise(r => setTimeout(r, CLICK_MS));
    const panel = d.getElementById('scenarioPanel');
    if (!panel?.classList?.contains('open')) {
      failures.push(`clicking #editPlanBtn did not add .open to #scenarioPanel`);
    }
    // Confirm the Plan Editor actually populated FV rows
    const fvRowsInEditor = d.querySelectorAll('.sp-fv-row')?.length || 0;
    if (fvRowsInEditor === 0) {
      failures.push(`Plan Editor panel opened but rendered 0 .sp-fv-row entries`);
    }
    // Close via overlay so the next click starts from a clean slate
    d.getElementById('spOverlay')?.click();
    await new Promise(r => setTimeout(r, CLICK_MS));
  }

  // (3) What-If button also opens the panel
  const whatIfBtn = d.getElementById('openWhatIfBtn');
  if (!whatIfBtn) failures.push(`#openWhatIfBtn not found in DOM`);
  else {
    whatIfBtn.click();
    await new Promise(r => setTimeout(r, CLICK_MS));
    const panel = d.getElementById('scenarioPanel');
    if (!panel?.classList?.contains('open')) {
      failures.push(`clicking #openWhatIfBtn did not add .open to #scenarioPanel`);
    }
  }

  // (4) No uncaught script errors anywhere along the way. localStorage-from-
  // opaque-origin is the one false positive (jsdom default) — we set a real
  // URL above so it shouldn't fire, but filter just in case.
  const realErrs = errs.filter(e => !/opaque origin/i.test(e));
  if (realErrs.length > 0) {
    failures.push(`${realErrs.length} uncaught JS error(s): ${realErrs.slice(0, 3).join(' | ')}`);
  }

  if (failures.length > 0) {
    console.log(`❌ ${file}: ${failures.length} failure(s)`);
    failures.forEach(f => console.log(`     · ${f}`));
    return false;
  }
  console.log(`✅ ${file}: ${rowCount} rows · Edit Plan opens · What-If opens · 0 errors`);
  return true;
}

(async () => {
  const files = process.argv.slice(2);
  if (files.length === 0) {
    console.log('usage: node tools/smoke-test.js <file1.html> [<file2.html> ...]');
    process.exit(2);
  }
  let allOk = true;
  for (const f of files) {
    const ok = await check(f);
    if (!ok) allOk = false;
  }
  process.exit(allOk ? 0 : 1);
})();
