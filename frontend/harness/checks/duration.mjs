// Item 2: the run header must not move while the run's timer ticks.
//
// `.opt-runfacts` is a fixed grid — `repeat(auto-fit, minmax(120px, 1fr))` — so
// the Started fact's live subtitle cannot widen its cell. It wraps inside it
// instead, and the whole row grows, pushing the chart and the step table down.
//
// Measured before the fix, `.opt-runfacts` height in px:
//
//   width   9s     59s     1m01s   1h01m
//   520     150    150     150     166.5
//   620     150    166.5   166.5   166.5
//   760     82     98.5    98.5    98.5
//   820     82     82      98.5    98.5
//
// After, every row must be flat: one height per width, whatever the elapsed
// time. The widths below are the ones that moved, plus two that did not.
import { chromium } from '/opt/node22/lib/node_modules/playwright/index.mjs';

const WIDTHS = [520, 620, 700, 760, 820, 900];
const ELAPSED = [9, 59, 61, 599, 3661, 36061];

const b = await chromium.launch();
const p = await b.newPage({ viewport: { width: 1200, height: 600 } });
p.on('pageerror', (e) => { console.log('PAGEERROR', e.message); process.exitCode = 1; });

let bad = 0;
for (const w of WIDTHS) {
  const seen = [];
  for (const secs of ELAPSED) {
    await p.goto(`http://localhost:5199/harness/index.html?view=duration&secs=${secs}&w=${w}`);
    await p.waitForTimeout(350);
    seen.push({
      secs,
      h: await p.evaluate(() => document.querySelector('.opt-runfacts').getBoundingClientRect().height),
      text: await p.evaluate(() => {
        const subs = document.querySelectorAll('.opt-fact-sub');
        return subs[subs.length - 1].textContent;
      }),
    });
  }
  const heights = [...new Set(seen.map((x) => x.h))];
  const flat = heights.length === 1;
  if (!flat) bad += 1;
  console.log(`${flat ? 'PASS' : 'FAIL'}  width ${w}  height ${heights.join(' / ')}`);
  if (!flat) for (const x of seen) console.log(`        ${String(x.secs).padStart(5)}s  ${x.h}  ${x.text}`);
}

// And the strings themselves, so a pass above cannot be a row that is flat
// because the number stopped changing at all.
await p.goto('http://localhost:5199/harness/index.html?view=duration&secs=9&w=820');
await p.waitForTimeout(300);
const texts = [];
for (const secs of ELAPSED) {
  await p.goto(`http://localhost:5199/harness/index.html?view=duration&secs=${secs}&w=820`);
  await p.waitForTimeout(300);
  texts.push(await p.evaluate(() => {
    const subs = document.querySelectorAll('.opt-fact-sub');
    return subs[subs.length - 1].textContent;
  }));
}
console.log('       labels:', texts.join('  |  '));
const distinct = new Set(texts).size;
console.log(`${distinct >= 4 ? 'PASS' : 'FAIL'}  the label still tracks the elapsed time  ${distinct} distinct`);
if (distinct < 4) bad += 1;

if (bad) process.exitCode = 1;
await b.close();
