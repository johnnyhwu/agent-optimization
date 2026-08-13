import test from "node:test";
import assert from "node:assert/strict";
import { diffRows, fileStatus, lineCounts, splitLines } from "./diff.js";

// The side-by-side diff is the only part of Part 2 the browser computes rather
// than reads. Everything else on that page — the per-file `+5 / −10`, the
// totals on the step row, the answer-leak lines — is decided once by
// `skillio.py` and rendered verbatim, precisely so two implementations cannot
// disagree on screen about one edit.
//
// Which makes the rows the thing worth testing hard: they are the half that
// can be wrong on its own. An alignment that slips by one line does not look
// broken, it looks like the model rewrote a paragraph it never touched.

const rowsOf = (before, after) =>
  diffRows(before, after).map((r) => [r.type, r.left, r.right]);

// --- How a line is defined ---------------------------------------------------

test("a line keeps its terminator, so a missing final newline is one change", () => {
  // Splitting on "\n" and discarding it would turn "a\n" into ["a", ""] and
  // report a *deleted blank line* when a file simply lost its trailing
  // newline. It also has to match how the server counted the same edit:
  // `skillio` splits with `keepends=True`, and the tree's `+1 / −1` would
  // otherwise sit beside two rows that say something else.
  assert.deepEqual(splitLines("a\nb\n"), ["a\n", "b\n"]);
  assert.deepEqual(splitLines("a\nb"), ["a\n", "b"]);
  assert.deepEqual(splitLines(""), []);

  const rows = rowsOf("policy\n", "policy");
  assert.equal(rows.length, 1);
  assert.equal(rows[0][0], "change");
});

test("the text a row carries is stripped of its terminator", () => {
  // The rows go straight into table cells. A trailing "\n" inside a cell is
  // invisible in the DOM but survives a copy-paste, which is how a reader ends
  // up pasting a "fixed" line that still differs from the skill.
  const rows = diffRows("one\ntwo\n", "one\ntwo\n");
  assert.deepEqual(rows.map((r) => r.left), ["one", "two"]);
});

// --- Alignment ---------------------------------------------------------------

test("an inserted line does not shift the lines below it", () => {
  // The failure this exists for: a diff that pairs by position rather than by
  // content marks every line after an insertion as changed. A one-line addition
  // then renders as a rewritten file, and the reader cannot tell which it was.
  const before = "alpha\nbeta\ngamma\n";
  const after = "alpha\nbeta\nNEW\ngamma\n";
  assert.deepEqual(rowsOf(before, after), [
    ["equal", "alpha", "alpha"],
    ["equal", "beta", "beta"],
    ["add", null, "NEW"],
    ["equal", "gamma", "gamma"],
  ]);
});

test("a replaced line is one row with both versions on it", () => {
  // Side by side is the whole point: "5 days" becoming "4 days" has to be
  // readable as a substitution. Emitting a delete row followed by an add row
  // puts the two halves of one edit on different lines of the table, and the
  // reader has to do the pairing themselves.
  assert.deepEqual(rowsOf("Refunds take 5 days.\n", "Refunds take 4 days.\n"), [
    ["change", "Refunds take 5 days.", "Refunds take 4 days."],
  ]);
});

test("an uneven replacement pairs what it can and lists the rest", () => {
  // Two lines becoming three is not two changes and a spare null row: a row
  // with a null on both sides renders as an empty stripe in the middle of the
  // diff, which reads as a deliberate blank line in the skill.
  const rows = rowsOf("one\ntwo\n", "1\n2\n3\n");
  assert.deepEqual(rows, [
    ["change", "one", "1"],
    ["change", "two", "2"],
    ["add", null, "3"],
  ]);
  assert.ok(rows.every((r) => r[1] !== null || r[2] !== null));
});

test("repeated identical lines are matched without scrambling the block", () => {
  // The classic way a naive matcher goes wrong. A skill full of "" and "---"
  // separators gives an aligner many equally valid anchors, and picking them
  // greedily interleaves the added block with the lines around it.
  const before = "a\n\nb\n\nc\n";
  const after = "a\n\nb\n\nMID\n\nc\n";
  const rows = diffRows(before, after);
  assert.equal(rows.filter((r) => r.type !== "equal").length, 2);
  assert.deepEqual(
    rows.filter((r) => r.type === "add").map((r) => r.right),
    ["MID", ""],
  );
});

test("a file rewritten end to end has no accidental matches", () => {
  const rows = rowsOf("aaa\nbbb\n", "xxx\nyyy\n");
  assert.deepEqual(rows, [
    ["change", "aaa", "xxx"],
    ["change", "bbb", "yyy"],
  ]);
});

// --- Whole files -------------------------------------------------------------

test("a file the step created has no left-hand side at all", () => {
  // `before: null` is the endpoint saying the file did not exist, which is not
  // the same as it having been empty. Rendering it as "" would produce a first
  // row pairing a blank left cell against real text — a file that was there and
  // got rewritten.
  assert.deepEqual(rowsOf(null, "new one\nnew two\n"), [
    ["add", null, "new one"],
    ["add", null, "new two"],
  ]);
  assert.equal(fileStatus({ before: null, after: "x" }), "added");
});

test("a file the step removed keeps its left-hand side", () => {
  assert.deepEqual(rowsOf("gone one\ngone two\n", null), [
    ["del", "gone one", null],
    ["del", "gone two", null],
  ]);
  assert.equal(fileStatus({ before: "x", after: null }), "removed");
});

test("a file that exists on both sides is modified, whatever changed inside it", () => {
  assert.equal(fileStatus({ before: "a", after: "b" }), "modified");
  assert.equal(fileStatus({ before: "", after: "b" }), "modified");
  assert.equal(fileStatus({ before: "a", after: "" }), "modified");
});

test("an empty file on both sides produces nothing to render", () => {
  // Reached when a step is asked for its diff against itself — step 0, or a
  // step whose every edit was skipped. A single blank row would read as an
  // edit that produced one empty line.
  assert.deepEqual(diffRows("", ""), []);
  assert.deepEqual(diffRows(null, null), []);
});

test("an unchanged file produces only equal rows", () => {
  const rows = diffRows("same\ntext\n", "same\ntext\n");
  assert.ok(rows.every((r) => r.type === "equal"));
});

// --- Numbering ---------------------------------------------------------------

test("each side is numbered in its own file, and an added row has no left number", () => {
  // The gutter numbers have to be the numbers in the two files, because that is
  // what a reader takes to an editor. Numbering the *rows* would give a line
  // number that is off by the count of insertions above it.
  const rows = diffRows("a\nb\nc\n", "a\nNEW\nb\nc\n");
  assert.deepEqual(
    rows.map((r) => [r.leftNo, r.rightNo]),
    [[1, 1], [null, 2], [2, 3], [3, 4]],
  );
});

test("a replaced row carries both files' numbers for that line", () => {
  // A `change` row is the only one holding two real line numbers, and it is the
  // row a reader takes to an editor to make the same fix by hand. Numbering the
  // rows of the *replaced block* instead of the lines of the files gives 1, 2, 3
  // for a substitution that starts at line 40 — plausible-looking numbers that
  // point at the top of the file.
  const rows = diffRows("intro\nold one\nold two\ntail\n", "intro\nnew one\nnew two\ntail\n");
  assert.deepEqual(rows.map((r) => r.type), ["equal", "change", "change", "equal"]);
  assert.deepEqual(
    rows.map((r) => [r.leftNo, r.rightNo]),
    [[1, 1], [2, 2], [3, 3], [4, 4]],
  );
});

test("a replacement that is not aligned in both files keeps each side's own numbers", () => {
  // The numbers only coincide when nothing has been inserted above. Once they
  // diverge, a row numbered from the block rather than from the files is wrong
  // on at least one side, and the diff is the thing a reader trusts to say
  // where in the file to look.
  const rows = diffRows("a\nb\nold\n", "a\nINS\nb\nnew\n");
  assert.deepEqual(rows.map((r) => r.type), ["equal", "add", "equal", "change"]);
  const change = rows.at(-1);
  assert.deepEqual([change.left, change.right], ["old", "new"]);
  assert.deepEqual([change.leftNo, change.rightNo], [3, 4]);
});

test("a deleted row has no right number", () => {
  const rows = diffRows("a\nb\nc\n", "a\nc\n");
  assert.deepEqual(
    rows.map((r) => [r.leftNo, r.rightNo]),
    [[1, 1], [2, null], [3, 2]],
  );
});

// --- The rows and the server's counts have to describe the same edit ---------

test("counting the rows reproduces the +N / -M the server sent", () => {
  // The tree shows `skillio`'s numbers and the pane shows these rows. If they
  // disagree the page contradicts itself — "+1" beside two green stripes — and
  // there is no way for a reader to tell which half is lying. A `change` row is
  // one addition *and* one deletion, the same way `difflib` counts a `replace`.
  const before = "alpha\nbeta\ngamma\n";
  const after = "alpha\nBETA\ngamma\ndelta\n";
  assert.deepEqual(lineCounts(diffRows(before, after)), { added: 2, removed: 1 });
  assert.deepEqual(lineCounts(diffRows(null, "one\ntwo\n")), { added: 2, removed: 0 });
  assert.deepEqual(lineCounts(diffRows("one\ntwo\n", null)), { added: 0, removed: 2 });
  assert.deepEqual(lineCounts(diffRows("same\n", "same\n")), { added: 0, removed: 0 });
});
