import React, { useEffect, useState } from "react";
import { IconAlert, IconCheck, IconRefresh, IconTarget } from "./icons.jsx";
import Button from "./ui/Button.jsx";
import NumberInput from "./ui/NumberInput.jsx";
import { Disclosure } from "./ui/Field.jsx";
import { deriveSkillsUrl, looksUnauthorized, splitHint } from "../agent_endpoints.js";

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
  chatUrl,
  skillsUrl,
  timeoutS,
  version,
  skillCount,
  stale,           // the agent's version when it has moved past the snapshot
  error,
  recent = [],
  onConnect,       // ({ chat_url, skills_url, timeout_s, api_key, auth_header }) => void
  onChangeAgent,   // back to the form; the caller confirms unsaved edits first
  onReload,
  // The chat probe's result, which arrives after the bar is already connected:
  // reading the skill files is what unblocks the screen, and asking the agent a
  // question is slower and costs a model call, so it lands second.
  chatProbe = null,
  chatBusy = false,
  // Optional credentials, kept by the caller so a reconnect does not lose them.
  apiKey: apiKeyValue = "",
  authHeader: authHeaderValue = "",
}) {
  const connected = status === "connected" || status === "fake";
  const [chat, setChat] = useState(chatUrl || "");
  const [skills, setSkills] = useState(skillsUrl || "");
  const [timeout_s, setTimeout_s] = useState(timeoutS ?? "");
  // Blank for almost every agent. Authentication is not part of the agent
  // server contract — see integrations/real/agent_auth.py — so it is a folded
  // panel rather than two more fields in a row everybody has to read past.
  const [apiKey, setApiKey] = useState(apiKeyValue || "");
  const [authHeader, setAuthHeader] = useState(authHeaderValue || "");
  // Opened by a refusal, never closed by one: it must not shut under somebody
  // who opened it to type.
  const [authOpen, setAuthOpen] = useState(false);
  const refused = looksUnauthorized({ error }) || looksUnauthorized(chatProbe?.chat);
  useEffect(() => {
    if (refused) setAuthOpen(true);
  }, [refused]);

  if (status === "fake") {
    return (
      <div className="agent-bar is-fake">
        <span className="agent-dot" />
        <div className="agent-bar-main">
          <strong>Demo agent</strong>
          <span className="hint">
            Demo mode — a built-in simulated agent, with canned skill
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
          {/* Both endpoints, not just one. They can point at different hosts,
              and "connected" that names one of two addresses is a claim about
              an agent nobody can identify from the bar. */}
          <span className="agent-url" title={chatUrl}>{chatUrl}</span>
          <span className="agent-chat-state">
            {chatBusy ? (
              <span className="hint">testing…</span>
            ) : chatProbe?.chat?.ok === false ? (
              <span className="error-text" title={chatProbe.chat.error}>
                <IconAlert size={13} /> not answering
              </span>
            ) : chatProbe?.override?.ok === false ? (
              // Worth saying and not worth blocking: asking questions of the
              // deployed skills still works, and this check has real false
              // positives — a refusal, a tool that did not load.
              <span className="amber-text" title={chatProbe.override.error}>
                <IconAlert size={13} /> edits may not apply
              </span>
            ) : chatProbe?.chat?.ok ? (
              <span className="ok-text">
                <IconCheck size={13} /> answering
              </span>
            ) : null}
          </span>
          <span className="agent-url" title={skillsUrl}>{skillsUrl}</span>
          <span className="agent-meta">
            {version ? (
              // A version this platform computed from the skill files, because
              // the agent supplied none. Worth saying: it moves when a skill
              // file changes and stays put when the agent's model or prompt
              // does, so the staleness check below it is only half a check.
              <code
                className="agent-version"
                title={
                  version.startsWith("sha256.")
                    ? "Derived from the skill files — this agent reports no version of its own, so a model or prompt change will not be noticed."
                    : "Reported by the agent server."
                }
              >
                {version}
                {version.startsWith("sha256.") && (
                  <span className="agent-version-derived"> derived</span>
                )}
              </code>
            ) : null}
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
          <Button
            size="sm"
            icon={<IconRefresh size={13} />}
            onClick={onReload}
            title="Re-read this agent's skill files"
          >
            Reload
          </Button>
          <Button size="sm" onClick={onChangeAgent}>Change agent</Button>
        </div>
      </div>
    );
  }

  const busy = status === "connecting";
  const submit = () => {
    const trimmed = chat.trim();
    if (!trimmed || busy) return;
    const n = Number(timeout_s);
    onConnect({
      chat_url: trimmed,
      skills_url: skills.trim(),
      // Blank means the environment's, the same as it does everywhere else here.
      timeout_s: timeout_s === "" || !Number.isFinite(n) || n <= 0 ? null : n,
      api_key: apiKey,
      auth_header: authHeader.trim(),
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
            The playground reads this agent's skill files before you
            can ask it anything.
          </span>
        </div>

        <div className="agent-bar-fields">
          <div className="field">
            <label htmlFor="agent-chat-url">Chat endpoint</label>
            <input
              id="agent-chat-url"
              value={chat}
              placeholder="http://agent-host:8080/v1/chat/completions"
              autoFocus
              spellCheck={false}
              onChange={(e) => setChat(e.target.value)}
              // Offered, not forced: it fills a field nobody has typed in, and
              // only when the chat URL sits at the conventional path. A guess
              // written over somebody's own URL is help that loses work.
              onBlur={(e) => {
                if (skills.trim()) return;
                const guess = deriveSkillsUrl(e.target.value);
                if (guess) setSkills(guess);
              }}
              onKeyDown={(e) => e.key === "Enter" && submit()}
            />
          </div>
          <div className="field">
            <label htmlFor="agent-skills-url">Skills endpoint</label>
            <input
              id="agent-skills-url"
              value={skills}
              placeholder="http://agent-host:8080/skills"
              spellCheck={false}
              onChange={(e) => setSkills(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && submit()}
            />
          </div>
          <div className="field agent-bar-timeout">
            <label htmlFor="agent-timeout">Timeout (sec)</label>
            <NumberInput
              id="agent-timeout"
              min="1"
              value={timeout_s}
              onChange={(e) => setTimeout_s(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && submit()}
            />
          </div>
          <Button variant="primary" disabled={!chat.trim()} loading={busy} onClick={submit}>
            {busy ? "Connecting…" : "Connect"}
          </Button>
        </div>

        {/* Folded, because most agent servers ask for no credential and two
            more fields in this row would be a question everybody has to decide
            not to answer. Opened by a refusal from the connect attempt, which
            is the only moment anyone has a reason to look for it. */}
        <Disclosure
          summary="Authentication"
          detail="Optional"
          className="agent-bar-auth"
          open={authOpen}
          onOpenChange={setAuthOpen}
        >
          <div className="agent-bar-fields">
            <div className="field">
              <label htmlFor="agent-api-key">API key</label>
              <input
                id="agent-api-key"
                type="password"
                autoComplete="new-password"
                value={apiKey}
                placeholder="Most agents need none"
                spellCheck={false}
                onChange={(e) => setApiKey(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && submit()}
              />
            </div>
            <div className="field">
              <label htmlFor="agent-auth-header">Auth header</label>
              <input
                id="agent-auth-header"
                value={authHeader}
                placeholder="Authorization"
                spellCheck={false}
                onChange={(e) => setAuthHeader(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && submit()}
              />
            </div>
          </div>
        </Disclosure>

        {/* Offered rather than applied: prefilling one of these is a shortcut,
            connecting to it on your behalf is a guess about which agent you
            meant. */}
        {recent.length > 0 && (
          <div className="agent-bar-recent">
            <span className="hint">Recent:</span>
            {recent.map((a) => (
              <button
                key={a.chat_url}
                className="ui-btn ui-btn-link"
                onClick={() => {
                  setChat(a.chat_url);
                  setSkills(a.skills_url || "");
                  setTimeout_s(a.timeout_s ?? "");
                }}
              >
                {a.chat_url}
              </button>
            ))}
          </div>
        )}

        {/* The agent server's own words, not a summary of them: "no skills here"
            and "your URL is wrong" have to stay distinguishable, and only
            the reason it gave can tell them apart. It stays on screen rather
            than passing as a toast, because it is a state to fix, not news. */}
        {status === "error" && error && (
          <>
            {/* The agent's words, then what to do about them on their own line.
                The backend joins the two with a blank line, which HTML
                collapses — so a 401 and "add an API key" ran together into one
                sentence with the advice at the end, where it is least read. */}
            <div className="hint error-text agent-bar-error">
              <IconAlert size={13} /> {splitHint(error).message}
            </div>
            {splitHint(error).hint && (
              <div className="hint agent-bar-error">{splitHint(error).hint}</div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
