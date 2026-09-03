// Item 5: the wheel must not edit the number under the cursor.
//
// The trigger is narrower than it first looks, and getting it wrong produces a
// check that passes against the bug. Chromium hands the wheel to a focused
// `<input type="number">` as a spinner only when the scroll container cannot
// move in that direction — at the top of the step scrolling up, or at the
// bottom scrolling down. Anywhere in between the scroll is consumed and the
// value is never touched, so a check that scrolls from the middle passes with
// or without the fix and tests nothing.
//
// So this drives both cases:
//   blocked      scrollTop 0, wheel up. Before: 3 -> 4. After: 3 -> 3.
//   scrollable   mid-step, wheel up. The page must still scroll, and the value
//                must still be left alone (true before too — this is the
//                regression guard on the fix, not a reproduction of the bug).
import { chromium } from '/opt/node22/lib/node_modules/playwright/index.mjs';

const b = await chromium.launch();
const p = await b.newPage({ viewport: { width: 1440, height: 900 } });
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
await next();                                              // -> skill
await next();                                              // -> split
await p.locator('.opt-col').first().locator('.opt-row').first()
  .locator('button[aria-label="Move to validation"]').click();
await p.waitForTimeout(300);
await next();                                              // -> settings
await next();                                              // -> review

const scroller = p.locator('.opt-wizard-body');
const num = p.locator('.opt-wizard-body input[type="number"]').first();

const results = [];
const ok = (label, pass, detail) => {
  results.push(pass);
  console.log(`${pass ? 'PASS' : 'FAIL'}  ${label}  ${detail}`);
};

async function wheelOverField(deltaY, ticks = 3) {
  const box = await num.boundingBox();
  if (box.y < 0 || box.y > 900) throw new Error(`field is off-screen at y=${box.y}`);
  await p.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  for (let i = 0; i < ticks; i++) {
    await p.mouse.wheel(0, deltaY);
    await p.waitForTimeout(60);
  }
  await p.waitForTimeout(250);
}

// --- blocked: the case that actually reproduced the bug ---------------------
await num.click();                                   // focus, as a user would
await scroller.evaluate((el) => { el.scrollTop = 0; });
await p.waitForTimeout(200);
const v0 = await num.inputValue();
await wheelOverField(-100);
ok('at the top of the step, a wheel up leaves the value alone',
   (await num.inputValue()) === v0, `${v0} -> ${await num.inputValue()}`);

// --- scrollable: the fix must not have broken ordinary scrolling ------------
await num.evaluate((el) => {
  const box = el.getBoundingClientRect();
  el.closest('.opt-wizard-body').scrollTop += box.top - 300;
});
await p.waitForTimeout(200);
await num.evaluate((el) => el.focus({ preventScroll: true }));
const v1 = await num.inputValue();
const s1 = await scroller.evaluate((el) => el.scrollTop);
await wheelOverField(-100);
const s2 = await scroller.evaluate((el) => el.scrollTop);
ok('mid-step, the wheel still scrolls', s2 < s1, `scrollTop ${s1} -> ${s2}`);
ok('mid-step, the value is still left alone',
   (await num.inputValue()) === v1, `${v1} -> ${await num.inputValue()}`);

// --- the keyboard is the only thing that may change it ----------------------
await num.click();
await num.fill('7');
ok('typing still sets it', (await num.inputValue()) === '7', `typed -> ${await num.inputValue()}`);

if (!results.every(Boolean)) process.exitCode = 1;
await b.close();
