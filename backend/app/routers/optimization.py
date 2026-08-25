"""Optimize (Stage 3): the run list and one run's overview.

Mounted at `/optimization`, which shares no prefix with `/eval-sets` or
`/playground` — Optimize is a sibling section, not a fourth tier of Evaluation
(`docs/spec.md` §10.1), and the URL says so.

**Visibility is derived, not shared.** A run has no role table of its own. You
can see it if you can read *every* eval set it drew questions from, which is the
only rule that cannot leak: a run's item snapshots carry question text from
those sets, so being able to open the run is being able to read them. Deriving it
also means there is no second sharing UI to keep in step with the first, and no
way for the two to disagree.

The creator owns it — cancel, resume and delete are theirs. That matches how a
run already works (`§6.16`: a viewer may stop their own run) without inventing a
new role vocabulary for a second kind of run.
"""
from __future__ import annotations

import asyncio
import json
import math
import uuid
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from app import cancellation
from app.auth import current_subject
from app.config import settings
from app.db import SessionLocal, get_session
from app.integrations import build_seams
from app.models import (
    EvalSet,
    EvalSetRole,
    OptimizationItem,
    OptimizationMinibatch,
    OptimizationResult,
    OptimizationRollout,
    OptimizationRun,
    OptimizationSkill,
    OptimizationStageCall,
    OptimizationStep,
    Question,
    QuestionResult,
    QuestionSkill,
    Run,
)
from app.optimizer import dataset, hyperparams, runner, skillio, stopping
from app.services.agent_skills import top_level_skills
from app.schemas import (
    AnswerLeak,
    EditReportOut,
    ImportPreview,
    ImportPreviewRequest,
    OptimizationConfig,
    OptimizationRunCreate,
    OptimizationMinibatchOut,
    OptimizationStageCallOut,
    OptimizationResultOut,
    OptimizationRolloutDetail,
    OptimizationRunDetail,
    OptimizationRunOut,
    OptimizationRunPage,
    OptimizationRunRename,
    OptimizationSkillDiff,
    OptimizationStepSummary,
    PreviewQuestion,
    PreviewSource,
    SkillCheck,
    SkillDiffFile,
    SkillGroup,
    TraceView,
)
from app.services import deletion, judge_prompt, run_config, user_secrets, user_settings
from app.services.trace_view import resolve_trace_spans, span_to_out
from app.sse import hub, resync_if_dropped, resync_or_ping

router = APIRouter(prefix="/optimization", tags=["optimization"])


async def _readable_run_ids(session: AsyncSession, subject: str):
    """A subquery of the run ids this subject may see.

    "Reader on every source eval set" expressed in SQL rather than in Python
    over a loaded page: filtering after the fact would make the page size depend
    on how many runs the caller happens to be locked out of, and "Showing 12 of
    40" would stop meaning anything.

    A run whose sources have all been deleted (`ON DELETE SET NULL` leaves a NULL
    behind) falls back to its creator — the questions it quoted are gone, so
    there is nothing left to leak, and the run is still their history.
    """
    readable = select(EvalSetRole.eval_set_id).where(EvalSetRole.user_subject == subject)
    # Runs with at least one source the caller cannot read.
    blocked = (
        select(OptimizationItem.run_id)
        .where(
            OptimizationItem.source_eval_set_id.is_not(None),
            OptimizationItem.source_eval_set_id.not_in(readable),
        )
        .distinct()
    )
    return select(OptimizationRun.id).where(
        OptimizationRun.id.not_in(blocked),
        # Nothing here is public: a run you have no relationship to at all is not
        # yours to see just because its sources happen to be readable.
        OptimizationRun.id.in_(
            select(OptimizationItem.run_id).where(
                OptimizationItem.source_eval_set_id.in_(readable)
            )
        )
        | (OptimizationRun.created_by == subject),
    )


async def _load_visible_run(
    session: AsyncSession, run_id: uuid.UUID, subject: str
) -> OptimizationRun:
    """One run the caller may see, or 404.

    404 rather than 403 for a run they may not see, the same choice the
    playground makes for another developer's attempt: whether a run exists at a
    given id is itself not theirs to learn.
    """
    run = await session.get(OptimizationRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="optimization run not found")
    visible = await session.scalar(
        select(func.count()).select_from(
            (await _readable_run_ids(session, subject)).where(
                OptimizationRun.id == run_id
            ).subquery()
        )
    )
    if not visible:
        raise HTTPException(status_code=404, detail="optimization run not found")
    return run


async def _counts(session: AsyncSession, run_ids: list[uuid.UUID]):
    """Per-run split sizes, source sets and finished-step counts, in three queries.

    Not one query per run: `docs/spec.md` §10.2③ records what N+1 did to
    `GET /eval-sets` (180 queries for one page), and a run list is exactly the
    same shape of surface.
    """
    if not run_ids:
        return {}, {}, {}

    split_rows = await session.execute(
        select(OptimizationItem.run_id, OptimizationItem.split, func.count())
        .where(OptimizationItem.run_id.in_(run_ids))
        .group_by(OptimizationItem.run_id, OptimizationItem.split)
    )
    splits: dict[uuid.UUID, dict[str, int]] = {}
    for run_id, split, count in split_rows:
        splits.setdefault(run_id, {})[split] = count

    source_rows = await session.execute(
        select(OptimizationItem.run_id, OptimizationItem.source_eval_set_id)
        .where(
            OptimizationItem.run_id.in_(run_ids),
            OptimizationItem.source_eval_set_id.is_not(None),
        )
        .distinct()
    )
    sources: dict[uuid.UUID, list[uuid.UUID]] = {}
    for run_id, eval_set_id in source_rows:
        sources.setdefault(run_id, []).append(eval_set_id)

    step_rows = await session.execute(
        select(OptimizationStep.run_id, func.count())
        .where(OptimizationStep.run_id.in_(run_ids), OptimizationStep.status == "done")
        .group_by(OptimizationStep.run_id)
    )
    steps = {run_id: count for run_id, count in step_rows}

    return splits, sources, steps


def _run_out(run: OptimizationRun, splits, sources, steps_done) -> OptimizationRunOut:
    return OptimizationRunOut(
        id=run.id,
        name=run.name,
        created_by=run.created_by,
        status=run.status,
        mode=run.mode,
        skill_name=run.skill_name,
        num_epochs=run.num_epochs,
        batch_size=run.batch_size,
        steps_per_epoch=run.steps_per_epoch,
        total_steps=run.total_steps,
        steps_done=steps_done,
        best_step=run.best_step,
        best_score=float(run.best_score) if run.best_score is not None else None,
        cancel_requested=run.cancel_requested,
        error_message=run.error_message,
        stop_reason=run.stop_reason,
        started_at=run.started_at,
        completed_at=run.completed_at,
        source_eval_set_ids=sorted(sources, key=str),
        n_train=splits.get("train", 0),
        n_val=splits.get("val", 0),
    )


# --- The wizard -------------------------------------------------------------

# How many recent runs the prior-accuracy figure is drawn from. Enough to smooth
# a single flaky run, few enough that a question fixed six months ago is not
# still dragging its own history around.
PRIOR_RUNS_WINDOW = 10


@router.get("/defaults")
async def optimization_defaults(
    subject: str = Depends(current_subject),
    session: AsyncSession = Depends(get_session),
):
    """Prefill values for the wizard, plus the rules it has to enforce.

    The limits come from here rather than being written into the browser bundle
    so that the split editor's disabled Start button and the create endpoint's
    400 can never disagree — a button enabled on a request the server rejects is
    worse than no button.

    Nothing here is a credential. Every other field is environment-derived and
    safe to render into a page; a secret among them would be sent to everyone who
    opens the wizard, and would look like a conveniently prefilled field.
    """
    # This deployment's values with the caller's own laid over them. Assembled by
    # `user_settings`, which builds the same dictionary the block below
    # describes — one function, so the wizard's prefill and the settings page
    # cannot disagree about what an untouched run would do. The overlay is
    # deliberately *not* inside `run_config.defaults()` or
    # `hyperparams.algorithm_defaults()`: those are on the path a run executes
    # through, and what a run does must not depend on who started it.
    effective = await user_settings.effective_optimization_defaults(session, subject)
    return {
        "defaults": {
            **effective,
            # Not in the catalogue and so not in `effective`: this one's default
            # is a literal in `optimizer/dataset.py` rather than an environment
            # variable, and a personal default is only offered where a
            # deployment can already configure one.
            "train_share": dataset.DEFAULT_TRAIN_SHARE,
        },
        # What this deployment would have used, so the wizard can say whether
        # anything above came from the caller's own settings.
        "system_defaults": user_settings.optimization_defaults({}),
        "judge_prompt": dict(zip(
            ("system", "user"), judge_prompt.effective(None, None)
        )),
        "judge_score_threshold": settings.judge_score_threshold,
        "limits": {
            "min_train": dataset.MIN_TRAIN,
            "min_val": dataset.MIN_VAL,
            "warn_train": dataset.WARN_TRAIN,
            "warn_val": dataset.WARN_VAL,
        },
        "impls": {
            "agent": settings.agent_impl,
            "judge": settings.judge_impl,
            "trace": settings.trace_impl,
            "workspace": settings.workspace_impl,
            # Fake means the skill edits are canned. Worth saying plainly: the
            # optimizer model field below it would otherwise look like it does
            # something.
            "optimizer": settings.optimizer_impl,
        },
    }


@router.post("/import-preview", response_model=ImportPreview)
async def import_preview(
    body: ImportPreviewRequest,
    subject: str = Depends(current_subject),
    session: AsyncSession = Depends(get_session),
):
    """The questions of one or more eval sets, grouped by skill (wizard step 2).

    Refuses the whole request if any source is unreadable, rather than dropping
    that set quietly: a preview missing half its questions is how someone builds
    a run they believe covers something it does not.
    """
    if not body.eval_set_ids:
        return ImportPreview()
    await _require_reader_on_all(session, body.eval_set_ids, subject)

    sets = (
        await session.scalars(
            select(EvalSet).where(EvalSet.id.in_(body.eval_set_ids))
        )
    ).all()
    names = {es.id: es.name for es in sets}

    questions = (
        await session.scalars(
            select(Question)
            .where(Question.eval_set_id.in_(body.eval_set_ids))
            .order_by(Question.eval_set_id, Question.question_id)
        )
    ).all()
    skills = await _skills_of(session, [q.id for q in questions])
    accuracy = await _prior_accuracy(session, body.eval_set_ids)

    candidates = [
        dataset.Candidate(
            item_key=dataset.item_key(q.eval_set_id, q.question_id),
            question_id=q.question_id,
            question=q.question,
            ground_truth_response=q.ground_truth_response,
            ground_truth_reasoning=q.ground_truth_reasoning,
            eval_set_id=q.eval_set_id,
            eval_set_name=names.get(q.eval_set_id, ""),
            skills=tuple(skills.get(q.id, ())),
            prior_accuracy=accuracy.get(q.id, (None, 0))[0],
            prior_runs=accuracy.get(q.id, (None, 0))[1],
            question_pk=q.id,
        )
        for q in questions
    ]
    groups, ambiguous = dataset.group_by_skill(candidates)

    per_set: dict[uuid.UUID, int] = {}
    for candidate in candidates:
        per_set[candidate.eval_set_id] = per_set.get(candidate.eval_set_id, 0) + 1

    return ImportPreview(
        groups=[
            SkillGroup(skill_name=name, questions=[_preview(c) for c in group])
            for name, group in groups.items()
        ],
        ambiguous=[_preview(c) for c in ambiguous],
        sources=[
            PreviewSource(
                id=es.id, name=es.name, n_questions=per_set.get(es.id, 0),
                judge_prompt_fingerprint=judge_prompt.fingerprint(
                    es.judge_system_prompt, es.judge_user_prompt
                ),
            )
            for es in sorted(sets, key=lambda s: s.name)
        ],
    )


async def _read_workspace(seams):
    """The agent's skill files, or a 503 carrying the agent server's own words.

    Both callers below are answering "can this run start?", and both used to let
    a `WorkspaceFetchError` escape as a 500 — which reads as a bug in this
    platform rather than as an agent server that is down, unreachable, or has
    not implemented `/skills`. The distinction is the whole answer: one of them
    is fixed by editing a URL, and the other by filing a bug here.

    Unwrapped, deliberately, the same way `routers/agent.py` does it: the
    workspace client's messages already name what was tried and what came back,
    and the UI prints its own heading above the line.
    """
    try:
        return await seams.workspace.get_workspace()
    except Exception as exc:  # noqa: BLE001 - the agent server's problem, not ours
        raise HTTPException(
            status_code=503, detail=str(exc) or type(exc).__name__
        ) from exc


@router.get("/skill-check", response_model=SkillCheck)
async def skill_check(
    skill_name: str = Query(..., min_length=1),
    # `Annotated` rather than `= Query(...)` for these two: the endpoints in this
    # package are also called directly as plain functions by the tests, and a
    # bare `Query(default)` leaves the parameter holding a FastAPI object rather
    # than the default when nobody routes the call.
    agent_base_url: Annotated[str, Query(description="blank uses the server's own")] = "",
    agent_timeout_s: Annotated[float | None, Query(gt=0)] = None,
    subject: str = Depends(current_subject),
):
    """Does the agent actually have this skill directory? (wizard step 3)

    Decision 6 treats a question's skill tag and the agent's directory name as
    the same name. That is only safe if it is checked before the run starts —
    otherwise a one-character typo costs a run row, an item snapshot and a batch
    of agent calls before anyone finds out.

    The agent is a parameter, not an environment lookup. The wizard asks for a
    base URL and then starts the run against it; a check that always read the
    server's own value could clear a skill on one agent and hand the run to
    another, which looks exactly like a check that passed.

    No session dependency: this reads the agent server, not the database.
    """
    config = {"agent_base_url": agent_base_url, "agent_timeout_s": agent_timeout_s}
    seams = build_seams(config, include_workspace=True)
    # What the run would resolve to, by the same rule `run_config.resolve` uses —
    # so the card names the agent that answered rather than the box that was
    # left blank.
    effective_url = (agent_base_url or "").strip() or settings.agent_base_url
    workspace = await _read_workspace(seams)
    files = {
        path: text for path, text in workspace.skills.items()
        if path == skill_name or path.startswith(f"{skill_name}/")
    }
    available = top_level_skills(workspace.skills)

    if not files:
        return SkillCheck(
            skill_name=skill_name, exists=False, available_skills=available,
            workspace_version=workspace.version, agent_base_url=effective_url,
            routing_blocked_reason="this skill was not found on the agent",
        )

    has_frontmatter = skillio.has_frontmatter(files, skill_name)
    return SkillCheck(
        skill_name=skill_name,
        exists=True,
        files=sorted(files),
        file_chars={path: len(text) for path, text in files.items()},
        n_chars=sum(len(text) for text in files.values()),
        has_frontmatter=has_frontmatter,
        agent_base_url=effective_url,
        routing_blocked_reason=None if has_frontmatter else (
            f"{skillio.entry_point_for(skill_name)} has no YAML frontmatter, so "
            "there is no description for routing mode to optimise"
        ),
        available_skills=available,
        workspace_version=workspace.version,
    )


@router.post("/runs", response_model=OptimizationRunOut, status_code=201)
async def create_optimization_run(
    body: OptimizationRunCreate,
    subject: str = Depends(current_subject),
    session: AsyncSession = Depends(get_session),
):
    """Snapshot the dataset and the skill, then start the run (wizard step 6).

    Everything that can be refused is refused here, before a row exists: an
    unreadable source, a skill the agent does not have, a mode the skill cannot
    support, a split too small to measure anything. The alternative is a run
    that fails at step 0 having already spent a batch of agent calls, and a list
    accumulating dead rows for typos.
    """
    if body.mode not in ("isolated", "routing"):
        raise HTTPException(status_code=400, detail=f"unknown mode {body.mode!r}")

    # 1. The split, on its own terms.
    issues = dataset.split_issues(body.train, body.val)
    errors = [i for i in issues if i["level"] == "error"]
    if errors:
        raise HTTPException(status_code=400, detail="; ".join(i["message"] for i in errors))

    # 2. The questions behind the keys, and permission on every set they name.
    wanted = list(dict.fromkeys(body.train + body.val))
    set_ids = {uuid.UUID(dataset.split_item_key(key)[0]) for key in wanted}
    await _require_reader_on_all(session, sorted(set_ids), subject)

    questions = (
        await session.scalars(
            select(Question).where(Question.eval_set_id.in_(set_ids))
        )
    ).all()
    by_key = {dataset.item_key(q.eval_set_id, q.question_id): q for q in questions}
    missing = [key for key in wanted if key not in by_key]
    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"{len(missing)} question(s) no longer exist: {', '.join(missing[:3])}",
        )

    # 3. The skill, read from the agent and pinned. A run optimises a snapshot;
    #    if the agent moves underneath it the numbers still describe the snapshot.
    seams = build_seams(body.config.model_dump(), body.secrets.model_dump(),
                        include_workspace=True)
    workspace = await _read_workspace(seams)
    initial = {
        path: text for path, text in workspace.skills.items()
        if path == body.skill_name or path.startswith(f"{body.skill_name}/")
    }
    if not initial:
        available = top_level_skills(workspace.skills)
        raise HTTPException(
            status_code=400,
            detail=(
                f"the agent has no skill directory named {body.skill_name!r}"
                + (f" — it has: {', '.join(available)}" if available else "")
            ),
        )
    if body.mode == "routing" and not skillio.has_frontmatter(initial, body.skill_name):
        raise HTTPException(
            status_code=400,
            detail=(
                f"routing mode optimises the description in "
                f"{skillio.entry_point_for(body.skill_name)}'s YAML frontmatter, "
                "and this skill has none"
            ),
        )

    # 4. Materialise the config, the same way `run_config.resolve` does for an
    #    eval run: a field left blank is stored with the environment's value, so
    #    the record is readable after the environment has moved on.
    config = _resolve_optimization_config(body.config)

    n_train = len(body.train)
    steps_per_epoch = max(1, math.ceil(n_train / max(body.batch_size, 1)))
    run = OptimizationRun(
        name=(body.name or "").strip() or None,
        created_by=subject,
        status="pending",
        mode=body.mode,
        skill_name=body.skill_name,
        config=config,
        # Typed into this request, else this developer's saved default for the
        # endpoint this run is actually pointed at. Same `inject` as the eval and
        # playground paths — one implementation, so the endpoint binding cannot
        # hold on two screens and not the third.
        secrets=user_secrets.inject(
            await user_settings.stored_secrets(session, subject),
            config,
            body.secrets.model_dump(),
        ),
        workspace_version=workspace.version,
        initial_skill=initial,
        # Routing sends the whole workspace, so the *other* skills are part of
        # the experiment and must not shift mid-run.
        workspace_baseline=(
            {p: t for p, t in workspace.skills.items() if p not in initial}
            if body.mode == "routing" else None
        ),
        detector=body.detector.model_dump(),
        num_epochs=body.num_epochs,
        batch_size=body.batch_size,
        steps_per_epoch=steps_per_epoch,
        total_steps=body.num_epochs * steps_per_epoch,
    )
    session.add(run)
    await session.flush()

    accuracy = await _prior_accuracy(session, sorted(set_ids))
    for split, keys in (("train", body.train), ("val", body.val)):
        for ordinal, key in enumerate(dict.fromkeys(keys)):
            question = by_key[key]
            prior, runs = accuracy.get(question.id, (None, 0))
            session.add(OptimizationItem(
                run_id=run.id, split=split, item_key=key,
                question_pk=question.id, source_eval_set_id=question.eval_set_id,
                # Snapshotted, like `runs` does: a question edited tomorrow must
                # not change what this run was measuring.
                question=question.question,
                ground_truth_response=question.ground_truth_response,
                ground_truth_reasoning=question.ground_truth_reasoning,
                ordinal=ordinal, prior_accuracy=prior, prior_runs=runs,
            ))
    await session.commit()

    # Only now. The background task opens its own session and reads the run by
    # id, so spawning it before the commit would race the transaction.
    runner.start(run.id)
    return await _one_run_out(session, run)


def _preview(candidate: dataset.Candidate) -> PreviewQuestion:
    return PreviewQuestion(
        item_key=candidate.item_key,
        question_id=candidate.question_id,
        question=candidate.question,
        ground_truth_response=candidate.ground_truth_response,
        eval_set_id=candidate.eval_set_id,
        eval_set_name=candidate.eval_set_name,
        skills=list(candidate.skills),
        prior_accuracy=candidate.prior_accuracy,
        prior_runs=candidate.prior_runs,
    )


def _resolve_optimization_config(config: OptimizationConfig) -> dict:
    """Blank fields filled from the environment, and a judge prompt of our own.

    Materialised rather than stored blank for the reason `run_config.resolve`
    gives: a blank in a stored config is unreadable afterwards ("was that the
    environment's value, or nothing at all?"), and today's environment is no
    witness to what it held when the run started.
    """
    effective = dict(run_config.defaults())
    effective["optimizer_model"] = settings.optimizer_model
    for key, value in config.model_dump().items():
        if isinstance(value, str) and not value.strip():
            continue
        if value is None:
            continue
        effective[key] = value

    # The algorithm's own knobs and the stop conditions, resolved to values.
    # The loop above only materialises what the caller sent, and seven of these
    # have no control in the wizard — so without this the stored config was a
    # half-written record whose other half was whatever literal the engine
    # happened to carry that week. The stop conditions have a second reason:
    # the run's page shows the reader what will end this run, and a blank there
    # would have to be explained by re-deriving the server's defaults in the
    # browser.
    effective.update(hyperparams.resolve_algorithm(effective))
    effective.update(stopping.StopPolicy.from_config(effective).as_dict())

    system, user = judge_prompt.effective(
        config.judge_system_prompt, config.judge_user_prompt
    )
    effective["judge_system_prompt"] = system
    effective["judge_user_prompt"] = user
    effective["judge_prompt_fingerprint"] = judge_prompt.fingerprint(system, user)
    return effective


async def _require_reader_on_all(
    session: AsyncSession, eval_set_ids, subject: str
) -> None:
    """Every named set must be readable, or the request is refused entirely."""
    if not eval_set_ids:
        return
    readable = set(
        (await session.scalars(
            select(EvalSetRole.eval_set_id).where(
                EvalSetRole.user_subject == subject,
                EvalSetRole.eval_set_id.in_(eval_set_ids),
            )
        )).all()
    )
    missing = [str(i) for i in eval_set_ids if i not in readable]
    if missing:
        # 404, not 403: whether a set exists at a given id is not theirs to
        # learn, the same choice `_load_visible_run` makes.
        raise HTTPException(
            status_code=404,
            detail=f"{len(missing)} eval set(s) not found: {', '.join(missing[:3])}",
        )


async def _skills_of(session: AsyncSession, question_pks) -> dict[uuid.UUID, list[str]]:
    if not question_pks:
        return {}
    rows = (
        await session.execute(
            select(QuestionSkill.question_pk, QuestionSkill.skill_name)
            .where(QuestionSkill.question_pk.in_(question_pks))
            .order_by(QuestionSkill.question_pk, QuestionSkill.ordinal)
        )
    ).all()
    out: dict[uuid.UUID, list[str]] = {}
    for pk, name in rows:
        out.setdefault(pk, []).append(name)
    return out


async def _prior_accuracy(
    session: AsyncSession, eval_set_ids
) -> dict[uuid.UUID, tuple[float | None, int]]:
    """`{question_pk: (accuracy, n_runs)}` over the recent completed runs.

    **One query, not one per question.** `docs/spec.md` §10.2③ records what the
    per-row version did to `GET /eval-sets` — 180 queries for one page, growing
    with history — and a picker listing every question of several sets is the
    same shape of surface.

    Three rules about what counts, each of which changes the number a developer
    is choosing on:

      * only `completed` runs, because a cancelled run's questions are a sample
        of whatever happened to finish before somebody pressed stop;
      * only `done` results, because an agent timeout is not the question being
        hard, and scoring it wrong sends it to the training split for a problem
        no skill edit can fix;
      * only the most recent `PRIOR_RUNS_WINDOW`, so a question fixed months ago
        is not still dragging its own history around.
    """
    if not eval_set_ids:
        return {}
    ranked = (
        select(
            Run.id,
            func.row_number()
            .over(partition_by=Run.eval_set_id, order_by=Run.started_at.desc())
            .label("rn"),
        )
        .where(Run.eval_set_id.in_(eval_set_ids), Run.status == "completed")
        .subquery()
    )
    recent = select(ranked.c.id).where(ranked.c.rn <= PRIOR_RUNS_WINDOW)

    rows = (
        await session.execute(
            select(
                QuestionResult.question_pk,
                func.count(func.distinct(QuestionResult.run_id)).label("runs"),
                func.count().label("n"),
                func.sum(
                    case((QuestionResult.verdict == "correct", 1), else_=0)
                ).label("n_correct"),
            )
            .where(
                QuestionResult.run_id.in_(recent),
                QuestionResult.status == "done",
            )
            .group_by(QuestionResult.question_pk)
        )
    ).all()
    return {
        row.question_pk: ((row.n_correct / row.n) if row.n else None, row.runs)
        for row in rows
    }


@router.get("/runs", response_model=OptimizationRunPage)
async def list_optimization_runs(
    q: str | None = Query(None, description="case-insensitive name substring"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    subject: str = Depends(current_subject),
    session: AsyncSession = Depends(get_session),
):
    """A page of optimization runs, newest first."""
    visible = await _readable_run_ids(session, subject)
    base = select(OptimizationRun).where(OptimizationRun.id.in_(visible))
    if q:
        base = base.where(OptimizationRun.name.ilike(f"%{q}%"))

    total = await session.scalar(select(func.count()).select_from(base.subquery())) or 0
    # id as a tiebreaker, same reason as the run list: two runs started in the
    # same instant must not shuffle between pages.
    runs = (
        await session.scalars(
            base.order_by(OptimizationRun.started_at.desc(), OptimizationRun.id.asc())
            .limit(limit)
            .offset(offset)
        )
    ).all()

    splits, sources, steps = await _counts(session, [r.id for r in runs])
    items = [
        _run_out(r, splits.get(r.id, {}), sources.get(r.id, []), steps.get(r.id, 0))
        for r in runs
    ]
    return OptimizationRunPage(
        items=items, total=total, has_more=offset + len(items) < total
    )


@router.get("/runs/{run_id}", response_model=OptimizationRunDetail)
async def get_optimization_run(
    run_id: uuid.UUID,
    subject: str = Depends(current_subject),
    session: AsyncSession = Depends(get_session),
):
    """One run, its settings, and every step — the whole chart in one payload.

    One request rather than one per step: a run is a handful of steps and the
    chart needs all of them at once, so paging here would only add a loading
    state to a page that has nothing to page through.
    """
    run = await _load_visible_run(session, run_id, subject)
    splits, sources, steps_done = await _counts(session, [run_id])
    step_summaries = await _step_summaries(session, run_id)

    # The same question in both splits: allowed, warned about, never silent.
    overlap = (
        await session.scalars(
            select(OptimizationItem.item_key)
            .where(OptimizationItem.run_id == run_id)
            .group_by(OptimizationItem.item_key)
            .having(func.count(func.distinct(OptimizationItem.split)) > 1)
        )
    ).all()

    base = _run_out(
        run, splits.get(run_id, {}), sources.get(run_id, []), steps_done.get(run_id, 0)
    )
    return OptimizationRunDetail(
        **base.model_dump(),
        config=run.config or {},
        detector=run.detector or {},
        workspace_version=run.workspace_version,
        overlap_item_keys=sorted(overlap),
        steps=step_summaries,
    )


async def _step_summaries(
    session: AsyncSession, run_id: uuid.UUID
) -> list[OptimizationStepSummary]:
    """Every step of one run, with its two rollouts attached — the chart's data.

    Two queries, not one per step: the detail page and the progress snapshot
    both read this, and the snapshot is built while a stream is being set up.
    """
    step_rows = (
        await session.scalars(
            select(OptimizationStep)
            .where(OptimizationStep.run_id == run_id)
            .order_by(OptimizationStep.step_no)
        )
    ).all()
    rollouts = (
        await session.scalars(
            select(OptimizationRollout).where(
                OptimizationRollout.step_id.in_([s.id for s in step_rows] or [None])
            )
        )
    ).all()
    by_step: dict[uuid.UUID, dict[str, OptimizationRollout]] = {}
    for rollout in rollouts:
        by_step.setdefault(rollout.step_id, {})[rollout.split] = rollout
    return [_step_summary(s, by_step.get(s.id, {})) for s in step_rows]


async def _one_run_out(session: AsyncSession, run: OptimizationRun) -> OptimizationRunOut:
    """The list-shaped view of a single run, for the endpoints that mutate one."""
    splits, sources, steps_done = await _counts(session, [run.id])
    return _run_out(
        run, splits.get(run.id, {}), sources.get(run.id, []), steps_done.get(run.id, 0)
    )


def _num(value):
    return float(value) if value is not None else None


def _step_summary(step: OptimizationStep, rollouts: dict) -> OptimizationStepSummary:
    train = rollouts.get("train")
    val = rollouts.get("val")
    return OptimizationStepSummary(
        step_no=step.step_no,
        epoch_no=step.epoch_no,
        step_in_epoch=step.step_in_epoch,
        parent_step_no=step.parent_step_no,
        status=step.status,
        gate_action=step.gate_action,
        gate_reject_reason=step.gate_reject_reason,
        retried=step.retried,
        abort_reason=step.abort_reason,
        train_hard=_num(train.hard) if train else None,
        train_soft=_num(train.soft) if train else None,
        train_activation_rate=_num(train.activation_rate) if train else None,
        train_n_scored=train.n_scored if train else None,
        train_n_items=train.n_items if train else None,
        train_n_agent_error=train.n_agent_error if train else None,
        train_n_judge_error=train.n_judge_error if train else None,
        train_latency_min_ms=train.latency_min_ms if train else None,
        train_latency_p50_ms=train.latency_p50_ms if train else None,
        train_latency_mean_ms=train.latency_mean_ms if train else None,
        train_latency_max_ms=train.latency_max_ms if train else None,
        val_hard=_num(val.hard) if val else None,
        val_soft=_num(val.soft) if val else None,
        val_activation_rate=_num(val.activation_rate) if val else None,
        val_n_scored=val.n_scored if val else None,
        val_n_items=val.n_items if val else None,
        val_n_agent_error=val.n_agent_error if val else None,
        val_n_judge_error=val.n_judge_error if val else None,
        val_latency_min_ms=val.latency_min_ms if val else None,
        val_latency_p50_ms=val.latency_p50_ms if val else None,
        val_latency_mean_ms=val.latency_mean_ms if val else None,
        val_latency_max_ms=val.latency_max_ms if val else None,
        lines_added=step.lines_added,
        lines_removed=step.lines_removed,
        files_touched=step.files_touched,
        n_answer_leaks=step.n_answer_leaks,
        workspace_version=step.workspace_version,
        n_edits_applied=step.n_edits_applied,
        n_edits_skipped=step.n_edits_skipped,
        edit_summary=step.edit_summary,
        skill_len=step.skill_len,
        candidate_from_cache=step.candidate_from_cache,
        current_score=_num(step.current_score),
        best_score=_num(step.best_score),
        started_at=step.started_at,
        completed_at=step.completed_at,
    )


@router.patch("/runs/{run_id}", response_model=OptimizationRunOut)
async def rename_optimization_run(
    run_id: uuid.UUID,
    body: OptimizationRunRename,
    subject: str = Depends(current_subject),
    session: AsyncSession = Depends(get_session),
):
    """Rename a run. Creator only, for the same reason cancel is.

    The wizard offers a name on its last step and most runs are started without
    one, so the rail filled up with timestamps — and a rail of timestamps is
    exactly as useful as no rail. The name is a label on the experiment, so it
    belongs to whoever ran it; a reader who can see the run still cannot retitle
    someone else's work.
    """
    run = await _load_visible_run(session, run_id, subject)
    if run.created_by != subject:
        raise HTTPException(
            status_code=403, detail="only the developer who started this run can rename it"
        )
    run.name = body.name
    await session.commit()
    return await _one_run_out(session, run)


@router.post("/runs/{run_id}/cancel", response_model=OptimizationRunOut)
async def cancel_optimization_run(
    run_id: uuid.UUID,
    subject: str = Depends(current_subject),
    session: AsyncSession = Depends(get_session),
):
    """Stop a running optimization. Creator only.

    Narrower than an eval run's cancel, which any reader may call: an eval run
    belongs to an eval set several people share, while an optimization run is
    one developer's experiment against their own agent endpoint. Its steps are
    kept — the loop finishes nothing further, but every completed step and its
    best skill remain downloadable.
    """
    run = await _load_visible_run(session, run_id, subject)
    if run.created_by != subject:
        raise HTTPException(
            status_code=403, detail="only the developer who started this run can cancel it"
        )
    if run.status not in ("pending", "running"):
        raise HTTPException(
            status_code=409, detail=f"this run is already {run.status}; nothing to cancel"
        )
    run.cancel_requested = True
    await session.commit()
    # The flag is the durable record and survives a restart; the event is what
    # interrupts the agent call already in flight (app/cancellation.py).
    cancellation.signal(run_id)
    return await _one_run_out(session, run)


@router.post("/runs/{run_id}/resume", response_model=OptimizationRunOut)
async def resume_optimization_run(
    run_id: uuid.UUID,
    subject: str = Depends(current_subject),
    session: AsyncSession = Depends(get_session),
):
    """Continue a run the backend restart left `interrupted`. Creator only.

    Only `interrupted` — not `cancelled` and not `failed`. A cancelled run was
    stopped deliberately and restarting it under the same id would erase that
    decision from the record; a failed one stopped because continuing would
    produce a misleading result, which resuming does not fix. Both remain
    available as the starting point for a new run.

    The engine re-derives everything it needs from the completed steps, so there
    is nothing to pass here: it picks up at the step after the last one that
    finished.
    """
    run = await _load_visible_run(session, run_id, subject)
    if run.created_by != subject:
        raise HTTPException(
            status_code=403, detail="only the developer who started this run can resume it"
        )
    if run.status != "interrupted":
        raise HTTPException(
            status_code=409,
            detail=f"only an interrupted run can be resumed; this one is {run.status}",
        )
    run.status = "running"
    run.error_message = None
    run.cancel_requested = False
    await session.commit()
    cancellation.clear(run_id)
    runner.start(run_id)
    return await _one_run_out(session, run)


@router.get("/runs/{run_id}/progress")
async def optimization_progress(
    run_id: uuid.UUID,
    request: Request,
    # `current_subject` rather than a session-consuming dependency, for the
    # reason spelled out in `routers/runs.py:run_progress`: a stream that lasts
    # as long as the run would hold a pooled connection for that whole time.
    subject: str = Depends(current_subject),
):
    """SSE stream of one run's progress: a snapshot, then live step events.

    Same shape and same ordering rules as the eval run stream — authorize,
    subscribe, then read — because the hazards are identical and one of them is
    specific to how long these run: subscribing after the read would let a
    `run_completed` published in between fall on the floor, leaving the page
    pinging every fifteen seconds against a run that finished an hour ago.

    The steps come along in the snapshot rather than being fetched separately.
    A late subscriber — someone opening the page mid-run, or after a laptop woke
    up — needs the whole chart, and an SSE stream that only carries the future
    would leave them with a blank one until the next step landed. That can be
    several minutes.
    """
    async with SessionLocal() as session:
        queue = hub.subscribe(run_id)
        try:
            run = await _load_visible_run(session, run_id, subject)
            status = run.status
            steps = await _step_summaries(session, run_id)
            snapshot = {
                "status": status,
                "step_no": max((s.step_no for s in steps), default=0),
                "total_steps": run.total_steps,
                "best_step": run.best_step,
                "best_score": _num(run.best_score),
                "server_time": datetime.now(timezone.utc).isoformat(),
                "steps": [s.model_dump(mode="json") for s in steps],
            }
            # `commit`, not `rollback`: rollback expires every loaded object and
            # the values read above would go stale before the generator starts.
            await session.commit()
        except BaseException:
            hub.unsubscribe(run_id, queue)
            raise

    async def event_gen():
        try:
            yield {"event": "snapshot", "data": json.dumps(snapshot)}
            if status not in ("running", "pending"):
                yield {"event": "run_completed", "data": json.dumps({"status": status})}
                return

            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    yield resync_or_ping(queue)
                    continue
                dropped = resync_if_dropped(queue)
                if dropped:
                    yield dropped
                yield {"event": event.get("type", "message"), "data": json.dumps(event)}
                if event.get("type") == "run_completed":
                    break
        finally:
            hub.unsubscribe(run_id, queue)

    return EventSourceResponse(event_gen())


SPLITS = ("train", "val")


async def _load_rollout(
    session: AsyncSession, run_id: uuid.UUID, step_no: int, split: str, subject: str
):
    """`(run, step, rollout)` for one split of one step, or the right refusal."""
    if split not in SPLITS:
        # A path segment can hold anything. 400 rather than 404: the run and the
        # step may well exist, and "not found" would send a developer looking
        # for the wrong mistake.
        raise HTTPException(
            status_code=400, detail=f"split must be one of {', '.join(SPLITS)}"
        )
    run = await _load_visible_run(session, run_id, subject)
    step = await session.scalar(
        select(OptimizationStep).where(
            OptimizationStep.run_id == run_id, OptimizationStep.step_no == step_no
        )
    )
    if step is None:
        raise HTTPException(status_code=404, detail=f"this run has no step {step_no}")
    rollout = await session.scalar(
        select(OptimizationRollout).where(
            OptimizationRollout.step_id == step.id, OptimizationRollout.split == split
        )
    )
    # Step 0 has no training rollout — there is no candidate to train on yet — so
    # this is a reachable state, not a corrupt one. An empty page with zeroed
    # figures would read as "the baseline scored 0 on training".
    if rollout is None:
        raise HTTPException(
            status_code=404, detail=f"step {step_no} has no {split} rollout"
        )
    return run, step, rollout


@router.get(
    "/runs/{run_id}/steps/{step_no}/rollouts/{split}",
    response_model=OptimizationRolloutDetail,
)
async def get_rollout_detail(
    run_id: uuid.UUID,
    step_no: int,
    split: str,
    subject: str = Depends(current_subject),
    session: AsyncSession = Depends(get_session),
):
    """Part 1: what this rollout measured, and what the analysts made of it.

    One payload, because the parts are meaningless apart — an accuracy without
    its questions, a minibatch without the failures it was built from — and
    three requests would each need their own loading state on one screen.

    The questions come from `optimization_items`, the run's own snapshot, never
    from `questions`. An eval set edited since the run would otherwise put
    today's text beside a verdict about yesterday's.
    """
    run, step, rollout = await _load_rollout(session, run_id, step_no, split, subject)

    results = (
        await session.scalars(
            select(OptimizationResult)
            .where(OptimizationResult.rollout_id == rollout.id)
            .order_by(OptimizationResult.minibatch_no, OptimizationResult.item_key)
        )
    ).all()
    items = {
        item.item_key: item
        for item in (
            await session.scalars(
                select(OptimizationItem).where(
                    OptimizationItem.run_id == run_id, OptimizationItem.split == split
                )
            )
        ).all()
    }
    # Validation is measured, never reflected on. Returning the step's
    # minibatches here regardless of split would show analyst calls built from
    # training failures beside held-out questions, implying a connection the
    # gate depends on not existing.
    minibatches = (
        (
            await session.scalars(
                select(OptimizationMinibatch)
                .where(OptimizationMinibatch.step_id == step.id)
                .order_by(OptimizationMinibatch.minibatch_no)
            )
        ).all()
        if split == "train"
        else []
    )
    # The same rule, for the same reason: merge and ranking are steps in
    # producing the candidate, and they belong beside the training rollout that
    # produced it, not beside the held-out questions that judged it.
    stage_calls = (
        (
            await session.scalars(
                select(OptimizationStageCall)
                .where(OptimizationStageCall.step_id == step.id)
                .order_by(OptimizationStageCall.seq)
            )
        ).all()
        if split == "train"
        else []
    )
    # Whether the step bought a validation rollout at all. Queried rather than
    # read off `step.rollouts`: that relationship is lazy, and touching it in an
    # async session raises rather than loading.
    val_splits = set(
        (
            await session.scalars(
                select(OptimizationRollout.split).where(
                    OptimizationRollout.step_id == step.id
                )
            )
        ).all()
    )

    by_batch: dict[int, list[str]] = {}
    for row in results:
        if row.minibatch_no is not None:
            by_batch.setdefault(row.minibatch_no, []).append(row.item_key)

    return OptimizationRolloutDetail(
        run_id=run_id,
        step_no=step_no,
        split=split,
        epoch_no=step.epoch_no,
        step_in_epoch=step.step_in_epoch,
        skill_step_no=rollout.skill_step_no,
        parent_step_no=step.parent_step_no,
        step_status=step.status,
        gate_action=step.gate_action,
        gate_reject_reason=step.gate_reject_reason,
        edit_summary=step.edit_summary,
        n_items=rollout.n_items,
        n_scored=rollout.n_scored,
        n_agent_error=rollout.n_agent_error,
        n_judge_error=rollout.n_judge_error,
        hard=_num(rollout.hard),
        soft=_num(rollout.soft),
        activation_rate=_num(rollout.activation_rate),
        n_activated=rollout.n_activated,
        latency_min_ms=rollout.latency_min_ms,
        latency_p50_ms=rollout.latency_p50_ms,
        latency_mean_ms=rollout.latency_mean_ms,
        latency_max_ms=rollout.latency_max_ms,
        aborted=rollout.aborted,
        abort_reason=rollout.abort_reason,
        n_edits_applied=step.n_edits_applied,
        n_edits_skipped=step.n_edits_skipped,
        edit_reports=[EditReportOut(**report) for report in (step.edit_reports or [])],
        val_rolled_out="val" in val_splits,
        results=[_result_out(row, items.get(row.item_key)) for row in results],
        minibatches=[
            OptimizationMinibatchOut(
                minibatch_no=batch.minibatch_no,
                source_type=batch.source_type,
                n_items=batch.n_items,
                item_keys=sorted(by_batch.get(batch.minibatch_no, [])),
                prompt_system=batch.prompt_system,
                prompt_user=batch.prompt_user,
                raw_output=batch.raw_output,
                truncation=list(batch.truncation or []),
                chars_before=batch.chars_before,
                chars_after=batch.chars_after,
                error=batch.error,
                duration_ms=batch.duration_ms,
            )
            for batch in minibatches
        ],
        stage_calls=[
            OptimizationStageCallOut(
                seq=call.seq,
                stage=call.stage,
                level=call.level,
                prompt_system=call.prompt_system,
                prompt_user=call.prompt_user,
                output=call.output,
                error=call.error,
                duration_ms=call.duration_ms,
            )
            for call in stage_calls
        ],
    )


def _result_out(row: OptimizationResult, item) -> OptimizationResultOut:
    return OptimizationResultOut(
        id=row.id,
        item_key=row.item_key,
        question=item.question if item else None,
        ground_truth_response=item.ground_truth_response if item else None,
        correlation_id=row.correlation_id,
        agent_response=row.agent_response,
        agent_latency_ms=row.agent_latency_ms,
        verdict=row.verdict,
        judge_score=_num(row.judge_score),
        judge_comment=row.judge_comment,
        status=row.status,
        failure_kind=row.failure_kind,
        error_message=row.error_message,
        activated=row.activated,
        skills_read=row.skills_read,
        detector_hit=row.detector_hit,
        trace_ready=row.trace_ready,
        trace_error=row.trace_error,
        minibatch_no=row.minibatch_no,
    )


@router.get(
    "/runs/{run_id}/steps/{step_no}/rollouts/{split}/results/{result_id}/trace",
    response_model=TraceView,
)
async def get_rollout_result_trace(
    run_id: uuid.UUID,
    step_no: int,
    split: str,
    result_id: uuid.UUID,
    subject: str = Depends(current_subject),
    session: AsyncSession = Depends(get_session),
):
    """One question's spans, in the shape the evaluation pages already render.

    Reusing `TraceView` is the whole point: the span viewer, its five trace
    states and the structured payload rendering all arrive unchanged, and a
    trace looks the same here as it does in Evaluation — which is what makes the
    two comparable at all.

    There is no diagnosis for an optimization result. Diagnoses belong to
    `question_results`, and inventing a second table for a view that already has
    the analyst's own reasoning next to it would be a worse answer to the same
    question.
    """
    run, step, rollout = await _load_rollout(session, run_id, step_no, split, subject)

    # Scoped to this rollout, not looked up by id alone: the id is a uuid in a
    # path that already names a run, a step and a split, and trusting it on its
    # own would make every other segment decorative.
    result = await session.scalar(
        select(OptimizationResult).where(
            OptimizationResult.id == result_id,
            OptimizationResult.rollout_id == rollout.id,
        )
    )
    if result is None:
        raise HTTPException(status_code=404, detail="no such result in this rollout")

    item = await session.scalar(
        select(OptimizationItem).where(
            OptimizationItem.run_id == run_id,
            OptimizationItem.split == split,
            OptimizationItem.item_key == result.item_key,
        )
    )
    traceable = result.status not in ("failed", "cancelled")
    config, secrets = (run.config, run.secrets) if traceable else (None, None)

    # Hand the connection back before touching the trace store: `resolve_trace_spans`
    # polls, and each attempt can wait the full Langfuse timeout. Holding a
    # pooled connection through that takes the backend down with a slow trace
    # store rather than just this view (see app/db.py, and results.py which does
    # the same thing for the same reason).
    await session.commit()

    spans: list = []
    trace_error: str | None = None
    if not traceable:
        state = "no_trace"
    else:
        try:
            seams = build_seams(config, secrets)
        except Exception as exc:  # noqa: BLE001 — the developer needs the reason
            state, trace_error = "error", f"{type(exc).__name__}: {exc}"
        else:
            trace, fetch_error, fatal = await resolve_trace_spans(
                result.correlation_id, seams.trace
            )
            if trace is not None:
                state = "ready"
                spans = [span_to_out(s) for s in trace.spans]
            elif fetch_error is not None and fatal:
                state, trace_error = "error", fetch_error
            else:
                # Still ingesting. Surface whatever the run itself hit, so
                # "waiting" does not hide an authentication failure from an hour
                # ago.
                state, trace_error = "generating", fetch_error or result.trace_error

    return TraceView(
        trace_state=state,
        trace_error=trace_error,
        spans=spans,
        analysis=None,
        verdict=result.verdict,
        judge_comment=result.judge_comment,
        agent_response=result.agent_response,
        ground_truth_response=item.ground_truth_response if item else None,
        ground_truth_reasoning=item.ground_truth_reasoning if item else None,
        error_message=result.error_message,
        failure_kind=result.failure_kind,
    )


BASES = ("parent", "initial")


def _pick_snapshot(by_kind: dict | None, order: tuple[str, ...]) -> dict | None:
    """The first snapshot kind present, in the caller's order of preference.

    A step number is no longer unique in `optimization_skills`: an epoch boundary
    that runs the slow update records a second row against the last accepted step.
    Taking whichever row the database returned first would make the diff and the
    download nondeterministic between two skills that differ by a whole block of
    guidance.
    """
    for kind in order:
        files = (by_kind or {}).get(kind)
        if files is not None:
            return files
    return None


@router.get(
    "/runs/{run_id}/steps/{step_no}/skill", response_model=OptimizationSkillDiff
)
async def get_step_skill_diff(
    run_id: uuid.UUID,
    step_no: int,
    base: str = Query("parent", description="`parent` (last accepted step) or `initial`"),
    subject: str = Depends(current_subject),
    session: AsyncSession = Depends(get_session),
):
    """Part 2: what this step did to the skill, against the right baseline.

    "The right baseline" is the entire difficulty. `parent_step_no` is the last
    step whose candidate the gate **accepted**, which is usually not
    `step_no - 1`: a rejected step is rolled back, so step 4 may well be derived
    from step 2. Diffing against the previous step number would fold a discarded
    proposal into the next step's diff and attribute one model's edits to
    another, on the one page whose job is to say who changed what.

    Both snapshots are whole files rather than stored patches, so the comparison
    is a straight one — and the second base, `initial`, answers the question the
    first cannot: not "what did this step do" but "what does this run's skill now
    contain that the original did not". That is the view worth reading before
    deploying, and the reason the answer-leak check runs against whichever base
    was actually asked for.
    """
    if base not in BASES:
        raise HTTPException(
            status_code=400, detail=f"base must be one of {', '.join(BASES)}"
        )
    run = await _load_visible_run(session, run_id, subject)
    step = await session.scalar(
        select(OptimizationStep).where(
            OptimizationStep.run_id == run_id, OptimizationStep.step_no == step_no
        )
    )
    if step is None:
        raise HTTPException(status_code=404, detail=f"this run has no step {step_no}")

    # `parent_step_no` is NULL until the gate accepts something, and every step
    # before that really is derived from the skill as it arrived. Reporting the
    # fallback rather than quietly substituting step 0 lets the page say which
    # question it is answering.
    wanted = 0 if base == "initial" else step.parent_step_no
    base_step_no = 0 if wanted is None else wanted
    fallback = base == "parent" and step.parent_step_no is None

    snapshots: dict[int, dict[str, dict[str, str]]] = {}
    for row in (
        await session.scalars(
            select(OptimizationSkill).where(
                OptimizationSkill.run_id == run_id,
                OptimizationSkill.step_no.in_({step_no, base_step_no}),
            )
        )
    ).all():
        snapshots.setdefault(row.step_no, {})[row.kind] = dict(row.files)

    # A step can hold two snapshots: the candidate its own edits produced, and —
    # if the slow update ran at the epoch boundary after it — the skill the
    # *next* step actually started from. Which one is wanted depends on the side.
    #   after:  the candidate, always. This page is about what this step's edits
    #           did, and the boundary's guidance was written by a different pass.
    #   before: the slow-update version if there is one, because that is what the
    #           step being displayed was derived from. Reading the candidate here
    #           would show the guidance block as an addition made by this step.
    after = _pick_snapshot(snapshots.get(step_no), ("candidate", "initial"))
    # A step row exists from the moment the step starts; the candidate only
    # exists once the update stage finishes. An empty diff for the gap between
    # them would read as "this step changed nothing" — a claim about the skill,
    # when the truth is about the run being cut short.
    if after is None:
        raise HTTPException(
            status_code=404, detail=f"no skill was recorded for step {step_no}"
        )
    before = _pick_snapshot(
        snapshots.get(base_step_no), ("slow_update", "candidate", "initial")
    )
    if before is None:
        before = dict(run.initial_skill or {})

    stats = skillio.per_file_stats(before, after)
    files = [
        SkillDiffFile(
            path=path,
            before=before.get(path),
            after=after.get(path),
            added=counts["added"],
            removed=counts["removed"],
        )
        for path, counts in stats.items()
    ]
    # The files this step left alone, with their contents rather than only their
    # names. The page renders a real diff for them — both sides identical, every
    # row context — because a step that changed nothing used to replace the whole
    # diff view with one sentence, and the layout jumping between "a diff" and "a
    # paragraph" depending on the outcome is a worse way to say "no change" than
    # a diff that visibly has none.
    unchanged = sorted((set(before) | set(after)) - set(stats))
    unchanged_files = [
        SkillDiffFile(
            path=path,
            before=after.get(path, before.get(path)),
            after=after.get(path, before.get(path)),
            added=0,
            removed=0,
        )
        for path in unchanged
    ]
    lines_added, lines_removed = skillio.total_line_changes(before, after)

    # Training answers only. Held-out validation answers are never shown to an
    # analyst, so one appearing in the skill cannot have been copied from a
    # prompt — flagging it would put a coincidence behind the same red banner as
    # a real leak, and the two would stop being distinguishable. A question in
    # both splits has a training row, so overlap is still covered.
    golds = (
        await session.scalars(
            select(OptimizationItem.ground_truth_response).where(
                OptimizationItem.run_id == run_id, OptimizationItem.split == "train"
            )
        )
    ).all()

    return OptimizationSkillDiff(
        run_id=run_id,
        skill_name=run.skill_name,
        mode=run.mode,
        step_no=step_no,
        base=base,
        base_step_no=base_step_no,
        base_is_fallback=fallback,
        gate_action=step.gate_action,
        gate_reject_reason=step.gate_reject_reason,
        is_best=run.best_step is not None and run.best_step == step_no,
        step_status=step.status,
        edit_summary=step.edit_summary,
        n_edits_applied=step.n_edits_applied,
        n_edits_skipped=step.n_edits_skipped,
        files=files,
        unchanged_paths=unchanged,
        unchanged_files=unchanged_files,
        lines_added=lines_added,
        lines_removed=lines_removed,
        answer_leaks=[
            AnswerLeak(**leak) for leak in skillio.find_answer_leaks(before, after, golds)
        ],
        edit_reports=[EditReportOut(**report) for report in (step.edit_reports or [])],
    )


@router.get("/runs/{run_id}/skill/download")
async def download_optimized_skill(
    run_id: uuid.UUID,
    step: str = Query("best", description="`best`, or a step number"),
    subject: str = Depends(current_subject),
    session: AsyncSession = Depends(get_session),
):
    """The run's actual output: one step's skill directory, as a zip.

    Any step is fetchable, not only the winner. Reading the edits the gate
    turned down is a legitimate reason to download one, and refusing would send
    people to the database for it.

    That generosity is exactly why the manifest matters. The zip outlives the
    page that explained it — it is opened days later, in a downloads folder,
    with no chart on screen — so everything that qualifies the skill inside it
    has to travel in the file: which run and step, what it scored against what
    baseline, whether the gate kept it, and whether validation was held out at
    all. `warnings` is the part that has to be read before deploying.
    """
    run = await _load_visible_run(session, run_id, subject)

    if step == "best":
        step_no = run.best_step
        if step_no is None:
            raise HTTPException(
                status_code=404,
                detail="this run has not finished a step yet, so it has no best skill",
            )
    else:
        try:
            step_no = int(step)
        except ValueError:
            raise HTTPException(
                status_code=400, detail="step must be 'best' or a step number"
            )

    # Ordered, because a step that ended an epoch may have two snapshots. The
    # slow-update one wins: it is the skill this run actually carried forward,
    # and it is the one a download is meant to reproduce. The manifest names the
    # kind, so which one arrived is never a guess on the far side.
    rows = {
        row.kind: row
        for row in (
            await session.scalars(
                select(OptimizationSkill).where(
                    OptimizationSkill.run_id == run_id,
                    OptimizationSkill.step_no == step_no,
                )
            )
        ).all()
    }
    snapshot = next(
        (rows[kind] for kind in ("slow_update", "candidate", "initial") if kind in rows),
        None,
    )
    # An empty archive would be the worst possible answer: it unzips cleanly,
    # changes nothing on the agent, and reads as a successful download.
    if snapshot is None or not snapshot.files:
        raise HTTPException(
            status_code=404, detail=f"no skill was recorded for step {step_no}"
        )

    step_row = await session.scalar(
        select(OptimizationStep).where(
            OptimizationStep.run_id == run_id, OptimizationStep.step_no == step_no
        )
    )
    scores = await _val_scores(session, run_id, (step_no, 0))
    splits, sources, _ = await _counts(session, [run_id])
    overlap = (
        await session.scalars(
            select(OptimizationItem.item_key)
            .where(OptimizationItem.run_id == run_id)
            .group_by(OptimizationItem.item_key)
            .having(func.count(func.distinct(OptimizationItem.split)) > 1)
        )
    ).all()

    is_best = run.best_step is not None and step_no == run.best_step
    warnings: list[str] = []
    if step_row is not None and step_row.gate_action == "reject":
        warnings.append(
            "The validation gate rejected these edits: this candidate scored worse "
            f"({step_row.gate_reject_reason or 'accuracy'}) than the skill it was "
            "derived from, and the run did not keep it."
        )
    if not is_best and run.best_step is not None:
        warnings.append(
            f"This is not the run's best skill. Step {run.best_step} scored "
            f"{_num(run.best_score)} on validation; download `step=best` for that one."
        )
    if overlap:
        warnings.append(
            f"{len(overlap)} question(s) were in both the training and validation "
            "splits, so validation was not fully held out: part of the score "
            "below is the skill being measured on questions it was edited for."
        )

    manifest = {
        "run_id": str(run_id),
        "run_name": run.name,
        "run_status": run.status,
        "skill_name": run.skill_name,
        "mode": run.mode,
        "step_no": step_no,
        "snapshot": snapshot.kind,
        "content_hash": snapshot.content_hash,
        "is_best_by_validation": is_best,
        "gate": (
            {"action": step_row.gate_action, "reject_reason": step_row.gate_reject_reason}
            if step_row is not None
            else None
        ),
        "validation": scores.get(step_no),
        "baseline_validation": scores.get(0),
        "best_step": run.best_step,
        "best_score": _num(run.best_score),
        "warnings": warnings,
        "hyperparameters": {
            "num_epochs": run.num_epochs,
            "batch_size": run.batch_size,
            "steps_per_epoch": run.steps_per_epoch,
            "total_steps": run.total_steps,
        },
        "n_train": splits.get(run_id, {}).get("train", 0),
        "n_val": splits.get(run_id, {}).get("val", 0),
        "source_eval_set_ids": [str(s) for s in sources.get(run_id, [])],
        "workspace_version": run.workspace_version,
        # `config` only. `secrets` is a separate column precisely so that it
        # cannot be swept into a payload by a `**run.__dict__`, and a zip is the
        # easiest artifact here to forward to somebody else.
        "config": run.config or {},
        "exported_by": subject,
        "exported_at": datetime.now(timezone.utc).isoformat(),
    }

    # Always the step number, never just "best": the file is identified in a
    # downloads folder weeks later, and two zips from the same run both called
    # `billing-best.zip` are indistinguishable — which is the situation where
    # someone deploys the wrong one.
    stem = run.skill_name.replace("/", "-")
    filename = f"{stem}-step-{step_no}{'-best' if is_best else ''}.zip"
    return Response(
        content=skillio.skill_zip(dict(snapshot.files), manifest),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


async def _val_scores(
    session: AsyncSession, run_id: uuid.UUID, step_nos
) -> dict[int, dict]:
    """`{step_no: {hard, soft}}` from the validation rollouts of those steps."""
    rows = (
        await session.execute(
            select(OptimizationStep.step_no, OptimizationRollout.hard, OptimizationRollout.soft)
            .join(OptimizationRollout, OptimizationRollout.step_id == OptimizationStep.id)
            .where(
                OptimizationStep.run_id == run_id,
                OptimizationStep.step_no.in_(set(step_nos)),
                OptimizationRollout.split == "val",
            )
        )
    ).all()
    return {
        step_no: {"hard": _num(hard), "soft": _num(soft)} for step_no, hard, soft in rows
    }


@router.delete("/runs/{run_id}", status_code=204)
async def delete_optimization_run(
    run_id: uuid.UUID,
    subject: str = Depends(current_subject),
    session: AsyncSession = Depends(get_session),
):
    """Delete a run and everything under it. Creator only, and not while it lives.

    Creator only, like cancel and resume: a run is one developer's experiment
    against their own agent endpoint, even though everyone who shares its source
    eval sets can read it.

    `pending` is refused as well as `running`, and that is not tidiness. The
    background task is spawned after the transaction commits and reads the run
    back by id; it then works for a while before flipping the status to
    `running`. A delete landing inside that window leaves a task holding an id
    that no longer exists — it goes on buying agent calls until its first step
    insert trips the foreign key, which surfaces as a traceback in the log and a
    bill for nothing. Stopping first closes the window: cancel accepts `pending`,
    and a cancelled run deletes.

    The engine may still be between its own checks even so, so the cancellation
    event is signalled on the way out. It costs nothing when nothing is
    listening, and it is what stops an in-flight agent call rather than waiting
    for it to come back to a run that is gone.
    """
    run = await _load_visible_run(session, run_id, subject)
    if run.created_by != subject:
        raise HTTPException(
            status_code=403, detail="only the developer who started this run can delete it"
        )
    if run.status in ("running", "pending"):
        raise HTTPException(
            status_code=409, detail="stop this run before deleting it"
        )
    await deletion.delete_optimization_run(session, run_id)
    await session.commit()
    cancellation.signal(run_id)
