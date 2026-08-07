import React, { useState } from "react";
import { IconAlert, IconRefresh, IconTarget } from "./icons.jsx";
import Button from "./ui/Button.jsx";

// Which agent the playground is talking to.
//
// This used to be two fields inside the "Endpoints & keys" panel, sitting beside
// the Langfuse timeout as though they were the same kind of setting. They are
// not, and the difference is the reason this component exists:
//
//   * An LLM base URL or a judge model is a parameter of *sending a question*.
//     Get it wrong and the next send tells you.
//   * The agent's base URL is the **premise of the whole screen**. Two of the
//     four composer panels — Agent config and Skill files — have no content at
//     all until it is answered, because their content is read from that server.
//
// So the agent is a state the page is in, not a field on a form, and it is on
// screen permanently rather than behind an icon. Connecting is one call
// (GET /playground/workspace): reaching it proves the host is there, that it
// speaks the contract, and hands over the version the staleness check needs.
//
// Two deliberate refusals:
//
//   * **The URL is read-only once connected.** Changing agents invalidates the
//     snapshot every workspace edit is diffed against, so it goes through
//     "Change agent" and the confirm the caller puts on it. As an always-live
//     input it was possible — and quiet — to leave the editor showing agent A's
//     skill files while the next question went to agent B.
//   * **This bar does not gate the page, only the composer.** Attempts live in
//     the backend's memory per subject and have nothing to do with which agent
//     is connected now; blocking the whole screen would mean connecting to
//     something before being allowed to re-read a trace from an hour ago.
export default function AgentConnectionBar({
  status,          // "disconnected" | "connecting" | "connected" | "error" | "fake"
  baseUrl,
  timeoutS,
  version,
  skillCount,
  stale,           // the agent's version when it has moved past the snapshot
  error,
  recent = [],
  onConnect,       // ({ base_url, timeout_s }) => void
  onChangeAgent,   // back to the form; the caller confirms unsaved edits first
  onReload,
}) {
  const connected = status === "connected" || status === "fake";
  const [url, setUrl] = useState(baseUrl || "");
  const [timeout_s, setTimeout_s] = useState(timeoutS ?? "");

  if (status === "fake") {
    return (
      <div className="agent-bar is-fake">
        <span className="agent-dot" />
        <div className="agent-bar-main">
          <strong>Demo agent</strong>
          <span className="hint">
            Demo mode — a built-in simulated agent, with canned config and skill
            files. No URL needed.
          </span>
        </div>
      </div>
    );
  }

  if (connected) {
    return (
      <div className={`agent-bar ${stale ? "is-stale" : "is-connected"}`}>
        <span className="agent-dot" />
        <div className="agent-bar-main">
          <span className="agent-url" title={baseUrl}>{baseUrl}</span>
          <span className="agent-meta">
            {version ? <code className="agent-version">{version}</code> : null}
            {skillCount != null && (
              <span className="hint">
                {skillCount} skill file{skillCount === 1 ? "" : "s"}
              </span>
            )}
          </span>
        </div>
        {stale && (
          <span className="hint amber-text">
            <IconAlert size={13} /> this agent has moved on to{" "}
            <code className="agent-version">{stale}</code>
          </span>
        )}
        <div className="agent-bar-actions">
          <Button size="sm" onClick={onReload} title="Re-read this agent's config and skill files">
            <IconRefresh size={13} /> Reload
          </Button>
          <Button size="sm" onClick={onChangeAgent}>Change agent</Button>
        </div>
      </div>
    );
  }

  const busy = status === "connecting";
  const submit = () => {
    const trimmed = url.trim();
    if (!trimmed || busy) return;
    const n = Number(timeout_s);
    onConnect({
      base_url: trimmed,
      // Blank means the environment's, the same as it does everywhere else here.
      timeout_s: timeout_s === "" || !Number.isFinite(n) || n <= 0 ? null : n,
    });
  };

  return (
    <div className={`agent-bar is-form ${status === "error" ? "is-error" : ""}`}>
      <span className="agent-dot" />
      <div className="agent-bar-form">
        <div className="agent-bar-head">
          <IconTarget size={14} />
          <strong>Target agent</strong>
          <span className="hint">
            The playground reads this agent's config and skill files before you
            can ask it anything.
          </span>
        </div>

        <div className="agent-bar-fields">
          <div className="field">
            <label htmlFor="agent-url">Agent Base URL</label>
            <input
              id="agent-url"
              value={url}
              placeholder="http://agent-host:8080"
              autoFocus
              spellCheck={false}
              onChange={(e) => setUrl(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && submit()}
            />
          </div>
          <div className="field agent-bar-timeout">
            <label htmlFor="agent-timeout">Timeout (sec)</label>
            <input
              id="agent-timeout"
              type="number"
              min="1"
              value={timeout_s}
              onChange={(e) => setTimeout_s(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && submit()}
            />
          </div>
          <Button variant="primary" disabled={!url.trim()} loading={busy} onClick={submit}>
            {busy ? "Connecting…" : "Connect"}
          </Button>
        </div>

        {/* Offered rather than applied: prefilling one of these is a shortcut,
            connecting to it on your behalf is a guess about which agent you
            meant. */}
        {recent.length > 0 && (
          <div className="agent-bar-recent">
            <span className="hint">Recent:</span>
            {recent.map((a) => (
              <button
                key={a.base_url}
                className="ui-btn ui-btn-link"
                onClick={() => {
                  setUrl(a.base_url);
                  setTimeout_s(a.timeout_s ?? "");
                }}
              >
                {a.base_url}
              </button>
            ))}
          </div>
        )}

        {/* The agent server's own words, not a summary of them: "no skills here"
            and "your URL is wrong" have to stay distinguishable, and only
            the reason it gave can tell them apart. It stays on screen rather
            than passing as a toast, because it is a state to fix, not news. */}
        {status === "error" && error && (
          <div className="hint error-text agent-bar-error">
            <IconAlert size={13} /> {error}
          </div>
        )}
      </div>
    </div>
  );
}
