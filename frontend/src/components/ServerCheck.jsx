import React, { useState } from "react";
import { api } from "../api.js";
import {
  deriveSkillsUrl,
  looksUnauthorized,
  splitHint,
  TIER_LABELS,
} from "../agent_endpoints.js";
import Banner, { BannerDetail } from "./ui/Banner.jsx";
import Button from "./ui/Button.jsx";
import Field, { Disclosure } from "./ui/Field.jsx";
import PageHeader from "./ui/PageHeader.jsx";
import { IconAlert, IconCheck, IconInfo, IconPlay } from "./icons.jsx";
import { href } from "../useHashRoute.js";

// "Did I implement the contract correctly?" — asked by someone who has just
// written a server and has nothing to point it at yet.
//
// Deliberately not part of the Run-eval dialog or the wizard. Those ask a
// narrower question on the way to doing something, and they only exercise what
// they need. This runs the cases ordinary use never reaches — an empty skills
// map, a traversing path, an override that outlives its request — and those are
// the ones implementations get wrong, because each of them produces a
// correct-looking answer right up until it matters.
//
// It costs several model calls, so there is a button and nothing happens before
// it is pressed.

function CaseRow({ item }) {
  const { result } = item;
  const mark =
    result.ok === true ? (
      <span className="ok-text"><IconCheck size={14} /></span>
    ) : result.ok === false ? (
      <span className="error-text"><IconAlert size={14} /></span>
    ) : (
      <span className="hint"><IconInfo size={14} /></span>
    );
  return (
    <div className="check-case">
      <div className="check-case-mark">{mark}</div>
      <div className="check-case-body">
        <div className="check-case-title">{item.title}</div>
        {/* The message first, then why the case exists at all. A checklist that
            says which line failed and not what it means is a worse version of
            running the curl commands by hand. */}
        {/* The server's words, then what to do about them on their own line.
            The backend joins the two with a blank line, which HTML collapses —
            so a 401 and "add an API key" ran together into one long sentence
            with the advice at the end, where it is least read. */}
        {(result.error || result.detail) && (
          <div className={`check-case-detail${result.ok === false ? " error-text" : " hint"}`}>
            {result.error ? splitHint(result.error).message : result.detail}
          </div>
        )}
        {result.error && splitHint(result.error).hint && (
          <div className="check-case-detail hint">{splitHint(result.error).hint}</div>
        )}
        <div className="check-case-why hint">{item.why}</div>
      </div>
    </div>
  );
}

export default function ServerCheck() {
  const [chatUrl, setChatUrl] = useState("");
  const [skillsUrl, setSkillsUrl] = useState("");
  // Optional, and folded away. Without it this page would be unusable by
  // exactly the people most likely to need it: someone who has just written a
  // server and put it behind their team's gateway.
  const [apiKey, setApiKey] = useState("");
  const [authHeader, setAuthHeader] = useState("");
  // Opened by a refusal and never closed by one, so it does not shut under
  // somebody who opened it to type. Same rule as the other two screens.
  const [authOpen, setAuthOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [report, setReport] = useState(null);
  const [error, setError] = useState(null);

  async function run() {
    setBusy(true);
    setError(null);
    try {
      const result = await api.agentConformance({
        agent_chat_url: chatUrl.trim(),
        agent_skills_url: skillsUrl.trim(),
        agent_api_key: apiKey,
        agent_auth_header: authHeader.trim(),
      });
      setReport(result);
      if ((result.cases || []).some((c) => looksUnauthorized(c.result))) setAuthOpen(true);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  // Tier 0 means two different things — "the chat endpoint is dead" and "there
  // is no skills endpoint" — so the tier alone cannot title the result. The
  // chat case is what separates them.
  const usable = report?.cases?.some((c) => c.id === "chat" && c.result.ok === true);

  return (
    <div className="doc-page">
      <PageHeader
        title="Test your server"
        subtitle="Run the whole acceptance checklist against an agent server, including the cases ordinary use never reaches."
      />

      <div className="check-form">
        <Field
          label="Chat endpoint"
          htmlFor="check-chat"
          help="OpenAI chat completions. Required."
        >
          <input
            id="check-chat"
            value={chatUrl}
            placeholder="http://agent-host:8080/v1/chat/completions"
            spellCheck={false}
            autoFocus
            onChange={(e) => setChatUrl(e.target.value)}
            onBlur={(e) => {
              if (skillsUrl.trim()) return;
              const guess = deriveSkillsUrl(e.target.value);
              if (guess) setSkillsUrl(guess);
            }}
          />
        </Field>
        <Field
          label="Skills endpoint"
          htmlFor="check-skills"
          help="Optional. Leave blank if your server does not have one — nothing else is assumed in its place."
        >
          <input
            id="check-skills"
            value={skillsUrl}
            placeholder="http://agent-host:8080/skills"
            spellCheck={false}
            onChange={(e) => setSkillsUrl(e.target.value)}
          />
        </Field>
        <Disclosure
          summary="Authentication"
          detail="Optional"
          open={authOpen}
          onOpenChange={setAuthOpen}
        >
          <Field
            label="API key"
            htmlFor="check-api-key"
            help={
              "Most agent servers need none, and nothing here checks whether " +
              "yours does — asking for no credential is not a defect."
            }
          >
            <input
              id="check-api-key"
              type="password"
              autoComplete="new-password"
              value={apiKey}
              spellCheck={false}
              onChange={(e) => setApiKey(e.target.value)}
            />
          </Field>
          <Field
            label="Auth header"
            htmlFor="check-auth-header"
            help="Blank sends Authorization: Bearer. Name a header to send the key as its value instead."
          >
            <input
              id="check-auth-header"
              value={authHeader}
              placeholder="Authorization"
              spellCheck={false}
              onChange={(e) => setAuthHeader(e.target.value)}
            />
          </Field>
        </Disclosure>
        <Button
          variant="primary"
          icon={<IconPlay size={14} />}
          loading={busy}
          disabled={!chatUrl.trim()}
          onClick={run}
        >
          {busy ? "Running…" : "Run the checklist"}
        </Button>
        {/* Said before it is spent, not after. */}
        <div className="hint">
          This sends several real questions to your agent, so it costs a handful
          of model calls.
        </div>
      </div>

      {error && (
        <Banner tone="error" className="is-block" title="The checklist could not run">
          <BannerDetail>{error}</BannerDetail>
        </Banner>
      )}

      {report && (
        <div className="check-report">
          {/* The headline is what this agent *can* do — except when it cannot
              do anything, which tier 0 also covers. A server that never
              answered was being announced as "Evaluation only", which is a
              claim about a working agent. */}
          <Banner
            tone={usable ? (report.tier === 2 ? "success" : "info") : "error"}
            title={usable ? TIER_LABELS[report.tier] : "This agent is not usable yet"}
          >
            {report.summary}
          </Banner>
          {report.cases.map((c) => (
            <CaseRow key={c.id} item={c} />
          ))}
          <div className="hint">
            Every case here is described in{" "}
            <a href={href.docs("agent-server", "acceptance-checklist")}>
              the acceptance checklist
            </a>
            .
          </div>
        </div>
      )}
    </div>
  );
}
