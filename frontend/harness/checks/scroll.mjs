// Item 6: the wizard must scroll wherever the pointer is, not only mid-page.
//
// The only scroll container on the step is `.opt-wizard-body`, and it used to
// sit inside `.page`'s gutter — so the wheel did nothing in the 44px at either
// edge of a 1440px window, and in far more than that on a wide one, where
// `.page` is centred inside `--page-max`. `.main` has `overflow-y: auto` but
// nothing to scroll on this route, so the event had nowhere to go.
//
// Measured before the fix, 1440px wide, on the Settings step:
//   x=8    scrollTop 0 -> 0
//   x=720  scrollTop 0 -> 600
import { chromium } from '/opt/node22/lib/node_modules/playwright/index.mjs';

const results = [];
const ok = (label, pass, detail = '') => {
  results.push(pass);
  console.log(`${pass ? 'PASS' : 'FAIL'}  ${label}  ${detail}`);
};

const b = await chromium.launch();
for (const width of [1440, 2200]) {
  const p = await b.newPage({ viewport: { width, height: 900 } });
  p.on('pageerror', (e) => { console.log('PAGEERROR', e.message); process.exitCode = 1; });
  await p.goto('http://localhost:5199/harness/index.html');
  await p.waitForTimeout(1200);
  const next = async () => {
    await p.getByRole('button', { name: 'Continue' }).click();
    await p.waitForTimeout(900);
  };
  await next();
  for (const s of await p.locator('.opt-source').all()) await s.click();
  await p.waitForTimeout(1200);
  await next();                                       // -> skill
  await next();                                       // -> split
  await p.locator('.opt-col').first().locator('.opt-row').first()
    .locator('button[aria-label^="Move to validation"]').click();
  await p.waitForTimeout(300);
  await next();                                       // -> settings, the long one

  const scroller = p.locator('.opt-wizard-body');
  const room = await scroller.evaluate((el) => el.scrollHeight - el.clientHeight);
  ok(`${width}px: the step is long enough to be worth scrolling`, room > 300, `${room}px of it`);

  for (const [name, x] of [['left edge', 6], ['middle', Math.round(width / 2)],
                           ['right edge', width - 6]]) {
    await scroller.evaluate((el) => { el.scrollTop = 0; });
    await p.waitForTimeout(150);
    await p.mouse.move(x, 500);
    await p.mouse.wheel(0, 400);
    await p.waitForTimeout(300);
    const after = await scroller.evaluate((el) => el.scrollTop);
    ok(`${width}px: the wheel scrolls at the ${name} (x=${x})`, after > 0, `scrollTop -> ${after}`);
  }
  await p.close();
}
if (!results.every(Boolean)) process.exitCode = 1;
await b.close();
