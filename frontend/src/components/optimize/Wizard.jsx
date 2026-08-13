import React, { useEffect, useMemo, useState } from "react";
import { api } from "../../api.js";
import { href, navigate } from "../../useHashRoute.js";
import Badge from "../ui/Badge.jsx";
import Banner from "../ui/Banner.jsx";
import Button from "../ui/Button.jsx";
import Card, { CardHeader } from "../ui/Card.jsx";
import Field, { FormSection } from "../ui/Field.jsx";
import Skeleton from "../ui/Skeleton.jsx";
import { IconAlert, IconCheck, IconPlay, IconRefresh } from "../icons.jsx";
import { useToast } from "../Toast.jsx";
import SkillGroups from "./SkillGroups.jsx";
import SplitEditor from "./SplitEditor.jsx";
import { canStart, counts, makeSplit } from "../../optimize_split.js";

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

const STEPS = [
  { id: "source", label: "Source", hint: "Which eval sets" },
  { id: "skill", label: "Skill", hint: "What to optimise" },
  { id: "split", label: "Split", hint: "Train and validate" },
  { id: "target", label: "Target", hint: "The agent" },
  { id: "settings", label: "Settings", hint: "Models and grading" },
  { id: "review", label: "Review", hint: "Start" },
];

export default function Wizard() {
  const toast = useToast();
  const [stepIndex, setStepIndex] = useState(0);
  const [defaults, setDefaults] = useState(null);
  const [evalSets, setEvalSets] = useState(null);
  const [sourceIds, setSourceIds] = useState([]);
  const [preview, setPreview] = useState(null);
  const [previewing, setPreviewing] = useState(false);
  const [skill, setSkill] = useState(null);
  const [split, setSplit] = useState(null);
  const [check, setCheck] = useState(null);
  const [checking, setChecking] = useState(false);
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

  // The preview is fetched when the sources change, not on every render of step
  // 2: it reads every question of every chosen set, and a wizard that refetched
  // on each keystroke would make the picker feel like the slowest screen here.
  async function loadPreview() {
    setPreviewing(true);
    setError(null);
    try {
      const result = await api.importPreview(sourceIds);
      setPreview(result);
      setSkill(null);
      setSplit(null);
    } catch (e) {
      setError(e.message);
    } finally {
      setPreviewing(false);
    }
  }

  function chooseSkill(skillName) {
    setSkill(skillName);
    const group = preview.groups.find((g) => g.skill_name === skillName);
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

  async function runSkillCheck(skillName) {
    setChecking(true);
    try {
      const result = await api.skillCheck(skillName);
      setCheck(result);
      if (!result.has_frontmatter && mode === "routing") setMode("isolated");
    } catch (e) {
      setError(e.message);
    } finally {
      setChecking(false);
    }
  }

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
        num_epochs: Number(hyper.num_epochs ?? defaults.defaults.num_epochs),
        batch_size: Number(hyper.batch_size ?? defaults.defaults.batch_size),
        config: { ...config, ...pickHyper(hyper) },
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

  const blocked = blockingReason({ stepIndex, sourceIds, preview, skill, split, limits, check, mode });

  if (!defaults) return <Skeleton variant="row" count={5} />;

  return (
    <div className="opt-wizard">
      <StepBar steps={STEPS} current={stepIndex} onGo={setStepIndex} furthest={furthest({ sourceIds, preview, skill, split, check })} />

      {error && (
        <Banner tone="error" title="That did not work">
          {error}
        </Banner>
      )}

      <div className="opt-wizard-body">
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
            onLoad={loadPreview}
          />
        )}

        {step.id === "skill" && (
          previewing || !preview ? (
            <Skeleton variant="row" count={3} />
          ) : (
            <SkillGroups preview={preview} selected={skill} onSelect={chooseSkill} />
          )
        )}

        {step.id === "split" && split && (
          <SplitEditor split={split} limits={limits} onChange={setSplit} />
        )}

        {step.id === "target" && (
          <TargetStep
            skill={skill}
            check={check}
            checking={checking}
            onCheck={() => runSkillCheck(skill)}
            mode={mode}
            onMode={setMode}
            impls={defaults.impls}
          />
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
            impls={defaults.impls}
          />
        )}
      </div>

      <div className="opt-wizard-foot">
        <Button
          variant="ghost"
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
            onClick={() => {
              const next = stepIndex + 1;
              if (STEPS[next].id === "target" && !check) runSkillCheck(skill);
              setStepIndex(next);
            }}
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

// Which steps have enough behind them to be jumped back to. Forward jumps are
// not offered: a later step reads what an earlier one produced, and arriving
// with half of it is how a wizard shows an empty screen and blames the user.
function furthest({ sourceIds, preview, skill, split, check }) {
  if (check) return 5;
  if (split) return 3;
  if (skill) return 2;
  if (preview) return 1;
  return sourceIds.length ? 0 : 0;
}

function blockingReason({ stepIndex, sourceIds, preview, skill, split, limits, check, mode }) {
  const id = STEPS[stepIndex].id;
  if (id === "source") {
    if (!sourceIds.length) return "Choose at least one eval set.";
    if (!preview) return "Load the questions to continue.";
  }
  if (id === "skill" && !skill) return "Pick the skill this run optimises.";
  if (id === "split") {
    if (!split) return "Pick a skill first.";
    if (!canStart(split, limits)) return "The split is too small — see above.";
  }
  if (id === "target") {
    if (!check) return "Checking the agent…";
    if (!check.exists) return `The agent has no skill directory named “${skill}”.`;
    if (mode === "routing" && !check.has_frontmatter) return check.routing_blocked_reason;
  }
  return null;
}

function pickHyper(hyper) {
  const { num_epochs, batch_size, ...rest } = hyper;
  return rest;
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

function SourceStep({ evalSets, selected, onToggle, preview, previewing, onLoad }) {
  if (!evalSets) return <Skeleton variant="row" count={4} />;
  const fingerprints = new Set((preview?.sources || []).map((s) => s.judge_prompt_fingerprint));

  return (
    <FormSection
      title="Where the questions come from"
      description="A run may draw from several eval sets at once. It grades them all with one prompt of its own, chosen on the Settings step."
    >
      <ul className="opt-sources">
        {evalSets.map((es) => (
          <li key={es.id}>
            <label>
              <input
                type="checkbox"
                checked={selected.includes(es.id)}
                onChange={() => onToggle(es.id)}
              />
              <span className="opt-source-name">{es.name}</span>
              <span className="opt-source-meta">{es.question_count} questions</span>
            </label>
          </li>
        ))}
      </ul>

      <Button
        variant="secondary"
        icon={<IconRefresh size={15} />}
        loading={previewing}
        disabled={!selected.length}
        onClick={onLoad}
      >
        {preview ? "Reload questions" : "Load questions"}
      </Button>

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

function TargetStep({ skill, check, checking, onCheck, mode, onMode, impls }) {
  return (
    <FormSection
      title="The agent this run measures against"
      description="The skill is sent with every question as a per-request override, exactly as the playground does. Nothing is written back to the agent."
    >
      {impls.workspace === "fake" && (
        <Banner tone="info" title="The agent is a fake">
          <code>WORKSPACE_IMPL=fake</code>, so the skill below is canned rather
          than read from a real agent server. Everything works; the numbers are
          make-believe.
        </Banner>
      )}

      <div className="opt-check">
        <div className="opt-check-head">
          <strong>{skill}</strong>
          <Button variant="ghost" icon={<IconRefresh size={14} />} loading={checking} onClick={onCheck}>
            Re-check
          </Button>
        </div>
        {!check ? (
          <Skeleton variant="row" count={2} />
        ) : check.exists ? (
          <>
            <Badge tone="success" icon={<IconCheck size={13} />}>Found on the agent</Badge>
            <ul className="opt-files">
              {check.files.map((path) => (
                <li key={path}><code>{path}</code></li>
              ))}
            </ul>
            <p className="opt-hint">{check.n_chars.toLocaleString()} characters in total.</p>
          </>
        ) : (
          <Banner tone="error" title={`No skill directory named “${skill}”`}>
            The agent has: {check.available_skills.join(", ") || "no skills at all"}.
            A question's skill tag and the agent's directory name have to be the
            same word.
          </Banner>
        )}
      </div>

      <Field label="What this run edits" help="The two modes are mirror images: each freezes what the other changes.">
        <div className="opt-modes">
          <ModeOption
            id="isolated"
            title="Isolated — the skill's body"
            selected={mode === "isolated"}
            onSelect={() => onMode("isolated")}
          >
            Only this skill is sent, so there is no routing decision to influence
            and accuracy moves with the instructions themselves. The frontmatter
            is frozen.
          </ModeOption>
          <ModeOption
            id="routing"
            title="Routing — the description"
            selected={mode === "routing"}
            disabled={check ? !check.has_frontmatter : true}
            reason={check?.routing_blocked_reason}
            onSelect={() => onMode("routing")}
          >
            The whole workspace is sent and only the frontmatter description
            changes, so what is being optimised is <em>when</em> the agent reaches
            for this skill. The body is frozen.
          </ModeOption>
        </div>
      </Field>
    </FormSection>
  );
}

function ModeOption({ id, title, children, selected, disabled, reason, onSelect }) {
  return (
    <label className={`opt-mode${selected ? " is-selected" : ""}${disabled ? " is-disabled" : ""}`}>
      <input
        type="radio"
        name="opt-mode"
        value={id}
        checked={selected}
        disabled={disabled}
        onChange={onSelect}
      />
      <span className="opt-mode-title">{title}</span>
      <span className="opt-mode-desc">{children}</span>
      {/* Never a bare disabled control: the reason is the whole message. */}
      {disabled && reason && (
        <span className="opt-mode-reason">
          <IconAlert size={13} /> {reason}
        </span>
      )}
    </label>
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
        description="Blank means the server's own environment value — which is what makes the fake demo runnable without filling anything in."
      >
        <Field label="Agent base URL" help={d.agent_base_url || "not set in the environment"}>
          <input value={config.agent_base_url || ""} onChange={set("agent_base_url")} placeholder={d.agent_base_url} />
        </Field>
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

function ReviewStep({ name, onName, skill, mode, split, defaults, hyper, onHyper, impls }) {
  const c = counts(split);
  const epochs = Number(hyper.num_epochs ?? defaults.defaults.num_epochs);
  const batch = Number(hyper.batch_size ?? defaults.defaults.batch_size);
  const stepsPerEpoch = Math.max(1, Math.ceil(c.train / Math.max(batch, 1)));
  const totalSteps = epochs * stepsPerEpoch;
  const set = (key) => (e) => onHyper({ ...hyper, [key]: e.target.value });

  // Every step answers the training batch once and the validation split once,
  // plus one baseline. Stated as agent calls rather than as money, because the
  // price of a call is the developer's to know and the count is ours.
  const agentCalls = c.val + totalSteps * (Math.min(batch, c.train) + c.val);

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
        <Field label="Epochs" help="One pass over the whole training split.">
          <input type="number" min="1" max="20" value={epochs} onChange={set("num_epochs")} />
        </Field>
        <Field label="Batch size" help="Questions per step. Set it to the size of the training split and one epoch is one step.">
          <input type="number" min="1" value={batch} onChange={set("batch_size")} />
        </Field>
        <Field label="Learning rate" help="The most edits one step may apply. This is the whole meaning of the word here.">
          <input
            type="number"
            min="1"
            value={hyper.learning_rate ?? defaults.defaults.learning_rate}
            onChange={set("learning_rate")}
          />
        </Field>
      </FormSection>

      <Card className="opt-review">
        <CardHeader title="What this will do" />
        <dl className="opt-review-grid">
          <div><dt>Skill</dt><dd><code>{skill}</code> · {mode}</dd></div>
          <div><dt>Training</dt><dd>{c.train} questions</dd></div>
          <div><dt>Validation</dt><dd>{c.val} questions{c.overlap ? ` · ${c.overlap} shared` : ""}</dd></div>
          <div><dt>Steps</dt><dd>{stepsPerEpoch} per epoch · {totalSteps} in total</dd></div>
          <div><dt>Agent calls</dt><dd>≈ {agentCalls.toLocaleString()}</dd></div>
        </dl>
        {impls.agent === "fake" && (
          <Banner tone="info" title="Nothing real will be called">
            The agent, judge and optimizer seams are set to fake where marked, so
            this run costs nothing and proves the machinery rather than the skill.
          </Banner>
        )}
        {c.overlap > 0 && (
          <Banner tone="warning" title="Validation is not fully held out">
            {c.overlap} question(s) are in both splits, so part of what the gate
            measures is the skill being fitted to them.
          </Banner>
        )}
      </Card>
    </>
  );
}
