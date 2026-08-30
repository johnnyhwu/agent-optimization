"""Our half of SkillOpt's environment contract: rolling out and scoring.

Upstream calls this an `EnvAdapter`: given a batch and a skill, run the tasks and
return `{"id", "hard", "soft"}` per item. Its own environments do that locally —
a prediction scored against a gold answer by exact match, F1 or ANLS. Ours is an
HTTP agent and an LLM judge, which changes two things:

**There is no local evaluator.** SkillOpt ships none that fits free-form answers
("ACME owed $42,180.00." against "As of the end of Q2, ACME's outstanding balance
was $42,180." scores ~0.3 on token F1), so `hard`/`soft` come from this
platform's own judge. That is also what keeps the numbers comparable with the
Evaluation section: the stated workflow is optimise, download the skill, put it
on the agent, re-run a normal eval — and two different graders would break it.

**Items fail for reasons the skill cannot fix.** An agent timeout is not the
skill being wrong. `score_rollout` excludes those from every figure and counts
them separately; past a threshold it refuses to score the batch at all. See its
docstring for why refusing beats scoring a fraction.

The skill under test reaches the agent as a per-request workspace override — the
same mechanism the playground uses (`WorkspaceOverride`, docs/agent-server-api.md
§4), so nothing
is written back to the agent server and a run cannot disturb the deployed agent.
"""
from __future__ import annotations

import asyncio
import logging
import statistics
import time
import uuid
from datetime import datetime, timezone
from typing import Iterable, Mapping, Sequence

from app.config import settings
from app.integrations import Seams
from app.integrations.base import LlmOutputError, Trace, WorkspaceOverride
from app.optimizer.detector import (
    build_markers,
    detect_activation,
    entry_body_visible,
    shown_to_model as _shown_to_model,
)
from app.optimizer.trajectory import Trajectory, build_trajectory
from app.optimizer.routing import routing_scores
from app.optimizer.store import Item, ResultRow, RolloutSummary
from app.pipeline import RunCancelled, call_agent, call_judge, clip, wait_for_trace
from app.services.failure_text import describe_failure

log = logging.getLogger(__name__)

# Above this share of a batch failing, the step is not scored at all.
DEFAULT_ERROR_THRESHOLD = 0.2


def build_workspace_skills(
    mode: str,
    skill_files: Mapping[str, str],
    workspace_baseline: Mapping[str, str] | None,
) -> dict[str, str]:
    """The complete file set to send with one call.

    `skills` in an override *replaces* the agent's directory for that call (only
    replacement can express deleting a file), so this is always the whole set,
    never a patch.

    The two modes send different things, and that is the experiment:

      * `isolated` sends **only** the skill under optimisation. The agent then
        either uses it or does not — there is no second skill for it to pick
        instead, so accuracy moves with the body and nothing else.
      * `routing` sends the whole workspace with the candidate swapped in,
        because a description exists to win a choice and there has to be a choice
        to win. The other skills come from a snapshot pinned at run start: if
        they moved mid-run, two steps would not be comparable.
    """
    if mode == "routing":
        merged = dict(workspace_baseline or {})
        merged.update(skill_files)
        return merged
    return dict(skill_files)



# --- Proving the override was applied ---------------------------------------
#
# `detect_activation` answers "was this skill loaded?". It cannot answer "was
# *our copy* of it loaded?", because a candidate is usually a light edit of the
# agent's deployed file and both leave the same evidence — the same long body
# lines, in the same places. So an agent server that ignored `metadata.skills`
# would look perfect: full activation, a passing pre-flight, and an accuracy
# curve that never moves because every step measured the same unchanging
# deployed text.
#
# The marker closes that gap by making one copy distinguishable. It goes only
# into the pre-flight's copy — a scored rollout must carry the candidate text
# and nothing else — and it is an HTML comment so that a model reading the
# skill sees noise rather than an instruction.

PROBE_MARKER_TEMPLATE = "<!-- {marker}: platform override check, ignore this line -->"


def make_probe_marker() -> str:
    """A token this run alone will look for, so traces cannot cross-validate."""
    return f"probe-{uuid.uuid4().hex[:12]}"


def inject_probe_marker(
    skill_files: Mapping[str, str], skill_name: str, marker: str
) -> dict[str, str]:
    """A copy of `skill_files` whose entry point carries `marker` on its own line.

    **After the frontmatter, not at the end of the file.** Two reasons, and they
    pull in the same direction: routing mode optimises the description inside
    the frontmatter and `skillio.frontmatter_span` finds it by the leading
    `---`, so nothing may be inserted above it; and an agent's read tool may cap
    how much of a long file it returns, in which case the end is what is lost.
    The first line of the body satisfies both.

    Line-based rather than using `frontmatter_span`'s character offsets, because
    that function only recognises `\n` and only a *terminated* block, and both
    of its failure modes corrupt the file we are about to hand the agent: a CRLF
    `SKILL.md` reported no frontmatter at all and took the marker above its
    opening `---`, and one with no trailing newline reported a span ending at
    EOF and had the comment glued onto its closing `---`. Splitting on lines
    cannot land mid-line, and treats both delimiters the same way whatever ends
    them.

    A skill with no entry point is returned unchanged — there is nowhere to put
    the marker, which costs us the check rather than the run.
    """
    entry = f"{skill_name}/SKILL.md"
    text = skill_files.get(entry)
    if text is None:
        return dict(skill_files)

    line = PROBE_MARKER_TEMPLATE.format(marker=marker)
    lines = text.split("\n")
    # Where the body starts: the line after the frontmatter's closing `---`, or
    # the top of the file when there is no frontmatter to stay below. `rstrip`
    # so a `\r` left by CRLF does not stop a delimiter being recognised.
    at = 0
    if lines and lines[0].rstrip() == "---":
        for i, raw in enumerate(lines[1:], start=1):
            if raw.rstrip() == "---":
                at = i + 1
                break
        else:
            # An opening delimiter with no closing one is not a frontmatter
            # block; treat the whole file as body rather than guessing.
            at = 0

    marked = "\n".join([*lines[:at], line, *lines[at:]])
    return {**skill_files, entry: marked}


def verify_probe_marker(
    trajectory: Trajectory | None, marker: str | None, *, content_visible: bool = False
) -> bool | None:
    """Did the marker reach the model? True / False / None for "cannot tell".

    Seeing it is proof on its own. **Not** seeing it is only evidence when this
    trace demonstrably carries skill file content — which is what
    `content_visible` says, and why a bare absence is `None`.

    That asymmetry is the whole safety of the check. An agent can apply the
    override correctly and still leave a trajectory with no file text in it —
    it logs the tool call but not the result, or logs neither. Reading that
    silence as "you ignored us" would hard-fail a run that would have succeeded,
    which is the worst way for this to be wrong.

    `None` for a missing trajectory is load-bearing for the same reason:
    Langfuse ingestion lags and sometimes fails outright, and reading that as
    "the agent ignored us" would stop runs over a trace store hiccup.

    **One path stays undetectable, and it is worth stating rather than
    discovering.** The probe sends the agent's own files, so the marker line is
    the *only* textual difference between our copy and its copy — nothing else
    can discriminate them. An agent that strips HTML comments while rendering a
    skill into its prompt therefore looks exactly like one that ignored the
    override. The block message names that possibility so a false positive is
    diagnosable rather than baffling.
    """
    if marker is None or trajectory is None:
        return None
    if marker in _shown_to_model(trajectory):
        return True
    return False if content_visible else None


async def run_rollout(
    items: Sequence[Item],
    *,
    skill_files: Mapping[str, str],
    mode: str,
    skill_name: str,
    seams: Seams,
    config: Mapping,
    workspace_baseline: Mapping[str, str] | None = None,
    cancel_event: asyncio.Event | None = None,
    concurrency: int = 4,
    probe_marker: str | None = None,
    on_progress=None,
) -> list[ResultRow]:
    """Answer and judge every item once, with `skill_files` in the agent's hands.

    Structurally the same sequence as one eval question (`app/pipeline.py`,
    shared with the orchestrator and the playground): agent → judge → wait for
    the trace. The trace is not optional here the way it is for a passing eval
    question — the reflect stage reads it, and the activation detector reads it —
    but a trace that never lands still must not fail the item: the verdict is
    the score, and a missing trace only costs this item its place in reflection.
    """
    cancel_event = cancel_event or asyncio.Event()
    skills = build_workspace_skills(mode, skill_files, workspace_baseline)
    if probe_marker is not None:
        # Only the pre-flight passes one. The markers below are built from the
        # *unmarked* files, so the marker cannot become one of the body lines
        # the detector matches on and inflate activation.
        skills = inject_probe_marker(skills, skill_name, probe_marker)
    override = WorkspaceOverride(skills=skills)
    # Computed once for the whole split rather than per item: routing checks
    # every skill in the workspace on every question, and the candidate is fixed
    # for the length of this rollout.
    #
    # From `build_workspace_skills`, not from the marked copy: a probe marker
    # must never become one of the body lines the detector matches on, or the
    # pre-flight would be measuring the line it inserted itself.
    markers = build_markers(build_workspace_skills(mode, skill_files, workspace_baseline))
    timeout_s = config.get("agent_timeout_s") or settings.agent_timeout_s
    semaphore = asyncio.Semaphore(max(concurrency, 1))
    rows: list[ResultRow] = [None] * len(items)  # type: ignore[list-item]

    async def one(position: int, item: Item) -> None:
        async with semaphore:
            rows[position] = await _run_item(
                item,
                override=override,
                skill_files=skill_files,
                skill_name=skill_name,
                seams=seams,
                timeout_s=timeout_s,
                cancel_event=cancel_event,
                markers=markers,
                probe_marker=probe_marker,
            )
        if on_progress is not None:
            await on_progress(rows[position])

    # return_exceptions=True: one unexpected per-item error must not cancel its
    # siblings, exactly as the orchestrator does for a run's questions.
    outcomes = await asyncio.gather(
        *(one(i, item) for i, item in enumerate(items)), return_exceptions=True
    )
    for position, outcome in enumerate(outcomes):
        if isinstance(outcome, BaseException):
            log.exception("unexpected rollout error", exc_info=outcome)
            if rows[position] is None:
                rows[position] = ResultRow(
                    item_key=items[position].item_key,
                    correlation_id=uuid.uuid4().hex,
                    status="failed",
                    failure_kind="agent",
                    error_message=clip(f"{type(outcome).__name__}: {outcome}"),
                )
    return [row for row in rows if row is not None]


async def _run_item(
    item: Item,
    *,
    override: WorkspaceOverride,
    skill_files: Mapping[str, str],
    skill_name: str,
    seams: Seams,
    timeout_s: float,
    cancel_event: asyncio.Event,
    markers: Mapping[str, list[str]],
    probe_marker: str | None = None,
) -> ResultRow:
    correlation_id = uuid.uuid4().hex
    row = ResultRow(
        item_key=item.item_key,
        correlation_id=correlation_id,
        status="pending",
        question_pk=item.question_pk,
        started_at=datetime.now(timezone.utc),
    )

    if cancel_event.is_set():
        row.status = "failed"
        row.failure_kind = "cancelled"
        row.error_message = "Cancelled before this question started."
        return row

    # 1) agent. Measured rather than derived from the timeout: `call_agent`
    #    retries, so the time actually spent is a multiple of the limit set.
    started = time.monotonic()
    try:
        response = await call_agent(
            seams, item.question, correlation_id, "optimizer",
            ["optimize", f"skill_{skill_name}"], timeout_s, cancel_event,
            workspace=override,
        )
    except RunCancelled:
        row.status = "failed"
        row.failure_kind = "cancelled"
        row.error_message = "Cancelled while waiting for the agent."
        return row
    except Exception as exc:  # noqa: BLE001
        message, kind = describe_failure(
            "agent", exc, timeout_s=timeout_s,
            attempts=settings.agent_max_retries + 1,
            waited_s=time.monotonic() - started,
        )
        row.status = "failed"
        row.failure_kind = kind
        row.error_message = clip(message)
        return row

    row.agent_latency_ms = response.latency_ms
    if response.failed:
        row.agent_response = response.response or None
        row.status = "failed"
        row.failure_kind = "agent"
        row.error_message = clip(response.error or "Agent reported a failure.")
        return row
    row.agent_response = response.response

    # 2) judge. Unlike the playground, a failure here fails the *item*: an
    #    unjudged answer has no score, and scoring it as wrong would be inventing
    #    a gradient. It is excluded from the batch instead.
    judge_started = time.monotonic()
    try:
        verdict = await call_judge(
            seams, item.question, response.response,
            item.ground_truth_response, cancel_event,
        )
    except RunCancelled:
        row.status = "failed"
        row.failure_kind = "cancelled"
        row.error_message = "Cancelled while judging; the agent's answer was kept."
        return row
    except LlmOutputError as exc:
        # The judge answered in the wrong shape. That usually indicts this run's
        # judge prompt — the one item on the list a developer can go and fix — so
        # it keeps its own kind instead of being buried among timeouts.
        row.status = "failed"
        row.failure_kind = "judge_invalid"
        row.error_message = clip(f"Judge output could not be parsed: {exc!s}")
        return row
    except Exception as exc:  # noqa: BLE001
        message, kind = describe_failure(
            "judge", exc, timeout_s=settings.llm_timeout_s,
            attempts=settings.llm_max_retries + 1,
            waited_s=time.monotonic() - judge_started,
        )
        row.status = "failed"
        row.failure_kind = kind
        row.error_message = clip(message)
        return row

    row.verdict = verdict.verdict
    row.judge_score = verdict.score
    row.judge_comment = verdict.comment
    row.status = "done"

    # 3) the trace, for reflection and for activation. Best-effort by design: a
    #    trace that never lands costs this item its place in the analyst's
    #    minibatch, not its score.
    if not cancel_event.is_set():
        trace, trace_error = await wait_for_trace(correlation_id, seams.trace, cancel_event)
        row.trace_ready = trace is not None
        row.trace_error = trace_error
        # Kept in memory for the reflect stage, which runs minutes later in the
        # same step and would otherwise re-fetch what we are holding.
        row.trace = trace
        # Folded once, here, and carried on the row: the reflect stage needs the
        # same conversation minutes later and re-folding a fifteen-span trace is
        # not free. `build_trajectory` is also what makes the detector dialect-
        # agnostic — it is the one place that knows how each agent shapes a
        # tool result.
        trajectory = build_trajectory(trace) if trace is not None else None
        row.trajectory = trajectory
        activation = detect_activation(
            trajectory,
            skill_name=skill_name,
            skill_files=skill_files,
            markers=markers,
        )
        row.activated = activation.activated
        row.skills_read = activation.skills_read
        row.detector_hit = activation.hit
        if probe_marker is not None:
            # Guarded rather than passed unconditionally: `entry_body_visible`
            # re-materialises the whole trace payload, and only the pre-flight
            # ever asks for a verdict. Evaluated eagerly it cost every scored
            # rollout of every step that work for a value thrown away on
            # `verify_probe_marker`'s first line.
            #
            # The entry point's own text, not any body text: the marker lives in
            # `SKILL.md` alone, so a visible reference file is not evidence that
            # the marker would have been visible too.
            row.override_verified = verify_probe_marker(
                trajectory, probe_marker,
                content_visible=entry_body_visible(
                    trajectory, skill_name=skill_name, skill_files=skill_files
                ),
            )

    return row


def score_rollout(
    results: Sequence[ResultRow],
    *,
    split: str,
    skill_step_no: int,
    error_threshold: float = DEFAULT_ERROR_THRESHOLD,
    items: Sequence[Item] | None = None,
) -> RolloutSummary:
    """Aggregate one split into the numbers behind a single point on the chart.

    Two rules, and both are about what failure means.

    **Failures are excluded, not scored zero.** An agent timeout is not the skill
    being wrong. Counting it as a wrong answer hands the optimizer a gradient
    pointing at a network problem, and the gate then accepts or rejects a skill
    edit on the strength of how flaky the last two minutes were. Accuracy,
    latency and activation all measure the items that actually produced an
    answer, and the counts sit beside them so the exclusion is visible rather
    than implied.

    **Past a threshold, nothing is scored at all.** Excluding failures creates
    the opposite hazard: a step scored on 60% of its batch is not a smaller
    measurement, it is an unrepresentative one, and the gate has no way to tell.
    Whatever it accepts on that basis contaminates every later step. So the batch
    is abandoned instead — the same call `docs/spec.md` makes about the upload
    path, where a set built from half the rows "looks normal but is wrong".
    `aborted` says that happened and the score fields stay `None`; what the
    caller does about it is `app/optimizer/stopping.py`'s decision, and the
    threshold it passes in is that split's own (train and validation are
    configured separately).
    """
    rows = list(results)
    n_items = len(rows)
    scored = [r for r in rows if r.status == "done"]
    n_scored = len(scored)

    summary = RolloutSummary(
        split=split,
        skill_step_no=skill_step_no,
        n_items=n_items,
        n_scored=n_scored,
        n_agent_error=sum(
            1 for r in rows if r.status != "done" and (r.failure_kind or "").startswith("agent")
        ),
        n_judge_error=sum(
            1 for r in rows if r.status != "done" and (r.failure_kind or "").startswith("judge")
        ),
        results=rows,
    )

    if n_items:
        failed_share = (n_items - n_scored) / n_items
        if failed_share > error_threshold:
            summary.aborted = True
            summary.abort_reason = (
                f"{failed_share:.0%} of the batch failed "
                f"({n_items - n_scored} of {n_items}); "
                f"a gradient from the remainder would not represent this split"
            )
            # And that is the end of it: no hard, no soft, no activation rate.
            # "Refuses to score" has to mean the numbers are absent rather than
            # present-and-not-to-be-trusted — an accuracy on this row would be
            # plotted on the chart, cached against the candidate's hash, and
            # read by the gate, and each of those is a decision made on a
            # measurement of whichever questions the outage happened to spare.
            # The rows themselves are kept, so the page can still show which
            # questions failed and why.
            return summary

    if not n_scored:
        return summary

    summary.hard = sum(1 for r in scored if r.verdict == "correct") / n_scored
    summary.soft = sum(float(r.judge_score or 0.0) for r in scored) / n_scored

    # Both pairs, always, when the caller supplied the questions' tags. Which
    # one gates is the run's `gate_metric` and the mode's choice; the other is
    # how a developer sees that routing improved while the answers did not —
    # the finding that says to run an isolated pass next.
    summary.routing_hard, summary.routing_soft = routing_scores(scored, items)

    latencies = sorted(r.agent_latency_ms for r in scored if r.agent_latency_ms is not None)
    if latencies:
        summary.latency_min_ms = latencies[0]
        summary.latency_max_ms = latencies[-1]
        summary.latency_p50_ms = int(statistics.median(latencies))
        # The mean as well as the median. They answer different questions, and
        # the gap between them is what says whether a slow rollout was slow all
        # the way through or was one question hanging until the timeout.
        summary.latency_mean_ms = int(statistics.fmean(latencies))

    # Unknown is not false. Averaging an unobservable item in as zero would
    # invent a number; the rate describes what could actually be seen, and
    # `n_activated` beside it says how much that was.
    observed = [r for r in scored if r.activated is not None]
    summary.n_activated = sum(1 for r in observed if r.activated)
    summary.activation_rate = (
        summary.n_activated / len(observed) if observed else None
    )
    return summary
