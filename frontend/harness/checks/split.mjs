// Items 3 and 4: the excluded drawer shows what the columns show, the columns
// can be edited in bulk, and every edit is undoable.
import { chromium } from '/opt/node22/lib/node_modules/playwright/index.mjs';

const OUT = process.env.SHOTS || '/tmp/shots';
const b = await chromium.launch();
const p = await b.newPage({ viewport: { width: 1440, height: 1000 } });
p.on('pageerror', (e) => { console.log('PAGEERROR', e.message); process.exitCode = 1; });

const results = [];
const ok = (label, pass, detail = '') => {
  results.push(pass);
  console.log(`${pass ? 'PASS' : 'FAIL'}  ${label}  ${detail}`);
};

await p.goto('http://localhost:5199/harness/index.html');
await p.waitForTimeout(1200);
const next = async () => {
  await p.getByRole('button', { name: 'Continue' }).click();
  await p.waitForTimeout(900);
};
await next();
for (const s of await p.locator('.opt-source').all()) await s.click();
await p.waitForTimeout(1200);
await next();                                                  // -> skill
await next();                                                  // -> split

const counts = () => p.locator('.opt-split-counts').textContent();
const col = (i) => p.locator('.opt-col').nth(i);
const before = await counts();

// --- item 4: copy the whole training column into validation ----------------
await col(0).locator('.opt-col-bulk button').nth(1).click();    // "also add all"
await p.waitForTimeout(400);
const copied = await counts();
ok('copy-all puts every training question into validation',
   /10 training/.test(copied) && /14 validation/.test(copied) && /10 in both/.test(copied),
   copied);
ok('and the editor says validation is no longer held out',
   (await p.locator('.opt-issue.is-warning .opt-issue-title').allTextContents())
     .some((t) => /not fully held out/i.test(t)));

// --- undo it ---------------------------------------------------------------
await p.getByRole('button', { name: 'Undo' }).click();
await p.waitForTimeout(400);
ok('undo puts the split back exactly as it was', (await counts()) === before,
   `${copied} -> ${await counts()}`);

// --- undo covers ordinary row edits too, and by keyboard -------------------
await col(0).locator('.opt-row').first()
  .locator('button[aria-label^="Move to validation"]').click();
await p.waitForTimeout(300);
const moved = await counts();
await p.keyboard.press('Control+z');
await p.waitForTimeout(400);
ok('Ctrl+Z undoes a single-row edit', (await counts()) === before,
   `${moved} -> ${await counts()}`);
ok('and Undo is disabled once there is nothing left',
   await p.getByRole('button', { name: 'Undo' }).isDisabled());

// --- the exclude fix: a copy in both columns loses only the one pressed ----
await col(0).locator('.opt-row').first()
  .locator('button[aria-label^="Also add to validation"]').click();
await p.waitForTimeout(300);
const inBoth = await col(0).locator('.opt-row').first()
  .locator('button[aria-label^="Remove from training"]');
ok('the ✕ on a question in both columns says which copy it takes',
   await inBoth.count() === 1,
   await inBoth.getAttribute('aria-label') || '(no such button)');
await inBoth.click();
await p.waitForTimeout(300);
const afterX = await counts();
ok('and excluding that copy leaves the other one working',
   /9 training/.test(afterX) && /5 validation/.test(afterX) && !/excluded/.test(afterX),
   afterX);

// --- item 3: the drawer shows what the columns show ------------------------
await col(0).locator('.opt-row').first()
  .locator('button[aria-label="Exclude from this run"]').click();
await p.waitForTimeout(300);
await p.locator('.opt-excluded-toggle').click();
await p.waitForTimeout(300);
const row = p.locator('.opt-excluded-list .opt-row').first();
ok('an excluded row carries the accuracy badge', await row.locator('.ui-badge').count() > 0);
ok('an excluded row names its eval set',
   (await row.locator('.opt-qset').textContent() || '').length > 0,
   await row.locator('.opt-qset').textContent());
const back = await row.locator('.opt-row-actions button').count();
ok('and offers both columns to put it back into', back === 2, `${back} buttons`);

await p.locator('.opt-excluded').scrollIntoViewIfNeeded();
await p.locator('.opt-excluded').screenshot({ path: `${OUT}/after-03-excluded.png` });
await col(0).screenshot({ path: `${OUT}/after-04-column.png` });

if (!results.every(Boolean)) process.exitCode = 1;
await b.close();
