import React from "react";
import { IconAlert, IconCheck, IconInfo, IconRefresh, IconSend } from "./icons.jsx";
import Button, { IconButton } from "./ui/Button.jsx";
import Field, { Disclosure } from "./ui/Field.jsx";
import {
  credentialReachesSkills,
  deriveSkillsUrl,
  splitHint,
} from "../agent_endpoints.js";
import { href } from "../useHashRoute.js";

// The two URLs that name an agent server, wherever they are asked for.
//
// One component for three screens, because the *reading* of these fields is the
// same everywhere even though the gating is not: what a check proves belongs
// here, what a screen does about it belongs to the screen (`agent_endpoints.js`
// holds that policy).
//
// Three things it is deliberately opinionated about:
//
//   * **The skills endpoint is prefilled, never forced.** Most agents keep it
//     beside the chat endpoint, so typing it twice is a tax on the common case;
//     but a guess written into a field reads as a value somebody chose, so it
//     only fills a field the developer has not touched, and only when the chat
//     URL sits at the conventional path.
//   * **The chat endpoint has a test control and the skills endpoint does not.**
//     The asymmetry is cost, not taste: reading a skill listing is a free GET
//     the caller can fire on a keystroke, while the chat probe spends a real
//     model call. Nothing may spend that on somebody's behalf. It is an icon on
//     the field's own row rather than a button on a line of its own — a
//     full-width form with a stray button under one of its inputs reads as an
//     action on the form, not on the address above it.
//   * **The three groups are laid out in the order they are decided in**, each
//     under its own heading and none of them folded away: the credential first,
//     because it applies to both addresses under it, then the endpoint every run
//     needs, then the optional one. This was three nested disclosures — a panel
//     inside a panel inside a panel — which is how a form ends up with more
//     chevrons than fields. The one thing still folded is the raw exchange,
//     which is debug detail rather than a setting.
//   * **The request is shown before it is sent.** An implementer reading the
//     actual bytes finds a field-name mismatch in seconds; the same mismatch
//     hides in a prose spec for an afternoon. So the preview panel is populated
//     from the URL alone, and the response half fills in afterwards.

// One line of status per endpoint. `null` is not a failure — see `check.ok`'s
// tri-state in agent_endpoints.js — so it renders as a plain note, never in red.
function StatusLine({ check, busy, busyLabel }) {
  if (busy) return <div className="agent-ep-status hint">{busyLabel}</div>;
  if (!check) return null;
  if (check.ok === true) {
    return (
      <div className="agent-ep-status ok-text">
        <IconCheck size={13} /> {check.detail || "OK"}
      </div>
    );
  }
  if (check.ok === false) {
    // The server's own words, then what to do about them on a line of their
    // own. Rendering the joined string put a 401 and "add an API key" into one
    // run-on sentence — HTML collapses the blank line between them.
    const { message, hint } = splitHint(check.error);
    return (
      <>
        <div className="agent-ep-status error-text">
          <IconAlert size={13} /> {message}
        </div>
        {hint && <div className="agent-ep-status hint">{hint}</div>}
      </>
    );
  }
  return (
    <div className="agent-ep-status hint">
      <IconInfo size={13} /> {check.detail}
    </div>
  );
}

// The "?" beside a field label. A deep link, not a link to the front of the
// docs: someone clicking this has a specific question, and landing them on a
// table of contents makes them find the answer twice.
function HelpLink({ anchor, label }) {
  return (
    <a
      className="agent-ep-help ui-btn ui-btn-ghost ui-btn-icon"
      href={href.docs("agent-server", anchor)}
      title={label}
      aria-label={label}
    >
      <IconInfo size={14} />
    </a>
  );
}

// One named group of fields inside the agent block. Not a `FormSection`: these
// sit *inside* one, and reusing that heading would give a group the same weight
// as the section containing it.
//
// Exported because the block does not end at this component: the host adds the
// timeout, and a field with no heading after three that have one reads as part
// of whichever group it happens to follow.
export function EndpointGroup({ title, children }) {
  return (
    <div className="agent-ep-group">
      <h5 className="agent-ep-group-title">{title}</h5>
      {children}
    </div>
  );
}

// What was sent and what came back, folded away. Debug detail by default,
// because the one-line status above answers the question most people have.
function Exchange({ request, response }) {
  if (!request && !response) return null;
  return (
    <Disclosure summary="Request and response" className="agent-ep-exchange">
      {request && (
        <>
          <div className="agent-ep-exchange-label">What we send</div>
          <pre className="agent-ep-payload">{request}</pre>
        </>
      )}
      {response && (
        <>
          <div className="agent-ep-exchange-label">What came back</div>
          <pre className="agent-ep-payload">{response}</pre>
        </>
      )}
    </Disclosure>
  );
}

// The credential, and the header it goes in. Rendered only for a screen that
// passes `onChangeApiKey`.
//
// It leads the block rather than trailing it. Most agent servers need no
// credential, which was the case for folding it away — but a panel nobody opens
// is also a panel nobody finds when a server does answer 401, and the field it
// hides applies to both addresses underneath it. Reading top to bottom now
// matches the order the settings are decided in: who am I, where do questions
// go, where are the skills.
function Authentication({
  apiKey,
  authHeader,
  onChangeApiKey,
  onChangeAuthHeader,
  keptPlaceholder,
  chatUrl,
  skillsUrl,
  disabled,
  idPrefix,
}) {
  const reaches = credentialReachesSkills(chatUrl, skillsUrl);
  return (
    <EndpointGroup title="Endpoint authentication">
      <Field
        label="API key"
        htmlFor={`${idPrefix}-api-key`}
        hint={<HelpLink anchor="authentication" label="How the platform sends a credential" />}
        help="Most agent servers need none. Leave this blank and nothing is sent."
      >
        <input
          id={`${idPrefix}-api-key`}
          type="password"
          autoComplete="new-password"
          value={apiKey}
          placeholder={keptPlaceholder}
          spellCheck={false}
          disabled={disabled}
          onChange={(e) => onChangeApiKey(e.target.value)}
        />
      </Field>
      <Field
        label="Auth header"
        htmlFor={`${idPrefix}-auth-header`}
        help="Blank sends Authorization: Bearer. Name a header to send the key as its value instead."
      >
        <input
          id={`${idPrefix}-auth-header`}
          value={authHeader}
          placeholder="Authorization"
          spellCheck={false}
          disabled={disabled}
          onChange={(e) => onChangeAuthHeader(e.target.value)}
        />
      </Field>
      {/* Said here rather than discovered as a 401 on a server the developer
          has just authenticated against successfully. */}
      {skillsUrl.trim() && !reaches && (
        <div className="agent-ep-status hint">
          <IconInfo size={13} /> The skills endpoint is on a different host, so
          this key is not sent there.
        </div>
      )}
    </EndpointGroup>
  );
}

export default function AgentEndpointsFields({
  chatUrl = "",
  skillsUrl = "",
  onChangeChat,
  onChangeSkills,
  // Credentials are opt-in per screen: a screen that cannot store one does not
  // show the group. `keptApiKey` is the placeholder for a value already saved,
  // which is never sent back to the browser.
  apiKey = "",
  authHeader = "",
  onChangeApiKey,
  onChangeAuthHeader,
  keptApiKey = "",
  // { chat, override, trace } tri-states plus previews, or null before a probe.
  chatProbe = null,
  chatBusy = false,
  onTestChat,
  // The free half: { check, request_preview, response_preview }, or null while
  // the first read is in flight.
  skillsProbe = null,
  skillsBusy = false,
  // Re-runs the skills read. Beside the status line it is about, because a bare
  // "Try again" at the bottom of the block was a button with no visible subject.
  onRetrySkills = null,
  disabled = false,
  idPrefix = "agent",
}) {
  // Prefilling only ever writes into an empty field. Overwriting a URL somebody
  // typed because they then edited the chat one is the kind of help that loses
  // work.
  const fillSkills = (nextChat) => {
    if (skillsUrl.trim()) return;
    const guess = deriveSkillsUrl(nextChat);
    if (guess) onChangeSkills(guess);
  };

  return (
    <>
      {onChangeApiKey && (
        <Authentication
          apiKey={apiKey}
          authHeader={authHeader}
          onChangeApiKey={onChangeApiKey}
          onChangeAuthHeader={onChangeAuthHeader}
          keptPlaceholder={keptApiKey}
          chatUrl={chatUrl}
          skillsUrl={skillsUrl}
          disabled={disabled}
          idPrefix={idPrefix}
        />
      )}

      <EndpointGroup title="Chat endpoint">
        <Field
          label="URL"
          htmlFor={`${idPrefix}-chat-url`}
          hint={<HelpLink anchor="chat-endpoint" label="What this endpoint must do" />}
          help="Where questions are sent. OpenAI chat completions."
        >
          <div className="agent-ep-row">
            <input
              id={`${idPrefix}-chat-url`}
              value={chatUrl}
              placeholder="http://agent-host:8080/v1/chat/completions"
              spellCheck={false}
              disabled={disabled}
              onChange={(e) => onChangeChat(e.target.value)}
              onBlur={(e) => fillSkills(e.target.value)}
            />
            {onTestChat && (
              <IconButton
                variant="secondary"
                icon={<IconSend size={14} />}
                loading={chatBusy}
                disabled={disabled || !chatUrl.trim()}
                onClick={onTestChat}
                // The name carries the cost. It spends a real model call, and a
                // cost nobody was warned about is a cost they did not agree to
                // — so the warning is in the accessible name rather than only
                // in a tooltip that touch never shows.
                label="Test endpoint — sends one real question to this agent"
              />
            )}
          </div>
        </Field>
        <div className="agent-ep-result">
          <StatusLine
            check={chatProbe?.chat}
            busy={chatBusy}
            busyLabel="Asking the agent a test question…"
          />
          {chatProbe?.override?.ok === false && (
            <div className="agent-ep-status amber-text">
              <IconAlert size={13} /> {chatProbe.override.error}
            </div>
          )}
          {chatProbe?.override?.ok === true && (
            <div className="agent-ep-status ok-text">
              <IconCheck size={13} /> {chatProbe.override.detail}
            </div>
          )}
          {chatProbe?.trace?.ok === false && (
            <div className="agent-ep-status amber-text">
              <IconAlert size={13} /> {chatProbe.trace.error}
            </div>
          )}
          {chatProbe?.trace?.ok === true && (
            <div className="agent-ep-status ok-text">
              <IconCheck size={13} /> {chatProbe.trace.detail}
            </div>
          )}
          <Exchange
            request={chatProbe?.request_preview}
            response={chatProbe?.response_preview}
          />
        </div>
      </EndpointGroup>

      <EndpointGroup title="Skills endpoint">
        <Field
          label="URL"
          htmlFor={`${idPrefix}-skills-url`}
          hint={<HelpLink anchor="skills-endpoint" label="What this endpoint must do" />}
          help={
            "Optional. Without it an evaluation still runs — the playground, the " +
            "skill-coverage warning and optimization are what need it."
          }
        >
          <input
            id={`${idPrefix}-skills-url`}
            value={skillsUrl}
            placeholder="http://agent-host:8080/skills"
            spellCheck={false}
            disabled={disabled}
            onChange={(e) => onChangeSkills(e.target.value)}
          />
        </Field>
        <div className="agent-ep-result">
          <StatusLine
            check={skillsProbe?.check}
            busy={skillsBusy}
            busyLabel="Reading this agent's skill files…"
          />
          {/* Under the line that failed, so what is being tried again is the
              read whose error is directly above. A read can fail because a
              server was restarting, and retyping the URL to re-trigger the
              check is not a fix anyone should have to discover. */}
          {onRetrySkills && skillsProbe?.check?.ok === false && !skillsBusy && (
            <Button size="sm" icon={<IconRefresh size={13} />} onClick={onRetrySkills}>
              Read again
            </Button>
          )}
          <Exchange
            request={skillsProbe?.request_preview}
            response={skillsProbe?.response_preview}
          />
        </div>
      </EndpointGroup>
    </>
  );
}
