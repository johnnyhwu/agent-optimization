import React, { useEffect, useState } from "react";
import { IconAlert, IconCheck, IconInfo, IconRefresh, IconSend } from "./icons.jsx";
import Button, { IconButton } from "./ui/Button.jsx";
import Field, { Disclosure } from "./ui/Field.jsx";
import {
  credentialReachesSkills,
  deriveSkillsUrl,
  looksUnauthorized,
  splitHint,
} from "../agent_endpoints.js";
import DocsHelp from "./DocsHelp.jsx";

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
//   * **The groups are laid out in the order they are decided in**: the
//     credential first, because it applies to both addresses under it, then the
//     endpoint every run needs, then the optional one. Only the credential is
//     folded — see `EndpointAuthGroup` for why that one and not the others.
//     This was once three nested disclosures, a panel inside a panel inside a
//     panel, which is how a form ends up with more chevrons than fields.
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

// The credential, and the header it goes in.
//
// Folded, and labelled optional, because most agent servers ask for no
// credential: two more fields open in front of everybody is a question everybody
// has to decide not to answer. That is the playground's bargain
// (`AgentConnectionBar`) and this is the same one, so the three screens that ask
// for an agent server now ask for it the same way.
//
// Folding an optional field is only safe if it opens itself when it stops being
// optional, and there are two such moments:
//
//   * the server refused the call — a folded panel is not where anyone looks
//     for a field they have no reason to believe exists, and "401" beside a URL
//     is not an instruction;
//   * a credential is already set, saved or typed. A key hidden behind a lid
//     reads as a form with no key in it.
//
// Opened by either, never closed by either: it must not shut under somebody who
// opened it to type.
export function EndpointAuthGroup({
  apiKey,
  authHeader,
  onChangeApiKey,
  onChangeAuthHeader,
  keptPlaceholder = "",
  apiKeyHelp = "Most agent servers need none. Leave this blank and nothing is sent.",
  chatUrl = "",
  skillsUrl = "",
  // The checks whose refusal is a reason to open this. Any tri-state check
  // object; `looksUnauthorized` reads the backend's own hint, not a status code.
  refusals = [],
  // This deployment sends the signed-in user's SSO token, so the platform owns
  // the credential and the reader has nothing to type. See `app/agent_sso.py`.
  //
  // The fields are not removed, only demoted: an agent server that wants its
  // own gateway key rather than the caller's identity is still a supported
  // configuration, and a key entered here still wins (`user_secrets.inject`
  // takes what was typed over anything else). Deleting them would leave that
  // deployment with a 401 and no field on any screen to answer it — exactly
  // the situation these fields were added to fix.
  sso = false,
  disabled,
  idPrefix,
}) {
  const reaches = credentialReachesSkills(chatUrl, skillsUrl);
  const [open, setOpen] = useState(false);
  const wanted =
    Boolean(apiKey) || Boolean(keptPlaceholder) || refusals.some(looksUnauthorized);
  useEffect(() => {
    if (wanted) setOpen(true);
  }, [wanted]);

  return (
    <Disclosure
      summary={sso ? "Endpoint authentication (advanced)" : "Endpoint authentication"}
      detail={sso ? "Handled for you" : "Optional"}
      className="agent-ep-auth"
      open={open}
      onOpenChange={setOpen}
    >
      {sso && (
        <div className="agent-ep-status hint">
          <IconInfo size={13} /> Requests are sent to this agent as you, using
          your sign-in. Leave these blank unless the agent server wants its own
          key instead.
        </div>
      )}
      <Field
        label="API key"
        htmlFor={`${idPrefix}-api-key`}
        hint={<DocsHelp anchor="authentication" label="How the platform sends a credential" />}
        help={apiKeyHelp}
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
        hint={<DocsHelp anchor="authentication" label="Where the credential is sent" />}
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
    </Disclosure>
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
  // True where the deployment forwards the caller's SSO token. Only changes how
  // the credential group presents itself — see `EndpointAuthGroup`.
  sso = false,
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
        <EndpointAuthGroup
          apiKey={apiKey}
          authHeader={authHeader}
          onChangeApiKey={onChangeApiKey}
          onChangeAuthHeader={onChangeAuthHeader}
          keptPlaceholder={keptApiKey}
          sso={sso}
          chatUrl={chatUrl}
          skillsUrl={skillsUrl}
          refusals={[chatProbe?.chat, skillsProbe?.check]}
          disabled={disabled}
          idPrefix={idPrefix}
        />
      )}

      <EndpointGroup title="Chat endpoint">
        <Field
          label="URL"
          htmlFor={`${idPrefix}-chat-url`}
          hint={<DocsHelp anchor="chat-endpoint" label="What this endpoint must do" />}
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
          hint={<DocsHelp anchor="skills-endpoint" label="What this endpoint must do" />}
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
