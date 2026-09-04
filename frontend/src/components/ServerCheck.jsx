import React, { useState } from "react";
import { api } from "../api.js";
import { deriveSkillsUrl, TIER_LABELS } from "../agent_endpoints.js";
import Banner, { BannerDetail } from "./ui/Banner.jsx";
import Button from "./ui/Button.jsx";
import Field from "./ui/Field.jsx";
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
        {(result.error || result.detail) && (
          <div className={`check-case-detail${result.ok === false ? " error-text" : " hint"}`}>
            {result.error || result.detail}
          </div>
        )}
        <div className="check-case-why hint">{item.why}</div>
      </div>
    </div>
  );
}

export default function ServerCheck() {
  const [chatUrl, setChatUrl] = useState("");
  const [skillsUrl, setSkillsUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [report, setReport] = useState(null);
  const [error, setError] = useState(null);

  async function run() {
    setBusy(true);
    setError(null);
    try {
      setReport(
        await api.agentConformance({
          agent_chat_url: chatUrl.trim(),
          agent_skills_url: skillsUrl.trim(),
        })
      );
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

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
          <Banner
            tone={report.tier === 2 ? "success" : report.tier === 0 ? "warning" : "info"}
            title={TIER_LABELS[report.tier]}
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
