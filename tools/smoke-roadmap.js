#!/usr/bin/env node
// Smoke test for the rendered Roadmap pages (roadmap-*.html).
// Fails on any uncaught JS error, on too few release cards, or when the
// expand / tray / zoom controls throw.
// Usage: node tools/smoke-roadmap.js roadmap-igaming.html roadmap-v2.html roadmap-css.html

const fs = require('fs');
const { JSDOM, VirtualConsole } = require('jsdom');

const MIN_CARDS = 3;

async function check(file) {
  const errs = [];
  const vc = new VirtualConsole();
  vc.on('jsdomError', e => errs.push((e?.detail?.message || e?.message || String(e)).slice(0, 300)));
  const dom = new JSDOM(fs.readFileSync(file, 'utf-8'), {
    url: 'https://mayankyadav1994.github.io/pong-exec-dashboard/',
    runScripts: 'dangerously', pretendToBeVisual: true, virtualConsole: vc,
  });
  await new Promise(r => setTimeout(r, 400));
  const d = dom.window.document, fails = [];

  const cards = d.querySelectorAll('.tcard').length;
  const lanes = d.querySelectorAll('.lane').length;
  const minCards = d.body.dataset.kind === 'planning' ? 0 : MIN_CARDS;
  if (cards < minCards) fails.push(`expected ≥${minCards} .tcard, got ${cards}`);
  if (!lanes) fails.push('no lanes rendered');

  const more = d.querySelector('.more');
  if (more) { more.click(); await new Promise(r => setTimeout(r, 50)); }
  const tray = d.querySelector('.tray-btn');
  if (tray) {
    tray.click();
    if (!d.querySelector('.tray.open')) fails.push('tray button did not open its tray');
  }
  d.getElementById('zoomIn').click();
  d.getElementById('zoomOut').click();

  if (errs.length) fails.push(...errs.map(e => 'JS error: ' + e));
  const pubs = d.querySelectorAll('.pub').length;
  console.log(`${fails.length ? '✗' : '✓'} ${file}: ${lanes} lanes, ${cards} cards, ${pubs} go-lives`);
  fails.forEach(f => console.log('   - ' + f));
  dom.window.close();
  return fails.length === 0;
}

(async () => {
  const files = process.argv.slice(2);
  if (!files.length) { console.error('usage: smoke-roadmap.js <file.html> ...'); process.exit(2); }
  let ok = true;
  for (const f of files) ok = (await check(f)) && ok;
  process.exit(ok ? 0 : 1);
})();
