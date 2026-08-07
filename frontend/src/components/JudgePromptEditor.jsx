import React, { useEffect, useMemo, useState } from "react";
import { api } from "../api.js";
import Button from "./ui/Button.jsx";

// The eval set's grading criteria — the "Judging" tab of the config dialog.
//
// Why it lives here and not in the run-config dialog: everything in that dialog
// answers "where do I connect and how fast", which is the caller's business, and
// a viewer is allowed to set it. This answers "what counts as correct", which is
// the question set's — if every caller brought their own, two runs of the same
// set would produce pass rates nobody could compare. Keeping it on the set also
// means the existing owner-only guard covers it, with no per-field permission
// rule to explain to anyone.
//
// The two checks in here do different jobs and are deliberately not one button:
//
//   * The placeholder check is free (string matching) and runs on every
//     keystroke. It has to, because the failure it catches is silent — a
//     template with no {ground_truth} does not error, it grades every answer
//     against nothing and returns a pass rate that looks entirely normal.
//   * "Verify prompt" costs two real LLM calls, so it is a button. Two, not one:
//     a single parse proves the reply was JSON, not that the prompt still tells
//     a right answer from a wrong one — and a prompt that says "correct" to
//     everything parses perfectly.
const PLACEHOLDER_LABELS = {
  question: "{question}",
  ground_truth: "{ground_truth}",
  agent_response: "{agent_response}",
};

export default function JudgePromptEditor({
  evalSet,
  system,
  setSystem,
  user,
  setUser,
  impls = {},
  threshold = null,
}) {
  const [questions, setQuestions] = useState(null);
  const [questionPk, setQuestionPk] = useState("");
  const [model, setModel] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [verifying, setVerifying] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);

  const prompt = evalSet.judge_prompt || {};
  const fake = impls.judge === "fake";

  useEffect(() => {
    api
      .listQuestions(evalSet.id)
      .then((qs) => {
        setQuestions(qs);
        if (qs.length) setQuestionPk(qs[0].id);
      })
      .catch((e) => setError(e.message));
  }, [evalSet.id]);

  // Recomputed from the textarea, not from the payload: the point is to warn
  // while the mistake is being made, not after it has been saved.
  const missing = useMemo(
    () => Object.keys(PLACEHOLDER_LABELS).filter((p) => !user.includes(`{${p}}`)),
    [user]
  );

  // A verification describes exact words. Editing after one makes it a claim
  // about text that no longer exists, so it stops being shown — the same rule
  // the backend applies when it clears `verified_at` on save.
  const edited =
    system !== (prompt.system_prompt || "") || user !== (prompt.user_prompt || "");
  const verifiedAt = !edited && prompt.verified_at ? new Date(prompt.verified_at) : null;

  async function verify() {
    setError(null);
    setResult(null);
    setVerifying(true);
    try {
      setResult(
        await api.verifyJudgePrompt(evalSet.id, {
          question_pk: questionPk,
          system_prompt: system,
          user_prompt: user,
          model,
          api_key: apiKey,
        })
      );
    } catch (e) {
      setError(e.message);
    } finally {
      setVerifying(false);
    }
  }

  return (
    <>
      {fake && (
        <div className="hint callout">
          <strong>Grading is simulated here.</strong> The built-in grader returns a
          canned verdict and never reads a prompt, so nothing below changes a run
          in this environment. It is still saved, and takes effect as soon as a
          real grading model is connected.
        </div>
      )}
      {!fake && prompt.is_default && (
        <div className="hint callout">
          This set grades with the built-in prompt. That is a fine place to start
          — but nobody has confirmed it matches what your questions actually
          expect. Edit it, or just save to mark it checked.
        </div>
      )}

      <div className="field">
        <label>Judge system prompt</label>
        <textarea
          rows={12}
          value={system}
          onChange={(e) => setSystem(e.target.value)}
          spellCheck={false}
        />
        <div className="hint">
          Fully yours to rewrite — including the JSON contract. That is also the
          way to break every run at once: the judge’s reply has to parse into{" "}
          <code>{'{"verdict", "score", "comment"}'}</code>, and a reply that
          doesn’t is recorded as “could not be judged” rather than as a wrong
          answer. Verify below before you rely on it.
        </div>
      </div>

      <div className="field">
        <label>Judge user prompt</label>
        <textarea
          rows={10}
          value={user}
          onChange={(e) => setUser(e.target.value)}
          spellCheck={false}
        />
        {missing.length > 0 ? (
          <div className="error" style={{ marginTop: 6 }}>
            Missing {missing.map((m) => PLACEHOLDER_LABELS[m]).join(", ")}. The
            judge never sees{" "}
            {missing.includes("ground_truth")
              ? "the expected answer, so it will grade against nothing and still return verdicts that look normal"
              : "that input"}
            .
          </div>
        ) : (
          <div className="hint">
            Placeholders: <code>{"{question}"}</code>,{" "}
            <code>{"{ground_truth}"}</code>, <code>{"{agent_response}"}</code>.
            All three are required. Braces anywhere else are left exactly as you
            typed them.
          </div>
        )}
      </div>

      <h4 className="cfg-section">Verify</h4>
      {threshold !== null && threshold !== undefined && (
        <div className="hint" style={{ marginBottom: 8 }}>
          <code>JUDGE_SCORE_THRESHOLD={threshold}</code> is set on this
          deployment, so the run’s pass/fail comes from the score your prompt
          returns, not from its <code>verdict</code> field. Worth knowing before
          you rewrite what the score means.
        </div>
      )}
      <div className="field">
        <label>Verify against</label>
        <select
          value={questionPk}
          onChange={(e) => setQuestionPk(e.target.value)}
          disabled={!questions || questions.length === 0}
        >
          {(questions || []).map((q) => (
            <option key={q.id} value={q.id}>
              {q.question_id} · {q.question.slice(0, 70)}
            </option>
          ))}
        </select>
        <div className="hint">
          Graded twice: once with this question’s own expected answer (must come
          back <em>correct</em>), once with a contradictory one (must come back{" "}
          <em>incorrect</em>). One call would only prove the reply parses.
        </div>
      </div>
      <div className="field">
        <label>Model (optional)</label>
        <input
          value={model}
          placeholder="defaults to JUDGE_MODEL"
          onChange={(e) => setModel(e.target.value)}
        />
      </div>
      <div className="field">
        <label>LLM API Key (optional)</label>
        <input
          type="password"
          autoComplete="new-password"
          value={apiKey}
          placeholder="defaults to the server’s"
          onChange={(e) => setApiKey(e.target.value)}
        />
        <div className="hint">
          Left blank, this uses the environment’s LLM settings — the same ones a
          run with a blank config would use. Nothing typed here is stored.
        </div>
      </div>

      <Button size="sm" onClick={verify} loading={verifying} disabled={fake || !questionPk}>
        {verifying ? "Verifying…" : "Verify prompt"}
      </Button>
      {verifiedAt && !result && (
        <span className="muted" style={{ marginLeft: 10, fontSize: 12 }}>
          Verified {verifiedAt.toLocaleString()}
          {prompt.verified_model ? ` · ${prompt.verified_model}` : ""}
        </span>
      )}

      {error && <div className="error" style={{ marginTop: 10 }}>{error}</div>}

      {result && (
        <div style={{ marginTop: 12 }}>
          <div className={result.ok ? "hint callout" : "error"}>
            {result.ok
              ? `Both probes came back as expected on ${result.model}.`
              : "This prompt did not behave. See below — a run with it would produce results you cannot trust."}
          </div>
          <div className="cfg-view" style={{ marginTop: 8 }}>
            {result.cases.map((c, i) => (
              <div className="cfg-row" key={i}>
                <span className="cfg-label">{c.label}</span>
                <span className={`cfg-value ${c.ok ? "" : "empty"}`}>
                  {c.error
                    ? c.error
                    : `expected ${c.expected_verdict}, got ${c.verdict}${
                        c.score !== null && c.score !== undefined
                          ? ` (score ${c.score})`
                          : ""
                      }`}
                </span>
              </div>
            ))}
          </div>
          {/* Temperature exists, so one green run is evidence and not a
              guarantee. Saying so here is cheaper than having someone discover
              it 200 questions in. */}
          <div className="hint" style={{ marginTop: 6 }}>
            One pass, not a promise: the same prompt can still fail on a question
            that looks different from this one.
          </div>
        </div>
      )}
    </>
  );
}
