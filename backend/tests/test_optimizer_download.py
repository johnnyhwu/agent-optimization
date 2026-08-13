"""The zip: the only thing a run actually produces.

Everything else in Optimize is a chart. This endpoint is the deliverable — the
bytes a developer copies onto an agent server — and it is used exactly once per
run, days after the chart that justified it has been closed. That gap is what
the tests here are about.

Two failures matter more than the rest.

**Shipping a rejected candidate.** Any step's snapshot is downloadable, which is
deliberate: a developer may want to read the edits the gate turned down. But a
rejected candidate is a skill that *measured worse* than the one it came from,
and a zip that does not say so is a zip that gets deployed. The manifest is the
only place that warning can survive the trip.

**Paths that do not match the agent's layout.** The archive is unzipped over a
skills directory. Entries stored as `SKILL.md` rather than `billing/SKILL.md`
land in the wrong place — or, worse, over another skill's entry point.

These need a real database, like `test_optimizer_endpoints.py`: what is being
protected includes which rows the query joins, and a stub session cannot answer
that.
"""
from __future__ import annotations

import io
import json
import os
import uuid
import zipfile

import pytest
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db import Base
from app.models import (
    EvalSet,
    EvalSetRole,
    OptimizationItem,
    OptimizationRollout,
    OptimizationRun,
    OptimizationSkill,
    OptimizationStep,
)
from app.routers import optimization as opt

TEST_DB = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DB, reason="set TEST_DATABASE_URL to run the database-backed download tests"
)

INITIAL = {
    "billing/SKILL.md": "---\nname: billing\n---\n\nRefunds are pro-rata.\n",
    "billing/references/refunds.md": "Old text.\n",
}
CANDIDATE = {
    "billing/SKILL.md": "---\nname: billing\n---\n\nRefunds are pro-rata.\nCheck the term.\n",
    "billing/references/refunds.md": "Old text.\n",
    "billing/references/terms.md": "New file.\n",
}


@pytest.fixture
async def engine():
    eng = create_async_engine(TEST_DB)
    async with eng.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest.fixture
async def session(engine):
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
        await s.rollback()
    async with engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            await conn.execute(text(f'TRUNCATE TABLE "{table.name}" CASCADE'))


async def make_run(
    session,
    *,
    subject="alice",
    best_step=1,
    steps=((0, None, None, 0.5), (1, "accept_new_best", None, 0.75)),
    overlap=False,
    secrets=None,
):
    """A finished run with a baseline, some steps, and a snapshot for each.

    `steps` is `(step_no, gate_action, reject_reason, val_hard)`. Step 0 stores
    the initial skill; every other step stores the candidate it produced,
    whether or not the gate kept it.
    """
    eval_set = EvalSet(name="set", source_format="jsonl", meta={})
    session.add(eval_set)
    await session.flush()
    session.add(EvalSetRole(eval_set_id=eval_set.id, user_subject=subject, role="owner"))

    run = OptimizationRun(
        name="tune billing", created_by=subject, status="completed",
        mode="isolated", skill_name="billing",
        config={"optimizer_model": "gpt-5", "gate_metric": "hard"},
        secrets=secrets or {"optimizer_api_key": "sk-live-do-not-ship"},
        workspace_version="ws-7", initial_skill=INITIAL, detector={},
        num_epochs=1, batch_size=4, steps_per_epoch=2, total_steps=2,
        best_step=best_step, best_score=0.75,
    )
    session.add(run)
    await session.flush()

    for ordinal, split in enumerate(["train"] * 8 + ["val"] * 5):
        session.add(OptimizationItem(
            run_id=run.id, split=split, item_key=f"{eval_set.id}:q{ordinal}",
            source_eval_set_id=eval_set.id, question=f"q{ordinal}",
            ground_truth_response="gold", ground_truth_reasoning="because",
            ordinal=ordinal,
        ))
    if overlap:
        session.add(OptimizationItem(
            run_id=run.id, split="val", item_key=f"{eval_set.id}:q0",
            source_eval_set_id=eval_set.id, question="q0",
            ground_truth_response="gold", ground_truth_reasoning="because", ordinal=99,
        ))

    for step_no, action, reason, val_hard in steps:
        step = OptimizationStep(
            run_id=run.id, step_no=step_no, epoch_no=0 if step_no == 0 else 1,
            step_in_epoch=step_no, status="done",
            gate_action=action, gate_reject_reason=reason,
            current_score=0.5, best_score=0.75 if step_no else 0.5,
        )
        session.add(step)
        await session.flush()
        session.add(OptimizationRollout(
            step_id=step.id, split="val", skill_step_no=step_no,
            n_items=5, n_scored=5, hard=val_hard, soft=val_hard,
        ))
        session.add(OptimizationSkill(
            run_id=run.id, step_no=step_no,
            kind="initial" if step_no == 0 else "candidate",
            files=INITIAL if step_no == 0 else CANDIDATE,
            content_hash=f"hash-{step_no}", per_file_stats={},
        ))
    await session.commit()
    return run, eval_set


def unzip(response):
    archive = zipfile.ZipFile(io.BytesIO(response.body))
    files = {name: archive.read(name).decode() for name in archive.namelist()}
    manifest = json.loads(files.pop("manifest.json"))
    return files, manifest


def everything_in(response) -> str:
    """Every byte the archive expands to, as text.

    Searching `response.body` for a leaked credential proves nothing: the zip is
    DEFLATE-compressed, so a secret sitting in plain sight inside the manifest
    does not appear in the compressed stream. The search has to happen after
    decompression, and it has to cover the skill files too — an edit could have
    copied a key out of a prompt.
    """
    files, manifest = unzip(response)
    return "\n".join([*files.values(), json.dumps(manifest)])


# --- What lands on disk -----------------------------------------------------


async def test_the_default_download_is_the_best_step(session):
    """No argument means the skill the run recommends, not the last one it tried.

    The last step of a run is very often a rejected candidate — that is what
    running until the epochs are used up looks like. Handing that one over by
    default would ship the worst thing the run produced roughly as often as the
    best.
    """
    run, _ = await make_run(session)
    response = await opt.download_optimized_skill(
        run.id, step="best", subject="alice", session=session
    )
    files, manifest = unzip(response)
    assert manifest["step_no"] == 1
    assert manifest["is_best_by_validation"] is True
    assert files == CANDIDATE


async def test_the_zip_stores_files_under_the_skill_directory(session):
    """Entries keep the paths the agent server expects.

    The archive is unzipped over a skills directory. An entry named `SKILL.md`
    instead of `billing/SKILL.md` either lands in the wrong place or overwrites
    a different skill's entry point — and both look like a successful download.
    """
    run, _ = await make_run(session)
    files, _ = unzip(
        await opt.download_optimized_skill(run.id, step="best", subject="alice", session=session)
    )
    assert set(files) == {
        "billing/SKILL.md",
        "billing/references/refunds.md",
        "billing/references/terms.md",
    }


async def test_the_baseline_is_downloadable_as_the_skill_it_started_from(session):
    """Step 0 hands back the initial skill.

    "Give me back exactly what I had before this run" is the undo button for a
    developer who has already copied a candidate onto a server. It has to come
    from the same snapshot the run measured, not from whatever is on the agent
    now.
    """
    run, _ = await make_run(session)
    files, manifest = unzip(
        await opt.download_optimized_skill(run.id, step="0", subject="alice", session=session)
    )
    assert files == INITIAL
    assert manifest["snapshot"] == "initial"
    assert manifest["step_no"] == 0


async def test_the_filename_names_the_skill_and_the_step(session):
    """The zip is opened days later, in a downloads folder full of other zips.

    `download.zip` is unidentifiable by then, and the two things needed to
    identify it — which skill, which step — are exactly what the filename can
    carry without being opened.
    """
    run, _ = await make_run(session)
    response = await opt.download_optimized_skill(
        run.id, step="best", subject="alice", session=session
    )
    disposition = response.headers["Content-Disposition"]
    assert "billing" in disposition
    assert "step-1" in disposition
    assert response.media_type == "application/zip"


# --- What the manifest has to say -------------------------------------------


async def test_downloading_a_rejected_candidate_says_so(session):
    """A rejected step measured *worse* than the skill it came from.

    It stays downloadable on purpose — reading the edits the gate turned down is
    a legitimate reason to fetch one. But nothing about the zip itself
    distinguishes it from the winning candidate, and a developer who fetched it
    to read it will still have it in their downloads folder next week. The
    manifest is the only warning that survives that long.
    """
    run, _ = await make_run(
        session,
        best_step=1,
        steps=(
            (0, None, None, 0.5),
            (1, "accept_new_best", None, 0.75),
            (2, "reject", "accuracy", 0.4),
        ),
    )
    _, manifest = unzip(
        await opt.download_optimized_skill(run.id, step="2", subject="alice", session=session)
    )
    assert manifest["gate"]["action"] == "reject"
    assert manifest["gate"]["reject_reason"] == "accuracy"
    assert any("reject" in w.lower() for w in manifest["warnings"]), manifest["warnings"]


async def test_downloading_an_accepted_step_that_is_not_the_best_says_so(session):
    """`accept` is not `accept_new_best`.

    A step can clear the gate without beating the run's best — the gate compares
    against the current skill, and the best-so-far line is a separate threshold.
    A zip from such a step carries a skill the run itself would not recommend,
    and nothing in it is wrong enough to notice.
    """
    run, _ = await make_run(
        session,
        best_step=1,
        steps=((0, None, None, 0.5), (1, "accept_new_best", None, 0.75), (2, "accept", None, 0.75)),
    )
    _, manifest = unzip(
        await opt.download_optimized_skill(run.id, step="2", subject="alice", session=session)
    )
    assert manifest["is_best_by_validation"] is False
    assert any("best" in w.lower() for w in manifest["warnings"]), manifest["warnings"]


async def test_the_manifest_records_that_validation_was_not_held_out(session):
    """Overlap between the splits makes the headline score partly self-marked.

    The wizard allows it and the overview page warns about it. Neither is on
    screen when the zip is opened, and "0.75 on validation" is the number that
    gets repeated — so the qualification has to travel with the file.
    """
    run, _ = await make_run(session, overlap=True)
    _, manifest = unzip(
        await opt.download_optimized_skill(run.id, step="best", subject="alice", session=session)
    )
    assert any("held out" in w.lower() for w in manifest["warnings"]), manifest["warnings"]


async def test_the_manifest_carries_the_score_and_what_it_improved_on(session):
    """One number is not an argument; two are.

    "0.75 on validation" says nothing without the baseline it started from. The
    manifest is read when the chart is gone, so it has to carry the comparison
    rather than the endpoint of it.
    """
    run, _ = await make_run(session)
    _, manifest = unzip(
        await opt.download_optimized_skill(run.id, step="best", subject="alice", session=session)
    )
    assert manifest["validation"]["hard"] == 0.75
    assert manifest["baseline_validation"]["hard"] == 0.5
    assert manifest["run_id"] == str(run.id)
    assert manifest["skill_name"] == "billing"
    assert manifest["mode"] == "isolated"


async def test_a_step_that_was_never_validated_reports_no_validation_score(session):
    """A training score is not a validation score, and must never stand in for one.

    A step interrupted between its two rollouts has a candidate and a training
    number but nothing held-out. The manifest's headline figure is the one that
    justifies deploying the skill, so a query that reached for "this step's
    rollout" rather than "this step's *validation* rollout" would print the
    training accuracy — measured on the very questions the edits were derived
    from, and reliably the higher of the two.
    """
    run, _ = await make_run(session)
    step = OptimizationStep(
        run_id=run.id, step_no=2, epoch_no=1, step_in_epoch=2, status="aborted",
        abort_reason="cancelled",
    )
    session.add(step)
    await session.flush()
    session.add(OptimizationRollout(
        step_id=step.id, split="train", skill_step_no=1,
        n_items=8, n_scored=8, hard=0.99, soft=0.99,
    ))
    session.add(OptimizationSkill(
        run_id=run.id, step_no=2, kind="candidate", files=CANDIDATE,
        content_hash="hash-2", per_file_stats={},
    ))
    await session.commit()

    _, manifest = unzip(
        await opt.download_optimized_skill(run.id, step="2", subject="alice", session=session)
    )
    assert manifest["validation"] is None


async def test_the_download_never_carries_the_run_credentials(session):
    """`secrets` is a separate column so that it cannot be serialized by accident.

    A zip is the easiest artifact in the system to forward to somebody else. The
    manifest embeds the run's config, and config and secrets sit next to each
    other on the same row — a `run.__dict__` or a merged dict away from being
    the same thing.
    """
    run, _ = await make_run(session, secrets={"optimizer_api_key": "sk-live-do-not-ship"})
    response = await opt.download_optimized_skill(
        run.id, step="best", subject="alice", session=session
    )
    assert "sk-live-do-not-ship" not in everything_in(response)
    _, manifest = unzip(response)
    assert "secrets" not in manifest
    assert manifest["config"]["optimizer_model"] == "gpt-5"


# --- Refusals ---------------------------------------------------------------


async def test_a_run_the_caller_cannot_read_is_not_downloadable(session):
    """The zip contains the skill, and the skill is what the run was about.

    The visibility rule is derived from the source eval sets. A download route
    that skipped it would be the one endpoint where a run id is enough — and it
    hands over the whole artifact, not a summary of it.
    """
    run, _ = await make_run(session, subject="alice")
    with pytest.raises(HTTPException) as excinfo:
        await opt.download_optimized_skill(run.id, step="best", subject="mallory", session=session)
    assert excinfo.value.status_code == 404


async def test_a_step_with_no_snapshot_is_a_404_not_an_empty_zip(session):
    """A step aborted before its update stage has no candidate to hand over.

    An empty archive is the dangerous answer here: it unzips without complaint,
    leaves the agent's skill directory as it was, and looks like the download
    worked.
    """
    run, _ = await make_run(session)
    with pytest.raises(HTTPException) as excinfo:
        await opt.download_optimized_skill(run.id, step="7", subject="alice", session=session)
    assert excinfo.value.status_code == 404


async def test_a_run_that_has_not_finished_a_step_yet_is_refused(session):
    """`best` needs a best. A run still on its baseline rollout has none.

    The refusal has to name the reason. Falling through to the snapshot lookup
    with `best_step` still None produces a 404 as well — but one that says "no
    skill was recorded for step None", which reads as a bug in the run rather
    than as "wait for it to finish".
    """
    run, _ = await make_run(session, best_step=None, steps=())
    with pytest.raises(HTTPException) as excinfo:
        await opt.download_optimized_skill(run.id, step="best", subject="alice", session=session)
    assert excinfo.value.status_code == 404
    assert "not finished a step" in excinfo.value.detail


async def test_a_step_that_is_not_a_number_is_refused_as_a_bad_request(session):
    """`?step=latest` is a plausible guess at this API and is not one of the two.

    Letting it reach `int()` turns a typo in a URL into a 500 and an exception in
    the logs, which is how a wrong query string gets reported as an outage.
    """
    run, _ = await make_run(session)
    with pytest.raises(HTTPException) as excinfo:
        await opt.download_optimized_skill(run.id, step="latest", subject="alice", session=session)
    assert excinfo.value.status_code == 400


async def test_an_unknown_run_is_a_404(session):
    """Nothing about the id path should differ from any other run endpoint."""
    with pytest.raises(HTTPException) as excinfo:
        await opt.download_optimized_skill(
            uuid.uuid4(), step="best", subject="alice", session=session
        )
    assert excinfo.value.status_code == 404
