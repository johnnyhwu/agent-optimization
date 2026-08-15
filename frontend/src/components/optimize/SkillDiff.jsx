import React, { useEffect, useMemo, useState } from "react";
import { api } from "../../api.js";
import Badge from "../ui/Badge.jsx";
import Banner from "../ui/Banner.jsx";
import Button from "../ui/Button.jsx";
import Card, { CardHeader } from "../ui/Card.jsx";
import Skeleton from "../ui/Skeleton.jsx";
import { SegmentedControl } from "../ui/Toolbar.jsx";
import { IconArrowLeft, IconDownload } from "../icons.jsx";
import { plural } from "../../plural.js";
import { useToast } from "../Toast.jsx";
import { diffRows } from "../../diff.js";
import DiffFileTree from "./DiffFileTree.jsx";

// Part 2: what this step did to the skill.
//
// The page answers two different questions and the toggle is which one:
//
//   vs previous   what *this step* changed, against the last skill the gate
//                 accepted — which is usually not the previous step number,
//                 because a rejected step is rolled back.
//   vs initial    what this run's skill now contains that the original did not.
//                 The view to read before deploying: every individual step can
//                 be reasonable and the total still be somewhere you would not
//                 have gone deliberately.
//
// The line counts are the server's. The rows are ours. Both sides compute a
// real longest common subsequence so the two cannot disagree — see the note at
// the top of `src/diff.js`.

export default function SkillDiff({ runId, stepNo, onBack }) {
  const toast = useToast();
  const [base, setBase] = useState("parent");
  const [view, setView] = useState(null);
  const [error, setError] = useState(null);
  const [path, setPath] = useState(null);
  const [downloading, setDownloading] = useState(false);

  // Offered here as well as on the overview because this is where someone
  // decides they want these particular edits — a step the gate rejected is
  // downloadable too, and reading its diff is the reason to want it.
  async function download() {
    setDownloading(true);
    try {
      toast.success(`Saved ${await api.downloadOptimizedSkill(runId, stepNo)}`);
    } catch (e) {
      toast.error(e.message);
    } finally {
      setDownloading(false);
    }
  }

  useEffect(() => {
    setView(null);
    setError(null);
    api
      .getStepSkillDiff(runId, stepNo, base)
      .then((data) => {
        setView(data);
        // Open on the first changed file, falling back to an unchanged one so
        // a step that edited nothing still opens on a real (all-context) diff
        // rather than on an empty pane beside a populated tree.
        setPath((current) => {
          const paths = [...data.files, ...(data.unchanged_files || [])].map((f) => f.path);
          return current && paths.includes(current) ? current : paths[0] || null;
        });
      })
      .catch((e) => setError(e.message));
  }, [runId, stepNo, base]);

  // Changed files first, then the untouched ones — both are selectable, and a
  // diff of a file against itself is a legitimate thing to want to read.
  const allFiles = view ? [...view.files, ...(view.unchanged_files || [])] : [];
  const file = allFiles.find((f) => f.path === path) || null;
  const rows = useMemo(
    () => (file ? diffRows(file.before, file.after) : []),
    [file],
  );
  // Leaks are reported per file, so the marking has to be scoped to the file on
  // screen — the same sentence can legitimately appear in a reference document
  // and be a memorised answer in SKILL.md.
  const leaked = useMemo(() => {
    const here = (view?.answer_leaks || []).filter((leak) => leak.path === path);
    return (line) => line != null && here.some((leak) => line.includes(leak.answer));
  }, [view, path]);

  if (error) return <Banner tone="error" title="Could not load this diff">{error}</Banner>;
  if (!view) return <Skeleton variant="row" count={6} />;

  const rejected = view.gate_action === "reject";

  return (
    <div className="opt-skilldiff">
      <Card>
        <CardHeader
          title={`${view.step_no === 0 ? "Baseline" : `Step ${view.step_no}`} · what changed in the skill`}
          actions={
            <>
              <SegmentedControl
                value={base}
                onChange={setBase}
                ariaLabel="Diff baseline"
                size="sm"
                options={[
                  {
                    value: "parent",
                    label: "vs previous",
                    title: "Against the last skill the gate accepted — what this step alone changed",
                  },
                  {
                    value: "initial",
                    label: "vs initial",
                    title: "Against the skill this run started with — everything the run has done so far",
                  },
                ]}
              />
              {view.step_no > 0 && (
                <Button
                  variant="ghost"
                  icon={<IconDownload size={15} />}
                  loading={downloading}
                  onClick={download}
                >
                  Download this skill
                </Button>
              )}
              <Button variant="ghost" icon={<IconArrowLeft size={15} />} onClick={onBack}>
                Back to the run
              </Button>
            </>
          }
        />
        <div className="opt-run-meta">
          <Badge tone={rejected ? "neutral" : view.gate_action ? "success" : "info"} size="sm">
            {view.gate_action ? view.gate_action.replace(/_/g, " ") : "no gate verdict"}
          </Badge>
          {view.is_best && <Badge tone="success" size="sm">best by validation</Badge>}
          <span>
            <span className="added">+{view.lines_added}</span>{" "}
            <span className="removed">−{view.lines_removed}</span> across{" "}
            {plural(view.files.length, "file")}
          </span>
          {view.n_edits_applied != null && (
            <span>
              {plural(view.n_edits_applied, "edit")} applied
              {view.n_edits_skipped ? `, ${view.n_edits_skipped} skipped` : ""}
            </span>
          )}
          <span className="muted">
            compared against{" "}
            {view.base_step_no === 0 ? "the initial skill" : `step ${view.base_step_no}`}
          </span>
        </div>

        {/* One conclusion, not a stack of banners.
            This was two: "These edits were not kept" and, immediately under it,
            "Nothing had been accepted yet at this point". They are two halves of
            the same fact — what happened to this step's edits, and what the diff
            below is therefore measured against — and as separate coloured blocks
            they read as two independent problems. */}
        <Outcome view={view} rejected={rejected} />

        {view.answer_leaks.length > 0 && (
          <Banner tone="error" title="This step may have memorised an answer">
            {plural(view.answer_leaks.length, "added line")} contain a training question’s
            gold answer word for word. That raises training accuracy without
            teaching the agent anything, and validation only catches it if the
            question is genuinely held out. The lines are marked below.
          </Banner>
        )}
        {/* Attributed, so the analyst's sentence about its own patch is not
            mistaken for the page's. */}
        {view.edit_summary && (
          <div className="opt-rationale">
            <span className="opt-rationale-label">Analyst's rationale</span>
            <p>{view.edit_summary}</p>
          </div>
        )}
      </Card>

      {/* Above the diff, not below it.
          What was proposed and refused is the context for reading what landed —
          a diff missing the edit you expected is explained by this table, and at
          the bottom of the page it was read after the reader had already formed
          a conclusion from the diff, if it was read at all. */}
      <SkippedEdits reports={view.edit_reports} />

      <div className="opt-skilldiff-body">
        <Card padded={false} className="opt-skilldiff-tree">
          <CardHeader title="Files" count={allFiles.length} />
          <DiffFileTree
            files={view.files}
            unchanged={view.unchanged_files || []}
            selected={path}
            onSelect={setPath}
          />
        </Card>
        <Card padded={false} className="opt-skilldiff-pane">
          <CardHeader title={file ? file.path : "—"} variant="data" />
          {/* Rendered whether or not anything changed. A step that edited
              nothing used to replace this whole pane with one sentence, so the
              page's layout depended on the outcome — and the reader lost the
              ability to read the file at all on exactly the steps where "what
              does it say now?" is the question. Both sides are identical and
              every row is context, which is what "no change" looks like. */}
          <DiffTable rows={rows} isLeak={leaked} />
        </Card>
      </div>
    </div>
  );
}

// What became of this step's edits, as one statement.
//
// Three facts have to arrive together or the diff below is misread: whether the
// edits were kept, why not if not, and what the diff is being measured against.
// They used to be a warning banner and an info banner stacked on top of each
// other, which is two colours and two headings for one outcome — and a reader
// who took the second as a separate warning came away thinking two things had
// gone wrong.
function Outcome({ view, rejected }) {
  const baseline = view.step_no === 0;
  if (baseline) {
    return (
      <Banner tone="info" title="This is where the run started">
        The baseline is the skill as it arrived. Nothing has been edited at this
        point, so there is nothing before it to compare against — every later
        step's diff is measured from here.
      </Banner>
    );
  }

  const nothingChanged = view.files.length === 0;
  const against =
    view.base_step_no === 0 ? "the skill this run started with" : `step ${view.base_step_no}`;
  // The fallback note, folded in as a clause rather than raised as its own
  // banner: it is a fact about the comparison, not a second problem.
  const fallback = view.base_is_fallback
    ? " No candidate had passed the gate yet, so “vs previous” and “vs initial” are the same comparison here."
    : "";

  if (nothingChanged) {
    return (
      <Banner tone="warning" title="This step changed nothing">
        Every edit it proposed was refused before it reached the skill — the
        table below says why for each one. The skill it produced is identical to{" "}
        {against}, which is also why no validation rollout was bought for it: that
        skill's score was already known.{fallback}
      </Banner>
    );
  }

  if (rejected) {
    return (
      <Banner tone="warning" title="These edits were not kept">
        The gate rejected this candidate
        {view.gate_reject_reason === "activation"
          ? " because the agent stopped reading the skill"
          : " because it did not beat the current skill on validation"}
        , and the run carried on from {against}. What follows is what the step
        proposed, not what the skill contains.{fallback}
      </Banner>
    );
  }

  return (
    <Banner tone="success" title="These edits were kept">
      The gate accepted this candidate
      {view.gate_action === "accept_new_best"
        ? " as a new best on validation"
        : ", though it did not beat the best score so far"}
      . The diff below is what the skill gained over {against}.{fallback}
    </Banner>
  );
}

function DiffTable({ rows, isLeak }) {
  return (
    <div className="opt-diff-scroll">
      <table className="opt-diff">
        <tbody>
          {rows.map((row, i) => {
            const leak = row.type !== "del" && isLeak(row.right);
            return (
              <tr key={i} className={leak ? `${row.type} leak` : row.type}>
                <td className="opt-diff-no">{row.leftNo ?? ""}</td>
                <td className="opt-diff-text">{row.left}</td>
                <td className="opt-diff-no">{row.rightNo ?? ""}</td>
                <td className="opt-diff-text">
                  {row.right}
                  {leak && (
                    <Badge tone="danger" size="sm" title="Contains a training question's gold answer verbatim">
                      gold answer
                    </Badge>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

// The edits that were proposed and never reached the skill. The reason is
// decided while the patch is applied and cannot be worked out afterwards from
// the two snapshots, which is why it is stored rather than derived: "the model
// had a bad idea" and "the model mistyped the line it meant to replace" are
// different problems, and only this tells them apart.
function SkippedEdits({ reports }) {
  const skipped = reports.filter((r) => !r.status.startsWith("applied"));
  if (!skipped.length) return null;
  return (
    <Card>
      <CardHeader title="Edits that were not applied" count={skipped.length} />
      {/* `opt-qtext` is `display: block`, so putting it on a `<td>` takes that
          cell out of the table's column model. Two of them in a row here, and
          the file and the target stacked on top of each other under one column
          while the "What it aimed at" heading sat over an empty one. It belongs
          on a span inside the cell; the widths belong to the table. */}
      <table className="opt-steptable opt-skipped">
        <colgroup>
          <col className="opt-skipped-no" />
          <col className="opt-skipped-reason" />
          <col className="opt-skipped-file" />
          <col />
        </colgroup>
        <thead>
          <tr>
            <th className="num">#</th>
            <th>Reason</th>
            <th>File</th>
            <th>What it aimed at</th>
          </tr>
        </thead>
        <tbody>
          {skipped.map((report, i) => (
            <tr key={report.index ?? i}>
              <td className="num">{report.index ?? "—"}</td>
              <td>
                <Badge tone="warning" size="sm">{REASONS[report.status] || report.status}</Badge>
              </td>
              <td>
                <code className="opt-qtext">{report.path || "—"}</code>
                {report.path_defaulted && <span className="muted"> (no path given)</span>}
              </td>
              <td title={report.target || report.content_preview}>
                <span className="opt-qtext">
                  {report.target || report.content_preview || "—"}
                </span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  );
}

const REASONS = {
  skipped_invalid_path: "path is outside this skill",
  skipped_readonly_file: "this mode does not edit that file",
  skipped_append_not_allowed: "append is not allowed in this mode",
  skipped_protected_region: "protected region",
  skipped_would_empty_entry_point: "would have emptied SKILL.md",
  skipped_replace_target_not_found: "target text was not in the file",
  error: "the edit could not be read",
};
