"""The four endpoints the wizard is built on.

These need a real database, like `test_pagination.py` and for the same reason:
what is being protected is partly *what SQL gets issued*, and a stub session
cannot answer that. They skip unless `TEST_DATABASE_URL` is set.

Three themes run through them.

**Permission is derived and has to hold at every entrance.** A run is readable
by whoever can read every eval set it drew from; the preview and the create
endpoint are where questions from those sets are first exposed, so a missing
check here leaks the thing the visibility rule exists to protect — and it leaks
question text, not just a count.

**Credentials go in and never come out.** `secrets` is a separate column for
exactly this reason, and the test is written against the serialized response
rather than against the model, because a field added to the wrong schema is how
that stops being true.

**The preview must not be an N+1.** `docs/spec.md` §10.2③ records what that did
to `GET /eval-sets`: 180 queries to render one page, growing with history. A
per-question accuracy lookup is the same shape of mistake, and a query count is
the only assertion that stays honest as the data grows.
"""
from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from fastapi import HTTPException

from app.config import settings
from app.db import Base
from app.models import (
    EvalSet,
    EvalSetRole,
    OptimizationItem,
    OptimizationRun,
    Question,
    QuestionResult,
    QuestionSkill,
    Run,
)
from app.optimizer import dataset, hyperparams
from app.routers import optimization as opt
from app.schemas import ImportPreviewRequest, OptimizationRunCreate

TEST_DB = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DB, reason="set TEST_DATABASE_URL to run the database-backed wizard tests"
)


class QueryCounter:
    def __init__(self, engine):
        self.engine = engine
        self.count = 0

    def _bump(self, *args, **kwargs):
        self.count += 1

    def __enter__(self):
        event.listen(self.engine.sync_engine, "before_cursor_execute", self._bump)
        return self

    def __exit__(self, *exc):
        event.remove(self.engine.sync_engine, "before_cursor_execute", self._bump)


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
    # Leave the schema clean for the next test in the same database.
    async with engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            await conn.execute(text(f'TRUNCATE TABLE "{table.name}" CASCADE'))


async def make_set(session, name, subject="alice", *, questions, role="owner"):
    """An eval set with questions, their skills, and a role for `subject`."""
    eval_set = EvalSet(name=name, source_format="jsonl", meta={})
    session.add(eval_set)
    await session.flush()
    session.add(EvalSetRole(eval_set_id=eval_set.id, user_subject=subject, role=role))
    rows = []
    for qid, skills in questions:
        question = Question(
            eval_set_id=eval_set.id, question_id=qid, question=f"text of {qid}",
            ground_truth_response=f"gold {qid}", ground_truth_reasoning="because",
        )
        session.add(question)
        await session.flush()
        for ordinal, skill in enumerate(skills):
            session.add(QuestionSkill(
                question_pk=question.id, skill_name=skill, ordinal=ordinal
            ))
        rows.append(question)
    await session.commit()
    return eval_set, rows


async def add_eval_run(session, eval_set, results, *, status="completed"):
    """A finished eval run with one result per (question, verdict) pair given."""
    run = Run(eval_set_id=eval_set.id, triggered_by="alice", status=status,
              config={}, secrets={})
    session.add(run)
    await session.flush()
    for question, verdict in results:
        session.add(QuestionResult(
            run_id=run.id, question_pk=question.id,
            correlation_id=uuid.uuid4().hex,
            status="done" if verdict else "failed",
            verdict=verdict,
        ))
    await session.commit()
    return run


# --- GET /optimization/defaults ---------------------------------------------


async def test_defaults_never_carry_a_credential():
    """The form starts its secret fields blank, and this is why it can.

    Every other prefill on that screen comes from the environment. If an API key
    came with them, it would be rendered into a page, sent to every developer
    who opens the wizard, and sit in their browser's memory — and nobody would
    notice, because the field would simply look conveniently filled in.
    """
    payload = await opt.optimization_defaults(subject="alice")
    flat = str(payload).lower()
    for forbidden in ("api_key", "secret_key", "password", "token"):
        assert forbidden not in flat


async def test_defaults_report_which_seams_are_real():
    """A wizard that hides this asks people to configure things with no effect.

    With `OPTIMIZER_IMPL=fake` the optimizer model field changes nothing at all,
    and someone will otherwise spend an afternoon wondering why their edits look
    canned.
    """
    payload = await opt.optimization_defaults(subject="alice")
    assert set(payload["impls"]) >= {"agent", "judge", "trace", "optimizer"}


async def test_defaults_include_the_split_limits_the_wizard_enforces():
    """One source for a rule the browser and the server both apply.

    The split editor greys out its Start button below the minimum; the create
    endpoint refuses below the minimum. If the browser carried its own copy of
    the number, the two would drift and the button would be enabled on a request
    the server rejects.
    """
    payload = await opt.optimization_defaults(subject="alice")
    assert payload["limits"]["min_train"] == dataset.MIN_TRAIN
    assert payload["limits"]["min_val"] == dataset.MIN_VAL


async def test_defaults_include_the_conditions_that_stop_a_run_early():
    """The wizard cannot offer a setting whose default it has to guess.

    All four conditions are environment-derived, so a deployment whose agent
    server is flaky can loosen them once instead of asking every developer to
    retype the same numbers — and the form shows what an untouched run would
    actually do.
    """
    payload = await opt.optimization_defaults(subject="alice")

    assert payload["defaults"]["early_stop_val_error_share"] == (
        settings.early_stop_val_error_share
    )
    assert payload["defaults"]["early_stop_val_error_streak"] == (
        settings.early_stop_val_error_streak
    )
    assert payload["defaults"]["early_stop_patience"] == settings.early_stop_patience
    assert "early_stop_target_score" in payload["defaults"]


# --- GET /optimization/skill-check ------------------------------------------


async def test_skill_check_lists_the_files_of_a_skill_that_exists():
    """Step 4 confirms the tag and the agent's directory are the same name.

    Decision 6 treats them as equal, which is only safe if the wizard proves it
    before the run starts rather than discovering it at step 0 — after a run
    row, an item snapshot and a batch of agent calls have already been spent.
    """
    check = await opt.skill_check(skill_name="billing", subject="alice")
    assert check.exists is True
    assert any(path.endswith("SKILL.md") for path in check.files)
    assert len(check.files) > 1, "the fake skill has a reference file; the tree needs it"


async def test_skill_check_measures_each_file_and_not_only_their_sum():
    """The card draws a tree, and a tree needs a number per file.

    `n_chars` alone is one figure for a whole directory, which cannot say
    whether a skill is one long SKILL.md or a short one beside a large
    reference — the difference that decides what an optimization run can move.
    """
    check = await opt.skill_check(skill_name="billing", subject="alice")
    assert set(check.file_chars) == set(check.files)
    assert all(n > 0 for n in check.file_chars.values())
    assert sum(check.file_chars.values()) == check.n_chars


async def test_skill_check_answers_for_the_agent_it_was_given():
    """The wizard collects a base URL; the check has to use that one.

    Reading the environment instead let a skill be cleared against one agent and
    the run be sent to another — a mismatch with no symptom, because the check
    passed.
    """
    check = await opt.skill_check(
        skill_name="billing", agent_base_url="http://agent.example:9000", subject="alice"
    )
    assert check.agent_base_url == "http://agent.example:9000"

    # Blank keeps meaning "the server's own", as everywhere else in this config.
    fallback = await opt.skill_check(skill_name="billing", subject="alice")
    assert fallback.agent_base_url == settings.agent_base_url


async def test_skill_check_names_the_agent_even_when_the_skill_is_missing():
    """A skill that was not found most needs to say *where* it was looked for."""
    check = await opt.skill_check(
        skill_name="billling", agent_base_url="http://agent.example:9000", subject="alice"
    )
    assert check.exists is False
    assert check.agent_base_url == "http://agent.example:9000"


async def test_skill_check_names_the_skills_that_do_exist_when_one_is_missing():
    """'Not found' with no list is a dead end; with a list it is a typo.

    The developer picked the name from a question tag, so a mismatch is usually
    one character — and the answer is on the agent server they just connected to.
    """
    check = await opt.skill_check(skill_name="billling", subject="alice")
    assert check.exists is False
    assert "billing" in check.available_skills


async def test_skill_check_reports_whether_routing_mode_is_possible():
    """Routing edits the frontmatter description. No frontmatter, no routing.

    The mode picker on step 4 reads this to disable the option with a reason,
    which is the difference between an informed choice and a run that rejects
    every candidate for an hour.
    """
    check = await opt.skill_check(skill_name="billing", subject="alice")
    assert check.has_frontmatter is False  # the fake workspace's skills carry none
    assert check.routing_blocked_reason


async def test_skill_check_reports_an_unreachable_agent_as_a_503(monkeypatch):
    """Not a 500. The wizard prints this sentence beside the skill card, and the
    agent server's own words are the only thing that tells a developer whether
    the URL is wrong, the host is down, or /skills is not implemented.
    """
    class Broken:
        async def get_workspace(self):
            raise RuntimeError("could not reach the agent server at http://x/skills")

    class Seams:
        workspace = Broken()

    monkeypatch.setattr(opt, "build_seams", lambda *a, **k: Seams())
    with pytest.raises(HTTPException) as exc:
        await opt.skill_check(skill_name="billing", subject="alice")

    assert exc.value.status_code == 503
    assert "/skills" in exc.value.detail


# --- POST /optimization/import-preview --------------------------------------


async def test_the_preview_groups_questions_by_skill(session):
    eval_set, _ = await make_set(session, "set", questions=[
        ("q1", ["billing"]), ("q2", ["billing"]), ("q3", ["reporting"]),
        ("q4", []), ("q5", ["billing", "reporting"]),
    ])
    preview = await opt.import_preview(
        ImportPreviewRequest(eval_set_ids=[eval_set.id]), subject="alice", session=session
    )
    by_name = {g.skill_name: g for g in preview.groups}
    assert sorted(by_name) == ["billing", "reporting"]
    # q5 carries both tags and appears under both: a routing run optimises
    # several descriptions together and a question spanning two of them is what
    # says where the boundary between them belongs.
    assert {q.question_id for q in by_name["billing"].questions} == {"q1", "q2", "q5"}
    assert {q.question_id for q in by_name["reporting"].questions} == {"q3", "q5"}
    # Only the untagged one has nowhere to go.
    assert {q.question_id for q in preview.ambiguous} == {"q4"}


async def test_a_source_the_caller_cannot_read_is_refused(session):
    """The preview is the first place another set's question text is exposed.

    Visibility for the run itself is derived from "reader on every source", and
    that rule is worth nothing if the screen that *builds* the run will happily
    read a set the caller has no role on. It refuses the whole request rather
    than silently dropping the set: a preview quietly missing half its questions
    is how someone builds a run they think covers something it does not.
    """
    mine, _ = await make_set(session, "mine", "alice", questions=[("q1", ["billing"])])
    theirs, _ = await make_set(session, "theirs", "bob", questions=[("q2", ["billing"])])

    with pytest.raises(HTTPException) as exc:
        await opt.import_preview(
            ImportPreviewRequest(eval_set_ids=[mine.id, theirs.id]),
            subject="alice", session=session,
        )
    assert exc.value.status_code in (403, 404)


async def test_a_viewer_may_preview_a_set_they_can_read(session):
    """Building a run is not an owner-only act, the same way running an eval is not."""
    eval_set, _ = await make_set(
        session, "shared", "carol", questions=[("q1", ["billing"])], role="viewer"
    )
    preview = await opt.import_preview(
        ImportPreviewRequest(eval_set_ids=[eval_set.id]), subject="carol", session=session
    )
    assert preview.groups[0].questions[0].question_id == "q1"


async def test_prior_accuracy_reports_a_fraction_and_not_a_bare_percentage(session):
    """`60%` from five runs and `60%` from one are different claims.

    The picker is where a developer decides which questions are worth training
    on. A percentage with no denominator invites them to trust a number derived
    from a single run, and the questions most worth optimising are exactly the
    ones with the least history.
    """
    eval_set, questions = await make_set(session, "set", questions=[("q1", ["billing"])])
    await add_eval_run(session, eval_set, [(questions[0], "correct")])
    await add_eval_run(session, eval_set, [(questions[0], "incorrect")])

    preview = await opt.import_preview(
        ImportPreviewRequest(eval_set_ids=[eval_set.id]), subject="alice", session=session
    )
    question = preview.groups[0].questions[0]
    assert question.prior_accuracy == pytest.approx(0.5)
    assert question.prior_runs == 2


async def test_a_question_that_has_never_been_run_reports_none_not_zero(session):
    """Zero means 'always wrong'. Unknown means nobody has asked yet.

    Shown as 0% it looks like the hardest question in the set — the first one a
    developer would reach for — when in fact there is no evidence about it at
    all. It also decides which side of the stratified split it lands on.
    """
    eval_set, _ = await make_set(session, "set", questions=[("q1", ["billing"])])
    preview = await opt.import_preview(
        ImportPreviewRequest(eval_set_ids=[eval_set.id]), subject="alice", session=session
    )
    question = preview.groups[0].questions[0]
    assert question.prior_accuracy is None
    assert question.prior_runs == 0


async def test_unfinished_runs_do_not_count_towards_prior_accuracy(session):
    """A cancelled run's questions are a sample of whatever ran before the stop.

    Counting them makes the figure depend on when somebody pressed a button.
    """
    eval_set, questions = await make_set(session, "set", questions=[("q1", ["billing"])])
    await add_eval_run(session, eval_set, [(questions[0], "correct")])
    await add_eval_run(session, eval_set, [(questions[0], "incorrect")], status="cancelled")

    preview = await opt.import_preview(
        ImportPreviewRequest(eval_set_ids=[eval_set.id]), subject="alice", session=session
    )
    assert preview.groups[0].questions[0].prior_accuracy == pytest.approx(1.0)
    assert preview.groups[0].questions[0].prior_runs == 1


async def test_a_failed_question_is_excluded_rather_than_counted_wrong(session):
    """An agent timeout is not the question being hard.

    Scoring it as incorrect makes flaky infrastructure look like a difficult
    question, and sends it to the training split for a problem no skill edit can
    fix.
    """
    eval_set, questions = await make_set(session, "set", questions=[("q1", ["billing"])])
    await add_eval_run(session, eval_set, [(questions[0], "correct")])
    await add_eval_run(session, eval_set, [(questions[0], None)])  # status='failed'

    preview = await opt.import_preview(
        ImportPreviewRequest(eval_set_ids=[eval_set.id]), subject="alice", session=session
    )
    assert preview.groups[0].questions[0].prior_accuracy == pytest.approx(1.0)


async def test_the_preview_query_count_does_not_grow_with_the_question_count(
    session, engine
):
    """The guard that keeps this from becoming `GET /eval-sets` before its fix.

    A per-question accuracy lookup is the most natural way to write this and the
    most expensive: it is invisible on the seeded demo and quadratic on a real
    set, and by the time anyone notices, the wizard is the slowest screen in the
    product. A constant query count is what stops it coming back; a wall-clock
    assertion would only be flaky.
    """
    small, small_qs = await make_set(session, "small", questions=[
        (f"q{i}", ["billing"]) for i in range(3)
    ])
    await add_eval_run(session, small, [(q, "correct") for q in small_qs])

    with QueryCounter(engine) as counter:
        await opt.import_preview(
            ImportPreviewRequest(eval_set_ids=[small.id]), subject="alice", session=session
        )
    small_count = counter.count

    big, big_qs = await make_set(session, "big", questions=[
        (f"b{i}", ["billing"]) for i in range(40)
    ])
    await add_eval_run(session, big, [(q, "correct") for q in big_qs])

    with QueryCounter(engine) as counter:
        await opt.import_preview(
            ImportPreviewRequest(eval_set_ids=[big.id]), subject="alice", session=session
        )
    assert counter.count == small_count, (
        f"{counter.count} queries for 40 questions vs {small_count} for 3 — "
        "the accuracy lookup is per-question"
    )


# --- POST /optimization/runs ------------------------------------------------


def create_body(train, val, **overrides):
    body = dict(
        name="tune billing",
        mode="isolated",
        skill_name="billing",
        train=train,
        val=val,
        num_epochs=1,
        batch_size=4,
        config={},
        secrets={},
        detector={},
    )
    body.update(overrides)
    return OptimizationRunCreate(**body)


async def make_runnable_set(session, n=20):
    eval_set, questions = await make_set(session, "set", questions=[
        (f"q{i}", ["billing"]) for i in range(n)
    ])
    keys = [f"{eval_set.id}:{q.question_id}" for q in questions]
    return eval_set, questions, keys


async def test_creating_a_run_reports_an_unreachable_agent_as_a_503(
    session, monkeypatch
):
    """Same reason as the skill check, and it matters more here: Start is the
    last place a developer can be told the agent is not answering. A 500 would
    read as a bug in this platform rather than a URL to fix."""
    eval_set, questions, keys = await make_runnable_set(session)

    class Broken:
        async def get_workspace(self):
            raise RuntimeError("agent server returned 404 for /skills")

    class Seams:
        workspace = Broken()

    monkeypatch.setattr(opt, "build_seams", lambda *a, **k: Seams())
    with pytest.raises(HTTPException) as exc:
        await opt.create_optimization_run(
            create_body(keys[:14], keys[14:]), subject="alice", session=session
        )

    assert exc.value.status_code == 503
    assert "404" in exc.value.detail


async def test_creating_a_run_snapshots_the_questions(session, monkeypatch):
    """A question edited tomorrow must not change what this run measured.

    The same rule `runs` already follows. Without it, a developer looking at a
    six-week-old chart sees the accuracy the run recorded beside question text
    that has since been rewritten — and the two no longer describe the same
    experiment.
    """
    _stub_start(monkeypatch)
    eval_set, questions, keys = await make_runnable_set(session)

    run = await opt.create_optimization_run(
        create_body(keys[:14], keys[14:]), subject="alice", session=session
    )

    questions[0].question = "edited afterwards"
    await session.commit()

    items = (await session.scalars(
        (await _items_of(run.id))
    )).all()
    snapshot = next(i for i in items if i.item_key == keys[0])
    assert snapshot.question == "text of q0"
    assert snapshot.ground_truth_response == "gold q0"


async def test_a_runs_stop_conditions_are_stored_as_numbers_not_as_blanks(
    session, monkeypatch
):
    """The run's page shows the reader what will end this run.

    A blank in the stored config would have to be explained by re-deriving the
    server's defaults in the browser — and today's environment is no witness to
    what it held when the run started, which is the same reason every other
    setting here is materialised.
    """
    _stub_start(monkeypatch)
    _, _, keys = await make_runnable_set(session)

    run = await opt.create_optimization_run(
        create_body(keys[:14], keys[14:], config={"early_stop_patience": 4}),
        subject="alice", session=session,
    )

    stored = (await session.get(OptimizationRun, run.id)).config
    assert stored["early_stop_patience"] == 4
    assert stored["early_stop_val_error_share"] == settings.early_stop_val_error_share
    assert stored["early_stop_val_error_streak"] == settings.early_stop_val_error_streak


async def test_a_run_records_every_algorithm_setting_it_ran_with(
    session, monkeypatch
):
    """The stored config is the record of how a run was run.

    Seven of these have no control in the wizard, so nothing sends them — and
    before they were materialised here, the value that actually ran was a
    literal in the engine. Change that literal and every finished run is
    retroactively described wrong, silently.
    """
    _stub_start(monkeypatch)
    _, _, keys = await make_runnable_set(session)

    run = await opt.create_optimization_run(
        create_body(keys[:14], keys[14:]), subject="alice", session=session
    )

    stored = (await session.get(OptimizationRun, run.id)).config
    assert set(stored) >= set(hyperparams.algorithm_defaults())
    assert stored["minibatch_size"] == settings.optimizer_minibatch_size
    assert stored["gate_metric"] == settings.optimizer_gate_metric


async def test_a_gate_weight_of_zero_is_stored_as_zero(session, monkeypatch):
    """`mixed_weight: 0` means "hard accuracy alone", and `or` used to eat it.

    It is the one falsy value in this config the schema actually allows, and
    the gate compares a different number for the whole run when it is lost.
    """
    _stub_start(monkeypatch)
    _, _, keys = await make_runnable_set(session)

    run = await opt.create_optimization_run(
        create_body(keys[:14], keys[14:], config={"gate_metric": "mixed", "mixed_weight": 0}),
        subject="alice", session=session,
    )

    stored = (await session.get(OptimizationRun, run.id)).config
    assert stored["mixed_weight"] == 0


async def test_a_switch_the_caller_never_mentioned_takes_the_deployments_default(
    session, monkeypatch
):
    """A `bool` that defaults to False cannot say "unset".

    These three were typed that way, so every request arrived asking for False
    and a deployment that wanted one of them on by default had no way to say so.
    """
    _stub_start(monkeypatch)
    monkeypatch.setattr(settings, "optimizer_failure_only", True)
    _, _, keys = await make_runnable_set(session)

    run = await opt.create_optimization_run(
        create_body(keys[:14], keys[14:]), subject="alice", session=session
    )

    stored = (await session.get(OptimizationRun, run.id)).config
    assert stored["failure_only"] is True


async def test_a_switch_turned_off_on_the_form_stays_off(session, monkeypatch):
    """…and an explicit False must still beat the deployment's True."""
    _stub_start(monkeypatch)
    monkeypatch.setattr(settings, "optimizer_failure_only", True)
    _, _, keys = await make_runnable_set(session)

    run = await opt.create_optimization_run(
        create_body(keys[:14], keys[14:], config={"failure_only": False}),
        subject="alice", session=session,
    )

    stored = (await session.get(OptimizationRun, run.id)).config
    assert stored["failure_only"] is False


async def test_a_stop_condition_switched_off_stays_off(session, monkeypatch):
    """0 means "never stop early", and it is one `or` away from meaning 3."""
    _stub_start(monkeypatch)
    _, _, keys = await make_runnable_set(session)

    run = await opt.create_optimization_run(
        create_body(keys[:14], keys[14:], config={"early_stop_val_error_streak": 0}),
        subject="alice", session=session,
    )

    stored = (await session.get(OptimizationRun, run.id)).config
    assert stored["early_stop_val_error_streak"] == 0


async def test_creating_a_run_computes_the_step_counts_the_engine_reads(
    session, monkeypatch
):
    """The engine reads `steps_per_epoch` and `total_steps` off the row.

    It does not derive them. If they were left at whatever the request sent, a
    run would train for a number of steps unrelated to its own batch size —
    silently short, so it would simply appear to stop improving early.
    """
    _stub_start(monkeypatch)
    _, _, keys = await make_runnable_set(session, n=30)

    run = await opt.create_optimization_run(
        create_body(keys[:21], keys[21:], batch_size=5, num_epochs=2),
        subject="alice", session=session,
    )
    # 21 training questions in batches of 5 -> 5 steps per epoch (the last is short).
    assert run.steps_per_epoch == 5
    assert run.total_steps == 10


async def test_a_split_below_the_minimum_is_refused_at_the_endpoint(
    session, monkeypatch
):
    """The browser's check is a convenience; this one is the rule.

    Anything else means the limit is enforced only for people using the UI as
    intended.
    """
    _stub_start(monkeypatch)
    _, _, keys = await make_runnable_set(session)

    with pytest.raises(HTTPException) as exc:
        await opt.create_optimization_run(
            create_body(keys[:3], keys[3:5]), subject="alice", session=session
        )
    assert exc.value.status_code == 400
    assert "training" in str(exc.value.detail).lower()


async def test_an_item_key_from_a_set_the_caller_cannot_read_is_refused(
    session, monkeypatch
):
    """Otherwise the run is a way to read questions through the back door.

    The preview checks the sets it was asked for; this checks the keys that
    actually arrived, which is the check that matters — the two lists are sent
    separately and a caller writes both.
    """
    _stub_start(monkeypatch)
    mine, _, mine_keys = await make_runnable_set(session)
    theirs, their_qs = await make_set(
        session, "theirs", "bob", questions=[(f"t{i}", ["billing"]) for i in range(10)]
    )
    stolen = [f"{theirs.id}:{q.question_id}" for q in their_qs]

    with pytest.raises(HTTPException) as exc:
        await opt.create_optimization_run(
            create_body(mine_keys[:10] + stolen, mine_keys[10:]),
            subject="alice", session=session,
        )
    assert exc.value.status_code in (400, 403, 404)


async def test_routing_mode_is_refused_for_a_skill_with_no_frontmatter(
    session, monkeypatch
):
    """Routing optimises the description. A skill without one has nothing to edit.

    Every proposed edit would be discarded at application time, the gate would
    reject every tied candidate, and the run would spend an hour producing a
    chart of flat rejections with no indication of the cause.
    """
    _stub_start(monkeypatch)
    _, _, keys = await make_runnable_set(session)

    with pytest.raises(HTTPException) as exc:
        await opt.create_optimization_run(
            create_body(keys[:14], keys[14:], mode="routing"),
            subject="alice", session=session,
        )
    assert exc.value.status_code == 400
    assert "frontmatter" in str(exc.value.detail).lower()


async def test_an_unknown_skill_is_refused_before_anything_is_created(
    session, monkeypatch
):
    """The skill tag and the agent's directory name are the same name (decision 6).

    Discovering the mismatch at step 0 instead would mean a run row, an item
    snapshot and a failed rollout for a typo.
    """
    _stub_start(monkeypatch)
    _, _, keys = await make_runnable_set(session)

    with pytest.raises(HTTPException) as exc:
        await opt.create_optimization_run(
            create_body(keys[:14], keys[14:], skill_name="nonexistent"),
            subject="alice", session=session,
        )
    assert exc.value.status_code == 400
    assert not (await session.scalars(await _all_runs())).all()


async def test_the_created_run_never_serializes_its_secrets(session, monkeypatch):
    """`secrets` is a separate column so this can be structural rather than a habit.

    Asserted against the serialized response, not the model: the way this stops
    being true is someone adding a convenient field to the wrong schema, and
    only the serialized form catches that.
    """
    _stub_start(monkeypatch)
    _, _, keys = await make_runnable_set(session)

    run = await opt.create_optimization_run(
        create_body(keys[:14], keys[14:],
                    secrets={"llm_api_key": "sk-must-never-appear"}),
        subject="alice", session=session,
    )
    assert "sk-must-never-appear" not in run.model_dump_json()

    detail = await opt.get_optimization_run(run.id, subject="alice", session=session)
    assert "sk-must-never-appear" not in detail.model_dump_json()


async def test_the_run_starts_only_after_it_has_been_committed(session, monkeypatch):
    """The background task opens its own session and reads the run by id.

    Spawned before the commit, it would race the transaction and find nothing —
    intermittently, under load, which is the worst way to find out.
    """
    _, _, keys = await make_runnable_set(session)

    # Recorded as a sequence, not as two independent facts: "the row exists" is
    # true from the session's identity map whether or not anything was
    # committed, so only the ordering distinguishes the bug from the fix.
    order: list[str] = []
    real_commit = session.commit

    async def tracking_commit():
        await real_commit()
        order.append("commit")

    monkeypatch.setattr(session, "commit", tracking_commit)
    monkeypatch.setattr(opt.runner, "start", lambda run_id: order.append("start"))

    run = await opt.create_optimization_run(
        create_body(keys[:14], keys[14:]), subject="alice", session=session
    )

    assert "start" in order, "the run was never spawned"
    assert order.index("commit") < order.index("start")
    persisted = await session.get(OptimizationRun, run.id)
    assert persisted is not None and persisted.status in ("pending", "running")


async def test_a_question_duplicated_into_both_splits_becomes_two_items(
    session, monkeypatch
):
    """The wizard offers it deliberately, so the schema has to allow it.

    One row per (split, question) is what lets the run answer 'was this question
    trained on *and* validated?' — which is the question the overlap warning on
    the overview page is answering.
    """
    _stub_start(monkeypatch)
    _, _, keys = await make_runnable_set(session)
    shared = keys[0]

    run = await opt.create_optimization_run(
        create_body(keys[:14], keys[14:] + [shared]), subject="alice", session=session
    )
    items = (await session.scalars(await _items_of(run.id))).all()
    both = [i for i in items if i.item_key == shared]
    assert sorted(i.split for i in both) == ["train", "val"]


def _stub_start(monkeypatch):
    monkeypatch.setattr(opt.runner, "start", lambda run_id: None)


async def _items_of(run_id):
    from sqlalchemy import select

    return select(OptimizationItem).where(OptimizationItem.run_id == run_id)


async def _all_runs():
    from sqlalchemy import select

    return select(OptimizationRun)


# --- The chart's payload -----------------------------------------------------
#
# Added after mutation testing found that `_step_summary` could drop the gate
# verdict entirely with the whole suite still green. The browser's chart tests
# cover what it does with `gate_action`; nothing covered whether the server
# sends it.


async def test_the_step_summary_carries_the_gate_verdict(session, monkeypatch):
    """Accepted or rejected is the one thing the chart cannot derive.

    Every validation marker's shape comes from `gate_action` — filled for a
    candidate that was kept, a grey cross for one that was not. Without it every
    point renders identically, and a run that kept nothing looks exactly like
    one that improved at every step.
    """
    from app.models import OptimizationRollout, OptimizationStep

    _stub_start(monkeypatch)
    _, _, keys = await make_runnable_set(session)
    run = await opt.create_optimization_run(
        create_body(keys[:14], keys[14:]), subject="alice", session=session
    )

    for step_no, action, reason in [(0, None, None), (1, "accept_new_best", None),
                                    (2, "reject", "accuracy")]:
        step = OptimizationStep(
            run_id=run.id, step_no=step_no, epoch_no=0 if not step_no else 1,
            step_in_epoch=step_no, status="done",
            gate_action=action, gate_reject_reason=reason,
            best_score=0.75, current_score=0.75,
        )
        session.add(step)
        await session.flush()
        session.add(OptimizationRollout(
            step_id=step.id, split="val", skill_step_no=step_no,
            n_items=6, n_scored=6, hard=0.5 + step_no / 10, soft=0.6,
        ))
    await session.commit()

    detail = await opt.get_optimization_run(run.id, subject="alice", session=session)
    assert [s.gate_action for s in detail.steps] == [None, "accept_new_best", "reject"]
    assert detail.steps[2].gate_reject_reason == "accuracy"


async def test_the_step_summary_reports_the_two_rollouts_apart(session, monkeypatch):
    """Train and validation are two different skills measured on two split sets.

    They are named apart on the wire for that reason. A summary that filled both
    from one rollout would draw the two series on top of each other, and the
    gap between them — the only visible sign of overfitting — would vanish.
    """
    from app.models import OptimizationRollout, OptimizationStep

    _stub_start(monkeypatch)
    _, _, keys = await make_runnable_set(session)
    run = await opt.create_optimization_run(
        create_body(keys[:14], keys[14:]), subject="alice", session=session
    )
    step = OptimizationStep(
        run_id=run.id, step_no=1, epoch_no=1, step_in_epoch=1, status="done",
        gate_action="reject", gate_reject_reason="accuracy",
    )
    session.add(step)
    await session.flush()
    session.add(OptimizationRollout(
        step_id=step.id, split="train", skill_step_no=0,
        n_items=6, n_scored=6, hard=0.9, soft=0.95, latency_p50_ms=1100,
    ))
    session.add(OptimizationRollout(
        step_id=step.id, split="val", skill_step_no=1,
        n_items=6, n_scored=5, n_agent_error=1, hard=0.2, soft=0.3, latency_p50_ms=2200,
    ))
    await session.commit()

    summary = (
        await opt.get_optimization_run(run.id, subject="alice", session=session)
    ).steps[0]
    assert (summary.train_hard, summary.val_hard) == (0.9, 0.2)
    assert (summary.train_soft, summary.val_soft) == (0.95, 0.3)
    assert (summary.train_latency_p50_ms, summary.val_latency_p50_ms) == (1100, 2200)
    # The exclusion belongs to the split that suffered it.
    assert (summary.train_n_agent_error, summary.val_n_agent_error) == (0, 1)
    assert (summary.train_n_scored, summary.val_n_scored) == (6, 5)


# --- DELETE /optimization/runs/{id} ------------------------------------------
#
# The endpoint existed with no test and no caller: nothing in the UI reached it,
# so its two rules — creator only, and not while the run is alive — had never
# been exercised at all. Both are the kind that fail open.


async def _seed_deletable_run(session, monkeypatch, *, subject="alice", status="completed"):
    """A finished run with a step, both rollouts, results, and a skill snapshot.

    Deep enough to prove the delete reaches the leaves: a result row hangs off a
    rollout, which hangs off a step, which hangs off the run, and none of those
    are reachable from the run's own row.
    """
    from app.models import (
        OptimizationMinibatch,
        OptimizationResult,
        OptimizationRollout,
        OptimizationSkill,
        OptimizationStageCall,
        OptimizationStep,
    )

    _stub_start(monkeypatch)
    eval_set, questions, keys = await make_runnable_set(session)
    run = await opt.create_optimization_run(
        create_body(keys[:14], keys[14:]), subject=subject, session=session
    )

    row = await session.get(OptimizationRun, run.id)
    row.status = status
    step = OptimizationStep(
        run_id=run.id, step_no=1, epoch_no=1, step_in_epoch=1, status="done",
        gate_action="accept_new_best",
    )
    session.add(step)
    await session.flush()
    session.add(OptimizationMinibatch(
        step_id=step.id, minibatch_no=0, source_type="failure", n_items=1,
    ))
    session.add(OptimizationStageCall(
        step_id=step.id, seq=0, stage="merge_final", prompt_system="s", prompt_user="u",
        output={"edits": []},
    ))
    session.add(OptimizationSkill(
        run_id=run.id, step_no=1, kind="candidate",
        files={"billing/SKILL.md": "# Billing\n"}, content_hash="abc", per_file_stats={},
    ))
    for split in ("train", "val"):
        rollout = OptimizationRollout(
            step_id=step.id, split=split, skill_step_no=1,
            n_items=2, n_scored=2, hard=1.0, soft=1.0,
        )
        session.add(rollout)
        await session.flush()
        session.add(OptimizationResult(
            rollout_id=rollout.id, item_key=keys[0], question_pk=questions[0].id,
            correlation_id=uuid.uuid4().hex, verdict="correct", status="done",
        ))
    await session.commit()
    return eval_set, run


async def test_deleting_a_run_takes_every_row_under_it(session, monkeypatch):
    """The run's children are not reachable from its own row, so "deleted" has to
    mean the whole tree.

    A rollout result left behind would be invisible — nothing lists results by
    anything but their rollout — while still holding a `question_pk` into an eval
    set the developer may later try to delete.
    """
    from sqlalchemy import func, select

    from app.models import (
        OptimizationMinibatch,
        OptimizationResult,
        OptimizationRollout,
        OptimizationSkill,
        OptimizationStageCall,
        OptimizationStep,
    )

    _, run = await _seed_deletable_run(session, monkeypatch)

    await opt.delete_optimization_run(run.id, subject="alice", session=session)

    assert await session.get(OptimizationRun, run.id) is None
    for model in (
        OptimizationItem, OptimizationStep, OptimizationRollout, OptimizationResult,
        OptimizationMinibatch, OptimizationStageCall, OptimizationSkill,
    ):
        left = await session.scalar(select(func.count()).select_from(model))
        assert left == 0, f"{model.__tablename__} still has rows"


async def test_deleting_a_run_leaves_the_questions_it_drew_from(session, monkeypatch):
    """A run quotes an eval set; it does not own it.

    The links across are ON DELETE SET NULL precisely so the two lifetimes stay
    separate, and this is the direction that was never tested: deleting the run
    must not take the eval set's questions — or anyone else's runs against them —
    with it.
    """
    from sqlalchemy import func, select

    eval_set, run = await _seed_deletable_run(session, monkeypatch)
    before = await session.scalar(
        select(func.count()).select_from(Question).where(Question.eval_set_id == eval_set.id)
    )

    await opt.delete_optimization_run(run.id, subject="alice", session=session)

    after = await session.scalar(
        select(func.count()).select_from(Question).where(Question.eval_set_id == eval_set.id)
    )
    assert after == before > 0
    assert await session.get(EvalSet, eval_set.id) is not None


async def test_only_the_developer_who_started_a_run_may_delete_it(session, monkeypatch):
    """Sharing an eval set makes someone a reader of the run, not its owner.

    Everyone with a role on the sources can open this run, watch its chart and
    download its skill. Deleting it is the one thing that cannot be undone by
    whoever it belongs to, so it follows cancel and resume: creator only.
    """
    eval_set, run = await _seed_deletable_run(session, monkeypatch)
    session.add(EvalSetRole(eval_set_id=eval_set.id, user_subject="bob", role="viewer"))
    await session.commit()

    # Readable — this is the premise of the test, not incidental.
    assert await opt.get_optimization_run(run.id, subject="bob", session=session)

    with pytest.raises(HTTPException) as exc:
        await opt.delete_optimization_run(run.id, subject="bob", session=session)
    assert exc.value.status_code == 403
    assert await session.get(OptimizationRun, run.id) is not None


async def test_a_run_you_cannot_see_is_not_there_to_delete(session, monkeypatch):
    """404, not 403: whether a run exists at a given id is itself not theirs to
    learn — the same choice `_load_visible_run` makes everywhere else."""
    _, run = await _seed_deletable_run(session, monkeypatch)

    with pytest.raises(HTTPException) as exc:
        await opt.delete_optimization_run(run.id, subject="carol", session=session)
    assert exc.value.status_code == 404
    assert await session.get(OptimizationRun, run.id) is not None


@pytest.mark.parametrize("status", ["running", "pending"])
async def test_a_live_run_must_be_stopped_before_it_can_be_deleted(
    session, monkeypatch, status
):
    """`pending` counts as live, and that is the whole point of this test.

    The background task is spawned after the create commits and reads the run
    back by id, then works for a while before flipping the status to `running`.
    A delete landing in that window leaves the task holding an id that no longer
    exists: it goes on buying agent calls until its first step insert trips the
    foreign key. Refusing both statuses closes the window — cancel accepts
    `pending`, so stopping first is a route that exists.
    """
    _, run = await _seed_deletable_run(session, monkeypatch, status=status)

    with pytest.raises(HTTPException) as exc:
        await opt.delete_optimization_run(run.id, subject="alice", session=session)
    assert exc.value.status_code == 409
    assert await session.get(OptimizationRun, run.id) is not None


async def test_a_cancelled_run_deletes(session, monkeypatch):
    """The route out of the 409 above has to actually work."""
    _, run = await _seed_deletable_run(session, monkeypatch, status="cancelled")
    await opt.delete_optimization_run(run.id, subject="alice", session=session)
    assert await session.get(OptimizationRun, run.id) is None


# --- Several skills in one routing run --------------------------------------

MULTI_WORKSPACE = {
    "billing/SKILL.md": (
        "---\nname: billing\ndescription: Invoices and balances.\n---\n# Billing\n"
    ),
    "billing/references/refunds.md": "# Refunds\nProrated.\n",
    "reporting/SKILL.md": (
        "---\nname: reporting\ndescription: Revenue reports.\n---\n# Reporting\n"
    ),
    "shipping/SKILL.md": (
        "---\nname: shipping\ndescription: Carriers.\n---\n# Shipping\n"
    ),
    "plain/SKILL.md": "# Plain\nNo frontmatter, so nothing to route on.\n",
}


async def _create_run(session, monkeypatch, **overrides):
    """A run against a workspace whose skills actually carry descriptions.

    The fake seam's skills have no frontmatter at all, which is fine for the
    isolated tests above and makes every routing target unusable here.
    """
    _stub_start(monkeypatch)

    class Workspace:
        version = "v1"
        skills = dict(MULTI_WORKSPACE)

    class Fake:
        async def get_workspace(self):
            return Workspace()

        async def get_version(self):
            return "v1"

    class Seams:
        workspace = Fake()

    monkeypatch.setattr(opt, "build_seams", lambda *a, **k: Seams())
    _, _, keys = await make_runnable_set(session)
    return await opt.create_optimization_run(
        create_body(keys[:14], keys[14:], **overrides),
        subject="alice", session=session,
    )


async def test_a_routing_run_can_target_several_skills(session, monkeypatch):
    """Descriptions compete, so they are optimised together.

    A run allowed to move only one boundary is scored against a workspace that
    was frozen against it: widening `billing` narrows `reporting` by
    implication, and the questions that say where the line belongs are the ones
    tagged for both.
    """
    out = await _create_run(
        session, monkeypatch, mode="routing", skill_names=["billing", "reporting"],
    )
    row = await session.get(OptimizationRun, out.id)

    assert sorted(out.target_skills) == ["billing", "reporting"]
    # Both targets are pinned as the skill under optimisation...
    assert set(row.initial_skill) >= {"billing/SKILL.md", "reporting/SKILL.md"}
    # ...and neither is in the frozen baseline, which is the rest of the
    # workspace. A target appearing in both would have the run competing against
    # a stale copy of the description it is editing.
    assert not any(p.startswith("billing/") for p in row.workspace_baseline)
    assert not any(p.startswith("reporting/") for p in row.workspace_baseline)
    assert "shipping/SKILL.md" in row.workspace_baseline


async def test_isolated_still_takes_exactly_one_skill(session, monkeypatch):
    """Isolated sends one skill and edits its body; two would be two experiments."""
    with pytest.raises(HTTPException) as exc:
        await _create_run(
            session, monkeypatch, mode="isolated", skill_names=["billing", "reporting"],
        )

    assert exc.value.status_code == 400
    assert "one skill" in exc.value.detail


async def test_a_routing_target_without_frontmatter_is_still_refused(session, monkeypatch):
    """The existing check, applied to every target rather than to the first."""
    with pytest.raises(HTTPException) as exc:
        await _create_run(
            session, monkeypatch, mode="routing",
            skill_names=["billing", "plain"],
        )

    assert exc.value.status_code == 400
    assert "plain" in exc.value.detail


async def test_a_single_skill_run_still_records_its_name(session, monkeypatch):
    """Every existing caller sends `skill_name`; none of them should change."""
    run = await _create_run(session, monkeypatch, mode="isolated", skill_name="billing")

    assert run.skill_name == "billing"
    assert run.target_skills == ["billing"]
