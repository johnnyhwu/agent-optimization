"""Run orchestrator (§6.15).

Runs as a background asyncio task with its own DB session. Per question (from a
snapshot read at run start):

    gen correlation_id -> fake agent (correlation_id in metadata)
      -> fake judge -> write question_results
      -> poll fake trace until ready (backoff) -> set trace_ready
      -> if incorrect: fetch+truncate trace -> diagnose -> write span_analyses
      -> push live progress (SSE)

A failed question is marked status=failed and the run continues (partial
completion). Diagnosis is intentionally gated on trace-ready (§6.12) so we never
store an empty diagnosis against a not-yet-ingested trace.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone

from sqlalchemy import select

from app import fake_config as fc
from app.db import SessionLocal
from app.integrations import agent_client, diagnosis_client, judge_client, trace_client
from app.integrations.base import NotReady
from app.models import Question, QuestionResult, Run, SpanAnalysis
from app.sse import hub


async def _poll_trace_ready(correlation_id: str):
    """Poll the (fake) trace store with backoff until ready or capped."""
    for attempt in range(fc.TRACE_POLL_MAX_ATTEMPTS):
        trace = await trace_client.fetch_trace(correlation_id)
        if not isinstance(trace, NotReady):
            return trace
        idx = min(attempt, len(fc.TRACE_POLL_BACKOFF_S) - 1)
        await asyncio.sleep(fc.TRACE_POLL_BACKOFF_S[idx])
    return None


async def run_eval(run_id: uuid.UUID) -> None:
    async with SessionLocal() as session:
        run = await session.get(Run, run_id)
        if run is None:
            return

        # Snapshot the question set at run start (§6.15) — later edits don't affect
        # this run.
        questions = (
            await session.scalars(
                select(Question).where(Question.eval_set_id == run.eval_set_id)
            )
        ).all()
        # Eager-load reasoning + text now (snapshot values).
        snapshot = [
            {
                "pk": q.id,
                "question": q.question,
                "reasoning": q.ground_truth_reasoning,
                "ground_truth": q.ground_truth_response,
            }
            for q in questions
        ]

        total = len(snapshot)
        correct = 0
        done = 0
        await hub.publish(run_id, {"type": "run_started", "total": total})

        for item in snapshot:
            correlation_id = uuid.uuid4().hex
            result = QuestionResult(
                run_id=run_id,
                question_pk=item["pk"],
                correlation_id=correlation_id,
                status="pending",
                trace_ready=False,
            )
            session.add(result)
            await session.commit()

            # 1) fake agent
            agent_resp = await agent_client.call(item["question"], correlation_id)
            if agent_resp.failed:
                result.status = "failed"
                await session.commit()
                done += 1
                await _publish_progress(run_id, item["pk"], result, done, total, correct)
                continue

            # 2) fake judge
            verdict = await judge_client.judge(agent_resp.response, item["ground_truth"])
            result.verdict = verdict.verdict
            result.judge_score = verdict.score
            result.judge_comment = verdict.comment
            result.status = "done"
            await session.commit()
            if verdict.verdict == "correct":
                correct += 1

            # 3) wait for trace ready (§6.12) before any diagnosis
            trace = await _poll_trace_ready(correlation_id)
            if trace is not None:
                result.trace_ready = True
                await session.commit()

            # 4) diagnose incorrect questions once, at run time (§6.12 cache)
            if verdict.verdict == "incorrect" and trace is not None:
                diag = await diagnosis_client.diagnose(
                    trace, item["reasoning"], verdict
                )
                session.add(
                    SpanAnalysis(
                        question_result_id=result.id,
                        overall_diagnosis=diag["overall_diagnosis"],
                        caveat=diag.get("caveat"),
                        raw_llm_output=diag,
                        model_used=diagnosis_client.MODEL_NAME,
                    )
                )
                await session.commit()

            done += 1
            await _publish_progress(run_id, item["pk"], result, done, total, correct)

        # Finalize aggregates (§6.13 card reads these directly).
        run.status = "completed"
        run.completed_at = datetime.now(timezone.utc)
        run.total_count = total
        run.correct_count = correct
        run.pass_rate = (correct / total) if total else None
        await session.commit()

        await hub.publish(
            run_id,
            {
                "type": "run_completed",
                "total": total,
                "correct": correct,
                "pass_rate": run.pass_rate,
            },
        )


async def _publish_progress(run_id, question_pk, result: QuestionResult,
                            done: int, total: int, correct: int) -> None:
    await hub.publish(
        run_id,
        {
            "type": "question_done",
            "question_pk": str(question_pk),
            "verdict": result.verdict,
            "status": result.status,
            "trace_ready": result.trace_ready,
            "done": done,
            "total": total,
            "correct": correct,
        },
    )
