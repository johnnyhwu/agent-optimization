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
        // Open on the first changed file. The alternative is an empty pane
        // beside a populated tree, which reads as a diff that failed to load.
        setPath((current) => {
          const paths = data.files.map((f) => f.path);
          return current && paths.includes(current) ? current : paths[0] || null;
        });
      })
      .catch((e) => setError(e.message));
  }, [runId, stepNo, base]);

  const file = view?.files.find((f) => f.path === path) || null;
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

        {/* The banner has to say the edits were discarded, because everything
            below it looks exactly like an applied change. A reader who skims
            the diff and misses the verdict walks away believing the skill says
            something it does not. */}
        {rejected && (
          <Banner tone="warning" title="These edits were not kept">
            The gate rejected this candidate
            {view.gate_reject_reason === "activation"
              ? " because the agent stopped reading the skill"
              : " because it did not beat the current skill on validation"}
            , and the run continued from step {view.base_step_no}. What follows is
            what the step proposed, not what the skill contains.
          </Banner>
        )}
        {/* Step 0 is a fallback by definition — it is the base — and saying so
            adds a caveat to the one page that has nothing to caveat. */}
        {view.base_is_fallback && view.step_no > 0 && (
          <Banner tone="info" title="Nothing had been accepted yet at this point">
            No earlier candidate had passed the gate, so this step was derived
            from the skill as it arrived. "vs previous" and "vs initial" are the
            same comparison here.
          </Banner>
        )}
        {view.answer_leaks.length > 0 && (
          <Banner tone="error" title="This step may have memorised an answer">
            {plural(view.answer_leaks.length, "added line")} contain a training question’s
            gold answer word for word. That raises training accuracy without
            teaching the agent anything, and validation only catches it if the
            question is genuinely held out. The lines are marked below.
          </Banner>
        )}
        {view.edit_summary && <p className="opt-stepcard-summary">{view.edit_summary}</p>}
      </Card>

      {view.files.length === 0 ? (
        <Card>
          <p className="opt-hint">
            {view.step_no === 0
              ? "The baseline is the skill as it arrived — there is nothing before it to compare against."
              : "This step changed nothing: every edit it proposed was skipped."}
          </p>
        </Card>
      ) : (
        <div className="opt-skilldiff-body">
          <Card padded={false} className="opt-skilldiff-tree">
            <CardHeader title="Files" count={view.files.length} />
            <DiffFileTree
              files={view.files}
              unchanged={view.unchanged_paths}
              selected={path}
              onSelect={setPath}
            />
          </Card>
          <Card padded={false} className="opt-skilldiff-pane">
            <CardHeader title={file ? file.path : "—"} />
            <DiffTable rows={rows} isLeak={leaked} />
          </Card>
        </div>
      )}

      <SkippedEdits reports={view.edit_reports} />
    </div>
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
      <table className="opt-steptable">
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
              <td className="opt-qtext">
                <code>{report.path || "—"}</code>
                {report.path_defaulted && <span className="muted"> (no path given)</span>}
              </td>
              <td className="opt-qtext" title={report.target || report.content_preview}>
                {report.target || report.content_preview || "—"}
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
