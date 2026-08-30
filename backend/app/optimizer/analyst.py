"""The analyst call: what one minibatch of trajectories is shown, and how.

Upstream's `vendor/reflect.py` builds this prompt itself, and `VENDORED.md`'s
rule is that changes belong outside `vendor/` and call in. Three of them are
needed here, and none can be made from the outside:

**The agent's answer was missing.** The item carried `agent_response` and the
formatter never rendered it, so the analyst was shown the correct answer and the
judge's complaint about a wrong one, and had to reconstruct the wrong one out of
the trajectory. It is the single most useful line on the page for a human
reviewing the same failure.

**"Hidden Reference" is a phrase no analyst prompt uses.** The system prompts
talk about being shown "the correct answers"; the user prompt labelled them with
a term of art from another codebase. It is now "Ground-truth Response".

**"Task type" was always blank.** Upstream's benchmarks classify tasks; ours do
not, and the field went out as an empty heading on every trajectory.

Everything else is upstream's and stays upstream's: the system prompts
(`vendor/prompts/`), the section order of the user message, the JSON contract,
the edit-budget clipping, and the merge and ranking stages that follow.
"""
from __future__ import annotations

from app.optimizer.trajectory import (
    Trajectory,
    render_conversation,
    render_preamble,
    shared_preamble,
)
from app.optimizer.vendor._model import chat_optimizer
from app.optimizer.vendor.json_utils import extract_json
from app.optimizer.vendor.meta_skill import format_meta_skill_context
from app.optimizer.vendor.prompts import load_prompt
from app.optimizer.vendor.update_modes import (
    is_full_rewrite_minibatch_mode,
    normalize_update_mode,
    payload_label,
    truncate_payload,
)


def analyst_system_prompt(source_type: str, mode: str) -> str:
    """The mode's own analyst prompt, through upstream's resolver.

    Upstream keys the override on the environment; here it is the optimization
    mode, because that — not the benchmark — is what changes the question being
    asked. Falling through to the generic prompt would ask a routing run to
    rewrite a body it is forbidden to touch.
    """
    name = "analyst_error" if source_type == "failure" else "analyst_success"
    return load_prompt(name, mode)


def format_trajectory_item(item: dict, ordinal: int, *, include_preamble: bool = True) -> str:
    """One trajectory, headed by the four things a reviewer needs.

    Task, what the agent answered, what right looked like, and why it was marked
    wrong — then the run itself. The order is the order a person reads them in:
    the verdict is only interpretable once both answers are on screen.
    """
    parts = [f"### Trajectory {ordinal} (id={item.get('id', '')})"]

    task = str(item.get("task_description") or "").strip()
    if task:
        parts.append(f"#### Task\n{task}")

    response = str(item.get("agent_response") or "").strip()
    parts.append(f"#### Agent Response\n{response or '(the agent produced no answer)'}")

    reference = str(item.get("reference_text") or "").strip()
    if reference:
        parts.append(f"#### Ground-truth Response\n{reference}")

    fail_reason = str(item.get("fail_reason") or "").strip()
    if fail_reason:
        parts.append(f"#### Failure Reason (from the judge)\n{fail_reason}")

    trajectory = item.get("trajectory")
    if isinstance(trajectory, Trajectory):
        if include_preamble:
            preamble = render_preamble(trajectory)
            if preamble:
                parts.append(preamble)
        conversation = render_conversation(trajectory)
        if conversation:
            parts.append(conversation)
        elif item.get("dropped"):
            # Said out loud rather than left as a silent gap. An analyst that
            # knows a run was withheld can qualify what it proposes; one that is
            # simply shown fewer trajectories than the batch contains cannot.
            parts.append(
                "#### Conversation\n(withheld — this run did not fit in the "
                "analyst's context budget, so only the question, the answers and "
                "the verdict above are shown for it)"
            )

    return "\n\n".join(parts)


def format_minibatch(items: list[dict]) -> str:
    """Every trajectory in one minibatch, separated as upstream separates them.

    With the setup hoisted when they share one, which they almost always do: a
    minibatch is several questions answered by the same agent under the same
    candidate skill, so the tool catalogue and the system prompt are identical
    across all of them. The system prompt is where the skill lives and in a real
    deployment runs to thousands of tokens — printed per trajectory, an
    eight-question batch spends eight copies of it before showing a single tool
    call.
    """
    trajectories = [
        item["trajectory"] for item in items
        if isinstance(item.get("trajectory"), Trajectory)
    ]
    shared = shared_preamble(trajectories)

    rendered = [
        text for text in (
            format_trajectory_item(item, i, include_preamble=shared is None)
            for i, item in enumerate(items, 1)
        ) if text.strip()
    ]
    body = "\n\n---\n\n".join(rendered)
    if shared is None:
        return body

    # Said explicitly, because an analyst that cannot see a system prompt under
    # a trajectory would otherwise have to assume there was none.
    return (
        "### What every agent below was set up with\n"
        "Identical for all of these trajectories, so it is shown once.\n\n"
        f"{render_preamble(shared)}\n\n---\n\n{body}"
    )


def build_user_prompt(
    skill_content: str,
    items: list[dict],
    *,
    source_type: str,
    edit_budget: int,
    mode: str,
    step_buffer_context: str = "",
    meta_skill_context: str = "",
    competing_skills: str = "",
) -> str:
    """The analyst's user message. Section order is upstream's, verbatim.

    `competing_skills` is the one section upstream has no equivalent of, because
    upstream has no routing mode: it is the menu of other skills this
    description is competing against, and it sits directly under the skill it is
    being compared with. Empty for isolated runs, which send one skill to the
    agent and so present no choice to inform — and empty means *absent*, so an
    isolated prompt is byte-for-byte what it was before this existed.
    """
    update_mode = normalize_update_mode(mode)
    user = f"## Current Skill\n{skill_content}\n\n"

    if competing_skills.strip():
        user += f"{competing_skills.rstrip()}\n\n"

    if is_full_rewrite_minibatch_mode(update_mode):
        user += (
            "## Update Format\n"
            "Produce one complete replacement skill candidate for this minibatch. "
            "Do not output edits, patches, or revise suggestions.\n\n"
        )
    else:
        user += (
            f"## {payload_label(update_mode, title=True)} Budget\n"
            f"Produce at most L={edit_budget} {payload_label(update_mode)}.\n\n"
        )

    if step_buffer_context.strip():
        user += f"## Previous Steps in This Epoch\n{step_buffer_context}\n\n"

    optimizer_ctx = format_meta_skill_context(meta_skill_context)
    if optimizer_ctx:
        user += optimizer_ctx + "\n\n"

    heading = "Failed" if source_type == "failure" else "Successful"
    user += f"## {heading} Trajectories ({len(items)} total)\n{format_minibatch(items)}"
    return user


def run_analyst_minibatch(
    skill_content: str,
    items: list[dict],
    *,
    source_type: str,
    mode: str,
    edit_budget: int = 4,
    update_mode: str = "patch",
    step_buffer_context: str = "",
    meta_skill_context: str = "",
    competing_skills: str = "",
) -> dict | None:
    """One optimizer call over one minibatch. `None` when it had nothing to say.

    Mirrors `vendor/reflect.py:run_error_analyst_minibatch` — same model call,
    same JSON extraction, same clip to the edit budget, same `source_type` stamp
    that decides which group the patch is merged in later.
    """
    if not items:
        return None
    normalized = normalize_update_mode(update_mode)
    system = analyst_system_prompt(source_type, mode)
    user = build_user_prompt(
        skill_content, items,
        source_type=source_type, edit_budget=edit_budget, mode=update_mode,
        step_buffer_context=step_buffer_context,
        meta_skill_context=meta_skill_context,
        competing_skills=competing_skills,
    )

    # Exceptions travel. Upstream swallows them and returns `None`, which is
    # indistinguishable from "the model had nothing to propose"; `run_update_stage`
    # catches per minibatch, records the reason and carries on with the rest.
    response, _ = chat_optimizer(
        system=system, user=user,
        max_completion_tokens=64000 if is_full_rewrite_minibatch_mode(normalized) else 16384,
        retries=3,
        stage="analyst",
    )
    result = extract_json(response)
    if result and "patch" in result:
        result["source_type"] = source_type
        if not is_full_rewrite_minibatch_mode(normalized):
            truncate_payload(result["patch"], edit_budget, normalized)
        return result
    return None
