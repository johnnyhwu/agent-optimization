import React, { useEffect, useRef, useState } from "react";
import { api } from "../../api.js";
import { href, navigate } from "../../useHashRoute.js";
import Banner from "../ui/Banner.jsx";
import Button from "../ui/Button.jsx";
import Card, { CardHeader } from "../ui/Card.jsx";
import Field, { FormSection } from "../ui/Field.jsx";
import Skeleton from "../ui/Skeleton.jsx";
import { IconCheck, IconPlay, IconRefresh } from "../icons.jsx";
import { plural } from "../../plural.js";
import { useToast } from "../Toast.jsx";
import SkillGroups from "./SkillGroups.jsx";
import SplitEditor from "./SplitEditor.jsx";
import { counts, makeSplit } from "../../optimize_split.js";
import { estimateRun, explainRun } from "../../optimize_cost.js";
import {
  HYPER_FIELDS,
  STEPS,
  blockingReason,
  cleanConfig,
  defaultSkill,
  extraConfig,
  furthestStep,
  hyperState,
  tokenEstimate,
} from "../../optimize_wizard.js";

// The new-run wizard: a whole page, at its own address, with a static horizontal
// step bar.
//
// A page rather than a dialog because the middle steps are lists of sixty
// questions with three controls each — a modal would either scroll internally
// (the worst of both) or cover the screen while pretending not to be a page.
//
// The step bar is static: it shows all six from the start and never reorders or
// hides one. Every conditional wizard turns "how much is left" into a question
// nobody can answer, and this one asks for real money at the end.

export default function Wizard() {
  const toast = useToast();
  const [stepIndex, setStepIndex] = useState(0);
  const [defaults, setDefaults] = useState(null);
  const [evalSets, setEvalSets] = useState(null);
  const [sourceIds, setSourceIds] = useState([]);
  const [preview, setPreview] = useState(null);
  const [previewing, setPreviewing] = useState(false);
  // Kept apart from the page-level `error`. The preview now runs unprompted, and
  // a failure of something nobody asked for belongs beside the list it was
  // reading — in the banner at the top it reads as the whole wizard having
  // broken.
  const [previewError, setPreviewError] = useState(null);
  const [skill, setSkill] = useState(null);
  // Whether the selection is the developer's or the wizard's. The default only
  // moves itself while this is false: re-picking under someone who has already
  // chosen would look like the page arguing with them.
  const [skillTouched, setSkillTouched] = useState(false);
  const [split, setSplit] = useState(null);
  // `{ [skillName]: { skill, status, result, error } }` — every candidate skill
  // checked against the agent, filed under its own name. The old shape was a
  // single check that carried the skill it was for and relied on being cleared;
  // two of the three places that changed the skill did not clear it.
  const [checks, setChecks] = useState({});
  const [mode, setMode] = useState("isolated");
  const [config, setConfig] = useState({});
  const [secrets, setSecrets] = useState({});
  const [hyper, setHyper] = useState({});
  const [name, setName] = useState("");
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    api.optimizationDefaults().then(setDefaults).catch((e) => setError(e.message));
    api
      .listEvalSets({ limit: 100 })
      .then((page) => setEvalSets(page.items || []))
      .catch((e) => setError(e.message));
  }, []);

  const limits = defaults?.limits || {};
  const step = STEPS[stepIndex];

  // Which agent every check, and the run itself, talks to. Collected on the
  // first step: the Skill step tells the developer a skill was "found on the
  // agent server", and that sentence needs a server they have seen the address
  // of. It used to be asked for on Settings — two steps *after* the checks had
  // already run against whatever the backend's own environment said, so the
  // wizard could clear a skill on one agent and start the run on another.
  const agentConfig = {
    agent_base_url: config.agent_base_url || "",
    agent_timeout_s: config.agent_timeout_s || "",
  };

  // The preview is fetched when the sources change, not on every render of step
  // 2: it reads every question of every chosen set, and a wizard that refetched
  // on each keystroke would make the picker feel like the slowest screen here.
  // The debounce keeps three quick ticks from becoming three requests, but it
  // cannot help once two are in flight — a slow answer for the first selection
  // arriving after a fast answer for the second would replace the questions with
  // a set nobody has chosen any more. Only the newest request may write.
  const previewSeq = useRef(0);

  async function loadPreview(ids = sourceIds) {
    const seq = (previewSeq.current += 1);
    setPreviewing(true);
    setPreviewError(null);
    try {
      const result = await api.importPreview(ids);
      if (seq !== previewSeq.current) return;
      setPreview(result);
      setSkill(null);
      setSkillTouched(false);
      setSplit(null);
    } catch (e) {
      if (seq !== previewSeq.current) return;
      setPreviewError(e.message);
    } finally {
      if (seq === previewSeq.current) setPreviewing(false);
    }
  }

  // Ticking a set is the whole instruction; there is nothing further to ask for.
  //
  // This used to be a button, and the footer beside it said "Load the questions
  // to continue" — a wizard that knew what it needed, could fetch it unprompted,
  // and instead blocked until the developer pressed the key it had just named.
  //
  // Debounced rather than fired per tick, which is the reason the button existed:
  // the request reads every question of every chosen set, and picking three sets
  // is three clicks in about as many hundred milliseconds. The last one wins and
  // the first two are never sent.
  const sourceKey = sourceIds.join(",");
  useEffect(() => {
    if (!sourceIds.length) {
      setPreview(null);
      setPreviewError(null);
      setSkill(null);
      setSkillTouched(false);
      setSplit(null);
      return undefined;
    }
    const ids = sourceKey.split(",");
    const timer = setTimeout(() => loadPreview(ids), 300);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sourceKey]);

  function chooseSkill(skillName, { touched = true } = {}) {
    setSkill(skillName);
    if (touched) setSkillTouched(true);
    const group = preview?.groups.find((g) => g.skill_name === skillName);
    const questions = group ? group.questions : [];
    // The 70/30 proposal is computed on the server side of the wizard's model —
    // here it is simply the order the preview arrived in, which is already
    // stratified by prior accuracy.
    const share = defaults?.defaults?.train_share ?? 0.7;
    const trainKeys = [];
    const valKeys = [];
    questions.forEach((q, index) => {
      const crossed =
        Math.floor((index + 1) * (1 - share)) > Math.floor(index * (1 - share));
      (crossed ? valKeys : trainKeys).push(q.item_key);
    });
    setSplit(makeSplit(questions, { train: trainKeys, val: valKeys }));
  }

  // One skill against the agent. Every candidate is checked as the Skill step
  // opens, so the cards can say which are eligible while the choice is being
  // made rather than after it. Keyed by name, so two checks in flight after a
  // quick change of mind cannot overwrite each other's answers — which is what
  // the old single-slot shape had to be defended against by hand.
  async function runSkillCheck(skillName) {
    setChecks((current) => ({ ...current, [skillName]: { skill: skillName, status: "checking" } }));
    try {
      const result = await api.skillCheck(skillName, agentConfig);
      setChecks((current) => ({
        ...current,
        [skillName]: { skill: skillName, status: "ok", result },
      }));
    } catch (e) {
      // A failed check belongs on the card beside its retry, not in the
      // page-level error banner where it reads as a dead end.
      setChecks((current) => ({
        ...current,
        [skillName]: { skill: skillName, status: "failed", error: e.message },
      }));
    }
  }

  // Check every candidate once the questions are grouped. Not on each render of
  // the step: the list is stable between preview loads, and re-checking on every
  // keystroke elsewhere in the wizard would put one agent call per skill behind
  // every state change.
  //
  // The agent is part of the key as well as the skill names. A check answers
  // "does *this* server have it", so changing the server on step 1 and coming
  // back has to re-ask rather than keep showing what a different one said.
  const groupNames = (preview?.groups || []).map((g) => g.skill_name).join(" ");
  const agentKey = `${agentConfig.agent_base_url} ${agentConfig.agent_timeout_s}`;
  useEffect(() => {
    if (!groupNames) return;
    groupNames.split(" ").forEach((name) => runSkillCheck(name));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [groupNames, agentKey]);

  // The default selection, and its one correction. It fills in as soon as the
  // groups exist so the step never opens with nothing chosen, then moves off a
  // skill this mode cannot edit once the agent has answered — but only while the
  // developer has not chosen for themselves.
  const wanted = skillTouched ? skill : defaultSkill(preview?.groups, checks, mode);
  useEffect(() => {
    if (skillTouched || !wanted || wanted === skill) return;
    chooseSkill(wanted, { touched: false });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [wanted, skill, skillTouched]);

  async function start() {
    setStarting(true);
    setError(null);
    try {
      const run = await api.createOptimizationRun({
        name: name.trim() || null,
        mode,
        skill_name: skill,
        train: split.train,
        val: split.val,
        // Coerced here and nowhere else: the fields hold what was typed, and
        // `blockingReason` has already refused to let this run with a value
        // these would turn into 0.
        num_epochs: hyperValues.num_epochs,
        batch_size: hyperValues.batch_size,
        // Blank fields are dropped, not sent as "". Every field on this form
        // means "use the server's environment" when empty, and the API says
        // that by the key being absent — an empty string in a numeric field is
        // a 422.
        config: cleanConfig({
          ...config,
          ...extraConfig(hyper),
          learning_rate: hyperValues.learning_rate,
          concurrency: hyperValues.concurrency,
          reflect_budget_chars: hyperValues.reflect_budget_chars,
        }),
        secrets,
        detector: {},
      });
      toast.success(`Optimization run started for ${skill}.`);
      navigate(href.optimizeRun(run.id));
    } catch (e) {
      setError(e.message);
      setStarting(false);
    }
  }

  // One description of where the wizard is, read by the footer, the step bar and
  // the Start button alike. They used to compute reachability separately from
  // blocking, which is how the bar could offer a step whose body rendered
  // nothing.
  const wizardState = {
    stepIndex, sourceIds, preview, previewError, skill, split, limits, checks, mode,
    hyper, defaults: defaults?.defaults,
  };
  const blocked = blockingReason(wizardState);
  const reachable = furthestStep(wizardState);
  const { values: hyperValues, errors: hyperErrors } = hyperState(hyper, defaults?.defaults);

  if (!defaults) return <Skeleton variant="row" count={5} />;

  return (
    <div className="opt-wizard">
      <StepBar steps={STEPS} current={stepIndex} onGo={setStepIndex} furthest={reachable} />

      {error && (
        <Banner tone="error" title="That did not work">
          {error}
        </Banner>
      )}

      <div className="opt-wizard-body">
        {step.id === "mode" && (
          <ModeStep
            mode={mode}
            onMode={setMode}
            config={config}
            onConfig={setConfig}
            defaults={defaults.defaults}
            impls={defaults.impls}
          />
        )}

        {step.id === "source" && (
          <SourceStep
            evalSets={evalSets}
            selected={sourceIds}
            onToggle={(id) =>
              setSourceIds((ids) =>
                ids.includes(id) ? ids.filter((x) => x !== id) : [...ids, id],
              )
            }
            preview={preview}
            previewing={previewing}
            error={previewError}
            onRetry={() => loadPreview()}
          />
        )}

        {step.id === "skill" && (
          previewing || !preview ? (
            <Skeleton variant="row" count={3} />
          ) : (
            <SkillGroups
              preview={preview}
              selected={skill}
              onSelect={chooseSkill}
              checks={checks}
              mode={mode}
              onRecheck={runSkillCheck}
              impls={defaults.impls}
            />
          )
        )}

        {/* Never an empty body. Reachability should keep this unreachable, but
            a step that renders nothing is how the old wizard reported a
            missing prerequisite, and the failure was silent. */}
        {step.id === "split" && (
          split ? (
            <SplitEditor split={split} limits={limits} onChange={setSplit} />
          ) : (
            <MissingPrerequisite
              title="No split yet"
              onBack={() => setStepIndex(STEPS.findIndex((s) => s.id === "skill"))}
            >
              The split is proposed from the skill's questions, and no skill is
              chosen — picking one builds it.
            </MissingPrerequisite>
          )
        )}

        {step.id === "settings" && (
          <SettingsStep
            defaults={defaults}
            config={config}
            onConfig={setConfig}
            secrets={secrets}
            onSecrets={setSecrets}
          />
        )}

        {step.id === "review" && (
          <ReviewStep
            name={name}
            onName={setName}
            skill={skill}
            mode={mode}
            split={split}
            defaults={defaults}
            hyper={hyper}
            onHyper={setHyper}
            values={hyperValues}
            errors={hyperErrors}
            impls={defaults.impls}
          />
        )}
      </div>

      <div className="opt-wizard-foot">
        {/* Secondary rather than ghost. The two ends of this row are one
            decision — go back, or go on — and a borderless Back beside a filled
            Continue read as a caption rather than as the other half of it. */}
        <Button
          variant="secondary"
          disabled={stepIndex === 0}
          onClick={() => setStepIndex((i) => Math.max(0, i - 1))}
        >
          Back
        </Button>
        <span className="opt-wizard-blocked">{blocked}</span>
        {stepIndex < STEPS.length - 1 ? (
          <Button
            variant="primary"
            disabled={Boolean(blocked)}
            onClick={() => setStepIndex((i) => i + 1)}
          >
            Continue
          </Button>
        ) : (
          <Button
            variant="primary"
            icon={<IconPlay size={15} />}
            loading={starting}
            disabled={Boolean(blocked)}
            onClick={start}
          >
            Start the run
          </Button>
        )}
      </div>
    </div>
  );
}

// A step reached without what it reads. Reachability should prevent it; this is
// what stands there if it ever does happen, because the alternative — which is
// what shipped — is a blank panel and a footer sentence blaming the user.
function MissingPrerequisite({ title, children, onBack }) {
  return (
    <Banner tone="warning" title={title}>
      {children}{" "}
      <Button variant="link" onClick={onBack}>
        Go back and choose one
      </Button>
    </Banner>
  );
}

function StepBar({ steps, current, onGo, furthest }) {
  return (
    <ol className="opt-steps" aria-label="Steps">
      {steps.map((step, index) => {
        const state = index === current ? "current" : index < current ? "done" : "todo";
        const reachable = index <= Math.max(current, furthest);
        return (
          <li key={step.id} className={`opt-step is-${state}`}>
            <button
              type="button"
              disabled={!reachable}
              onClick={() => onGo(index)}
              aria-current={index === current ? "step" : undefined}
            >
              <span className="opt-step-no">
                {index < current ? <IconCheck size={13} /> : index + 1}
              </span>
              <span className="opt-step-text">
                <span className="opt-step-label">{step.label}</span>
                <span className="opt-step-hint">{step.hint}</span>
              </span>
            </button>
          </li>
        );
      })}
    </ol>
  );
}

function SourceStep({ evalSets, selected, onToggle, preview, previewing, error, onRetry }) {
  if (!evalSets) return <Skeleton variant="row" count={4} />;
  const fingerprints = new Set((preview?.sources || []).map((s) => s.judge_prompt_fingerprint));

  return (
    <FormSection
      title="Where the questions come from"
      description="A run may draw from several eval sets at once. It grades them all with one prompt of its own, chosen on the Settings step."
    >
      {/* The whole row is the control. The <label> always wrapped it, so the
          text was already clickable — but the checkbox was being stretched to
          the row's full width by the bare `input` rule in styles.css, which put
          an invisible thousand-pixel box between the tick and the name and made
          the real hit area impossible to guess. */}
      <ul className="opt-sources">
        {evalSets.map((es) => {
          const checked = selected.includes(es.id);
          return (
            <li key={es.id}>
              <label className={`opt-source${checked ? " is-selected" : ""}`}>
                <input type="checkbox" checked={checked} onChange={() => onToggle(es.id)} />
                <span className="opt-source-name">{es.name}</span>
                <span className="opt-source-meta">{plural(es.question_count, "question")}</span>
              </label>
            </li>
          );
        })}
      </ul>

      {/* What the button used to be. The questions are read as soon as a set is
          ticked, so all that is left is to say what is happening — and, when it
          does not happen, to offer the retry that a silent auto-fetch would
          otherwise have no way to ask for. */}
      <p className="opt-source-status" aria-live="polite">
        {!selected.length && "Tick a set and its questions are read straight away."}
        {Boolean(selected.length) && previewing && "Reading the questions…"}
        {Boolean(selected.length) && !previewing && preview && (
          <>
            <IconCheck size={13} />{" "}
            {plural(
              preview.groups.reduce((n, g) => n + g.questions.length, 0)
                + (preview.ambiguous?.length || 0),
              "question",
            )}{" "}
            read from {plural(selected.length, "eval set")}.
          </>
        )}
      </p>

      {error && (
        <Banner
          tone="error"
          title="The questions could not be read"
          actions={
            <Button variant="secondary" icon={<IconRefresh size={15} />} onClick={onRetry}>
              Try again
            </Button>
          }
        >
          {error}
        </Banner>
      )}

      {/* Two sets graded by different words produce prior accuracies that are
          not comparable — and this screen is where those numbers start being
          used to decide what to train on. */}
      {fingerprints.size > 1 && (
        <Banner tone="warning" title="These sets are graded differently">
          The chosen sets use {fingerprints.size} different judge prompts, so the
          accuracy figures on the next screen are not directly comparable. This
          run will grade every question with a single prompt of its own.
        </Banner>
      )}
    </FormSection>
  );
}

// The first step. Neither option is ever disabled here: what makes `routing`
// impossible is one skill's missing frontmatter, and no skill has been chosen
// yet. The Skill step is where that becomes a per-card verdict, which is the
// whole reason this moved to the front — it used to be asked *after* the skill
// and the split, and then refuse the answer.
function ModeStep({ mode, onMode, config, onConfig, defaults, impls }) {
  const set = (key) => (e) => onConfig({ ...config, [key]: e.target.value });

  return (
    <>
    <FormSection
      title="What this run edits"
      description="The two modes are mirror images: each freezes what the other changes. This choice decides which skills the next steps can offer, so it comes first."
    >
      <div className="opt-modes">
        <ModeOption
          id="isolated"
          title="Isolated — the skill's body"
          selected={mode === "isolated"}
          onSelect={() => onMode("isolated")}
        >
          Only the chosen skill is sent to the agent, so there is no routing
          decision to influence and accuracy moves with the instructions
          themselves. The frontmatter is frozen.
        </ModeOption>
        <ModeOption
          id="routing"
          title="Routing — the description"
          selected={mode === "routing"}
          onSelect={() => onMode("routing")}
        >
          The whole workspace is sent and only the frontmatter description
          changes, so what is being optimised is <em>when</em> the agent reaches
          for this skill. The body is frozen, and so is every other skill.
        </ModeOption>
      </div>
    </FormSection>

    {/* Here rather than on Settings, which is two steps *after* the skills are
        checked against an agent. The Skill step tells the developer a skill was
        "found on the agent server"; until this was asked first, that sentence
        was about whichever agent the backend's own environment named, while the
        run went to whatever was typed later. Same agent, both times, or the
        check proves nothing. */}
    <FormSection
      title="Which agent server"
      description="Every skill on the next steps is looked up on this server, and the run answers its questions there. Blank means the one this deployment is configured with."
    >
      {impls?.agent === "fake" && (
        <Banner tone="info" title="The agent seam is fake">
          <code>AGENT_IMPL=fake</code> — questions are answered by canned code
          rather than by a server, so this address is recorded but not called.
        </Banner>
      )}
      <Field
        label="Agent server URL"
        help={
          defaults.agent_base_url
            ? `Blank uses ${defaults.agent_base_url}`
            : "Not set in this deployment's environment — a run needs one here."
        }
      >
        <input
          value={config.agent_base_url || ""}
          onChange={set("agent_base_url")}
          placeholder={defaults.agent_base_url || "http://agent:8080"}
        />
      </Field>
      <Field
        label="Request timeout (seconds)"
        help="How long one question may take before the run counts it as failed."
      >
        <input
          type="number"
          min={1}
          value={config.agent_timeout_s ?? ""}
          onChange={set("agent_timeout_s")}
          placeholder={defaults.agent_timeout_s}
        />
      </Field>
    </FormSection>
    </>
  );
}

function ModeOption({ id, title, children, selected, onSelect }) {
  return (
    <label className={`opt-mode${selected ? " is-selected" : ""}`}>
      <span className="opt-mode-head">
        <input
          type="radio"
          name="opt-mode"
          value={id}
          checked={selected}
          onChange={onSelect}
        />
        <span className="opt-mode-title">{title}</span>
      </span>
      <span className="opt-mode-desc">{children}</span>
    </label>
  );
}

// The `?` beside an estimated number: hover, or focus it, for the arithmetic.
//
// A real button rather than a `title` on the `<dd>`, which is what this was.
// A native tooltip is invisible until the pointer happens to rest on the number,
// gives no sign that resting there would do anything, and never appears at all
// for anyone driving by keyboard. The estimate is the last thing read before an
// hour of calls is authorised, so "where does this number come from" should look
// like a question the page is offering to answer.
function Explain({ text }) {
  return (
    <button
      type="button"
      className="opt-explain"
      title={text}
      aria-label={text}
      // The tooltip is the whole content; clicking it should do nothing but
      // leave focus on it, which is what makes the text reachable by keyboard.
      onClick={(e) => e.preventDefault()}
    >
      ?
    </button>
  );
}

function SettingsStep({ defaults, config, onConfig, secrets, onSecrets }) {
  const set = (key) => (e) => onConfig({ ...config, [key]: e.target.value });
  const setSecret = (key) => (e) => onSecrets({ ...secrets, [key]: e.target.value });
  const d = defaults.defaults;

  return (
    <>
      <FormSection
        title="Connections"
        description="Blank means the server's own environment value — which is what makes the fake demo runnable without filling anything in. The agent server is asked for on the first step instead, because the skills on step 3 are looked up on it."
      >
        <Field label="LLM base URL" help="Used by both the judge and the optimizer.">
          <input value={config.llm_base_url || ""} onChange={set("llm_base_url")} placeholder={d.llm_base_url} />
        </Field>
        <Field label="LLM API key" help="Write-only. It is stored apart from the rest of the config and never comes back out.">
          <input type="password" value={secrets.llm_api_key || ""} onChange={setSecret("llm_api_key")} autoComplete="off" />
        </Field>
        <Field label="Langfuse host" help="Traces are what the reflect stage reads; without them there is nothing to reflect on.">
          <input value={config.langfuse_host || ""} onChange={set("langfuse_host")} placeholder={d.langfuse_host} />
        </Field>
        <Field label="Langfuse public key">
          <input value={config.langfuse_public_key || ""} onChange={set("langfuse_public_key")} placeholder={d.langfuse_public_key} />
        </Field>
        <Field label="Langfuse secret key">
          <input type="password" value={secrets.langfuse_secret_key || ""} onChange={setSecret("langfuse_secret_key")} autoComplete="off" />
        </Field>
      </FormSection>

      <FormSection title="Models">
        <Field
          label="Judge model"
          help={
            defaults.impls.judge === "fake"
              ? "JUDGE_IMPL=fake — this field has no effect until a real judge is configured."
              : "Grades every answer, and its score is what the gate compares."
          }
        >
          <input value={config.judge_model || ""} onChange={set("judge_model")} placeholder={d.judge_model} />
        </Field>
        <Field
          label="Optimizer model"
          help={
            defaults.impls.optimizer === "fake"
              ? "OPTIMIZER_IMPL=fake — the skill edits will be canned rather than written by a model."
              : "Reads the failing trajectories and writes the skill edits."
          }
        >
          <input value={config.optimizer_model || ""} onChange={set("optimizer_model")} placeholder={d.optimizer_model} />
        </Field>
      </FormSection>

      <FormSection
        title="Grading"
        description="This run owns its judge prompt, unlike an eval run, which inherits one from its eval set — a run drawing from several sets would otherwise have several answers to “what counts as correct”."
      >
        <Field label="Judge system prompt" help="Blank uses the shipped default.">
          <textarea
            rows={5}
            value={config.judge_system_prompt || ""}
            onChange={set("judge_system_prompt")}
            placeholder={defaults.judge_prompt.system}
          />
        </Field>
      </FormSection>
    </>
  );
}

// What a character budget means in the unit a context window is sold in. The
// range is the honest form: the ratio depends on the text, and quoting one
// number invites it to be treated as measured.
function budgetHelp(chars) {
  const est = tokenEstimate(chars);
  const base =
    "Characters of trajectory per analyst call, shared across the whole minibatch. " +
    "What does not fit is trimmed — tool results first, never a tool call or a " +
    "final answer — and if it still does not fit, whole runs are withheld and " +
    "the step says which.";
  if (!est) return base;
  return `${base} About ${est.low.toLocaleString()}–${est.high.toLocaleString()} tokens.`;
}

function ReviewStep({ name, onName, skill, mode, split, defaults, hyper, onHyper, values, errors, impls }) {
  const c = counts(split);
  // The effective values, which are the typed ones when they parse and the
  // server's defaults when the field has not been touched. A field mid-edit —
  // empty, or "1x" — has no value here, and the estimate below says so rather
  // than quietly computing with a zero.
  const epochs = values.num_epochs;
  const batch = values.batch_size;
  // Raw, so the input renders exactly what was typed. Backing a number input
  // with `Number(raw)` is what made these fields impossible to clear.
  const raw = (key) => hyper[key] ?? String(defaults.defaults[key] ?? "");
  const set = (key) => (e) => onHyper({ ...hyper, [key]: e.target.value });

  // Stated as calls rather than as money: the models are whatever base URL the
  // developer pointed this at, so their rates are theirs to know and a number
  // with a currency symbol would be trusted more than a guess deserves. Three
  // counts, because the expensive one is not the biggest one — a run makes
  // thousands of agent calls on a small model and a few dozen optimizer calls
  // on the largest one available, each carrying a minibatch of traces.
  const estimable = epochs != null && batch != null;
  const estimateInput = {
    nTrain: c.train,
    nVal: c.val,
    epochs,
    batchSize: batch,
    minibatchSize: hyper.minibatch_size ?? defaults.defaults.minibatch_size,
  };
  const estimate = estimable ? estimateRun(estimateInput) : null;
  // The derivation behind each number, for the `?` beside it. Generated from the
  // same inputs as the estimate rather than written out beside it, so the two
  // cannot drift into confidently explaining a formula that has changed.
  const explain = estimable ? explainRun(estimateInput) : null;

  return (
    <>
      <FormSection title="Name this run">
        <Field label="Name" help="Optional. The list falls back to the time it started.">
          <input value={name} onChange={(e) => onName(e.target.value)} placeholder={`Tune ${skill}`} />
        </Field>
      </FormSection>

      <FormSection
        title="How long it trains"
        description="A step is one minibatch: answer, reflect, edit, then judge the edit against the validation split."
      >
        <Field
          label="Epochs"
          help="One pass over the whole training split."
          error={errors.num_epochs}
        >
          <input
            type="number"
            min={HYPER_FIELDS.num_epochs.min}
            max={HYPER_FIELDS.num_epochs.max}
            value={raw("num_epochs")}
            onChange={set("num_epochs")}
            aria-invalid={errors.num_epochs ? "true" : undefined}
          />
        </Field>
        <Field
          label="Batch size"
          help="Questions per step. Set it to the size of the training split and one epoch is one step."
          error={errors.batch_size}
        >
          <input
            type="number"
            min={HYPER_FIELDS.batch_size.min}
            value={raw("batch_size")}
            onChange={set("batch_size")}
            aria-invalid={errors.batch_size ? "true" : undefined}
          />
        </Field>
        <Field
          label="Learning rate"
          help="The most edits one step may apply. This is the whole meaning of the word here."
          error={errors.learning_rate}
        >
          <input
            type="number"
            min={HYPER_FIELDS.learning_rate.min}
            value={raw("learning_rate")}
            onChange={set("learning_rate")}
            aria-invalid={errors.learning_rate ? "true" : undefined}
          />
        </Field>
        {/* Not a hyperparameter: it changes how long the run takes and nothing
            about what it produces. It is here because it is the other number
            that decides how the run feels, and because the alternative — one
            question at a time, the shipped default — makes a sixty-question
            batch an afternoon. */}
        <Field
          label="Concurrency"
          help={`How many questions are sent to the agent server at once. With a minibatch of ${
            hyper.minibatch_size ?? defaults.defaults.minibatch_size
          } and a concurrency of ${values.concurrency ?? "n"}, a step collects its rollouts ${
            values.concurrency ?? "n"
          } at a time. Raise it only as far as the agent server can take.`}
          error={errors.concurrency}
        >
          <input
            type="number"
            min={HYPER_FIELDS.concurrency.min}
            max={HYPER_FIELDS.concurrency.max}
            value={raw("concurrency")}
            onChange={set("concurrency")}
            aria-invalid={errors.concurrency ? "true" : undefined}
          />
        </Field>
      </FormSection>

      {/* The one setting that decides whether an analyst call fits in the
          optimizer's context window at all. It lived in the API only, which
          made "the model refused the request" a thing you could hit and not
          adjust from anywhere you could see. */}
      <FormSection
        title="How much the analyst is shown"
        description="Each step sends the optimizer one prompt per minibatch: the skill, then the trajectories of the questions in it. This caps the trajectory half."
      >
        <Field
          label="Trajectory budget"
          help={budgetHelp(values.reflect_budget_chars)}
          error={errors.reflect_budget_chars}
        >
          <input
            type="number"
            min={HYPER_FIELDS.reflect_budget_chars.min}
            step={10000}
            value={raw("reflect_budget_chars")}
            onChange={set("reflect_budget_chars")}
            aria-invalid={errors.reflect_budget_chars ? "true" : undefined}
          />
        </Field>
        <Banner tone="info" title="Leave the model room to answer">
          This budget is <em>input</em>, and it is not the whole prompt: the
          skill goes in front of it, and the analyst's reply can run to 16,000
          tokens on top. The rule is the upper estimate above, plus the skill,
          plus 16,000, under your optimizer model's context window. Going over
          truncates nothing — the model refuses the call, and the step loses
          that minibatch's gradient entirely.
          {" "}Traces in Chinese or Japanese cost far more tokens per character
          than this estimate assumes; treat the upper figure as a floor there,
          or lower the budget.
        </Banner>
      </FormSection>

      {/* Upstream's two longitudinal passes. Off by default and stated as what
          they cost, because they are the only settings on this page that add
          calls on the *optimizer* model — the expensive one — and they do it
          per epoch, so a one-epoch run gets nothing out of either. */}
      <FormSection
        title="Longitudinal passes"
        description="Extra work at each epoch boundary, comparing the validation split under the previous epoch's skill and this one's. Both are off unless you turn them on."
      >
        <Field
          label="Slow update"
          help="Writes free-form guidance into a protected block of SKILL.md that step-level edits cannot touch. Needs at least two epochs to have anything to compare."
        >
          <label className="opt-switch">
            <input
              type="checkbox"
              checked={Boolean(hyper.slow_update)}
              onChange={(e) => onHyper({ ...hyper, slow_update: e.target.checked })}
            />
            <span>Write epoch guidance into the skill</span>
          </label>
        </Field>
        <Field
          label="Meta skill"
          help="Optimizer-side memory: what the last epoch taught it about its own editing, shown to the analyst on later steps. Never written into the skill itself."
        >
          <label className="opt-switch">
            <input
              type="checkbox"
              checked={Boolean(hyper.meta_skill)}
              onChange={(e) => onHyper({ ...hyper, meta_skill: e.target.checked })}
            />
            <span>Carry the optimizer's own notes between epochs</span>
          </label>
        </Field>
        {epochs != null && epochs < 2 && (hyper.slow_update || hyper.meta_skill) && (
          <Banner tone="warning" title="One epoch has no boundary to compare across">
            Both passes compare one epoch with the previous one. With a single
            epoch there is no previous, so neither will run and neither will cost
            anything. Raise the epoch count above, or leave them off.
          </Banner>
        )}
      </FormSection>

      {/* Two bands, not one seven-cell grid under a small-caps strip. What the
          run *is* — the skill, and the questions it moves between two columns —
          is a different kind of fact from what it will *spend*, and running them
          together in one auto-fit grid was what made this card read as a wall.
          The closing sentence is gone: "counts, not currency" was explaining a
          distinction the row labels already make. */}
      <Card className="opt-review">
        <div className="opt-review-head">
          <h3>What this will do</h3>
        </div>

        <dl className="opt-review-grid">
          <div><dt>Skill</dt><dd><code>{skill}</code> · {mode}</dd></div>
          <div><dt>Training</dt><dd>{plural(c.train, "question")}</dd></div>
          <div>
            <dt>Validation</dt>
            <dd>{plural(c.val, "question")}{c.overlap ? ` · ${c.overlap} shared` : ""}</dd>
          </div>
        </dl>

        {/* Withheld rather than guessed while a training number is mid-edit.
            An estimate computed from a zero is not a smaller estimate, it is
            a wrong one, and this table is the last thing read before an hour
            of calls is authorised. */}
        {estimate ? (
          <>
            <p className="opt-review-band">How much work that is</p>
            <dl className="opt-review-grid is-numbers">
              <div>
                <dt>Steps <Explain text={explain.steps} /></dt>
                <dd>{estimate.totalSteps}</dd>
                <dd className="opt-review-sub">{estimate.stepsPerEpoch} per epoch</dd>
              </div>
              <div>
                <dt>Agent calls <Explain text={explain.agentCalls} /></dt>
                <dd>≈ {estimate.agentCalls.toLocaleString()}</dd>
                <dd className="opt-review-sub">on the agent server</dd>
              </div>
              <div>
                <dt>Judge calls <Explain text={explain.judgeCalls} /></dt>
                <dd>≈ {estimate.judgeCalls.toLocaleString()}</dd>
                <dd className="opt-review-sub">one per answer</dd>
              </div>
              <div>
                <dt>Optimizer calls <Explain text={explain.optimizerCalls} /></dt>
                <dd>≤ {estimate.optimizerCallsMax.toLocaleString()}</dd>
                <dd className="opt-review-sub">the largest model</dd>
              </div>
            </dl>
          </>
        ) : (
          <p className="opt-review-band">
            Size of the run — fix the training settings above
          </p>
        )}

        {impls.agent === "fake" && (
          <Banner tone="info" title="Nothing real will be called">
            The agent, judge and optimizer seams are set to fake where marked, so
            this run costs nothing and proves the machinery rather than the skill.
          </Banner>
        )}
        {c.overlap > 0 && (
          <Banner tone="warning" title="Validation is not fully held out">
            {plural(c.overlap, "question")} are in both splits, so part of what the gate
            measures is the skill being fitted to them.
          </Banner>
        )}
      </Card>
    </>
  );
}
