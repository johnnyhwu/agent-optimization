import React from "react";
import Badge, { BadgeRow } from "../ui/Badge.jsx";
import Card, { CardHeader } from "../ui/Card.jsx";
import EmptyState from "../ui/EmptyState.jsx";
import Banner from "../ui/Banner.jsx";
import { IconAlert, IconTarget } from "../icons.jsx";

// Wizard step 2: the imported questions, grouped by the skill they are tagged
// with. Picking a group is picking what the run optimises.
//
// The ambiguous bucket is the part worth explaining. A question tagged with no
// skill, or with two, cannot be assigned — and guessing is worse than saying so,
// because a question dropped into the wrong group is reflected on by an analyst
// editing a skill it has nothing to do with, and the run looks entirely normal
// while learning from it. So the bucket is shown, disabled, listing the tags
// each question *does* carry: the fix is in the eval set, and that is where the
// developer is pointed.

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

export default function SkillGroups({ preview, selected, onSelect }) {
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
      {!groups.length && (
        <Banner tone="warning" title="Nothing here can be optimised">
          Every question in these sets carries either no skill tag or more than
          one, so there is no group to train against. Tagging them is done in the
          eval set.
        </Banner>
      )}

      {groups.map((group) => {
        const isSelected = selected === group.skill_name;
        return (
          <Card
            key={group.skill_name}
            interactive
            onClick={() => onSelect(group.skill_name)}
            className={`opt-group${isSelected ? " is-selected" : ""}`}
          >
            <CardHeader
              title={group.skill_name}
              count={group.questions.length}
              actions={
                isSelected ? <Badge tone="accent">Selected</Badge> : null
              }
            />
            <QuestionTable questions={group.questions.slice(0, 6)} />
            {group.questions.length > 6 && (
              <p className="opt-group-more">
                and {group.questions.length - 6} more — all of them go to the next
                step, where the split is chosen.
              </p>
            )}
          </Card>
        );
      })}

      {ambiguous.length > 0 && (
        <Card className="opt-group is-disabled">
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
          <QuestionTable questions={ambiguous.slice(0, 8)} showSkills />
          {ambiguous.length > 8 && (
            <p className="opt-group-more">and {ambiguous.length - 8} more.</p>
          )}
        </Card>
      )}
    </div>
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
