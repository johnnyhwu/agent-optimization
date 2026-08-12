import React, { useState } from "react";
import { api } from "../api.js";
import { COPY_OK, copyText } from "../clipboard.js";
import { useToast } from "./Toast.jsx";
import { IconCopy, IconDownload } from "./icons.jsx";
import Button from "./ui/Button.jsx";

// "What is this file supposed to look like?" — answered for all three upload
// formats behind one link.
//
// One entry point rather than three buttons on the picker row. There are now
// three ways to supply an eval set, and giving each its own "load a sample"
// affordance would put more chrome in front of the file picker than the file
// picker has. Behind the link, a format is a tab.
//
// Copy comes before download, deliberately: people building one of these have an
// editor open, and pasting a template beats finding a file in ~/Downloads.

const TABS = [
  { id: "python", label: "Python script", lang: "python" },
  { id: "jsonl", label: "JSONL", lang: "json" },
  { id: "csv", label: "CSV", lang: "csv" },
];

const SNIPPETS = {
  python: `def main(database_handler) -> list[dict]:
    rows = database_handler.run_sql(
        """
        SELECT customer_name, closing_balance
          FROM billing_summary
         WHERE quarter = %(quarter)s
        """,
        {"quarter": "2026Q2"},
    )
    print(f"fetched {len(rows)} rows")

    return [
        {
            "question": f"How much did {r['customer_name']} owe at end of Q2?",
            "ground_truth_response": f"\${r['closing_balance']:,.2f}",
            "ground_truth_reasoning_process_description":
                "Query billing_summary for that customer and quarter.",
            "skill": ["billing"],
        }
        for r in rows
    ]`,
  jsonl: `{"question": "How much did ACME owe at end of Q2?", "ground_truth_response": "ACME owed $42,180.00.", "ground_truth_reasoning_process_description": "Query billing_summary for ACME in 2026Q2.", "skill": ["billing"]}
{"question": "List the overdue invoices for EMEA.", "ground_truth_response": "INV-1021, INV-1044, INV-1102.", "ground_truth_reasoning_process_description": "Filter invoices to EMEA, unpaid, past due.", "skill": ["billing", "reports"]}`,
  csv: `question,ground_truth_response,ground_truth_reasoning_process_description,skill,question_id
How much did ACME owe at end of Q2?,"ACME owed $42,180.00.","Query billing_summary for ACME in 2026Q2.",billing,
List the overdue invoices for EMEA.,"INV-1021, INV-1044, INV-1102.","Filter invoices to EMEA, unpaid, past due.","billing, reports",`,
};

const NOTES = {
  python: (
    <>
      One file with a top-level <code>main(database_handler)</code>. Write whatever
      helpers you like — only <code>main()</code> is called, and it must return a
      list of dicts with the keys below. <code>database_handler.run_sql(sql, params)</code>
      is the only method you get; the connection is read-only, and{" "}
      <code>params</code> handles the quoting so you never build SQL with f-strings.
      On top of the standard library you may import <code>pandas</code> and{" "}
      <code>tabulate</code>; nothing else is installed.
    </>
  ),
  jsonl: <>One JSON object per line. <code>skill</code> is a list of strings.</>,
  csv: (
    <>
      A header row with these exact column names. <code>skill</code> may be a
      comma-separated list in one cell. <code>question_id</code> may be left blank —
      one is generated.
    </>
  ),
};

export default function FormatHelp({ onLoadSample }) {
  const toast = useToast();
  const [open, setOpen] = useState(false);
  const [tab, setTab] = useState("python");
  const [copied, setCopied] = useState(false);

  // Answered twice on purpose. The label flip is where the eyes already are —
  // on the button that was just pressed — but it is two words on a small
  // control, and someone reading the snippet above it will miss it entirely.
  // The toast is the one that carries across the dialog, and it is also the
  // only way to report the failure, which has no label state to flip.
  async function copy() {
    const label = TABS.find((t) => t.id === tab)?.label || "Example";
    if ((await copyText(SNIPPETS[tab])) === COPY_OK) {
      setCopied(true);
      setTimeout(() => setCopied(false), 1800);
      toast.success(`${label} example copied`);
      return;
    }
    // Names the way out rather than apologising: the download beside this
    // button produces the same text as a file, and always works.
    toast.error("Could not reach the clipboard — use Download example instead.");
  }

  if (!open) {
    return (
      <Button variant="link" onClick={() => setOpen(true)}>
        Formats &amp; examples
      </Button>
    );
  }

  return (
    <div className="format-help">
      <div className="format-help-head">
        <div className="format-help-tabs" role="tablist">
          {TABS.map((t) => (
            <button
              key={t.id}
              type="button"
              role="tab"
              aria-selected={tab === t.id}
              className={`format-help-tab${tab === t.id ? " active" : ""}`}
              onClick={() => setTab(t.id)}
            >
              {t.label}
            </button>
          ))}
        </div>
        <span className="grow" />
        <Button variant="link" size="sm" onClick={() => setOpen(false)}>
          Close
        </Button>
      </div>

      <p className="hint format-help-note">{NOTES[tab]}</p>
      <pre className="format-help-code">{SNIPPETS[tab]}</pre>

      <div className="format-help-actions">
        <Button size="sm" icon={<IconCopy size={14} />} onClick={copy}>
          {copied ? "Copied" : "Copy"}
        </Button>
        <Button
          size="sm"
          icon={<IconDownload size={14} />}
          onClick={() => api.downloadTemplate(tab)}
        >
          Download example
        </Button>
        {tab !== "python" && onLoadSample && (
          // Only for the file formats: a script has to be run against a database,
          // so there is nothing to drop into the preview without one.
          <Button variant="link" size="sm" onClick={onLoadSample}>
            load these rows into the preview
          </Button>
        )}
      </div>

      <p className="hint format-help-fields">
        Required on every row: <code>question</code>, <code>ground_truth_response</code>,{" "}
        <code>ground_truth_reasoning_process_description</code>, <code>skill</code>.
        Optional: <code>question_id</code>.
      </p>
    </div>
  );
}
