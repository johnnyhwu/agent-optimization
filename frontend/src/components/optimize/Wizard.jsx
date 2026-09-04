import React, { useEffect, useRef, useState } from "react";
import { api } from "../../api.js";
import { href, navigate } from "../../useHashRoute.js";
import Banner from "../ui/Banner.jsx";
import DefaultsNotice from "../settings/DefaultsNotice.jsx";
import Button from "../ui/Button.jsx";
import Card, { CardHeader } from "../ui/Card.jsx";
import Field, { FormSection } from "../ui/Field.jsx";
import NumberInput from "../ui/NumberInput.jsx";
import Skeleton from "../ui/Skeleton.jsx";
import { IconCheck, IconPlay, IconRefresh } from "../icons.jsx";
import { plural } from "../../plural.js";
import { useToast } from "../Toast.jsx";
import SkillGroups from "./SkillGroups.jsx";
import SplitEditor from "./SplitEditor.jsx";
import AgentEndpointsFields from "../AgentEndpointsFields.jsx";
import { useDebounced } from "../../useDebounced.js";
import { counts, makeSplit } from "../../optimize_split.js";
import { gateFor, probeMatches } from "../../agent_endpoints.js";
import * as undoStack from "../../optimize_split_history.js";
import { routingReviewWarnings } from "../../optimize_routing_warnings.js";
import { analystCallsPerStep, estimateRun, explainRun } from "../../optimize_cost.js";
import {
  HYPER_FIELDS,
  STEPS,
  blockingReason,
  cleanConfig,
  configFrom,
  GATE_METRICS,
  defaultSkills,
  defaultText,
  extraConfig,
  furthestStep,
  hyperState,
  needsMixedWeight,
  previewQuestionCount,
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
  const [skillsProbe, setSkillsProbe] = useState(null);
  const [skillsBusy, setSkillsBusy] = useState(false);
  const [chatProbe, setChatProbe] = useState(null);
  const [chatBusy, setChatBusy] = useState(false);
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
  // The skills this run will optimise. One in isolated mode, which sends a
  // single skill to the agent; one or several in routing, where the
  // descriptions compete and are moved together.
  const [skills, setSkills] = useState([]);
  // Whether the selection is the developer's or the wizard's. The default only
  // moves itself while this is false: re-picking under someone who has already
  // chosen would look like the page arguing with them.
  const [skillTouched, setSkillTouched] = useState(false);
  const [split, setSplit] = useState(null);
  // Undo for the split step. Two ways in, and keeping them apart is the whole
  // point of the split:
  //
  //   `editSplit`    the developer changed something. Remember where they were.
  //   `rebuildSplit` the split was replaced because the *questions* changed —
  //                  a different skill, a different source, or none. The old
  //                  history now describes a different set of questions, and
  //                  `makeSplit` filters keys it does not recognise, so undoing
  //                  across that boundary would not error: it would quietly
  //                  restore a half-empty editor. So the history goes with it.
  const [splitHistory, setSplitHistory] = useState(undoStack.empty);

  function editSplit(next) {
    setSplitHistory((h) => undoStack.push(h, split));
    setSplit(next);
  }

  function rebuildSplit(next) {
    setSplit(next);
    setSplitHistory(undoStack.reset());
  }

  function undoSplit() {
    const { history: rest, split: previous } = undoStack.undo(splitHistory);
    if (!previous) return;
    setSplit(previous);
    setSplitHistory(rest);
  }
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
    agent_chat_url: config.agent_chat_url || "",
    agent_skills_url: config.agent_skills_url || "",
    // `null`, never `""`. This object is both a query string (where blank is
    // dropped) and a JSON body typed `float | None` (where blank is a 422) —
    // and a 422 here reads as a chat endpoint that failed, which blocks
    // Continue on a wizard nobody has misconfigured.
    // Blank for almost every agent, and inert when blank. Not a secret — it is
    // the shape of the request, not the credential, which travels in `secrets`.
    agent_auth_header: config.agent_auth_header || "",
    agent_timeout_s: Number(config.agent_timeout_s) > 0
      ? Number(config.agent_timeout_s)
      : null,
  };

  // The free half of the pre-flight, re-read whenever the URL stops moving —
  // the same debounce the Run-eval dialog uses, for the same reason: a
  // half-typed URL is always a failure, and a status line that flashes red on
  // every keystroke teaches people to ignore it.
  //
  // All four inputs, not just the URL: the chat URL and the credential are part
  // of what is asked (they decide whether the key may travel to this endpoint),
  // so leaving them undebounced meant one request to the agent server per
  // keystroke while somebody typed an API key — a burst of failed
  // authentication against a gateway that may be counting them.
  const skillsUrl = useDebounced(config.agent_skills_url || "", 400);
  const chatUrl = useDebounced(config.agent_chat_url || "", 400);
  const authHeader = useDebounced(config.agent_auth_header || "", 400);
  const apiKey = useDebounced(secrets.agent_api_key || "", 400);
  useEffect(() => {
    if (
      skillsUrl !== (config.agent_skills_url || "") ||
      chatUrl !== (config.agent_chat_url || "") ||
      authHeader !== (config.agent_auth_header || "") ||
      apiKey !== (secrets.agent_api_key || "")
    ) {
      return undefined;
    }
    let cancelled = false;
    setSkillsBusy(true);
    api
      .agentSkills({
        config: {
          agent_skills_url: skillsUrl,
          // So the server can apply the same-origin rule before letting the
          // credential travel to a second address.
          agent_chat_url: chatUrl,
          agent_auth_header: authHeader,
        },
        secrets: { agent_api_key: apiKey },
      })
      .then((r) => {
        if (cancelled) return;
        setSkillsProbe({
          check: r.check,
          request_preview: r.request_preview,
          response_preview: r.response_preview,
        });
      })
      .catch((e) => {
        if (cancelled) return;
        setSkillsProbe({ check: { ok: false, error: e.message } });
      })
      .finally(() => {
        if (!cancelled) setSkillsBusy(false);
      });
    return () => {
      cancelled = true;
    };
    // Keyed on the credential too: typing a key in answer to a 401 changes
    // what the request is, and a status line still showing the refusal would
    // read as a key that did not work.
  }, [
    skillsUrl,
    chatUrl,
    authHeader,
    apiKey,
    config.agent_skills_url,
    config.agent_chat_url,
    config.agent_auth_header,
    secrets.agent_api_key,
  ]);

  // The expensive half. Never automatic: it spends a model call, and unlike the
  // Run-eval dialog it asks the agent to prove the override *and* the trace,
  // because an optimization run is meaningless without both.
  async function testChat() {
    if (chatBusy) return null;
    setChatBusy(true);
    try {
      const r = await api.agentChatProbe({
        config: agentConfig,
        secrets,
        with_override: true,
        with_trace: true,
      });
      const probe = {
        ...r,
        forChatUrl: agentConfig.agent_chat_url,
        forSkillsUrl: agentConfig.agent_skills_url,
      };
      setChatProbe(probe);
      return probe;
    } catch (e) {
      const failed = {
        chat: { ok: false, error: e.message },
        forChatUrl: agentConfig.agent_chat_url,
        forSkillsUrl: agentConfig.agent_skills_url,
      };
      setChatProbe(failed);
      return failed;
    } finally {
      setChatBusy(false);
    }
  }

  // What the footer gates on. Absent keys are "not asked", so Continue stays
  // pressable until something has actually been proved false — and pressing it
  // is what asks.
  const agentChecks = {
    ...(skillsProbe?.check ? { skills: skillsProbe.check } : {}),
    ...(chatProbe?.chat ? { chat: chatProbe.chat } : {}),
    ...(chatProbe?.override ? { override: chatProbe.override } : {}),
    ...(chatProbe?.trace ? { trace: chatProbe.trace } : {}),
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
      const result = await api.importPreview(ids, mode);
      if (seq !== previewSeq.current) return;
      setPreview(result);
      setSkills([]);
      setSkillTouched(false);
      rebuildSplit(null);
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
      setSkills([]);
      setSkillTouched(false);
      rebuildSplit(null);
      return undefined;
    }
    const ids = sourceKey.split(",");
    const timer = setTimeout(() => loadPreview(ids), 300);
    return () => clearTimeout(timer);
    // Refetched when the mode changes as well as the sources: the two modes
    // disagree about a question tagged with several skills, so the groups the
    // Skill step offers are the mode's and not the eval sets' alone.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sourceKey, mode]);

  function chooseSkills(names, { touched = true } = {}) {
    setSkills(names);
    if (touched) setSkillTouched(true);

    // Every question belonging to any selected skill, once. A question tagged
    // for two of them is in both groups — that is the point of allowing several
    // — and putting it in the split twice would train and score on it twice.
    const seen = new Set();
    const questions = [];
    for (const name of names) {
      const group = preview?.groups.find((g) => g.skill_name === name);
      for (const question of group?.questions || []) {
        if (seen.has(question.item_key)) continue;
        seen.add(question.item_key);
        questions.push(question);
      }
    }

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
    rebuildSplit(makeSplit(questions, { train: trainKeys, val: valKeys }));
  }

  // One card clicked: a radio in isolated mode, a checkbox in routing.
  function toggleSkill(skillName) {
    if (mode !== "routing") {
      // Clicking the card that is already selected is not a change, and acting
      // on it as if it were costs the developer their work: `chooseSkills`
      // rebuilds the split from the preview, so every move, copy and exclusion
      // made on step 4 goes back to the proposed 70/30 — and, since the rebuild
      // also clears the undo history, there is nothing left to take it back
      // with. The same click on a radio the browser owns does nothing at all.
      if (skills.length === 1 && skills[0] === skillName) return;
      chooseSkills([skillName]);
      return;
    }
    const next = skills.includes(skillName)
      ? skills.filter((n) => n !== skillName)
      : [...skills, skillName];
    // Order follows the groups rather than the clicks, so the review page and
    // the request read the same way however the selection was assembled.
    const order = (preview?.groups || []).map((g) => g.skill_name);
    chooseSkills(order.filter((n) => next.includes(n)));
  }

  // One skill against the agent. Every candidate is checked as the Skill step
  // opens, so the cards can say which are eligible while the choice is being
  // made rather than after it. Keyed by name, so two checks in flight after a
  // quick change of mind cannot overwrite each other's answers — which is what
  // the old single-slot shape had to be defended against by hand.
  async function runSkillCheck(skillName) {
    setChecks((current) => ({ ...current, [skillName]: { skill: skillName, status: "checking" } }));
    try {
      const result = await api.skillCheck(skillName, agentConfig, secrets);
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
  const groupNames = (preview?.groups || []).map((g) => g.skill_name).join("\0");
  // The credential is part of the key too: a check that 401'd is an answer
  // about a request that no longer exists once a key has been typed.
  const agentKey = [
    agentConfig.agent_skills_url,
    agentConfig.agent_chat_url,
    agentConfig.agent_auth_header,
    secrets.agent_api_key || "",
    agentConfig.agent_timeout_s,
  ].join("\0");
  useEffect(() => {
    if (!groupNames) return;
    groupNames.split("\0").forEach((name) => runSkillCheck(name));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [groupNames, agentKey]);

  // The default selection, and its one correction. It fills in as soon as the
  // groups exist so the step never opens with nothing chosen, then moves off a
  // skill this mode cannot edit once the agent has answered — but only while the
  // developer has not chosen for themselves.
  const wanted = skillTouched ? skills : defaultSkills(preview?.groups, checks, mode);
  const wantedKey = wanted.join("\u0000");
  useEffect(() => {
    if (skillTouched || !wanted.length || wantedKey === skills.join("\u0000")) return;
    chooseSkills(wanted, { touched: false });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [wantedKey, skillTouched]);

  // Switching to isolated cannot leave several selected: it sends one skill to
  // the agent, and a request naming two is refused by the API.
  useEffect(() => {
    if (mode !== "routing" && skills.length > 1) chooseSkills(skills.slice(0, 1));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode]);

  async function start() {
    setStarting(true);
    setError(null);
    try {
      const run = await api.createOptimizationRun({
        name: name.trim() || null,
        mode,
        skill_name: skills[0],
        skill_names: skills,
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
        // `configFrom` sends every validated field on the form except the two
        // the body carries. Each one used to be listed here by hand, so a field
        // added to the form and forgotten in this list was a setting the wizard
        // showed, validated, and never sent.
        config: cleanConfig({
          ...config,
          ...extraConfig(hyper),
          ...configFrom(hyperValues),
        }),
        secrets,
        detector: {},
      });
      toast.success(
        skills.length > 1
          ? `Optimization run started for ${skills.length} skills.`
          : `Optimization run started for ${skills[0]}.`,
      );
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
  // Leaving the agent step is where the expensive check happens, and only if
  // nothing has proved these URLs yet. Doing it on every Continue would spend a
  // model call each time somebody stepped back to reread the modes; never doing
  // it is how a run gets an hour in before discovering the override was ignored.
  //
  // A failure does not advance, and it does not need to say anything here: the
  // checks land under the fields the developer is already looking at, and the
  // footer's blocking line picks them up on the next render.
  async function advance() {
    const onAgentStep = STEPS[stepIndex]?.id === "mode";
    if (onAgentStep && !probeMatches(chatProbe, {
      chatUrl: agentConfig.agent_chat_url,
      skillsUrl: agentConfig.agent_skills_url,
    })) {
      const probe = await testChat();
      if (gateFor("optimization", {
        ...agentChecks,
        ...(probe?.chat ? { chat: probe.chat } : {}),
        ...(probe?.override ? { override: probe.override } : {}),
        ...(probe?.trace ? { trace: probe.trace } : {}),
      }).blocked) {
        return;
      }
    }
    setStepIndex((i) => i + 1);
  }

  const wizardState = {
    stepIndex, sourceIds, preview, previewError, skills, split, limits, checks, mode,
    hyper, defaults: defaults?.defaults, agentChecks,
  };
  const blocked = blockingReason(wizardState);
  const reachable = furthestStep(wizardState);
  const { values: hyperValues, errors: hyperErrors } = hyperState(hyper, defaults?.defaults);

  if (!defaults) return <Skeleton variant="row" count={5} />;

  return (
    <div className="opt-wizard">
      <StepBar steps={STEPS} current={stepIndex} onGo={setStepIndex} furthest={reachable} />

      <DefaultsNotice
        defaults={defaults.defaults}
        systemDefaults={defaults.system_defaults}
      />

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
            chatProbe={chatProbe}
            chatBusy={chatBusy}
            onTestChat={testChat}
            skillsProbe={skillsProbe}
            skillsBusy={skillsBusy}
            secrets={secrets}
            onSecrets={setSecrets}
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
              selected={skills}
              onSelect={toggleSkill}
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
            <SplitEditor
              split={split}
              limits={limits}
              onChange={editSplit}
              onUndo={undoSplit}
              canUndo={undoStack.canUndo(splitHistory)}
            />
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
            mode={mode}
          />
        )}

        {step.id === "review" && (
          <ReviewStep
            name={name}
            onName={setName}
            skills={skills}
            mode={mode}
            split={split}
            defaults={defaults}
            hyper={hyper}
            onHyper={setHyper}
            values={hyperValues}
            errors={hyperErrors}
            impls={defaults.impls}
            gateMetric={config.gate_metric || defaults.defaults?.gate_metric || "hard"}
            onHyperValue={(key, value) => setHyper({ ...hyper, [key]: String(value) })}
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
            loading={chatBusy}
            onClick={advance}
          >
            {chatBusy ? "Testing agent…" : "Continue"}
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
            {plural(previewQuestionCount(preview), "question")}{" "}
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
function ModeStep({
  mode, onMode, config, onConfig, defaults, impls,
  chatProbe, chatBusy, onTestChat, skillsProbe, skillsBusy,
  secrets, onSecrets,
}) {
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
      description="Every skill on the next steps is looked up on this server, and the run answers its questions there. Optimization needs both endpoints: it sends a candidate skill with every rollout and reads back what the agent used."
    >
      {impls?.agent === "fake" && (
        <Banner tone="info" title="The agent seam is fake">
          <code>AGENT_IMPL=fake</code> — questions are answered by canned code
          rather than by a server, so this address is recorded but not called.
        </Banner>
      )}
      <AgentEndpointsFields
        chatUrl={config.agent_chat_url || ""}
        skillsUrl={config.agent_skills_url || ""}
        onChangeChat={(v) => onConfig({ ...config, agent_chat_url: v })}
        onChangeSkills={(v) => onConfig({ ...config, agent_skills_url: v })}
        apiKey={secrets.agent_api_key || ""}
        authHeader={config.agent_auth_header || ""}
        onChangeApiKey={(v) => onSecrets({ ...secrets, agent_api_key: v })}
        onChangeAuthHeader={(v) => onConfig({ ...config, agent_auth_header: v })}
        chatProbe={chatProbe}
        chatBusy={chatBusy}
        onTestChat={onTestChat}
        skillsProbe={skillsProbe}
        skillsBusy={skillsBusy}
        idPrefix="opt"
      />
      <Field
        label="Request timeout (seconds)"
        help="How long one question may take before the run counts it as failed."
      >
        <NumberInput
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

function SettingsStep({ defaults, config, onConfig, secrets, onSecrets, mode }) {
  const set = (key) => (e) => onConfig({ ...config, [key]: e.target.value });
  const setSecret = (key) => (e) => onSecrets({ ...secrets, [key]: e.target.value });
  const d = defaults.defaults;
  // The server's own default when the form has not been touched, so the
  // selection shown is the one the run would actually use.
  const gateMetric = config.gate_metric || d.gate_metric || "hard";

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

      <FormSection
        title="What the gate compares"
        description={
          mode === "routing"
            ? "A routing run is kept or dropped on whether the agent opened the skills each question is tagged for. The judge still grades every answer and the chart still draws it — it just does not decide."
            : "An isolated run is kept or dropped on the judge's verdict on the answers."
        }
      >
        <Field label="Score">
          <div className="opt-metrics">
            {GATE_METRICS.map((metric) => (
              <label
                key={metric.id}
                className={`opt-metric${gateMetric === metric.id ? " is-selected" : ""}`}
              >
                <span className="opt-metric-head">
                  <input
                    type="radio"
                    name="opt-gate-metric"
                    value={metric.id}
                    checked={gateMetric === metric.id}
                    onChange={set("gate_metric")}
                  />
                  <span className="opt-metric-title">{metric.label}</span>
                </span>
                <span className="opt-metric-desc">{metric.help}</span>
              </label>
            ))}
          </div>
        </Field>
        {needsMixedWeight(gateMetric) && (
          <Field
            label="Weight on partial credit"
            help="Between 0 and 1. At 0 this is the exact score; at 1 it is partial credit alone."
          >
            <NumberInput
              min="0" max="1" step="0.1"
              value={config.mixed_weight ?? ""}
              onChange={set("mixed_weight")}
              placeholder={String(d.mixed_weight ?? 0.5)}
            />
          </Field>
        )}
      </FormSection>

      <FormSection title="Models">
        <Field
          label="Judge model"
          help={
            defaults.impls.judge === "fake"
              ? "JUDGE_IMPL=fake — this field has no effect until a real judge is configured."
              : mode === "routing"
                ? "Grades every answer. In a routing run its score is recorded and drawn, but the gate compares routing accuracy instead."
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

// The stop conditions are written as sentences with the numbers inside them, so
// these are the two inputs that sit inline in one. Deliberately not `Field`s:
// a label above each box is what made the share and the streak read as two
// unrelated settings.
const ERROR_FIELDS = [
  "early_stop_train_error_share",
  "early_stop_train_error_streak",
  "early_stop_val_error_share",
  "early_stop_val_error_streak",
];

function StopRule({ children }) {
  return <p className="opt-stoprule">{children}</p>;
}

function PercentInput({ field, raw, set, errors, placeholder }) {
  return (
    <span className="opt-stoprule-input">
      <NumberInput
        min={HYPER_FIELDS[field].min}
        max={HYPER_FIELDS[field].max}
        value={raw(field)}
        onChange={set(field)}
        placeholder={placeholder}
        aria-label={ariaLabel(field)}
        aria-invalid={errors[field] ? "true" : undefined}
      />
      %
    </span>
  );
}

function CountInput({ field, raw, set, errors }) {
  return (
    <span className="opt-stoprule-input">
      <NumberInput
        min={HYPER_FIELDS[field].min}
        value={raw(field)}
        onChange={set(field)}
        aria-label={ariaLabel(field)}
        aria-invalid={errors[field] ? "true" : undefined}
      />
    </span>
  );
}

// The sentence reads the number; a screen reader needs the field's name.
function ariaLabel(field) {
  return field.replace(/^early_stop_/, "").replace(/_/g, " ");
}

function firstError(errors, fields) {
  return fields.map((field) => errors[field]).find(Boolean);
}

// What an analyst batch size buys, in the numbers on this form.
//
// The sentence exists because the two batch sizes look like one setting until
// someone tells you otherwise: a step answers `batch` questions and then
// reflects on them in groups of `minibatch`, failures apart from successes, so
// lowering this one does not shrink what the step measures — it splits the
// analyst's reading into more, smaller prompts, which is the fix when the
// optimizer refuses a call for being too long.
function analystHelp(batch, minibatch) {
  const base =
    "Trajectories per analyst call. Failures and successes are grouped separately, " +
    "so a step makes one call per group of each.";
  if (!batch || !minibatch) return base;
  const calls = analystCallsPerStep(batch, minibatch);
  return `${base} With ${plural(batch, "question")} per step this is up to ${plural(calls, "call")} a step.`;
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
  // The token figure first: it is the number that decides whether the call
  // fits, and at the end of a four-line paragraph nobody reaches it.
  if (!est) return base;
  return `≈ ${est.low.toLocaleString()}–${est.high.toLocaleString()} tokens. ${base}`;
}

function ReviewStep({
  name, onName, skills, mode, split, defaults, hyper, onHyper, values, errors, impls,
  gateMetric, onHyperValue,
}) {
  const c = counts(split);
  // The effective values, which are the typed ones when they parse and the
  // server's defaults when the field has not been touched. A field mid-edit —
  // empty, or "1x" — has no value here, and the estimate below says so rather
  // than quietly computing with a zero.
  const epochs = values.num_epochs;
  const batch = values.batch_size;
  // Raw, so the input renders exactly what was typed. Backing a number input
  // with `Number(raw)` is what made these fields impossible to clear.
  // `defaultText` rather than the bare default, because two of these fields are
  // typed as whole percents and stored as fractions — 25 on the form, 0.25 in
  // the config — and it is the one place that conversion lives.
  const raw = (key) => hyper[key] ?? defaultText(key, defaults.defaults);
  const set = (key) => (e) => onHyper({ ...hyper, [key]: e.target.value });
  // The same rule for the switches: untouched shows what the server would do,
  // not a hard-coded off. A deployment that turns one of these on by default
  // was previously shown an unticked box beside a run that would tick it.
  const switchOn = (key) => Boolean(hyper[key] ?? defaults.defaults[key]);

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
    minibatchSize: values.minibatch_size,
    // Routing makes one analyst call a step and reaches neither merge nor
    // ranking, so an estimate blind to the mode overstates it by roughly three.
    mode,
  };
  const estimate = estimable ? estimateRun(estimateInput) : null;
  // The derivation behind each number, for the `?` beside it. Generated from the
  // same inputs as the estimate rather than written out beside it, so the two
  // cannot drift into confidently explaining a formula that has changed.
  const explain = estimable ? explainRun(estimateInput) : null;

  // Settings a routing run cannot recover from, while they are still settable.
  // Rendered here rather than decided here: the rules are a pure module so
  // `node --test` can reach them (`frontend/CLAUDE.md`).
  const routing = routingReviewWarnings({
    mode, skills, split, values: { ...values, gate_metric: gateMetric },
  });

  return (
    <>
      {routing.map((warning) => (
        <Banner key={warning.id} tone={warning.tone} title={warning.title}>
          {warning.body}
          {warning.suggestion ? (
            <>
              {" "}
              <button
                type="button"
                className="opt-inline-action"
                onClick={() => onHyperValue("batch_size", warning.suggestion)}
              >
                Use {warning.suggestion}
              </button>
            </>
          ) : null}
        </Banner>
      ))}

      <FormSection title="Name this run">
        <Field label="Name" help="Optional. The list falls back to the time it started.">
          <input value={name} onChange={(e) => onName(e.target.value)} placeholder={`Tune ${skills[0] || "this skill"}`} />
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
          <NumberInput
            min={HYPER_FIELDS.num_epochs.min}
            max={HYPER_FIELDS.num_epochs.max}
            value={raw("num_epochs")}
            onChange={set("num_epochs")}
            aria-invalid={errors.num_epochs ? "true" : undefined}
          />
        </Field>
        <Field
          label="Batch size"
          help={
            mode === "routing"
              ? "Questions per step, drawn so that every skill under optimisation is " +
                "represented — each description is rewritten from what its step saw, " +
                "so a skill absent from the batch would be edited on the others' evidence."
              : "Questions per step, drawn at random from the training split and " +
                "reshuffled each epoch. Set it to the size of the split and one epoch " +
                "is one step."
          }
          error={errors.batch_size}
        >
          <NumberInput
            min={HYPER_FIELDS.batch_size.min}
            value={raw("batch_size")}
            onChange={set("batch_size")}
            aria-invalid={errors.batch_size ? "true" : undefined}
          />
        </Field>
        <Field
          label="Learning rate"
          help={
            "The most edits one step may apply — the whole meaning of the word here. " +
            "Fixed for the run; it does not decay." +
            (mode === "routing"
              ? " One description is one edit, so set it to at least the number of " +
                "skills you are optimising."
              : "")
          }
          error={errors.learning_rate}
        >
          <NumberInput
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
          help={`How many questions are sent to the agent server at once. A step answers ${
            batch ?? "n"
          } training questions and then the whole validation split, ${
            values.concurrency ?? "n"
          } at a time. Raise it only as far as the agent server can take.`}
          error={errors.concurrency}
        >
          <NumberInput
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
        description={
          mode === "routing"
            ? "Each step sends the optimizer one prompt: the skills, the descriptions " +
              "they compete against, and every question in the step with the skills it " +
              "was tagged for beside the ones the agent opened. No trajectories — a " +
              "routing decision is made before the agent acts — so there is nothing " +
              "here to size."
            : "Each step sends the optimizer one prompt per minibatch: the skill, then " +
              "the trajectories of the questions in it. These two decide how many " +
              "prompts there are and how big each one gets."
        }
      >
        {/* Separate from the training batch size in the engine since the
            beginning, and absent from this form since the beginning — which
            made them look like one number. A step answers `batch_size`
            questions and then reflects on them in groups of this size, with
            failures and successes grouped separately, so a batch of 16 with a
            minibatch of 8 is up to four analyst calls rather than one. */}
        <Field
          label="Analyst batch size"
          help={
            mode === "routing"
              ? "Routing sends the whole step to one analyst. A description is a " +
                "single line, so splitting the step would produce one complete " +
                "rewrite of it per group and nothing downstream could see the " +
                "questions behind them to choose between them."
              : analystHelp(values.batch_size, values.minibatch_size)
          }
          error={mode === "routing" ? null : errors.minibatch_size}
        >
          <NumberInput
            min={HYPER_FIELDS.minibatch_size.min}
            value={mode === "routing" ? (values.batch_size ?? "") : raw("minibatch_size")}
            onChange={set("minibatch_size")}
            disabled={mode === "routing"}
            aria-invalid={
              mode !== "routing" && errors.minibatch_size ? "true" : undefined
            }
          />
        </Field>
        {mode !== "routing" && (
          <>
          <Field
            label="Trajectory budget"
            help={budgetHelp(values.reflect_budget_chars)}
            error={errors.reflect_budget_chars}
          >
            <NumberInput
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
          </>
        )}
      </FormSection>

      {/* When the run stops before it has run out of steps.
          Two of these four conditions existed before and neither was visible:
          a run stopped when its step counter ran out, or — invisibly, and
          configurable only through the API — when a rollout failed twice in a
          row, which failed the whole run. An hour of paid agent calls could end
          because the agent server was down for the last five minutes of it,
          and nothing on this screen had said that could happen.
          Written as sentences with the numbers in them, because each condition
          is a *pair*: a share of the split that may fail, and how many refused
          rollouts in a row are an outage rather than a bad afternoon. Two
          labelled boxes side by side would leave the reader to work out that
          they belong together. */}
      <FormSection
        title="When it stops early"
        description="A run always stops when it runs out of steps. These are the other four endings, and each is off when its number is 0 or blank."
      >
        <Field label="Unanswered questions" error={firstError(errors, ERROR_FIELDS)}>
          <StopRule>
            More than
            <PercentInput field="early_stop_train_error_share" raw={raw} set={set} errors={errors} />
            of a <strong>training</strong> batch coming back unanswered, for
            <CountInput field="early_stop_train_error_streak" raw={raw} set={set} errors={errors} />
            steps in a row.
          </StopRule>
          <StopRule>
            More than
            <PercentInput field="early_stop_val_error_share" raw={raw} set={set} errors={errors} />
            of a <strong>validation</strong> split coming back unanswered, for
            <CountInput field="early_stop_val_error_streak" raw={raw} set={set} errors={errors} />
            steps in a row.
          </StopRule>
          <p className="opt-stoprule-note">
            A question that never came back is not the skill being wrong, so it is
            left out of the accuracy rather than counted as an error. Past the
            share above, the split is not scored at all: a training batch that
            far gone is skipped, and a validation split that far gone drops its
            candidate unjudged rather than accepting an edit on whichever
            questions did answer.
          </p>
        </Field>
        <Field label="No progress" error={errors.early_stop_patience}>
          <StopRule>
            <CountInput field="early_stop_patience" raw={raw} set={set} errors={errors} />
            steps in a row without beating the best validation score.
          </StopRule>
        </Field>
        <Field label="Good enough" error={errors.early_stop_target_score}>
          <StopRule>
            Validation reaches
            <PercentInput field="early_stop_target_score" raw={raw} set={set} errors={errors} placeholder="—" />
            on the held-back questions.
          </StopRule>
        </Field>
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
          <label className="ui-switch">
            <input
              type="checkbox"
              checked={switchOn("slow_update")}
              onChange={(e) => onHyper({ ...hyper, slow_update: e.target.checked })}
            />
            <span>Write epoch guidance into the skill</span>
          </label>
        </Field>
        <Field
          label="Meta skill"
          help="Optimizer-side memory: what the last epoch taught it about its own editing, shown to the analyst on later steps. Never written into the skill itself."
        >
          <label className="ui-switch">
            <input
              type="checkbox"
              checked={switchOn("meta_skill")}
              onChange={(e) => onHyper({ ...hyper, meta_skill: e.target.checked })}
            />
            <span>Carry the optimizer's own notes between epochs</span>
          </label>
        </Field>
        {epochs != null && epochs < 2 && (switchOn("slow_update") || switchOn("meta_skill")) && (
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
          <div>
            <dt>{skills.length > 1 ? "Skills" : "Skill"}</dt>
            <dd>
              {skills.map((name, i) => (
                <React.Fragment key={name}>
                  {i > 0 ? ", " : ""}<code>{name}</code>
                </React.Fragment>
              ))}
              {" · "}{mode}
            </dd>
          </div>
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
