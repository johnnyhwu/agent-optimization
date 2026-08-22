"""Seed fake data that exercises the interesting Stage 1 cases (TASK.md).

Creates:
  - one eval set "Billing Agent Regression Suite" (owner=alice, viewer=bob)
  - 5 questions (2 carry fake-layer markers so LIVE runs also show a failure and
    a caveat — see app/integrations/fake.py)
  - 3 runs written directly to DB so the three incorrect modes visibly differ:
        pass rates 0.8 -> 0.6 -> 0.4 (downward sparkline; Q5 recently regressed)
        union={Q2,Q3,Q5}  intersection={Q2}  last_n=2={Q2,Q3}
  - incorrect results incl. a CAVEAT case (Q2) and a trace_ready=false
    "generating" case (Q3 in the newest run)
  - traces come from build_fake_trace(correlation_id); one span has an over-long
    body so §6.7 truncation fires when viewed.

Run:  python -m app.seed
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.db import SessionLocal
from app.integrations.fake import NOT_READY_MARKER, build_fake_trace
from app.models import (
    EvalSet,
    EvalSetRole,
    Question,
    QuestionResult,
    QuestionSkill,
    Run,
    SpanAnalysis,
)
from app.services.deletion import delete_eval_set

SET_NAME = "Billing Agent Regression Suite"
OWNER = "alice"
VIEWER = "bob"

# (question_id, question, ground_truth_response, reasoning, skills)
QUESTIONS = [
    ("q_acme001",
     "How much did customer ACME owe at the end of Q2?",
     "ACME owed $42,180 at the end of Q2.",
     "Read the billing skill, query the invoices table via the SQL tool filtered "
     "to ACME and Q2, then sum outstanding balances in the response.",
     ["billing"]),
    ("q_churn02",
     "Summarize the churn drivers for enterprise accounts last quarter. ⟦wrong⟧",
     "Top churn drivers were pricing changes and onboarding friction.",
     "Read the reporting skill, retrieve churn events, aggregate by driver, and "
     "summarize the top drivers. ⟦caveat⟧",
     ["reporting"]),
    ("q_emea03",
     "List the overdue invoices for region EMEA.",
     "Invoices INV-1021, INV-1044, and INV-1102 are overdue in EMEA.",
     "Read the billing skill, query invoices where status=overdue and region=EMEA, "
     "then list the invoice numbers.",
     ["billing"]),
    ("q_march04",
     "Generate the monthly revenue report for March. ⟦timeout⟧",
     "March revenue was $1.24M across 3,910 orders.",
     "Read the reporting skill, aggregate March orders, and produce the revenue "
     "report.",
     ["reporting"]),
    ("q_refund05",
     "What is the refund policy applied to order 8842?",
     "Order 8842 falls under the 30-day full-refund policy.",
     "Read the billing skill, look up order 8842, determine the applicable refund "
     "policy, and state it.",
     ["billing"]),
]

# Verdicts per run (oldest -> newest). True=correct.
# Run1: 0.8  Run2: 0.6  Run3: 0.4
RUN_VERDICTS = [
    {"q_acme001": True,  "q_churn02": False, "q_emea03": True,  "q_march04": True, "q_refund05": True},
    {"q_acme001": True,  "q_churn02": False, "q_emea03": False, "q_march04": True, "q_refund05": True},
    {"q_acme001": True,  "q_churn02": False, "q_emea03": False, "q_march04": True, "q_refund05": False},
]


# What each seeded question "cost": (agent latency in ms, model calls made).
#
# Seeded results are written straight into the database rather than produced by
# the orchestrator, so every figure a real run measures has to be supplied here
# or the demo shows a blank where the product's own screens promise a number —
# which is exactly what happened to these three columns until now.
#
# Deliberately spread, and deliberately containing one outlier. The whole reason
# the left column carries a call count is that two questions taking nine seconds
# are not the same question if one made a single model call and the other made
# eleven; a fake set where every row reads "4 calls" cannot demonstrate the one
# thing the column is for. Same argument the fake trace layer makes for spreading
# span latencies (see `integrations/fake.py`).
QUESTION_COST = {
    "q_acme001": (4_100, 4),
    "q_churn02": (9_400, 5),
    "q_emea03": (3_200, 3),
    # The expensive one: a question that went round the loop far more times than
    # its neighbours and took four times as long doing it.
    "q_march04": (38_600, 14),
    "q_refund05": (2_700, 3),
}


def _suspect_index(correlation_id: str) -> int:
    trace = build_fake_trace(correlation_id)
    return min(3, len(trace.spans) - 1)


def _analysis_payload(correlation_id: str, with_caveat: bool) -> dict:
    trace = build_fake_trace(correlation_id)
    idx = _suspect_index(correlation_id)
    span = trace.spans[idx]
    caveat = None
    if with_caveat:
        caveat = ("The error may not localize to a single span — it looks like a "
                  "compounding issue spanning retrieval and generation, possibly "
                  "outside what the skill controls.")
    return {
        "overall_diagnosis": (
            f"The trace appears to start diverging around span {idx} "
            f"({span.tool_name}); the final answer likely went wrong because that "
            "step returned too little."),
        "suspects": [
            {"span_index": idx, "confidence": "high",
             "reason": (f"Span {idx} ({span.tool_name}) looks incomplete relative to "
                        "the expected flow."),
             "evidence": span.output[:160]},
            {"span_index": max(0, idx - 1), "confidence": "low",
             "reason": "The upstream step may already have dropped data.",
             "evidence": trace.spans[max(0, idx - 1)].output[:120]},
        ],
        "caveat": caveat,
    }


async def seed() -> None:
    async with SessionLocal() as session:
        # Idempotent: remove any prior seed set (cascades to children).
        old_ids = (
            await session.scalars(select(EvalSet.id).where(EvalSet.name == SET_NAME))
        ).all()
        # FK-safe ordering lives in services/deletion.py (question_results ->
        # questions has no ON DELETE CASCADE, so cascade alone is not enough).
        for old_id in old_ids:
            await delete_eval_set(session, old_id)
        await session.commit()

        es = EvalSet(
            name=SET_NAME,
            description="Fake seed set demonstrating Stage 1 flows.",
            source_format="jsonl",
            meta={"team": "billing", "env": "staging"},
        )
        session.add(es)
        await session.flush()

        session.add_all([
            EvalSetRole(eval_set_id=es.id, user_subject=OWNER, role="owner"),
            EvalSetRole(eval_set_id=es.id, user_subject=VIEWER, role="viewer"),
            # carol is pre-shared as a viewer to demo the sharing feature.
            EvalSetRole(eval_set_id=es.id, user_subject="carol", role="viewer"),
        ])

        qmap: dict[str, Question] = {}
        for qid, text, gt, reasoning, skills in QUESTIONS:
            q = Question(
                eval_set_id=es.id, question_id=qid, question=text,
                ground_truth_response=gt, ground_truth_reasoning=reasoning,
            )
            session.add(q)
            await session.flush()
            for ordinal, sk in enumerate(skills):
                session.add(QuestionSkill(question_pk=q.id, skill_name=sk, ordinal=ordinal))
            qmap[qid] = q
        await session.flush()

        base_time = datetime.now(timezone.utc) - timedelta(hours=6)
        for run_no, verdicts in enumerate(RUN_VERDICTS):
            started = base_time + timedelta(hours=2 * run_no)
            correct = sum(1 for v in verdicts.values() if v)
            total = len(verdicts)
            run = Run(
                eval_set_id=es.id, triggered_by=OWNER, status="completed",
                started_at=started, completed_at=started + timedelta(minutes=3),
                total_count=total, correct_count=correct, pass_rate=correct / total,
            )
            session.add(run)
            await session.flush()

            for question_no, (qid, is_correct) in enumerate(verdicts.items()):
                verdict = "correct" if is_correct else "incorrect"
                # Newest run's Q3 demonstrates the trace-not-ready "generating" UI.
                generating = (run_no == len(RUN_VERDICTS) - 1 and qid == "q_emea03")
                # The view path retries the trace store rather than trusting
                # trace_ready, so the marker (not the flag) is what keeps this
                # question stuck in "generating" for the demo.
                cid = f"seed-{NOT_READY_MARKER}-{run_no}-{qid}" if generating \
                    else f"seed-{run_no}-{qid}"
                latency_ms, calls = QUESTION_COST[qid]
                qr = QuestionResult(
                    run_id=run.id, question_pk=qmap[qid].id, correlation_id=cid,
                    verdict=verdict,
                    judge_score=0.92 if is_correct else 0.34,
                    judge_comment=("Matches expected." if is_correct
                                   else "Missing key facts vs expected."),
                    status="done",
                    trace_ready=not generating,
                    # Staggered inside the run's own window, so the left column's
                    # timers settle on real-looking durations instead of showing
                    # nothing at all.
                    started_at=started + timedelta(seconds=20 * question_no),
                    agent_latency_ms=latency_ms,
                    # None for the question whose trace never arrives: the count
                    # is read off the trace, so "we never saw it" has to stay
                    # distinguishable from "it made no calls". The demo shows
                    # both states because the product has both.
                    llm_call_count=None if generating else calls,
                )
                session.add(qr)
                await session.flush()

                if verdict == "incorrect" and not generating:
                    payload = _analysis_payload(cid, with_caveat=(qid == "q_churn02"))
                    session.add(SpanAnalysis(
                        question_result_id=qr.id,
                        overall_diagnosis=payload["overall_diagnosis"],
                        caveat=payload["caveat"],
                        raw_llm_output=payload,
                        model_used="fake-diagnosis-v0",
                    ))
        await session.commit()
        print(f"Seeded eval set {es.id} ({SET_NAME}) with 3 runs.")
        print(f"  owner={OWNER}  viewer={VIEWER}")


if __name__ == "__main__":
    asyncio.run(seed())
