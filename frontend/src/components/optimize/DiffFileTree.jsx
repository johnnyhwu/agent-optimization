import React from "react";
import Badge from "../ui/Badge.jsx";
import { fileStatus } from "../../diff.js";

// The left column of Part 2: this skill's directory, and what the step did to
// each file.
//
// Only *this* skill's files, never the workspace. A routing run snapshots the
// whole workspace to detect drift, but the parameter being trained is one
// directory, and a tree that listed the others would invite the reader to look
// for edits that cannot exist.
//
// The `+N / −M` figures come from the server and are printed as they arrive.
// They are the same numbers as the step row and the chart tooltip, computed
// once in `skillio.py`, which is the only way three places can quote one edit
// without eventually contradicting each other.

const STATUS_TONE = { added: "success", removed: "danger", modified: "info" };
const STATUS_LABEL = { added: "new", removed: "deleted", modified: "" };

export default function DiffFileTree({ files, unchanged, selected, onSelect }) {
  return (
    <nav className="opt-difftree" aria-label="Files in this skill">
      <ul>
        {files.map((file) => {
          const status = fileStatus(file);
          return (
            <li key={file.path}>
              <button
                type="button"
                className={selected === file.path ? "opt-difffile selected" : "opt-difffile"}
                onClick={() => onSelect(file.path)}
                aria-current={selected === file.path}
              >
                <span className="opt-difffile-name" title={file.path}>
                  {basename(file.path)}
                </span>
                {STATUS_LABEL[status] && (
                  <Badge tone={STATUS_TONE[status]} size="sm">{STATUS_LABEL[status]}</Badge>
                )}
                <span className="opt-difffile-stat">
                  <span className="added">+{file.added}</span>{" "}
                  <span className="removed">−{file.removed}</span>
                </span>
              </button>
              <span className="opt-difffile-dir">{dirname(file.path)}</span>
            </li>
          );
        })}
        {/* Not silently dropped — a tree that shrank to the edited files would
            stop being a picture of the skill, and the reader would have no way
            to see how much of it this step left alone, which is most of the
            reassurance a diff offers.

            And now selectable, not just named. Opening one shows its diff
            against itself: identical sides, every row context. That is a real
            answer to "what does this file say?", which the tree used to raise
            and then refuse to answer. */}
        {unchanged.map((file) => (
          <li key={file.path}>
            <button
              type="button"
              className={
                selected === file.path
                  ? "opt-difffile is-quiet selected"
                  : "opt-difffile is-quiet"
              }
              onClick={() => onSelect(file.path)}
              aria-current={selected === file.path}
            >
              <span className="opt-difffile-name" title={file.path}>
                {basename(file.path)}
              </span>
              <span className="opt-difffile-stat muted">unchanged</span>
            </button>
            <span className="opt-difffile-dir">{dirname(file.path)}</span>
          </li>
        ))}
      </ul>
    </nav>
  );
}

function basename(path) {
  return path.slice(path.lastIndexOf("/") + 1);
}

function dirname(path) {
  const cut = path.lastIndexOf("/");
  return cut < 0 ? "" : path.slice(0, cut);
}
