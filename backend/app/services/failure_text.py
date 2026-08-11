"""What to write in `error_message` when a step fails.

This exists because of one measured fact: `str(exc)` is the empty string for
both of the exceptions a timed-out agent call actually raises —
`httpx.ReadTimeout` and `asyncio.TimeoutError`. The orchestrator's
`f"Agent call failed: {exc!s}"` therefore stored the literal text
``"Agent call failed: "`` and nothing else, for the single most common way a run
goes wrong. A developer reading that could not tell which step gave up, what
limit it hit, or how long it waited — the three things they need to decide
whether to raise the timeout or go and look at their agent.

So there are two rules here:

**A timeout says what timed out, at what limit, and how long it really took.**
The "how long" is measured wall clock rather than the configured limit, because
those are not the same number: each step is retried, so a 500s limit means a
question can be out for a quarter of an hour before it is called failed. Printing
only the limit would understate that by a factor of three and leave the developer
looking for a second, longer timeout that does not exist.

**No failure is ever recorded as a blank line.** When an exception has nothing to
say for itself, its type name is used — `httpx.ConnectError` is not a great
message, but it names the problem, and it is the difference between a developer
searching for a cause and a developer wondering whether the platform is broken.

Pure functions over plain values, in the style of `aggregation.py`: no database,
no event loop, no settings lookups, so every branch is directly testable.
"""
from __future__ import annotations

# Everything that means "the other side did not answer in time". Built as a tuple
# rather than checked by name because each library raises its own type and none
# of them share a base: httpx has its own hierarchy under `HTTPError`, and the
# OpenAI SDK's timeout is a subclass of its connection error. The imports are
# not guarded — both are hard dependencies — but they are done here, once, so
# callers do not have to know which client a seam happens to use.
import httpx
import openai

# `asyncio.TimeoutError` is the builtin `TimeoutError` from 3.11 on, so this one
# entry covers the `asyncio.wait_for` in `pipeline.call_agent` as well.
TIMEOUT_TYPES: tuple[type[BaseException], ...] = (
    TimeoutError,
    httpx.TimeoutException,
    openai.APITimeoutError,
)

# What each step is called in the interface. The agent server and the grading
# model are things the developer configured and can go and look at; `HttpAgentClient`
# and `LlmJudgeClient` are things they have never heard of.
STEP_SUBJECTS = {
    "agent": "The agent",
    "judge": "The grading model",
}
# The prefix on a non-timeout failure, unchanged from what these two paths have
# always written — an existing message that already reads well is not worth
# rewording just because the code around it moved.
STEP_PREFIXES = {
    "agent": "Agent call failed",
    "judge": "Judge call failed",
}


def is_timeout(exc: BaseException) -> bool:
    """Did this exception mean "no answer in time"?"""
    return isinstance(exc, TIMEOUT_TYPES)


def humanize_duration(seconds: float) -> str:
    """A duration a person reads at a glance: `45s`, `2m 5s`, `1h 3m`.

    Rounded, and never more than two units. The question being answered is "did
    this take about as long as I set it to?", and a third decimal place is in the
    way of that.
    """
    total = max(int(round(seconds)), 0)
    if total < 60:
        return f"{total}s"
    minutes, secs = divmod(total, 60)
    if minutes < 60:
        return f"{minutes}m {secs}s" if secs else f"{minutes}m"
    hours, mins = divmod(minutes, 60)
    return f"{hours}h {mins}m" if mins else f"{hours}h"


def reason(exc: BaseException) -> str:
    """What the exception says, or what it is when it says nothing."""
    text = str(exc).strip()
    return text or type(exc).__name__


def describe_failure(
    step: str,
    exc: BaseException,
    *,
    timeout_s: float | None = None,
    attempts: int = 1,
    waited_s: float | None = None,
) -> tuple[str, str]:
    """`(error_message, failure_kind)` for one failed step.

    `step` is `'agent'` or `'judge'`. `attempts` is the total number of tries the
    retry policy made, not the number of retries — "tried 3 times" is what a
    developer can compare against the elapsed time they watched.
    """
    if is_timeout(exc):
        subject = STEP_SUBJECTS.get(step, f"The {step}")
        limit = f" within {humanize_duration(timeout_s)}" if timeout_s else ""
        parts = [f"{subject} did not answer{limit}."]
        if attempts > 1:
            parts.append(f"Tried {attempts} times.")
        if waited_s is not None:
            parts.append(f"Gave up after {humanize_duration(waited_s)}.")
        return " ".join(parts), f"{step}_timeout"

    prefix = STEP_PREFIXES.get(step, f"{step.capitalize()} call failed")
    return f"{prefix}: {reason(exc)}", step
