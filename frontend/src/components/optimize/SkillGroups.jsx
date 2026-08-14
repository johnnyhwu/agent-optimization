import React from "react";
import Badge, { BadgeRow } from "../ui/Badge.jsx";
import Button from "../ui/Button.jsx";
import Card, { CardHeader } from "../ui/Card.jsx";
import EmptyState from "../ui/EmptyState.jsx";
import Banner from "../ui/Banner.jsx";
import Skeleton from "../ui/Skeleton.jsx";
import { IconAlert, IconCheck, IconRefresh, IconTarget } from "../icons.jsx";
import { plural } from "../../plural.js";
import { checkFor, skillStatus } from "../../optimize_wizard.js";

// Wizard step 3: the imported questions, grouped by the skill they are tagged
// with. Picking a group is picking what the run optimises.
//
// Three things this step has to do at once, which is why the card carries more
// than a title:
//
// **Look like a choice.** It did not. Each group was a plain card holding a
// six-row table, identical in every respect to the read-only tables elsewhere in
// the app, and the selected one differed by a 1px inset ring. Nothing on the
// screen said "pick one", the page ran to 1652px, and the footer sentence that
// did say it was below the fold. Now: one skill is selected on arrival, the
// cards carry a radio, and the questions are folded away behind a summary so all
// the options fit on one screen.
//
// **Say what cannot be picked, and why.** Two kinds of ineligible. The ambiguous
// bucket — questions tagged with no skill or with two — is not a skill at all and
// can never be chosen; guessing a group for them is worse than excluding them,
// because a question reflected on by an analyst editing an unrelated skill looks
// entirely normal while teaching the run nothing. And in routing mode, a skill
// whose SKILL.md has no frontmatter has no description to optimise. Both now read
// as unavailable rather than merely dimmer.
//
// **Clear the skill against the agent.** This was a step of its own, after the
// split, which meant the wizard accepted a skill and then rejected it two screens
// later. The check now runs for every candidate as this step opens, so "the agent
// has never heard of this skill" is part of the card rather than a verdict
// delivered afterwards.

export function accuracyLabel(question) {
  // "no data" and "0%" are different claims and must not render the same. A
  // question nobody has run is not the hardest in the set — it is unknown, and
  // it is the one a developer would otherwise reach for first.
  if (question.prior_accuracy == null) return "—";
  return `${Math.round(question.prior_accuracy * 100)}%`;
}

export function accuracyTone(question) {
  if (question.prior_accuracy == null) return "neutral";
  if (question.prior_accuracy >= 0.8) return "success";
  if (question.prior_accuracy <= 0.3) return "danger";
  return "warning";
}

export default function SkillGroups({
  preview,
  selected,
  onSelect,
  checks,
  mode,
  onRecheck,
  impls,
}) {
  const groups = preview?.groups || [];
  const ambiguous = preview?.ambiguous || [];

  if (!groups.length && !ambiguous.length) {
    return (
      <EmptyState icon={<IconTarget size={22} />} title="No questions in these sets">
        Pick at least one eval set that has questions in it.
      </EmptyState>
    );
  }

  return (
    <div className="opt-groups">
      {impls?.workspace === "fake" && (
        <Banner tone="info" title="The agent is a fake">
          <code>WORKSPACE_IMPL=fake</code>, so the skills below are canned rather
          than read from a real agent server. Everything works; the numbers are
          make-believe.
        </Banner>
      )}

      {!groups.length && (
        <Banner tone="warning" title="Nothing here can be optimised">
          Every question in these sets carries either no skill tag or more than
          one, so there is no group to train against. Tagging them is done in the
          eval set.
        </Banner>
      )}

      {groups.map((group) => (
        <SkillCard
          key={group.skill_name}
          group={group}
          selected={selected === group.skill_name}
          status={skillStatus(checkFor(checks, group.skill_name), mode)}
          check={checkFor(checks, group.skill_name)}
          mode={mode}
          onSelect={() => onSelect(group.skill_name)}
          onRecheck={() => onRecheck(group.skill_name)}
        />
      ))}

      {ambiguous.length > 0 && (
        <Card className="opt-group is-unavailable">
          <CardHeader
            title="Cannot be assigned"
            count={ambiguous.length}
            actions={<Badge tone="warning" icon={<IconAlert size={13} />}>Excluded</Badge>}
          />
          <p className="opt-group-note">
            These carry no skill tag, or more than one. A run trains one skill, so
            there is no correct place to put them — tagging them in the eval set
            is what brings them in.
          </p>
          <QuestionDetails questions={ambiguous} showSkills label="the excluded questions" />
        </Card>
      )}
    </div>
  );
}

// One selectable skill.
//
// `blocked` is the only state that refuses the click. A check still in flight
// does not — the card is selectable the moment it appears, and the footer says
// what is being waited for; making the whole step inert for the length of an
// agent round-trip is how the previous version felt like it had not loaded.
// `failed` does not either: the request not getting through says nothing about
// the skill, so the card offers a retry rather than a verdict.
function SkillCard({ group, selected, status, check, mode, onSelect, onRecheck }) {
  const blocked = status.state === "blocked";
  const result = check?.status === "ok" ? check.result : null;
  const className = [
    "opt-group",
    selected && "is-selected",
    blocked && "is-unavailable",
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <Card
      interactive={!blocked}
      onClick={blocked ? undefined : onSelect}
      className={className}
      aria-disabled={blocked || undefined}
    >
      <CardHeader
        title={
          <span className="opt-group-pick">
            {/* Presentational: the whole card is the button (Card's `interactive`
                gives it role and keyboard handling), so this must not be a
                second tab stop announcing itself separately. */}
            <span
              className={`opt-group-radio${selected ? " is-on" : ""}`}
              aria-hidden="true"
            />
            <code>{group.skill_name}</code>
          </span>
        }
        count={group.questions.length}
        actions={<SkillCardStatus status={status} onRecheck={onRecheck} mode={mode} />}
      />

      {status.state === "checking" && <Skeleton variant="row" count={1} />}

      {status.state === "failed" && (
        <Banner tone="error" title="The agent could not be checked">
          {check.error} — this says nothing about whether the skill is there; the
          request itself did not get through.
        </Banner>
      )}

      {blocked && (
        <p className="opt-group-blocked">
          <IconAlert size={13} /> {status.reason}
        </p>
      )}

      {result?.exists && (
        <p className="opt-hint">
          {plural(result.files.length, "file")} on the agent ·{" "}
          {result.n_chars.toLocaleString()} characters ·{" "}
          <code>{result.files.join(", ")}</code>
        </p>
      )}

      {result && !result.exists && (
        <p className="opt-hint">
          The agent has: {result.available_skills?.join(", ") || "no skills at all"}.
          A question's skill tag and the agent's directory name have to be the
          same word.
        </p>
      )}

      <QuestionDetails questions={group.questions} label="the questions" />
    </Card>
  );
}

function SkillCardStatus({ status, onRecheck, mode }) {
  if (status.state === "checking") {
    return <Badge tone="neutral" size="sm">Checking the agent…</Badge>;
  }
  if (status.state === "failed") {
    return (
      <Button
        variant="ghost"
        icon={<IconRefresh size={14} />}
        onClick={(e) => {
          // The card underneath is the select button. Without this, retrying a
          // check would also pick the skill it failed for.
          e.stopPropagation();
          onRecheck();
        }}
      >
        Try again
      </Button>
    );
  }
  if (status.state === "blocked") {
    return (
      <Badge tone="warning" icon={<IconAlert size={13} />}>
        {mode === "routing" ? "No description to edit" : "Not on the agent"}
      </Badge>
    );
  }
  return (
    <Badge tone="success" icon={<IconCheck size={13} />}>
      Found on the agent
    </Badge>
  );
}

// Folded by default. Three skills' worth of six-row tables was most of the
// 1652px this step used to run to, and none of it is needed to choose between
// them — the count and the accuracy spread are, and those are in the summary.
function QuestionDetails({ questions, showSkills = false, label }) {
  const scored = questions.filter((q) => q.prior_accuracy != null);
  const mean = scored.length
    ? Math.round((scored.reduce((sum, q) => sum + q.prior_accuracy, 0) / scored.length) * 100)
    : null;

  return (
    <details
      className="opt-group-questions"
      // The card is a button; a click that opens the fold must not also be read
      // as picking (or, on the excluded card, as picking nothing).
      onClick={(e) => e.stopPropagation()}
    >
      <summary>
        Show {label}
        <span className="opt-group-summary">
          {mean != null
            ? `${mean}% average prior accuracy over ${plural(scored.length, "question")}`
            : "never run"}
        </span>
      </summary>
      <QuestionTable questions={questions.slice(0, 8)} showSkills={showSkills} />
      {questions.length > 8 && (
        <p className="opt-group-more">
          and {questions.length - 8} more — all of them go to the next step, where
          the split is chosen.
        </p>
      )}
    </details>
  );
}

function QuestionTable({ questions, showSkills = false }) {
  return (
    <table className="opt-qtable">
      <thead>
        <tr>
          <th>Question</th>
          <th>Eval set</th>
          {showSkills && <th>Tagged</th>}
          <th className="num">Accuracy</th>
        </tr>
      </thead>
      <tbody>
        {questions.map((q) => (
          <tr key={q.item_key}>
            <td className="opt-qtext" title={q.question}>
              {q.question}
            </td>
            <td className="opt-qset">{q.eval_set_name}</td>
            {showSkills && (
              <td>
                <BadgeRow>
                  {q.skills.length ? (
                    q.skills.map((s) => (
                      <Badge key={s} tone="neutral" size="sm">
                        {s}
                      </Badge>
                    ))
                  ) : (
                    <Badge tone="neutral" size="sm" outline>
                      no tag
                    </Badge>
                  )}
                </BadgeRow>
              </td>
            )}
            <td className="num">
              <Badge tone={accuracyTone(q)} size="sm" mono>
                {accuracyLabel(q)}
              </Badge>
              {/* The denominator, always. 60% from five runs and 60% from one
                  are different claims, and the questions most worth optimising
                  are exactly the ones with the least history. */}
              <span className="opt-qruns">
                {q.prior_runs ? `${q.prior_runs} run${q.prior_runs === 1 ? "" : "s"}` : "never run"}
              </span>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
