import React, { useState } from "react";
import { IconAlert, IconCheck, IconClock, IconPlay, IconX } from "./icons.jsx";
import Button from "./ui/Button.jsx";
import Banner, { BannerDetail } from "./ui/Banner.jsx";

// The block that appears when the chosen file is a `.py`, and only then.
//
// Two design decisions worth stating, because both are load-bearing for keeping
// the dialog simple rather than merely making this feature fit in it:
//
// **The checks arrive in two waves, and the panel says which is which.** The
// static ones (does it parse, is there a `main`, does it take one argument) come
// back from the server the instant the file is chosen — no database, no
// execution. Only when those pass does the connection form appear. Nobody should
// type a production database password to find out they forgot `main()`.
//
// **The connection fields are inline, not a second modal.** A dialog on top of a
// dialog would cover the name, metadata and share list the user has just filled
// in, and reads as leaving the task rather than finishing it. Five fields in
// place is less machinery and less to look at.
//
// Marks are `IconCheck`/`IconAlert`/`IconX`, not ✅/⚠️/❌. See the comment at the
// top of ui/Banner.jsx: literal emoji render in the system font at whatever size
// and baseline that font decides, next to SVG icons drawn to sit on the text
// baseline, and this codebase removed the last of them on purpose.

function formatDuration(ms) {
  if (ms < 1000) return `${Math.max(1, Math.round(ms))}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

const MARK = {
  pass: { Icon: IconCheck, tone: "ok" },
  warn: { Icon: IconAlert, tone: "warn" },
  fail: { Icon: IconX, tone: "bad" },
  skipped: { Icon: IconClock, tone: "muted" },
};

function CheckList({ checks, title }) {
  if (!checks || checks.length === 0) return null;
  return (
    <div className="script-checks">
      <span className="script-checks-title hint">{title}</span>
      <ul>
        {checks.map((c) => {
          const { Icon, tone } = MARK[c.status] || MARK.skipped;
          return (
            <li key={c.id} className={`script-check script-check-${tone}`}>
              <Icon size={14} />
              <span className="grow">
                <span className="script-check-label">{c.label}</span>
                {c.detail && <span className="script-check-detail">{c.detail}</span>}
              </span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

export default function ScriptRunPanel({
  fileName,
  validation, // { ok, checks } from /script/validate, or null while loading
  connection,
  setConnection,
  onRun,
  running,
  result, // ScriptRunOut from /script/run, or null before the first run
}) {
  const [showOutput, setShowOutput] = useState(false);
  const set = (field) => (e) => setConnection({ ...connection, [field]: e.target.value });

  const output = [result?.stdout, result?.stderr].filter(Boolean).join("\n");
  // Scripts print with a trailing newline, so a naive split counts one line more
  // than was printed — and "2 lines" over a one-line output is the kind of small
  // wrongness that makes people distrust the rest of the numbers on screen.
  const outputLines = output.replace(/\n+$/, "").split("\n").length;
  const canRun =
    validation?.ok &&
    connection.host.trim() &&
    connection.database.trim() &&
    connection.user.trim();

  return (
    <div className="script-panel">
      <CheckList checks={validation?.checks} title={`Script checks · ${fileName}`} />

      {validation && !validation.ok && (
        <p className="hint script-panel-blocked">
          Fix the items marked above and choose the file again. Nothing is run, and
          no database is contacted, until the script has the right shape.
        </p>
      )}

      {validation?.ok && (
        <>
          <div className="script-conn">
            <span className="script-conn-title hint">
              Database connection <span className="hint">· used for this run only, never stored</span>
            </span>
            <div className="script-conn-grid">
              <label className="field script-conn-host">
                <span>Host</span>
                <input
                  value={connection.host}
                  onChange={set("host")}
                  placeholder="warehouse.internal"
                  autoComplete="off"
                />
              </label>
              <label className="field script-conn-port">
                <span>Port</span>
                <input
                  value={connection.port}
                  onChange={set("port")}
                  inputMode="numeric"
                  placeholder="5432"
                  autoComplete="off"
                />
              </label>
              <label className="field">
                <span>Database</span>
                <input
                  value={connection.database}
                  onChange={set("database")}
                  placeholder="sales"
                  autoComplete="off"
                />
              </label>
              <label className="field">
                <span>User</span>
                <input
                  value={connection.user}
                  onChange={set("user")}
                  placeholder="reader"
                  autoComplete="off"
                />
              </label>
              <label className="field">
                <span>Password</span>
                <input
                  type="password"
                  value={connection.password}
                  onChange={set("password")}
                  autoComplete="new-password"
                />
              </label>
            </div>
            <div className="script-conn-run">
              <span className="hint">
                Read-only. The script cannot write to this database, and never sees
                these credentials.
              </span>
              <Button
                variant="primary"
                icon={<IconPlay size={14} />}
                loading={running}
                disabled={!canRun}
                onClick={onRun}
              >
                {running ? "Running…" : result ? "Run again" : "Run script"}
              </Button>
            </div>
          </div>

          {result && !result.ok && result.error && (
            <Banner tone="error" title="The script did not produce rows">
              <p>{result.error}</p>
              {result.traceback && <BannerDetail>{result.traceback}</BannerDetail>}
            </Banner>
          )}

          {result && result.ok && (
            <CheckList
              title="Run"
              checks={[
                {
                  id: "ran",
                  status: "pass",
                  // Milliseconds below a second: a script that finished in 40ms
                  // rendered as "Completed in 0.0s", which reads as "nothing
                  // happened" rather than "that was fast".
                  label: `Completed in ${formatDuration(result.duration_ms)}`,
                  detail:
                    result.query_count === 1
                      ? "1 query"
                      : `${result.query_count} queries`,
                },
                {
                  id: "rows",
                  status: result.rows.length ? "pass" : "fail",
                  label: `Returned ${result.rows.length.toLocaleString()} usable row${
                    result.rows.length === 1 ? "" : "s"
                  }`,
                  detail: "",
                },
                ...(result.warnings.length
                  ? [
                      {
                        id: "warnings",
                        status: "warn",
                        label: `${result.warnings.length} item${
                          result.warnings.length === 1 ? "" : "s"
                        } were skipped`,
                        detail: result.warnings.slice(0, 3).join(" · "),
                      },
                    ]
                  : []),
              ]}
            />
          )}

          {result?.limits_hit?.map((note) => (
            // Separate from the per-row warnings above on purpose: these are our
            // ceilings rather than the user's data, and silently folding them in
            // with row problems is how someone ends up with 3,000 of 4,800 rows
            // and no idea it happened.
            <Banner key={note} tone="warning" title="Limit reached">
              {note}
            </Banner>
          ))}

          {result?.warnings?.length > 0 && (
            <details className="script-details">
              <summary>
                {result.warnings.length} skipped item
                {result.warnings.length === 1 ? "" : "s"}
              </summary>
              <ul className="script-warning-list">
                {result.warnings.map((w, i) => (
                  <li key={i}>{w}</li>
                ))}
              </ul>
            </details>
          )}

          {output && (
            // Collapsed, and absent entirely when the script printed nothing —
            // most do. Open by default only when the run failed, which is when
            // the prints are the reason they are there.
            <details
              className="script-details"
              open={showOutput || (result && !result.ok)}
              onToggle={(e) => setShowOutput(e.target.open)}
            >
              <summary>
                Script output ({outputLines} line{outputLines === 1 ? "" : "s"})
              </summary>
              <pre className="script-output">{output}</pre>
            </details>
          )}
        </>
      )}
    </div>
  );
}
