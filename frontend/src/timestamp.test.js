import test from "node:test";
import assert from "node:assert/strict";
import { relativeStamp, shortStamp } from "./timestamp.js";

// A fixed instant, so "now" is a value rather than the wall clock. Both
// functions render in the *reader's* timezone by design, so the assertions
// below are built from a local-time Date rather than a UTC literal — otherwise
// this suite passes in one timezone and fails in another, which is the same
// class of bug the module exists to avoid.
const at = (y, mo, d, h, mi, s = 0) => new Date(y, mo - 1, d, h, mi, s);
const NOW = at(2026, 3, 14, 15, 30, 0).getTime();
const ago = (seconds) => new Date(NOW - seconds * 1000).toISOString();

test("shortStamp pads every field to a fixed width", () => {
  // A column of these is compared down the page, so they have to be one width.
  assert.equal(shortStamp(at(2026, 3, 4, 9, 5)), "2026/03/04 09:05");
  assert.equal(shortStamp(at(2026, 12, 31, 23, 59)), "2026/12/31 23:59");
});

test("shortStamp returns an empty string for nothing and for nonsense", () => {
  assert.equal(shortStamp(null), "");
  assert.equal(shortStamp(undefined), "");
  assert.equal(shortStamp(""), "");
  assert.equal(shortStamp("not a date"), "");
});

test("relativeStamp counts seconds, then minutes, then hours", () => {
  assert.equal(relativeStamp(ago(0), NOW), "0s ago");
  assert.equal(relativeStamp(ago(25), NOW), "25s ago");
  assert.equal(relativeStamp(ago(59), NOW), "59s ago");
  assert.equal(relativeStamp(ago(60), NOW), "1m ago");
  assert.equal(relativeStamp(ago(20 * 60), NOW), "20m ago");
  assert.equal(relativeStamp(ago(3599), NOW), "59m ago");
  assert.equal(relativeStamp(ago(3600), NOW), "1h ago");
  assert.equal(relativeStamp(ago(5 * 3600), NOW), "5h ago");
  assert.equal(relativeStamp(ago(23 * 3600), NOW), "23h ago");
});

test("relativeStamp hands over to the absolute stamp after a day", () => {
  // The bug this replaces: at exactly one hour the old version fell through to
  // `toLocaleTimeString()`, which is locale-dependent, carries seconds, and —
  // worst of all — has no date, so last week's attempt and this morning's
  // rendered identically in the same column.
  const older = ago(30 * 3600);
  assert.equal(relativeStamp(older, NOW), shortStamp(older));
  assert.match(relativeStamp(older, NOW), /^\d{4}\/\d{2}\/\d{2} \d{2}:\d{2}$/);
});

test("relativeStamp never renders a locale-dependent clock time", () => {
  // The whole ladder, checked for the shape that started this: a bare
  // "9:19:22 AM" (or "上午9:19:22") anywhere in the output.
  for (const seconds of [0, 30, 90, 3600, 7200, 86399, 86400, 86400 * 9]) {
    const out = relativeStamp(ago(seconds), NOW);
    assert.doesNotMatch(out, /AM|PM|上午|下午/, `${seconds}s → ${out}`);
  }
});

test("relativeStamp does not count backwards for a clock that is behind", () => {
  // Server and browser clocks disagree, and a run started "in the future"
  // should read as brand new rather than as a negative age.
  assert.equal(relativeStamp(new Date(NOW + 5000).toISOString(), NOW), "0s ago");
});

test("relativeStamp returns an empty string for nothing and for nonsense", () => {
  assert.equal(relativeStamp(null, NOW), "");
  assert.equal(relativeStamp("not a date", NOW), "");
});
