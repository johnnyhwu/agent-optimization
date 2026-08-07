"""Paging, filtering, and the query-count guard for the two list endpoints.

Unlike the rest of the suite these need a real database: the whole point is what
SQL gets issued, which a stub session cannot answer. They **skip** unless
`TEST_DATABASE_URL` is set, so `make test` stays DB-free and network-free as
documented. To run them:

    createdb agenteval_test
    TEST_DATABASE_URL='postgresql+asyncpg://localhost/agenteval_test' pytest tests/test_pagination.py

The guard that matters is `test_card_query_count_does_not_grow_with_page_size`.
The endpoint it protects used to issue three queries *per eval set*, one of which
loaded every question_result of every run of that set in order to compute a
two-run regression summary — so the home page read hundreds of thousands of rows
to render a handful of numbers, and got slower every time anyone ran an eval.
Constant query count is the property that stops it coming back; asserting a
wall-clock number instead would just be flaky.
"""
from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db import Base
from app.models import EvalSet, EvalSetRole, Question, QuestionResult, Run
from app.routers.eval_sets import list_eval_sets
from app.routers.runs import list_runs

TEST_DB = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DB, reason="set TEST_DATABASE_URL to run the database-backed paging tests"
)


class QueryCounter:
    """Counts SQL statements issued inside the block."""

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


# Function-scoped: the async engine pools connections per event loop, and
# pytest-asyncio gives each test its own loop.
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
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        # Truncate rather than recreate: each test starts from an empty schema
        # without paying for a full drop/create.
        await s.execute(
            text(
                "TRUNCATE span_analyses, question_results, question_skills,"
                " questions, runs, eval_set_roles, eval_sets CASCADE"
            )
        )
        await s.commit()
        yield s


async def make_set(session, name, *, metadata=None, runs=0, questions=2, subject="alice"):
    es = EvalSet(name=name, source_format="jsonl", meta=metadata or {})
    session.add(es)
    await session.flush()
    session.add(EvalSetRole(eval_set_id=es.id, user_subject=subject, role="owner"))

    qs = []
    for i in range(questions):
        q = Question(
            eval_set_id=es.id, question_id=f"q_{i}", question="q",
            ground_truth_response="gt", ground_truth_reasoning="r",
        )
        session.add(q)
        await session.flush()
        qs.append(q)

    for r in range(runs):
        run = Run(
            eval_set_id=es.id, triggered_by=subject, name=f"{name} run {r}",
            status="completed", config={}, secrets={},
            pass_rate=0.5, total_count=questions, correct_count=questions // 2,
        )
        session.add(run)
        await session.flush()
        for i, q in enumerate(qs):
            session.add(
                QuestionResult(
                    run_id=run.id, question_pk=q.id, correlation_id=uuid.uuid4().hex,
                    verdict="correct" if i % 2 == 0 else "incorrect",
                    status="done", trace_ready=True,
                )
            )
    await session.commit()
    return es


async def fetch_cards(session, **kwargs):
    params = dict(
        q=None, metadata_key=None, metadata_value=None, sort="created_at",
        limit=24, offset=0, subject="alice",
    )
    params.update(kwargs)
    return await list_eval_sets(session=session, **params)


# --- The guard ---------------------------------------------------------------

async def test_card_query_count_does_not_grow_with_page_size(session, engine):
    for i in range(20):
        await make_set(session, f"Set {i:02d}", runs=3, questions=4)

    with QueryCounter(engine) as one:
        await fetch_cards(session, limit=1)
    with QueryCounter(engine) as many:
        page = await fetch_cards(session, limit=20)

    assert len(page.items) == 20
    # Identical, not merely "similar": the aggregates are computed for the whole
    # page in fixed queries, so the number is a property of the code, not of the
    # data. Any per-set query reintroduced here shows up immediately.
    assert one.count == many.count


async def test_run_query_count_does_not_grow_with_page_size(session, engine):
    es = await make_set(session, "Busy", runs=25, questions=4)

    with QueryCounter(engine) as one:
        await list_runs(eval_set_id=es.id, q=None, limit=1, offset=0,
                        subject="alice", session=session)
    with QueryCounter(engine) as many:
        page = await list_runs(eval_set_id=es.id, q=None, limit=20, offset=0,
                               subject="alice", session=session)

    assert len(page.items) == 20
    assert one.count == many.count


# --- Paging ------------------------------------------------------------------

async def test_paging_visits_every_card_exactly_once(session):
    for i in range(15):
        await make_set(session, f"Set {i:02d}", runs=1)

    seen, offset = [], 0
    while True:
        page = await fetch_cards(session, limit=4, offset=offset)
        seen += [c.id for c in page.items]
        if not page.has_more:
            break
        offset += len(page.items)

    assert len(seen) == 15
    assert len(set(seen)) == 15  # no card repeated across page boundaries


async def test_total_counts_all_matches_not_just_the_page(session):
    for i in range(10):
        await make_set(session, f"Set {i:02d}")
    page = await fetch_cards(session, limit=3)
    assert len(page.items) == 3
    assert page.total == 10
    assert page.has_more is True


async def test_last_page_reports_no_more(session):
    for i in range(5):
        await make_set(session, f"Set {i:02d}")
    page = await fetch_cards(session, limit=4, offset=4)
    assert len(page.items) == 1
    assert page.has_more is False


async def test_only_the_callers_own_sets_are_listed(session):
    await make_set(session, "Mine", subject="alice")
    await make_set(session, "Theirs", subject="bob")
    page = await fetch_cards(session)
    assert [c.name for c in page.items] == ["Mine"]
    assert page.total == 1


# --- Filtering and sorting (§6.10) -------------------------------------------

async def test_name_search_filters_in_sql_across_all_pages(session):
    await make_set(session, "Billing regression")
    await make_set(session, "Search relevance")
    await make_set(session, "Billing smoke")

    page = await fetch_cards(session, q="billing", limit=1)
    # The filter must apply before the limit: one item on the page, but the total
    # reflects every match. Filtering the loaded page instead would make the
    # result depend on how far the user had scrolled.
    assert len(page.items) == 1
    assert page.total == 2


async def test_metadata_key_and_value_filter(session):
    await make_set(session, "A", metadata={"team": "billing"})
    await make_set(session, "B", metadata={"team": "search"})
    await make_set(session, "C", metadata={"env": "prod"})

    by_key = await fetch_cards(session, metadata_key="team")
    assert by_key.total == 2  # key present, any value

    by_value = await fetch_cards(session, metadata_key="team", metadata_value="billing")
    assert [c.name for c in by_value.items] == ["A"]


async def test_sort_by_name(session):
    await make_set(session, "Charlie")
    await make_set(session, "Alpha")
    await make_set(session, "Bravo")
    page = await fetch_cards(session, sort="name")
    assert [c.name for c in page.items] == ["Alpha", "Bravo", "Charlie"]


# --- Card aggregates still correct after the rewrite --------------------------

async def test_trend_is_oldest_to_newest_and_capped(session):
    from app.routers.eval_sets import TREND_RUNS

    es = await make_set(session, "Long history", runs=TREND_RUNS + 5, questions=2)
    page = await fetch_cards(session)
    card = next(c for c in page.items if c.id == es.id)

    assert card.run_count == TREND_RUNS + 5  # the count is of everything
    # ...but the sparkline is bounded, so one long-lived set can't make the home
    # page load its entire run history to draw a thumbnail.
    assert len(card.trend) == TREND_RUNS


async def test_question_count_is_per_set_and_survives_the_page_query(session):
    # Counted in the same one-query-per-page way as everything else on the card:
    # a set with no questions has to come back as 0 rather than be missing from
    # the grouped result, and a set's count must not pick up its neighbour's.
    small = await make_set(session, "Small", questions=2)
    big = await make_set(session, "Big", questions=7)
    empty = await make_set(session, "Empty", questions=0)

    page = await fetch_cards(session)
    by_id = {c.id: c for c in page.items}
    assert by_id[small.id].question_count == 2
    assert by_id[big.id].question_count == 7
    assert by_id[empty.id].question_count == 0


async def test_regression_summary_uses_the_latest_two_runs(session):
    es = EvalSet(name="Regressions", source_format="jsonl", meta={})
    session.add(es)
    await session.flush()
    session.add(EvalSetRole(eval_set_id=es.id, user_subject="alice", role="owner"))

    questions = []
    for i in range(2):
        q = Question(
            eval_set_id=es.id, question_id=f"q_{i}", question="q",
            ground_truth_response="gt", ground_truth_reasoning="r",
        )
        session.add(q)
        await session.flush()
        questions.append(q)

    # Older run: both correct. Newer run: the first one regresses.
    for offset, verdicts in ((2, ["correct", "correct"]), (1, ["incorrect", "correct"])):
        run = Run(
            eval_set_id=es.id, triggered_by="alice", status="completed",
            config={}, secrets={}, pass_rate=0.5,
            started_at=text(f"now() - interval '{offset} hour'"),
        )
        session.add(run)
        await session.flush()
        for q, v in zip(questions, verdicts):
            session.add(
                QuestionResult(
                    run_id=run.id, question_pk=q.id, correlation_id=uuid.uuid4().hex,
                    verdict=v, status="done", trace_ready=True,
                )
            )
    await session.commit()

    page = await fetch_cards(session)
    card = next(c for c in page.items if c.id == es.id)
    assert card.regressed == 1
    assert card.improved == 0
