"""THE single latency / fake-timing config file (TASK.md hard requirement).

Every simulated-latency value for the fake integration layer lives here and
nowhere else. Tune the whole demo's feel from this one file.
"""
from __future__ import annotations

# --- AgentClient.call : fake A2A agent round-trip (1-3s) ---
AGENT_LATENCY_MIN_S: float = 1.0
AGENT_LATENCY_MAX_S: float = 3.0

# --- JudgeClient.judge : fake LLM-as-judge (0.5-1s) ---
JUDGE_LATENCY_MIN_S: float = 0.5
JUDGE_LATENCY_MAX_S: float = 1.0

# --- DiagnosisClient.diagnose : fake LLM diagnosis (2-4s) ---
DIAGNOSIS_LATENCY_MIN_S: float = 2.0
DIAGNOSIS_LATENCY_MAX_S: float = 4.0

# --- TraceClient.fetch_trace : fake Langfuse async ingestion (§6.12) ---
# First N polls return NotReady, then the trace is available. Exercises the
# poll + exponential-backoff path and the UI "generating/retrying" state.
TRACE_NOT_READY_POLLS: int = 2
# Per-poll network latency for a single fetch_trace call.
TRACE_FETCH_LATENCY_S: float = 0.2
# Orchestrator backoff schedule (seconds) between trace-ready polls. The list is
# consumed in order; the last value repeats if more polls are needed.
TRACE_POLL_BACKOFF_S: list[float] = [0.5, 1.0, 2.0, 4.0]
# Safety cap so a never-ready trace can't stall a run forever (partial completion).
TRACE_POLL_MAX_ATTEMPTS: int = 8
