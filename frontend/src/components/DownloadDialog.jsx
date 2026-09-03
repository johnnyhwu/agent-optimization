import React, { useEffect, useMemo, useState } from "react";
import { api } from "../api.js";
import Modal from "./Modal.jsx";
import { useToast } from "./Toast.jsx";
import { IconDownload, IconRefresh } from "./icons.jsx";
import Button from "./ui/Button.jsx";
import Badge from "./ui/Badge.jsx";
import Banner, { BannerDetail } from "./ui/Banner.jsx";

// Downloading an eval set (§6.13 card action).
//
// **The dialog is a preview of the output, not a list of options.** That is the
// whole design. A scope picker made of abstract nouns ("eval set", "runs")
// leaves the developer to translate selections into files, and the uncertainty
// lives in that translation — you press Download to find out what you get. Here
// the thing you tick *is* the file you receive: it is named, its real columns
// are printed, and its row count is a number the server counted.
//
// Consequences worth keeping if this is ever rearranged:
//
// * Column names come from `GET /export/preview`, never from a copy here. The
//   panel's only value is being trusted, and a stale column list destroys that
//   more thoroughly than showing nothing would.
// * Counts include the awkward ones — questions still running, traces not yet
//   ingested. Rounding those away is how a preview stops being believed.
// * One file selected downloads as that file. "Do I get a file or a zip?" is
//   its own small uncertainty, so the header always names the exact artefact.
// * Choices are remembered per user, which is what makes a single Download
//   button (rather than a quick menu that skips the preview) cheap on repeat.

const PREFS_KEY = "export-prefs";

const RUN_SCOPES = [
  ["latest", "Latest run"],
  ["latest_n", "Latest 5"],
  ["all", "All runs"],
  ["selected", "Choose…"],
];

function loadPrefs(subject) {
  try {
    const raw = localStorage.getItem(`${PREFS_KEY}:${subject || "anon"}`);
    const parsed = raw ? JSON.parse(raw) : null;
    if (parsed && typeof parsed === "object") return parsed;
  } catch {
    // A corrupt entry is not worth breaking the dialog over.
  }
  return {};
}

function savePrefs(subject, prefs) {
  try {
    localStorage.setItem(`${PREFS_KEY}:${subject || "anon"}`, JSON.stringify(prefs));
  } catch {
    // Private-mode storage failures must not block the download itself.
  }
}

// The server stamps filenames in UTC (services/export.today_stamp), so the
// preview has to as well or the name shown differs from the name saved.
function todayStamp() {
  return new Date().toISOString().slice(0, 10);
}

function plural(n, word) {
  return `${n.toLocaleString()} ${word}${n === 1 ? "" : "s"}`;
}

// Columns read as prose, not code: a `·` separator wraps naturally where a
// comma-joined header would run off the panel.
function Columns({ names }) {
  return <div className="dl-cols">{(names || []).join(" · ")}</div>;
}

export default function DownloadDialog({ evalSet, subject, seedRunIds = [], onClose }) {
  const toast = useToast();
  const prefs = useMemo(() => loadPrefs(subject), [subject]);

  const [sel, setSel] = useState(() => ({
    questions: prefs.questions !== false,
    runs: prefs.runs !== false,
    traces: Boolean(prefs.traces),
  }));
  const [fmt, setFmt] = useState(prefs.fmt === "jsonl" ? "jsonl" : "csv");
  // Arriving from a run history with runs ticked means those runs are what the
  // developer is already looking at — carrying the selection over is the
  // context handoff that makes the second entry point worth having.
  const [runScope, setRunScope] = useState(
    seedRunIds.length ? "selected" : prefs.runScope || "latest_n"
  );
  const [runIds, setRunIds] = useState(seedRunIds);
  const [runChoices, setRunChoices] = useState(null);

  const [preview, setPreview] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  // Counts depend only on which runs are in scope, so ticking a file does not
  // refetch. Responses can land out of order when the selector is clicked
  // quickly; `cancelled` keeps the older one from overwriting the newer.
  useEffect(() => {
    let cancelled = false;
    setError(null);
    api
      .exportPreview(evalSet.id, { runScope, runIds, lastN: 5 })
      .then((p) => !cancelled && setPreview(p))
      .catch((e) => !cancelled && setError(e.message));
    return () => {
      cancelled = true;
    };
  }, [evalSet.id, runScope, runIds]);

  // The run list is only needed once "Choose…" is picked, so it is not part of
  // opening the dialog.
  useEffect(() => {
    if (runScope !== "selected" || runChoices !== null) return;
    api
      .listRuns(evalSet.id, { limit: 100 })
      .then((page) => setRunChoices(page.items))
      .catch(() => setRunChoices([]));
  }, [runScope, runChoices, evalSet.id]);

  const ext = fmt === "jsonl" ? "jsonl" : "csv";
  const cols = preview?.columns || {};
  const noRuns = preview ? preview.total_runs === 0 : false;
  const noTraces = preview ? preview.traces === 0 : false;

  // What is *actually* going to be requested. A remembered preference can name
  // a file this set cannot produce — "runs" ticked from last time, on a set
  // with no runs — and every other derived value has to agree with the
  // disabled checkbox rather than with the stale preference. Reading `sel`
  // directly here is what made the header promise a zip for a lone CSV.
  const eff = {
    questions: sel.questions,
    runs: sel.runs && !noRuns,
    traces: sel.traces && !noTraces,
  };

  // Mirrors the endpoint's own assembly: `runs` writes two files, and a bundle
  // of more than one file gains a manifest and becomes a zip.
  const fileCount =
    (eff.questions ? 1 : 0) + (eff.runs ? 2 : 0) + (eff.traces ? 1 : 0);
  const single = fileCount === 1;
  const stem = preview?.filename_stem || "eval-set";
  const singleName = eff.questions
    ? `questions.${ext}`
    : eff.traces
      ? "traces.json"
      : null;
  const artefact = single
    ? `${stem}-${singleName.replace(/\.[^.]+$/, "")}-${todayStamp()}.${
        singleName.split(".").pop()
      }`
    : `${stem}-${todayStamp()}.zip`;

  const toggle = (key) => setSel((s) => ({ ...s, [key]: !s[key] }));
  const toggleRunId = (id) =>
    setRunIds((ids) => (ids.includes(id) ? ids.filter((x) => x !== id) : [...ids, id]));

  async function start() {
    setBusy(true);
    setError(null);
    savePrefs(subject, { ...sel, fmt, runScope });
    try {
      const filename = await api.downloadExport(evalSet.id, {
        questions: eff.questions,
        runs: eff.runs,
        traces: eff.traces,
        fmt,
        runScope,
        runIds,
        lastN: 5,
      }, artefact);
      toast.success(`Downloaded ${filename}`);
      onClose();
    } catch (e) {
      setError(e.message);
      toast.error("Download failed");
    } finally {
      setBusy(false);
    }
  }

  const count = (value) => (preview ? value.toLocaleString() : "…");

  return (
    <Modal
      title={`Download “${evalSet.name}”`}
      subtitle="Everything below is exactly what the archive will contain."
      onClose={onClose}
      width={640}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button
            variant="primary"
            disabled={busy || fileCount === 0 || !preview}
            onClick={start}
          >
            <IconDownload size={15} /> {busy ? "Preparing…" : "Download"}
          </Button>
        </>
      }
    >
      {error && (
        <Banner tone="error" title="Could not prepare the download">
          <BannerDetail>{error}</BannerDetail>
        </Banner>
      )}

      <div className="dl-artefact">
        <span className="muted">You’ll get</span>
        <code>{fileCount === 0 ? "— nothing selected —" : artefact}</code>
      </div>

      <div className="dl-files">
        <label className={`dl-file ${eff.questions ? "on" : ""}`}>
          <input
            type="checkbox"
            checked={eff.questions}
            onChange={() => toggle("questions")}
          />
          <div className="grow">
            <div className="dl-file-head">
              <code>questions.{ext}</code>
              <span className="dl-count">{count(preview?.questions ?? 0)} rows</span>
              {/* The badge is the promise; the footnote below is the part
                  people get wrong, so it is stated rather than implied. */}
              <Badge tone="success" icon={<IconRefresh size={11} />} title="Edit and re-upload to grow this set">
                re-uploadable
              </Badge>
            </div>
            <Columns names={cols.questions} />
          </div>
        </label>

        <div className="dl-group">
          <div className="dl-group-head">
            <span className="dl-group-title">Runs</span>
            <span className="muted">Which runs</span>
            <select
              value={runScope}
              onChange={(e) => setRunScope(e.target.value)}
              disabled={noRuns}
              aria-label="Which runs to export"
              style={{ width: "auto" }}
            >
              {RUN_SCOPES.map(([value, label]) => (
                <option key={value} value={value}>{label}</option>
              ))}
            </select>
            {!noRuns && preview && (
              <span className="muted dl-scope-note">
                {plural(preview.runs, "run")} of {preview.total_runs}
              </span>
            )}
          </div>

          {runScope === "selected" && (
            <div className="dl-runpick">
              {runChoices === null && <div className="hint">Loading runs…</div>}
              {runChoices && runChoices.length === 0 && (
                <div className="hint">No runs to choose from.</div>
              )}
              {(runChoices || []).map((r) => (
                <label key={r.id} className="dl-runpick-row">
                  <input
                    type="checkbox"
                    checked={runIds.includes(r.id)}
                    onChange={() => toggleRunId(r.id)}
                  />
                  <span className="grow">
                    {r.name || new Date(r.started_at).toLocaleString()}
                  </span>
                  <span className="muted">
                    {r.pass_rate === null ? "—" : `${Math.round(r.pass_rate * 100)}%`}
                  </span>
                </label>
              ))}
            </div>
          )}

          <label className={`dl-file ${eff.runs ? "on" : ""} ${noRuns ? "off" : ""}`}>
            <input
              type="checkbox"
              checked={eff.runs}
              disabled={noRuns}
              onChange={() => toggle("runs")}
            />
            <div className="grow">
              <div className="dl-file-head">
                <code>runs.{ext}</code>
                <span className="muted">and</span>
                <code>results.{ext}</code>
                {noRuns ? (
                  <span className="dl-count">no runs yet</span>
                ) : (
                  <span className="dl-count">
                    {count(preview?.runs ?? 0)} + {count(preview?.results ?? 0)} rows
                  </span>
                )}
              </div>
              {/* Run-level facts are kept out of results.* rather than repeated
                  down every row — a pivot table wants them in their own table. */}
              <div className="dl-subfile">
                <code>runs.{ext}</code>
                <Columns names={cols.runs} />
              </div>
              <div className="dl-subfile">
                <code>results.{ext}</code>
                <Columns names={cols.results} />
              </div>
              {preview?.results_running > 0 && (
                <div className="hint warn-text">
                  {plural(preview.results_running, "question")} still running — those
                  rows export with an empty verdict.
                </div>
              )}
            </div>
          </label>

          <label className={`dl-file ${eff.traces ? "on" : ""} ${noTraces ? "off" : ""}`}>
            <input
              type="checkbox"
              checked={eff.traces}
              disabled={noTraces}
              onChange={() => toggle("traces")}
            />
            <div className="grow">
              <div className="dl-file-head">
                <code>traces.json</code>
                {noTraces ? (
                  <span className="dl-count">nothing to fetch yet</span>
                ) : (
                  <>
                    <span className="dl-count">{count(preview?.traces ?? 0)} traces</span>
                    <Badge tone="warning">large · slow</Badge>
                  </>
                )}
              </div>
              <div className="dl-cols">
                agent spans + diagnosis, per question · always JSON
              </div>
              {/* Ingestion lag belongs to Langfuse, not to this system — but it
                  lands on this file, so the number is shown before the click
                  rather than explained afterwards. */}
              {preview && preview.traces > 0 && preview.traces_ready < preview.traces && (
                <div className="hint warn-text">
                  {preview.traces_ready} of {preview.traces} ready — the rest are still
                  being ingested and export with their state recorded.
                </div>
              )}
              {preview?.traces_capped && (
                <div className="hint warn-text">
                  Capped at {preview.max_traces.toLocaleString()}; narrow the run
                  selection to get the rest.
                </div>
              )}
            </div>
          </label>
        </div>

        {!single && fileCount > 0 && (
          <div className="dl-file always">
            <div className="grow">
              <div className="dl-file-head">
                <code>manifest.json</code>
                <span className="muted">always included</span>
              </div>
              <div className="dl-cols">
                source set · export time · what’s included · question-id policy
              </div>
            </div>
          </div>
        )}
      </div>

      <div className="dl-foot-controls">
        <span className="muted">Format</span>
        <div className="ui-segmented is-sm" role="tablist">
          <button type="button" role="tab" aria-selected={fmt === "csv"} className={fmt === "csv" ? "is-active" : ""} onClick={() => setFmt("csv")}>
            CSV
          </button>
          <button type="button" role="tab" aria-selected={fmt === "jsonl"} className={fmt === "jsonl" ? "is-active" : ""} onClick={() => setFmt("jsonl")}>
            JSONL
          </button>
        </div>
        <span className="muted">traces are always JSON</span>
      </div>

      <div className="hint dl-notes">
        <div>
          Join <code>questions</code> and <code>results</code> on{" "}
          <code>(eval_set_id, question_id)</code> — a question id is unique within an
          eval set, not across them.
        </div>
        <div>
          Re-uploading <code>questions.{ext}</code> creates a <strong>new</strong>{" "}
          eval set. It does not update this one — a set is locked after creation.
        </div>
      </div>
    </Modal>
  );
}
