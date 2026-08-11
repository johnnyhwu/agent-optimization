"""What a failed step writes into `error_message`.

The bug these exist for: `str(exc)` is the empty string for both exceptions a
timed-out agent call actually raises, so the stored message was the prefix and
nothing else. Every test here asserts the message is *informative*, not merely
non-empty.
"""
from __future__ import annotations

import asyncio

import httpx
import openai
import pytest

from app.services.failure_text import (
    describe_failure,
    humanize_duration,
    is_timeout,
    reason,
)


# --- what counts as a timeout ----------------------------------------------

@pytest.mark.parametrize(
    "exc",
    [
        # What `asyncio.wait_for` raises in pipeline.call_agent. On 3.11+ this is
        # the builtin TimeoutError, which is what the tuple actually matches.
        asyncio.TimeoutError(),
        TimeoutError(),
        # What httpx raises when the agent server accepts the request and then
        # never finishes answering — measured to be the one that fires first.
        httpx.ReadTimeout("", request=None),
        httpx.ConnectTimeout("", request=None),
        # The judge and the diagnosis both go through the OpenAI SDK.
        openai.APITimeoutError(request=httpx.Request("POST", "http://x")),
    ],
)
def test_timeouts_are_recognised(exc):
    assert is_timeout(exc)


@pytest.mark.parametrize(
    "exc",
    [
        httpx.ConnectError("connection refused"),
        ConnectionResetError("reset"),
        RuntimeError("nope"),
        ValueError("bad"),
    ],
)
def test_other_failures_are_not_timeouts(exc):
    assert not is_timeout(exc)


# --- the timeout sentence ---------------------------------------------------

def test_agent_timeout_names_the_step_the_limit_and_the_wait():
    message, kind = describe_failure(
        "agent", asyncio.TimeoutError(), timeout_s=500, attempts=3, waited_s=1503
    )

    assert kind == "agent_timeout"
    # The three questions a developer has: what gave up, at what limit, and how
    # long it really took — the last being ~3x the limit because of retries.
    assert "The agent" in message
    assert "8m 20s" in message, "the configured 500s limit, in readable form"
    assert "3 times" in message
    assert "25m 3s" in message, "the measured wall clock, which is not the limit"


def test_judge_timeout_names_the_grading_model():
    message, kind = describe_failure(
        "judge", openai.APITimeoutError(request=httpx.Request("POST", "http://x")),
        timeout_s=120, attempts=3, waited_s=366,
    )

    assert kind == "judge_timeout"
    # Not "LlmJudgeClient", and not "agent" — a judge timeout being mistaken for
    # an agent timeout is exactly the confusion this whole change is about.
    assert "The grading model" in message
    assert "2m" in message


def test_timeout_without_a_known_limit_still_says_what_happened():
    message, kind = describe_failure("agent", TimeoutError(), attempts=1)

    assert kind == "agent_timeout"
    assert message == "The agent did not answer."


# --- the blank-message rule -------------------------------------------------

def test_a_blank_exception_is_reported_by_type():
    # The original bug, in one assertion: httpx.ReadTimeout has no message, and
    # the old code stored "Agent call failed: " for it.
    assert str(httpx.ReadTimeout("", request=None)) == ""
    assert reason(httpx.ReadTimeout("", request=None)) == "ReadTimeout"


def test_non_timeout_failure_keeps_its_prefix_and_gains_a_reason():
    message, kind = describe_failure("agent", httpx.ConnectError("connection refused"))

    assert kind == "agent"
    assert message == "Agent call failed: connection refused"


def test_non_timeout_failure_with_no_message_falls_back_to_the_type():
    class Silent(RuntimeError):
        pass

    message, kind = describe_failure("judge", Silent())

    assert kind == "judge"
    assert message == "Judge call failed: Silent"
    assert not message.endswith(": "), "never store a message that trails off"


# --- durations --------------------------------------------------------------

@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (0, "0s"),
        (0.4, "0s"),
        (45, "45s"),
        (59.6, "1m"),
        (60, "1m"),
        (125, "2m 5s"),
        (1503, "25m 3s"),
        (3600, "1h"),
        (3780, "1h 3m"),
        (-5, "0s"),
    ],
)
def test_humanize_duration(seconds, expected):
    assert humanize_duration(seconds) == expected
