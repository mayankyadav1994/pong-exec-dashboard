#!/usr/bin/env node
// Smoke test for the rendered People page (people.html).
// Fails on any uncaught JS error, too few people rows, or when the view
// toggle / expand / tray / team filter / zoom controls throw or render nothing.
// Usage: node tools/smoke-people.js people.html

const fs = require('fs');
const { JSDOM, VirtualConsole } = require('jsdom');

const MIN_PEOPLE = 10;

async function check(file) {
  const errs = [];
  const vc = new VirtualConsole();
  vc.on('jsdomError', e => errs.push((e?.detail?.message || e?.message || String(e)).slice(0, 300)));
  const dom = new JSDOM(fs.readFileSync(file, 'utf-8'), {
    url: 'https://mayankyadav1994.github.io/pong-exec-dashboard/',
    runScripts: 'dangerously', pretendToBeVisual: true, virtualConsole: vc,
  });
  const tick = (ms = 30) => new Promise(r => setTimeout(r, ms));
  await tick(400);
  const d = dom.window.document, fails = [];

  const rows = d.querySelectorAll('.row[data-p]').length;
  const segs = d.querySelectorAll('.seg-b').length;
  if (rows < MIN_PEOPLE) fails.push(`expected ≥${MIN_PEOPLE} person rows, got ${rows}`);
  if (!segs) fails.push('no project segments drawn');

  const caret = d.querySelector('.caret[data-open]');
  if (caret) { caret.click(); await tick(); if (!d.querySelector('.subrow')) fails.push('expanding a person showed no project lines'); }
  const tray = d.querySelector('.tray-btn');
  if (tray) { tray.click(); await tick(); if (!d.querySelector('.tray-row')) fails.push('tray button did not open the tray'); }
  const team = d.querySelectorAll('#teamSeg button')[1];
  if (team) { team.click(); await tick(); }
  d.querySelector('#teamSeg button')?.click();

  d.querySelector('#viewSeg button[data-view="project"]').click();
  await tick();
  const items = d.querySelectorAll('.row').length;
  if (!items) fails.push('project view rendered no rows');
  d.querySelector('.caret[data-open]')?.click();
  await tick();
  d.querySelector('#viewSeg button[data-view="person"]').click();

  d.getElementById('zoomIn').click();
  d.getElementById('zoomOut').click();
  d.getElementById('expandAll').click();

  if (errs.length) fails.push(...errs.map(e => 'JS error: ' + e));
  console.log(`${fails.length ? '✗' : '✓'} ${file}: ${rows} people, ${segs} segments, ${items} project rows`);
  fails.forEach(f => console.log('   - ' + f));
  dom.window.close();
  return fails.length === 0;
}

(async () => {
  const files = process.argv.slice(2);
  if (!files.length) { console.error('usage: smoke-people.js <file.html>'); process.exit(2); }
  let ok = true;
  for (const f of files) ok = (await check(f)) && ok;
  process.exit(ok ? 0 : 1);
})();
