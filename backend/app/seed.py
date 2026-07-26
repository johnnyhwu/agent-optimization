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

from sqlalchemy import delete, select

from app.db import SessionLocal
from app.integrations.fake import build_fake_trace
from app.models import (
    EvalSet,
    EvalSetRole,
    Question,
    QuestionResult,
    QuestionSkill,
    Run,
    SpanAnalysis,
)

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
        if old_ids:
            # question_results.question_pk -> questions.id has no ON DELETE CASCADE
            # (the app never deletes questions from a locked set), so clean up
            # children explicitly in FK-safe order rather than relying on cascade.
            run_ids = (
                await session.scalars(select(Run.id).where(Run.eval_set_id.in_(old_ids)))
            ).all()
            q_ids = (
                await session.scalars(select(Question.id).where(Question.eval_set_id.in_(old_ids)))
            ).all()
            if run_ids:
                qr_ids = (
                    await session.scalars(
                        select(QuestionResult.id).where(QuestionResult.run_id.in_(run_ids))
                    )
                ).all()
                if qr_ids:
                    await session.execute(
                        delete(SpanAnalysis).where(SpanAnalysis.question_result_id.in_(qr_ids))
                    )
                await session.execute(
                    delete(QuestionResult).where(QuestionResult.run_id.in_(run_ids))
                )
            if q_ids:
                await session.execute(
                    delete(QuestionSkill).where(QuestionSkill.question_pk.in_(q_ids))
                )
            await session.execute(delete(Run).where(Run.eval_set_id.in_(old_ids)))
            await session.execute(delete(Question).where(Question.eval_set_id.in_(old_ids)))
            await session.execute(delete(EvalSetRole).where(EvalSetRole.eval_set_id.in_(old_ids)))
            await session.execute(delete(EvalSet).where(EvalSet.id.in_(old_ids)))
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

            for qid, is_correct in verdicts.items():
                cid = f"seed-{run_no}-{qid}"
                verdict = "correct" if is_correct else "incorrect"
                # Newest run's Q3 demonstrates the trace-not-ready "generating" UI.
                generating = (run_no == len(RUN_VERDICTS) - 1 and qid == "q_emea03")
                qr = QuestionResult(
                    run_id=run.id, question_pk=qmap[qid].id, correlation_id=cid,
                    verdict=verdict,
                    judge_score=0.92 if is_correct else 0.34,
                    judge_comment=("Matches expected." if is_correct
                                   else "Missing key facts vs expected."),
                    status="done",
                    trace_ready=not generating,
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
