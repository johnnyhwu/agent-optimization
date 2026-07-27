// Client-side parsing of an uploaded eval-set file (JSONL or CSV) into editable
// preview rows. Both formats normalize to the same row shape used by the upload
// table; on submit the (possibly edited) table is re-serialized to JSONL, since
// the Stage 1 backend contract stays JSONL-only (§6.11). Doing the parse in the
// browser is what lets us show an editable table before anything hits the DB.

// Editable row shape (kept flat/string-y so <input>/<textarea> bind directly):
//   { question, response, reasoning, skill (comma text), question_id }

export function detectFormat(filename) {
  return /\.csv$/i.test(filename || "") ? "csv" : "jsonl";
}

export function skillToText(arr) {
  return (arr || []).join(", ");
}

// A skill cell may be a JSON array literal (["billing","reports"]) or a plain
// delimited string ("billing, reports"). Normalize both to a string[].
export function parseSkillCell(v) {
  const s = (v == null ? "" : String(v)).trim();
  if (!s) return [];
  if (s.startsWith("[")) {
    try {
      const arr = JSON.parse(s);
      if (Array.isArray(arr)) return arr.map((x) => String(x).trim()).filter(Boolean);
    } catch {
      /* fall through to delimiter split */
    }
  }
  return s.split(/[,;|]/).map((x) => x.trim()).filter(Boolean);
}

function emptyRow() {
  return { question: "", response: "", reasoning: "", skill: "", question_id: "" };
}
export { emptyRow };

function rowFromObject(obj) {
  const skill = obj.skill;
  const skillText = Array.isArray(skill)
    ? skill.map((s) => String(s).trim()).filter(Boolean).join(", ")
    : skill == null
      ? ""
      : String(skill);
  return {
    question: obj.question == null ? "" : String(obj.question),
    response: obj.ground_truth_response == null ? "" : String(obj.ground_truth_response),
    reasoning:
      obj.ground_truth_reasoning_process_description == null
        ? ""
        : String(obj.ground_truth_reasoning_process_description),
    skill: skillText,
    question_id: obj.question_id == null ? "" : String(obj.question_id).trim(),
  };
}

export function parseJsonl(text) {
  const rows = [];
  const errors = [];
  (text || "").split(/\r?\n/).forEach((line, idx) => {
    if (!line.trim()) return;
    let obj;
    try {
      obj = JSON.parse(line);
    } catch {
      errors.push(`line ${idx + 1}: invalid JSON`);
      return;
    }
    if (typeof obj !== "object" || obj === null || Array.isArray(obj)) {
      errors.push(`line ${idx + 1}: expected a JSON object`);
      return;
    }
    rows.push(rowFromObject(obj));
  });
  return { rows, errors };
}

// Minimal RFC-4180-ish CSV tokenizer: handles quoted fields, escaped quotes
// (""), and newlines inside quotes. Returns an array of string[] (rows).
function tokenizeCsv(text) {
  const rows = [];
  let row = [];
  let field = "";
  let inQuotes = false;
  let i = 0;
  const s = text || "";
  while (i < s.length) {
    const c = s[i];
    if (inQuotes) {
      if (c === '"') {
        if (s[i + 1] === '"') { field += '"'; i += 2; continue; }
        inQuotes = false; i++; continue;
      }
      field += c; i++; continue;
    }
    if (c === '"') { inQuotes = true; i++; continue; }
    if (c === ",") { row.push(field); field = ""; i++; continue; }
    if (c === "\r") { i++; continue; }
    if (c === "\n") { row.push(field); rows.push(row); row = []; field = ""; i++; continue; }
    field += c; i++;
  }
  if (field.length > 0 || row.length > 0) { row.push(field); rows.push(row); }
  return rows;
}

const CSV_COLUMNS = {
  question: "question",
  response: "ground_truth_response",
  reasoning: "ground_truth_reasoning_process_description",
  skill: "skill",
  question_id: "question_id",
};

export function parseCsv(text) {
  const table = tokenizeCsv(text);
  if (table.length === 0) return { rows: [], errors: ["file is empty"] };

  const header = table[0].map((h) => h.trim().toLowerCase());
  const col = (name) => header.indexOf(name);
  const qi = col(CSV_COLUMNS.question);
  const ri = col(CSV_COLUMNS.response);
  const gi = col(CSV_COLUMNS.reasoning);
  const si = col(CSV_COLUMNS.skill);
  const ii = col(CSV_COLUMNS.question_id);

  const missing = [];
  if (qi < 0) missing.push(CSV_COLUMNS.question);
  if (ri < 0) missing.push(CSV_COLUMNS.response);
  if (gi < 0) missing.push(CSV_COLUMNS.reasoning);
  if (missing.length) return { rows: [], errors: [`missing column(s): ${missing.join(", ")}`] };

  const rows = [];
  const at = (cells, idx) => (idx >= 0 && idx < cells.length ? cells[idx] : "");
  for (let r = 1; r < table.length; r++) {
    const cells = table[r];
    if (cells.every((c) => !String(c).trim())) continue; // skip blank line
    rows.push({
      question: at(cells, qi),
      response: at(cells, ri),
      reasoning: at(cells, gi),
      skill: skillToText(parseSkillCell(at(cells, si))),
      question_id: String(at(cells, ii)).trim(),
    });
  }
  return { rows, errors: [] };
}

export function parseFile(text, format) {
  return format === "csv" ? parseCsv(text) : parseJsonl(text);
}

// Re-serialize the edited table back to JSONL for the backend. Rows with an
// empty question_id omit the key so the backend generates an immutable one.
export function rowsToJsonl(rows) {
  return rows
    .map((r) => {
      const obj = {
        question: r.question,
        ground_truth_response: r.response,
        ground_truth_reasoning_process_description: r.reasoning,
        skill: parseSkillCell(r.skill),
      };
      const qid = (r.question_id || "").trim();
      if (qid) obj.question_id = qid;
      return JSON.stringify(obj);
    })
    .join("\n");
}

// Light client-side check mirroring the backend's required-field rules, so the
// developer gets row-level feedback before the request. The backend remains the
// source of truth (it re-validates and can still 422).
export function validateRows(rows) {
  const errors = [];
  if (rows.length === 0) return ["Upload a JSONL or CSV file (or add a row) first."];
  rows.forEach((r, i) => {
    const n = i + 1;
    const missing = [];
    if (!r.question.trim()) missing.push("question");
    if (!r.response.trim()) missing.push("ground_truth_response");
    if (!r.reasoning.trim()) missing.push("ground_truth_reasoning_process_description");
    if (parseSkillCell(r.skill).length === 0) missing.push("skill");
    if (missing.length) errors.push(`row ${n}: missing ${missing.join(", ")}`);
  });
  return errors;
}
