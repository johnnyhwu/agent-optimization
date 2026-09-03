import React, { useEffect, useState } from "react";
import Badge from "../ui/Badge.jsx";
import Button from "../ui/Button.jsx";
import Card, { CardHeader } from "../ui/Card.jsx";
import { InlineEmpty } from "../ui/EmptyState.jsx";
import {
  IconAlert,
  IconChevronDown,
  IconChevronRight,
  IconCopyPlus,
  IconMoveLeft,
  IconMoveRight,
  IconUndo,
  IconX,
} from "../icons.jsx";
import {
  DEFAULT_SORT,
  actionsFor,
  counts,
  duplicate,
  duplicateAll,
  exclude,
  excludeAll,
  move,
  moveAll,
  restore,
  sortQuestions,
  splitIssues,
} from "../../optimize_split.js";
import { accuracyLabel, accuracyTone } from "./SkillGroups.jsx";

// Wizard step 3: which questions the skill is trained on, and which are held
// back to judge it by.
//
// Two columns, and three icon buttons per row. The buttons are icons rather than
// labelled controls because there are three of them on every row of a list that
// can be sixty rows long; each carries a `title` and an `aria-label` with the
// same sentence, and a disabled one carries the reason it is disabled — a greyed
// control with no explanation is a puzzle, and this screen has a lot of rows to
// be puzzled by.
//
// The remove control is an ✕ and deliberately not a bin. A bin says "delete",
// and what this does is exclude the question from *this run* — the eval set is
// untouched and the question comes back from the drawer at the bottom.

export default function SplitEditor({ split, limits, onChange, onUndo, canUndo }) {
  const [sort, setSort] = useState(DEFAULT_SORT);
  const [showExcluded, setShowExcluded] = useState(false);
  const c = counts(split);
  const issues = splitIssues(split, limits);

  const rows = (keys) =>
    sortQuestions(keys.map((k) => split.byKey.get(k)).filter(Boolean), sort);

  // Ctrl/Cmd+Z anywhere on the step. The bulk buttons are one click and change
  // sixty rows, so the way back has to be at least as cheap as the way there —
  // and a keyboard user should not have to find a button to get it.
  useEffect(() => {
    const onKey = (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "z" && !e.shiftKey) {
        // Not while typing: the sort select is the only field on this step, but
        // the browser's own undo inside a text field is not ours to take.
        const tag = document.activeElement?.tagName;
        if (tag === "INPUT" || tag === "TEXTAREA") return;
        if (!canUndo) return;
        e.preventDefault();
        onUndo();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onUndo, canUndo]);

  return (
    <div className="opt-split">
      <div className="opt-split-bar">
        <div className="opt-split-counts">
          <Badge tone="accent" mono>{c.train} training</Badge>
          <Badge tone="info" mono>{c.val} validation</Badge>
          {c.overlap > 0 && <Badge tone="warning" mono>{c.overlap} in both</Badge>}
          {c.excluded > 0 && <Badge tone="neutral" mono>{c.excluded} excluded</Badge>}
          {/* Beside the counts because the counts are what it puts back, and
              always present rather than appearing on the first edit — a control
              that materialises is one nobody knows to look for beforehand. */}
          <Button
            variant="ghost"
            size="sm"
            icon={<IconUndo size={14} />}
            disabled={!canUndo}
            onClick={onUndo}
            title={canUndo ? "Undo the last change (Ctrl+Z)" : "Nothing to undo yet"}
          >
            Undo
          </Button>
        </div>
        <label className="opt-sort">
          Sort
          <select value={sort} onChange={(e) => setSort(e.target.value)}>
            <option value={DEFAULT_SORT}>by question id</option>
            <option value="accuracy">by accuracy (worst first)</option>
            <option value="eval_set">by eval set</option>
          </select>
        </label>
      </div>

      {/* One line each until asked. Three warnings, each a full paragraph in its
          own padded box with 16px between them, filled the screen above the
          thing they were about — and each said only what was true of the split,
          never what to do about it. Now the box is the title and the number, and
          the reasoning and the move it implies are one click away. */}
      {issues.length > 0 && (
        <ul className="opt-issues">
          {issues.map((issue) => (
            <li key={issue.code}>
              <details className={`opt-issue is-${issue.level}`}>
                <summary>
                  <span className="opt-issue-mark">
                    <IconAlert size={14} />
                  </span>
                  <span className="opt-issue-title">{issue.title}</span>
                  <span className="opt-issue-summary">{issue.summary}</span>
                  <IconChevronDown size={14} className="opt-issue-chevron" />
                </summary>
                <div className="opt-issue-detail">
                  <p>{issue.detail}</p>
                  {/* The part that was missing. A warning a developer cannot act
                      on is one they learn to scroll past. */}
                  <p className="opt-issue-do">
                    <strong>What to do:</strong> {issue.suggestion}
                  </p>
                </div>
              </details>
            </li>
          ))}
        </ul>
      )}

      <div className="opt-split-cols">
        <Column
          title="Training"
          hint="Answered each step, then reflected on. This is what the optimizer learns from."
          column="train"
          questions={rows(split.train)}
          split={split}
          onChange={onChange}
        />
        <Column
          title="Validation"
          hint="Held back. The gate keeps a candidate only if it improves this — so anything in here is not learned from."
          column="val"
          questions={rows(split.val)}
          split={split}
          onChange={onChange}
        />
      </div>

      {split.excluded.length > 0 && (
        <Card className="opt-excluded">
          <button
            type="button"
            className="opt-excluded-toggle"
            aria-expanded={showExcluded}
            onClick={() => setShowExcluded((v) => !v)}
          >
            {showExcluded ? <IconChevronDown size={14} /> : <IconChevronRight size={14} />}
            Excluded from this run ({split.excluded.length})
          </button>
          {showExcluded && (
            <ul className="opt-excluded-list">
              {rows(split.excluded).map((q) => (
                // The same row as the columns above, deliberately. A question
                // here used to be a bare line of text, so deciding whether to
                // put it back — the only decision this drawer is for — meant
                // remembering which eval set it came from and how it had scored,
                // both of which were on screen a moment earlier.
                <li key={q.item_key} className="opt-row">
                  <QuestionCell q={q} />
                  <div className="opt-row-actions">
                    <IconAction
                      icon={<IconMoveLeft size={15} />}
                      action={{ enabled: true, reason: null, label: "Put back into training" }}
                      onClick={() => onChange(restore(split, q.item_key, "train"))}
                    />
                    <IconAction
                      icon={<IconMoveRight size={15} />}
                      action={{ enabled: true, reason: null, label: "Put back into validation" }}
                      onClick={() => onChange(restore(split, q.item_key, "val"))}
                    />
                  </div>
                </li>
              ))}
            </ul>
          )}
        </Card>
      )}
    </div>
  );
}

// One question, as both columns and the excluded drawer show it.
//
// Extracted rather than copied because the drawer used to show a bare line of
// text: the accuracy and the eval set were on screen while the question sat in
// a column and gone the moment it was excluded, which is precisely when the
// developer is deciding whether to put it back.
function QuestionCell({ q, inBoth = false }) {
  return (
    <div className="opt-row-main">
      <span className="opt-qtext" title={q.question}>{q.question}</span>
      <span className="opt-row-meta">
        <Badge tone={accuracyTone(q)} size="sm" mono>
          {accuracyLabel(q)}
        </Badge>
        <span className="opt-qset">{q.eval_set_name}</span>
        {/* An unexplained disabled button is a puzzle; a badge that says why is
            an answer. */}
        {inBoth && <Badge tone="warning" size="sm" outline>in both</Badge>}
      </span>
    </div>
  );
}

function Column({ title, hint, column, questions, split, onChange }) {
  const toward = column === "train" ? "val" : "train";
  const MoveIcon = column === "train" ? IconMoveRight : IconMoveLeft;
  const there = toward === "val" ? "validation" : "training";
  const n = questions.length;

  // The same three actions as a row, applied to the column. `n` is in every
  // label because "Move all" over a collapsed sixty-row list is a click whose
  // consequences are off-screen, and the number is the cheapest way to say how
  // much is about to happen.
  const bulk = [
    {
      icon: <MoveIcon size={15} />,
      label: `Move all ${n} to ${there}`,
      run: () => moveAll(split, column, toward),
    },
    {
      icon: <IconCopyPlus size={15} />,
      label: `Also add all ${n} to ${there} (keep them here)`,
      run: () => duplicateAll(split, column, toward),
    },
    {
      icon: <IconX size={15} />,
      label: `Exclude all ${n} from this run`,
      run: () => excludeAll(split, column),
    },
  ];

  return (
    <Card className="opt-col">
      <CardHeader title={title} count={n} />
      <p className="opt-col-hint">{hint}</p>
      {/* Above the list rather than in the card's header row, so each button
          sits in the same column as the row buttons it repeats — the header is
          where the count goes, and a control there reads as being about the
          card rather than about its contents. */}
      <div className="opt-col-bulk">
        <span className="opt-col-bulk-label">All {n}:</span>
        {bulk.map((b) => (
          <IconAction
            key={b.label}
            icon={b.icon}
            action={{ enabled: n > 0, reason: "this column is empty", label: b.label }}
            onClick={() => onChange(b.run())}
          />
        ))}
      </div>
      {questions.length === 0 ? (
        <InlineEmpty>Nothing here yet.</InlineEmpty>
      ) : (
        <ul className="opt-col-list">
          {questions.map((q) => {
            const actions = actionsFor(split, q.item_key, column);
            return (
              <li key={q.item_key} className="opt-row">
                <QuestionCell q={q} inBoth={actions.inBoth} />
                <div className="opt-row-actions">
                  <IconAction
                    icon={<MoveIcon size={15} />}
                    action={actions.move}
                    onClick={() => onChange(move(split, q.item_key, toward))}
                  />
                  <IconAction
                    icon={<IconCopyPlus size={15} />}
                    action={actions.duplicate}
                    onClick={() => onChange(duplicate(split, q.item_key, toward))}
                  />
                  <IconAction
                    icon={<IconX size={15} />}
                    action={actions.exclude}
                    // The column, so a question sitting in both loses the copy
                    // whose ✕ was pressed and keeps the other.
                    onClick={() => onChange(exclude(split, q.item_key, column))}
                  />
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </Card>
  );
}

function IconAction({ icon, action, onClick }) {
  const label = action.enabled ? action.label : `${action.label} — ${action.reason}`;
  return (
    <Button
      variant="ghost"
      icon={icon}
      disabled={!action.enabled}
      onClick={onClick}
      title={label}
      aria-label={label}
    />
  );
}
