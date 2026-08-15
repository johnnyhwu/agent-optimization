import React, { useState } from "react";
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
  IconX,
} from "../icons.jsx";
import {
  DEFAULT_SORT,
  actionsFor,
  counts,
  duplicate,
  exclude,
  move,
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

export default function SplitEditor({ split, limits, onChange }) {
  const [sort, setSort] = useState(DEFAULT_SORT);
  const [showExcluded, setShowExcluded] = useState(false);
  const c = counts(split);
  const issues = splitIssues(split, limits);

  const rows = (keys) =>
    sortQuestions(keys.map((k) => split.byKey.get(k)).filter(Boolean), sort);

  return (
    <div className="opt-split">
      <div className="opt-split-bar">
        <div className="opt-split-counts">
          <Badge tone="accent" mono>{c.train} training</Badge>
          <Badge tone="info" mono>{c.val} validation</Badge>
          {c.overlap > 0 && <Badge tone="warning" mono>{c.overlap} in both</Badge>}
          {c.excluded > 0 && <Badge tone="neutral" mono>{c.excluded} excluded</Badge>}
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
                <li key={q.item_key}>
                  <span className="opt-qtext" title={q.question}>{q.question}</span>
                  <Button
                    variant="link"
                    onClick={() => onChange(restore(split, q.item_key))}
                  >
                    Put back
                  </Button>
                </li>
              ))}
            </ul>
          )}
        </Card>
      )}
    </div>
  );
}

function Column({ title, hint, column, questions, split, onChange }) {
  const toward = column === "train" ? "val" : "train";
  const MoveIcon = column === "train" ? IconMoveRight : IconMoveLeft;

  return (
    <Card className="opt-col">
      <CardHeader title={title} count={questions.length} />
      <p className="opt-col-hint">{hint}</p>
      {questions.length === 0 ? (
        <InlineEmpty>Nothing here yet.</InlineEmpty>
      ) : (
        <ul className="opt-col-list">
          {questions.map((q) => {
            const actions = actionsFor(split, q.item_key, column);
            return (
              <li key={q.item_key} className="opt-row">
                <div className="opt-row-main">
                  <span className="opt-qtext" title={q.question}>{q.question}</span>
                  <span className="opt-row-meta">
                    <Badge tone={accuracyTone(q)} size="sm" mono>
                      {accuracyLabel(q)}
                    </Badge>
                    <span className="opt-qset">{q.eval_set_name}</span>
                    {/* An unexplained disabled button is a puzzle; a badge that
                        says why is an answer. */}
                    {actions.inBoth && (
                      <Badge tone="warning" size="sm" outline>in both</Badge>
                    )}
                  </span>
                </div>
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
                    onClick={() => onChange(exclude(split, q.item_key))}
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
