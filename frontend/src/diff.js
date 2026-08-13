// Side-by-side line diff, for Part 2.
//
// This is the only number-producing code on that page the browser runs itself.
// The `+5 / −10` beside each file, the totals on the step row and the chart
// tooltip all come from `skillio.py` and are rendered verbatim — deliberately,
// because two implementations of "how many lines changed" eventually disagree
// on screen about one edit and neither is checkable. What the browser computes
// is the *alignment*: which line became which.
//
// So the contract this module owes the page is that its rows describe the same
// edit those counts describe. Two decisions follow from that and neither is
// cosmetic:
//
//   * A line keeps its terminator, matching Python's `splitlines(keepends=True)`.
//     Splitting on "\n" and dropping it turns "policy\n" into two lines and
//     reports a deleted blank line whenever a file loses its final newline.
//   * A replaced line is one row carrying both versions, and a `change` counts
//     as one addition and one deletion — exactly how `difflib` counts a
//     `replace` opcode.
//
// A real longest-common-subsequence, not a greedy scan. A skill is a few
// hundred lines, so the quadratic table costs nothing, and the greedy version
// mis-aligns exactly where a skill is most repetitive: blank lines, `---`
// rules, and bulleted lists of near-identical rules.

export function splitLines(text) {
  if (!text) return [];
  // Lookbehind split: keeps the newline on the line it terminates.
  return text.split(/(?<=\n)/);
}

function trimEnd(line) {
  return line.endsWith("\n") ? line.slice(0, -1) : line;
}

// `equal` / `del` / `add` in file order, with the index each op consumed.
function operations(before, after) {
  const n = before.length;
  const m = after.length;
  // lcs[i][j] = length of the longest common subsequence of before[i:], after[j:]
  const lcs = Array.from({ length: n + 1 }, () => new Uint32Array(m + 1));
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      lcs[i][j] =
        before[i] === after[j]
          ? lcs[i + 1][j + 1] + 1
          : Math.max(lcs[i + 1][j], lcs[i][j + 1]);
    }
  }

  const ops = [];
  let i = 0;
  let j = 0;
  while (i < n && j < m) {
    if (before[i] === after[j]) ops.push({ type: "equal", i: i++, j: j++ });
    // Where both branches reach the same LCS length the alignment is a free
    // choice, and this is where it gets made: deletion first. Both are correct
    // and both produce the same counts — checked against 400 generated skill
    // edits — so what is being fixed here is only which of two equally valid
    // pictures a moved line gets, and that it is the same picture every time.
    else if (lcs[i + 1][j] >= lcs[i][j + 1]) ops.push({ type: "del", i: i++, j: -1 });
    else ops.push({ type: "add", i: -1, j: j++ });
  }
  while (i < n) ops.push({ type: "del", i: i++, j: -1 });
  while (j < m) ops.push({ type: "add", i: -1, j: j++ });
  return ops;
}

export function diffRows(before, after) {
  const left = splitLines(before);
  const right = splitLines(after);
  const rows = [];
  let dels = [];
  let adds = [];

  // A run of removals immediately followed by a run of additions is one edit
  // seen twice. Pairing them puts "5 days" and "4 days" on the same row, which
  // is the entire reason for laying the diff out side by side; the leftover of
  // an uneven run stays a plain add or del rather than becoming a row with a
  // null on both sides, which would render as a blank stripe in the file.
  const flush = () => {
    const paired = Math.min(dels.length, adds.length);
    for (let k = 0; k < paired; k++) {
      rows.push({
        type: "change",
        left: trimEnd(left[dels[k]]),
        right: trimEnd(right[adds[k]]),
        leftNo: dels[k] + 1,
        rightNo: adds[k] + 1,
      });
    }
    for (let k = paired; k < dels.length; k++) {
      rows.push({
        type: "del", left: trimEnd(left[dels[k]]), right: null,
        leftNo: dels[k] + 1, rightNo: null,
      });
    }
    for (let k = paired; k < adds.length; k++) {
      rows.push({
        type: "add", left: null, right: trimEnd(right[adds[k]]),
        leftNo: null, rightNo: adds[k] + 1,
      });
    }
    dels = [];
    adds = [];
  };

  for (const op of operations(left, right)) {
    if (op.type === "del") dels.push(op.i);
    else if (op.type === "add") adds.push(op.j);
    else {
      flush();
      rows.push({
        type: "equal",
        left: trimEnd(left[op.i]),
        right: trimEnd(right[op.j]),
        leftNo: op.i + 1,
        rightNo: op.j + 1,
      });
    }
  }
  flush();
  return rows;
}

export function lineCounts(rows) {
  let added = 0;
  let removed = 0;
  for (const row of rows) {
    if (row.type === "add" || row.type === "change") added += 1;
    if (row.type === "del" || row.type === "change") removed += 1;
  }
  return { added, removed };
}

// `null` means the file did not exist on that side, which is not the same as
// it having been empty — the endpoint is careful about the difference and the
// tree has to be too, because "new file" and "emptied file" produce identical
// line counts.
export function fileStatus(file) {
  if (file.before == null) return "added";
  if (file.after == null) return "removed";
  return "modified";
}
