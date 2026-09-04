import React from "react";
import { IconAlert, IconCheck, IconInfo } from "./icons.jsx";
import Button from "./ui/Button.jsx";
import Field, { Disclosure } from "./ui/Field.jsx";
import { deriveSkillsUrl } from "../agent_endpoints.js";
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
//   * **The chat endpoint has a button and the skills endpoint does not.** The
//     asymmetry is cost, not taste: reading a skill listing is a free GET the
//     caller can fire on a keystroke, while the chat probe spends a real model
//     call. Nothing may spend that on somebody's behalf.
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
    return (
      <div className="agent-ep-status error-text">
        <IconAlert size={13} /> {check.error}
      </div>
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

export default function AgentEndpointsFields({
  chatUrl = "",
  skillsUrl = "",
  onChangeChat,
  onChangeSkills,
  // { chat, override, trace } tri-states plus previews, or null before a probe.
  chatProbe = null,
  chatBusy = false,
  onTestChat,
  // The free half: { check, request_preview, response_preview }, or null while
  // the first read is in flight.
  skillsProbe = null,
  skillsBusy = false,
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
      <Field
        label="Chat endpoint"
        htmlFor={`${idPrefix}-chat-url`}
        hint={<HelpLink anchor="chat-endpoint" label="What this endpoint must do" />}
        help="Where questions are sent. OpenAI chat completions."
      >
        <input
          id={`${idPrefix}-chat-url`}
          value={chatUrl}
          placeholder="http://agent-host:8080/v1/chat/completions"
          spellCheck={false}
          disabled={disabled}
          onChange={(e) => onChangeChat(e.target.value)}
          onBlur={(e) => fillSkills(e.target.value)}
        />
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
        {onTestChat && (
          <Button
            size="sm"
            loading={chatBusy}
            disabled={disabled || !chatUrl.trim()}
            onClick={onTestChat}
            // Said on the button, not in a tooltip: it spends a model call, and
            // a cost nobody was warned about is a cost they did not agree to.
            title="Sends one real question to this agent"
          >
            {chatBusy ? "Testing…" : "Test endpoint"}
          </Button>
        )}
        <Exchange
          request={chatProbe?.request_preview}
          response={chatProbe?.response_preview}
        />
      </div>

      <Field
        label="Skills endpoint"
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
        <Exchange
          request={skillsProbe?.request_preview}
          response={skillsProbe?.response_preview}
        />
      </div>
    </>
  );
}
