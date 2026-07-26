"""Integration seams (§6.15 external deps).

Import the concrete clients from here. Stage 1 wires the fake implementations;
swapping to real is a one-file change (point these at real impls in `fake.py`
counterparts) per TASK.md.
"""
from app.integrations.fake import (
    FakeAgentClient,
    FakeDiagnosisClient,
    FakeJudgeClient,
    FakeTraceClient,
)

# The active clients used by the orchestrator / routers. Replace the right-hand
# side with real implementations to go live — nothing else changes.
agent_client = FakeAgentClient()
judge_client = FakeJudgeClient()
trace_client = FakeTraceClient()
diagnosis_client = FakeDiagnosisClient()

__all__ = ["agent_client", "judge_client", "trace_client", "diagnosis_client"]
