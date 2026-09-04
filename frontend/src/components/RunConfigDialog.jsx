import React, { useEffect, useMemo, useState } from "react";
import { api } from "../api.js";
import Modal from "./Modal.jsx";
import RunConfigFields, {
  DiagnosisModelField,
  servicesSummary,
} from "./RunConfigFields.jsx";
import RunPicker from "./RunPicker.jsx";
import DefaultsNotice from "./settings/DefaultsNotice.jsx";
import Button from "./ui/Button.jsx";
import Field, { Disclosure, FormSection } from "./ui/Field.jsx";
import Skeleton from "./ui/Skeleton.jsx";
import { IconAlert, IconGear, IconPlay } from "./icons.jsx";
import { useDebounced } from "../useDebounced.js";
import { coverageWarning, skillCoverage } from "../skill_coverage.js";
import { gateFor, probeMatches } from "../agent_endpoints.js";
import Banner, { BannerDetail } from "./ui/Banner.jsx";

// Config for one run (§9.2 seams), chosen at trigger time instead of baked into
// the deployment's environment. Prefilled from GET /run-config/defaults so the
// form and the server-side fallback always agree.
//
// Secrets are write-only: the backend never sends them back, so the two key
// fields start blank. "Use config from" fills the non-secret fields from an
// earlier run and asks the backend to carry that run's credentials over
// server-side — which it only does while the matching endpoint is unchanged, so
// the hint below tells the developer when a key still has to be retyped.
const SECRET_PAIRS = [
  ["llm_api_key", "llm_base_url"],
  ["langfuse_secret_key", "langfuse_host"],
];

export default function RunConfigDialog({ evalSetId, evalSet, onClose, onRun }) {
  const [defaults, setDefaults] = useState(null);
  // What the deployment alone would have prefilled. Only used to decide whether
  // to say the values came from the developer's own settings.
  const [systemDefaults, setSystemDefaults] = useState(null);
  const [impls, setImpls] = useState({});
  const [form, setForm] = useState(null);
  const [secrets, setSecrets] = useState({ llm_api_key: "", langfuse_secret_key: "" });
  const [reuseFrom, setReuseFrom] = useState("");
  // The run behind `reuseFrom`, kept here because RunPicker only ever holds the
  // page it fetched and the endpoint-match rule below needs the run's config.
  const [source, setSource] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  // The pre-flight. `null` until the defaults have arrived and there is a URL to
  // check; then "checking" -> "connected" | "failed" | "simulated".
  const [probe, setProbe] = useState(null);
  // Bumped by "Try again", so a retry re-runs the effect on an unchanged URL.
  const [probeNonce, setProbeNonce] = useState(0);
  const [evalSetSkills, setEvalSetSkills] = useState(null);
  // The chat probe: `null` until somebody asks for one, because it spends a
  // model call. Carries the URLs it was about, so an answer never outlives the
  // address it describes — see `probeMatches`.
  const [chatProbe, setChatProbe] = useState(null);
  const [chatBusy, setChatBusy] = useState(false);
  // Opened only when a check fails on the way to starting. Controlled rather
  // than left to the Disclosure's own state so that a failure can open it,
  // while everything else about it stays the developer's to close.
  const [connOpen, setConnOpen] = useState(false);

  useEffect(() => {
    api
      .runConfigDefaults()
      .then((r) => {
        setDefaults(r.defaults);
        setSystemDefaults(r.system_defaults);
        setImpls(r.impls || {});
        setForm({ name: new Date().toLocaleString(), ...r.defaults });
      })
      .catch((e) => setError(e.message));
  }, []);

  // This set's tags, fetched once. Needed only to interpret a successful probe,
  // so a failure here is silent: it costs the coverage warning, not the check.
  useEffect(() => {
    api
      .evalSetSkills(evalSetId)
      .then(setEvalSetSkills)
      .catch(() => setEvalSetSkills(null));
  }, [evalSetId]);

  // Debounced so this fires when typing stops rather than on every keystroke —
  // the same hook the share editor's directory lookup uses. The first value is
  // returned immediately, which is what makes the dialog check the URL it opened
  // with the moment it opens.
  const agentUrl = useDebounced(form?.agent_skills_url ?? "", 400);

  // Whether the probe can say anything at all. With either seam faked, the
  // workspace it would read is canned: the skills are make-believe, so a
  // coverage warning would be about nothing and a failure could not happen. The
  // section already says "simulated"; the run must not be blocked on a check
  // that is not being made.
  const simulated = impls.agent === "fake" || impls.workspace === "fake";

  // "Checking" the moment the URL changes; the request itself waits for the
  // typing to stop. Splitting the two is what keeps the Start button honest
  // while someone is halfway through editing the URL — the last answer was
  // about a different agent, and treating it as current would let a run start
  // against an address nothing has verified.
  useEffect(() => {
    if (!form) return;
    setProbe(simulated ? { state: "simulated" } : { state: "checking" });
  }, [Boolean(form), form?.agent_skills_url, simulated, probeNonce]);

  useEffect(() => {
    if (!form || simulated) return undefined;
    // Still settling — the debounced value is a URL the field no longer shows.
    // Skipping here is also what stops the probe firing once against the empty
    // string before the defaults have landed.
    if (agentUrl !== (form.agent_skills_url ?? "")) return undefined;
    let cancelled = false;
    api
      .agentSkills(agentUrl)
      .then((r) => {
        if (cancelled) return;
        // 200 in all three cases now, with the outcome inside. `check.ok ===
        // null` is "no skills endpoint configured", which is a supported way to
        // run an agent — so it is a state to describe, never a failure.
        setProbe({
          state: r.check?.ok === false ? "failed" : "connected",
          skills: r.skills,
          version: r.version,
          check: r.check,
          error: r.check?.error || "",
          request_preview: r.request_preview,
          response_preview: r.response_preview,
        });
      })
      .catch((e) => {
        if (cancelled) return;
        // The agent server's own words. A summary here would flatten "no such
        // host" and "401 from the agent" into the same unhelpful sentence.
        setProbe({ state: "failed", error: e.message });
      });
    return () => {
      cancelled = true;
    };
    // Deliberately *not* keyed on the eval set's tags. They decide what the
    // answer means, not what is asked — and the two requests race on open, so
    // having them here fired the probe a second time the moment the tags landed.
  }, [Boolean(form), simulated, agentUrl, form?.agent_skills_url, probeNonce]);

  // The reading of that answer, kept apart from the asking. Either input can
  // arrive first; this recomputes when either does, without another round trip.
  const coverage = useMemo(() => {
    if (probe?.state !== "connected" || !evalSetSkills) return null;
    return coverageWarning(
      skillCoverage(evalSetSkills.skills, probe.skills),
      evalSetSkills.untagged_question_count || 0
    );
  }, [probe, evalSetSkills]);

  // The four checks, in the shape `gateFor` reads. Absent keys mean "not
  // asked", which is what keeps the button pressable before anyone has spent a
  // model call on the chat probe — the dialog asks on the way past instead.
  const checks = useMemo(() => {
    const out = {};
    if (probe?.check) out.skills = probe.check;
    if (chatProbe?.chat) out.chat = chatProbe.chat;
    return out;
  }, [probe, chatProbe]);

  const gate = simulated
    ? { blocked: false, reason: "", warnings: [] }
    : gateFor("evaluation", checks);

  // An eval run never sends a skills override and never reads a trace, so a
  // broken skills endpoint costs it the coverage warning and nothing else. Only
  // a chat endpoint that has actually failed stops a run.
  const blocked = gate.blocked;

  const set = (k, v) => setForm((f) => ({ ...f, [k]: v }));
  // A cleared number input parses to 0/NaN, which the backend would reject
  // (timeouts and concurrency are >= 1). Send null instead and let it fall back
  // to the env default, which is what an empty field means everywhere else here.
  const setNum = (k, raw) => {
    const n = Number(raw);
    set(k, raw === "" || !Number.isFinite(n) || n <= 0 ? null : n);
  };

  // The source run's credentials only carry over while their endpoint matches.
  const needsRetype = useMemo(() => {
    if (!source || !form) return [];
    return SECRET_PAIRS.filter(
      ([secret, endpoint]) =>
        !secrets[secret] && (form[endpoint] || "") !== (source.config?.[endpoint] || "")
    ).map(([secret]) => secret);
  }, [source, form, secrets]);

  function applyReuse(runId, run) {
    setReuseFrom(runId);
    setSource(run);
    if (!run) {
      // Back to the environment defaults, undoing whatever a previous pick
      // copied in — otherwise "start from the defaults" silently keeps them.
      setForm((f) => ({ ...(defaults || {}), name: f.name }));
      return;
    }
    // Keep the name (this is a new run) and only take the settings that run used;
    // anything it left blank falls back to the env default we started from.
    setForm((f) => {
      const next = { ...f };
      Object.keys(defaults || {}).forEach((k) => {
        const v = run.config?.[k];
        if (v !== undefined && v !== null && v !== "") next[k] = v;
      });
      return next;
    });
  }

  // One chat probe, and what it was about. Kept together so a stale answer can
  // never be shown beside an address it does not describe.
  async function testChat() {
    if (!form || chatBusy) return;
    setChatBusy(true);
    try {
      const r = await api.agentChatProbe({
        config: { ...cleanedConfig(), agent_timeout_s: form.agent_timeout_s },
        secrets,
        // An eval run sends no override and reads no trace, so proving either
        // would gate this dialog on something it does not use. Asking anyway
        // would also make the check slower and more expensive for no answer
        // anybody here acts on.
        with_override: false,
        with_trace: false,
      });
      setChatProbe({
        ...r,
        forChatUrl: form.agent_chat_url || "",
        forSkillsUrl: form.agent_skills_url || "",
      });
      return r;
    } catch (e) {
      const failed = {
        chat: { ok: false, error: e.message },
        forChatUrl: form.agent_chat_url || "",
        forSkillsUrl: form.agent_skills_url || "",
      };
      setChatProbe(failed);
      return failed;
    } finally {
      setChatBusy(false);
    }
  }

  function cleanedConfig() {
    const { name, ...config } = form;
    return config;
  }

  async function submit() {
    setError(null);

    // Test the chat endpoint on the way past, but only when nothing has proved
    // it yet for *these* URLs. Doing it every time would spend a model call and
    // half a minute on every run; skipping it entirely is how a typo used to
    // cost a run row, a full set of result rows and one agent call per question.
    //
    // Success is silent. A dialog that stopped to report good news would make
    // the check feel like an obstacle, and the next person would look for a way
    // to turn it off.
    if (!simulated && !probeMatches(chatProbe, {
      chatUrl: form.agent_chat_url || "",
      skillsUrl: form.agent_skills_url || "",
    })) {
      const result = await testChat();
      if (result?.chat?.ok === false) {
        // Only a failure opens the panel. Auto-opening on the way in would make
        // the form jump under the cursor and re-open something just closed.
        setConnOpen(true);
        return;
      }
    }

    setBusy(true);
    const { name, ...config } = form;
    try {
      await onRun({
        name,
        config,
        secrets,
        reuse_secrets_from_run_id: reuseFrom || null,
      });
    } catch (e) {
      setError(e.message);
      setBusy(false);
    }
  }

  // Only promise a borrowed key when it will actually be carried over.
  const kept = (secretKey) =>
    source && !needsRetype.includes(secretKey) ? "kept from the selected run" : "";

  return (
    <Modal
      title="Run eval"
      subtitle="These settings apply to this run only."
      onClose={onClose}
      width={620}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button
            variant="primary"
            icon={<IconPlay size={14} />}
            disabled={!form || blocked}
            loading={busy || chatBusy}
            onClick={submit}
            // The button explains its own disabled state. Without this, a
            // button that will not depress reads as a broken dialog rather than
            // as a target that is not there.
            title={blocked ? `${gate.reason} See Connection settings.` : undefined}
          >
            {busy
              ? "Starting…"
              // The chat probe is the one wait a developer has not asked for —
              // it happens on the way past — so the button says what it is
              // doing rather than just spinning.
              : chatBusy
                ? "Testing agent…"
                : "Run eval"}
          </Button>
        </>
      }
    >
      {error && (
        <Banner tone="error" title="Could not start the run">
          <BannerDetail>{error}</BannerDetail>
        </Banner>
      )}
      {!form && <Skeleton variant="text" count={4} />}

      {form && (
        <>
          <DefaultsNotice defaults={defaults} systemDefaults={systemDefaults} />
          <Field label="Run name" help="Shown in the run history. Leave the timestamp if you have nothing better.">
            <input value={form.name} onChange={(e) => set("name", e.target.value)} autoFocus />
          </Field>

          <Field
            label="Start from an earlier run's settings"
            help={
              reuseFrom
                ? needsRetype.length === 0
                  ? "That run's keys carry over — no need to retype them."
                  : `Its endpoint changed, so re-enter: ${needsRetype
                      .map((k) => (k === "llm_api_key" ? "the LLM API key" : "the trace store secret key"))
                      .join(", ")}.`
                : undefined
            }
          >
            <RunPicker evalSetId={evalSetId} value={reuseFrom} onChange={applyReuse} />
          </Field>

          {/* Eleven connection fields used to sit open in front of anyone who
              only wanted to press the button, most of them greyed out and
              captioned with an environment-variable name. They are still all
              here — a run records the exact settings it was triggered with, and
              overriding one is a real need — but behind a summary that answers
              "do I need to look at this?" without being opened. */}
          <Disclosure
            summary="Connection settings"
            detail={servicesSummary(impls)}
            icon={<IconGear size={14} />}
            // Controlled, but only in one direction: a failed check on the way
            // to starting opens it, and after that it is the developer's to
            // close. A panel that re-opened whenever a check was unhappy would
            // fight whoever had just decided to ignore it.
            //
            // Nothing opens it on the way *in*. The skills read resolves after
            // the dialog is already on screen, so auto-opening would make the
            // form jump under the cursor. That is what the mark below is for.
            open={connOpen}
            onOpenChange={setConnOpen}
            aside={
              blocked ? (
                <span className="error-text" title={gate.reason}>
                  <IconAlert size={14} />
                </span>
              ) : gate.warnings.length ? (
                <span className="amber-text" title={gate.warnings[0]}>
                  <IconAlert size={14} />
                </span>
              ) : probe?.state === "connected" && coverage ? (
                <span className="amber-text" title="Some questions need skills this agent does not have">
                  <IconAlert size={14} />
                </span>
              ) : null
            }
          >
            <RunConfigFields
              form={form}
              set={set}
              setNum={setNum}
              secrets={secrets}
              setSecrets={setSecrets}
              impls={impls}
              kept={kept}
              showDiagnosisModel={false}
              probe={probe}
              coverage={coverage}
              onRetryProbe={() => setProbeNonce((n) => n + 1)}
              chatProbe={chatProbe}
              chatBusy={chatBusy}
              onTestChat={testChat}
            />
          </Disclosure>

          {/* Outside the disclosure on purpose. Everything inside it answers
              "which services does this talk to"; this answers "what will this
              run spend", which is a decision rather than a connection detail —
              and one taken by exactly the person who would otherwise press the
              button without opening anything.

              The two lines of explanation are the whole reason the switch is
              safe to offer. Turning off something called "trace diagnosis" is
              not a decision anyone can make from its name; knowing it costs one
              model call per wrong answer, and what that call reads and returns,
              is. */}
          <FormSection
            title="Trace diagnosis"
            description="What to do with the questions this run gets wrong."
          >
            <label className="ui-switch">
              <input
                type="checkbox"
                checked={form.diagnosis_enabled !== false}
                onChange={(e) => set("diagnosis_enabled", e.target.checked)}
              />
              <span>Diagnose wrong answers as the run goes</span>
            </label>
            <div className="hint" style={{ marginTop: 6, marginBottom: 14 }}>
              One extra model call per wrong answer. It reads the question’s
              expected reasoning process, the agent’s trace and the grader’s
              verdict, and returns a short summary plus the steps that look most
              suspect. Off, the run produces verdicts only — a single question
              can still be diagnosed later from its own page.
            </div>
            <DiagnosisModelField
              value={form.diagnosis_model}
              onChange={(v) => set("diagnosis_model", v)}
              simulated={impls.diagnosis === "fake"}
              disabled={form.diagnosis_enabled === false || impls.diagnosis === "fake"}
            />
          </FormSection>

          {/* One line, not two textareas. The grading criteria belong to the
              eval set (only its owner may change them), so this dialog states
              which prompt the run will use and where to go to change it —
              putting the full text here would double the dialog's height for
              something nobody edits from this screen. */}
          {evalSet?.judge_prompt && (
            <FormSection title="Grading criteria">
              <div className="cfg-view">
                <div className="cfg-row">
                  <span className="cfg-label">Prompt</span>
                  <span className="cfg-value">
                    {evalSet.judge_prompt.is_default ? "built-in default" : "custom"} ·{" "}
                    {evalSet.judge_prompt.fingerprint}
                    {evalSet.judge_prompt.verified_at ? " · verified" : ""}
                  </span>
                </div>
              </div>
              <div className="hint" style={{ marginTop: 6 }}>
                {evalSet.judge_prompt.missing_placeholders?.length > 0 ? (
                  <span className="danger-text">
                    This set’s grading prompt is missing{" "}
                    {evalSet.judge_prompt.missing_placeholders
                      .map((p) => `{${p}}`)
                      .join(", ")}
                    . Results from this run will not mean what they appear to.
                  </span>
                ) : (
                  <>
                    Set by the eval set’s owner, so every run of this set is
                    graded the same way and their pass rates can be compared.
                    {!evalSet.judge_prompt.verified_at &&
                      " It has not been verified against a real judge model."}
                  </>
                )}
              </div>
            </FormSection>
          )}
        </>
      )}
    </Modal>
  );
}
