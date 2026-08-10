// Run with: npm test  (node --test)
//
// The parsing and paging arithmetic behind the upload preview. Pure functions,
// deliberately: the preview is now paginated for every upload source, and the
// bug that pagination invites — editing row 3 of page 2 and writing to row 3 of
// the list — is silent, survives a glance at the screen, and would only surface
// as a wrong eval set weeks later. So the index maths is tested rather than
// eyeballed.
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  PAGE_SIZES,
  clampPage,
  detectFormat,
  globalIndex,
  pageCount,
  pageOfRow,
  pageSlice,
  rowsFromScriptOutput,
  rowsToJsonl,
  validateRows,
} from "./upload_parse.js";

const row = (over = {}) => ({
  question: "q",
  response: "r",
  reasoning: "g",
  skill: "billing",
  question_id: "",
  ...over,
});

const rows = (n) => Array.from({ length: n }, (_, i) => row({ question: `q${i + 1}` }));

// --- format detection --------------------------------------------------------

test("detectFormat recognises a python script", () => {
  assert.equal(detectFormat("fetch_eval_set.py"), "python");
  assert.equal(detectFormat("FETCH.PY"), "python");
});

test("detectFormat still recognises csv and defaults to jsonl", () => {
  assert.equal(detectFormat("rows.csv"), "csv");
  assert.equal(detectFormat("rows.jsonl"), "jsonl");
  assert.equal(detectFormat("rows.txt"), "jsonl");
  assert.equal(detectFormat(""), "jsonl");
});

test("a name merely containing .py is not a script", () => {
  // "quarterly.python-notes.txt" is not a script, and treating it as one would
  // send the user to a database prompt for a text file.
  assert.equal(detectFormat("quarterly.python-notes.txt"), "jsonl");
  assert.equal(detectFormat("py"), "jsonl");
});

// --- script output -> preview rows -------------------------------------------

test("script rows map onto the same shape as a file upload", () => {
  const mapped = rowsFromScriptOutput([
    {
      question: "How much?",
      ground_truth_response: "$42",
      ground_truth_reasoning_process_description: "Sum invoices.",
      skill: ["billing", "reports"],
    },
  ]);
  assert.deepEqual(mapped, [
    {
      question: "How much?",
      response: "$42",
      reasoning: "Sum invoices.",
      skill: "billing, reports",
      question_id: "",
    },
  ]);
});

test("a script row survives the round trip back to jsonl", () => {
  const source = {
    question: "How much?",
    ground_truth_response: "$42",
    ground_truth_reasoning_process_description: "Sum invoices.",
    skill: ["billing"],
    question_id: "q_keep",
  };
  const [mapped] = rowsFromScriptOutput([source]);
  assert.deepEqual(JSON.parse(rowsToJsonl([mapped])), source);
});

test("missing optional fields become empty strings, not undefined", () => {
  // undefined in a controlled <input> is how React switches it to uncontrolled
  // and starts warning; every cell has to be a string.
  const [mapped] = rowsFromScriptOutput([{ question: "only this" }]);
  Object.values(mapped).forEach((v) => assert.equal(typeof v, "string"));
});

test("script output that is not an array yields no rows rather than throwing", () => {
  assert.deepEqual(rowsFromScriptOutput(null), []);
  assert.deepEqual(rowsFromScriptOutput(undefined), []);
  assert.deepEqual(rowsFromScriptOutput({}), []);
});

// --- paging ------------------------------------------------------------------

test("the offered page sizes are the three the dialog shows", () => {
  assert.deepEqual(PAGE_SIZES, [20, 50, 100]);
});

test("pageCount rounds up and is never zero", () => {
  assert.equal(pageCount(0, 20), 1); // an empty preview still has a page 1
  assert.equal(pageCount(1, 20), 1);
  assert.equal(pageCount(20, 20), 1);
  assert.equal(pageCount(21, 20), 2);
  assert.equal(pageCount(312, 20), 16);
});

test("pageSlice returns the window and where it starts", () => {
  const all = rows(312);
  const { items, start, end } = pageSlice(all, 2, 20);
  assert.equal(items.length, 20);
  assert.equal(items[0].question, "q21"); // page 2 opens at the 21st row
  assert.equal(start, 20); // zero-based offset into the full list
  assert.equal(end, 40);
});

test("the last page is short rather than padded", () => {
  const { items, start, end } = pageSlice(rows(312), 16, 20);
  assert.equal(items.length, 12);
  assert.equal(start, 300);
  assert.equal(end, 312);
});

test("globalIndex maps a click on the page back to the real row", () => {
  // The whole point: row 3 of page 2 at 20 per page is row 23 of the list.
  assert.equal(globalIndex(2, 20, 2), 22);
  assert.equal(globalIndex(1, 20, 0), 0);
  assert.equal(globalIndex(16, 20, 11), 311);
});

test("globalIndex and pageSlice agree on which row is which", () => {
  const all = rows(312);
  const page = 7;
  const size = 50;
  const { items } = pageSlice(all, page, size);
  items.forEach((item, i) => {
    assert.equal(all[globalIndex(page, size, i)], item);
  });
});

test("pageOfRow finds the page a given row is on", () => {
  // Used to jump to the row a validation error names.
  assert.equal(pageOfRow(0, 20), 1);
  assert.equal(pageOfRow(19, 20), 1);
  assert.equal(pageOfRow(20, 20), 2);
  assert.equal(pageOfRow(286, 20), 15);
});

test("clampPage keeps the cursor on a page that exists", () => {
  // Removing rows shrinks the list under the current page; the dialog must not
  // then show an empty table with no way back.
  assert.equal(clampPage(9, 30, 20), 2);
  assert.equal(clampPage(0, 100, 20), 1);
  assert.equal(clampPage(-3, 100, 20), 1);
  assert.equal(clampPage(3, 0, 20), 1);
});

test("changing page size keeps the current rows in view", () => {
  // Going from 20 to 100 per page while on page 7 must not land past the end.
  const total = 312;
  assert.equal(clampPage(pageOfRow(globalIndex(7, 20, 0), 100), total, 100), 2);
});

// --- validation across pages -------------------------------------------------

test("validation errors carry the global row number, not the page's", () => {
  const all = rows(300);
  all[286] = row({ skill: "" });
  const errors = validateRows(all);
  assert.equal(errors.length, 1);
  assert.match(errors[0].message, /row 287/);
  assert.equal(errors[0].index, 286);
});

test("a validation error says which page to look on", () => {
  const all = rows(300);
  all[286] = row({ question: "" });
  const [error] = validateRows(all);
  assert.equal(pageOfRow(error.index, 20), 15);
});

test("validateRows is empty for a complete set of rows", () => {
  assert.deepEqual(validateRows(rows(50)), []);
});

test("an empty preview reports one actionable error", () => {
  const errors = validateRows([]);
  assert.equal(errors.length, 1);
  assert.equal(errors[0].index, -1);
  assert.match(errors[0].message, /file|script|row/i);
});

test("every missing required field is named in the message", () => {
  const [error] = validateRows([row({ question: "", response: "", skill: "" })]);
  assert.match(error.message, /question/);
  assert.match(error.message, /ground_truth_response/);
  assert.match(error.message, /skill/);
});
