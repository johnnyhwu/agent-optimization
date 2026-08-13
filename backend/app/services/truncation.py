"""§6.7 truncation: cut the BODY, never the SPAN.

Keep every span in the trace (root causes are often early, symptoms late). Only a
single span's over-long input/output body is shortened — head + tail kept, middle
elided — so the span skeleton and both ends of the evidence survive.

`truncate_body` is the original, unchanged: the diagnosis path builds its prompt
from `span.input` / `span.output` as text and shortens each independently.

`truncate_trace` below is the structure-aware form the reflect stage needs, and
it is an addition rather than a replacement — the two callers have genuinely
different problems. Diagnosis sends **one** trace and can afford a generous
per-body limit. Reflection sends a **minibatch** of them to a single analyst
call, so the budget is shared, and *which* part of a span gives matters:

  * A **tool call** — name and arguments — is the agent's decision, and the only
    place "it queried the wrong table" is visible at all. The final answer looks
    identical either way. Cutting it turns a diagnosable failure into an
    undiagnosable one.
  * A **tool result** is data. Losing its middle costs the analyst nothing.

Hence one shared implementation with two budgets rather than two implementations:
the elision marker a developer reads in a reflect prompt is the same one they
read in a diagnosis prompt.
"""
from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

from app.config import settings
from app.integrations.base import Span, Trace


def truncate_body(text: str, max_chars: int | None = None) -> tuple[str, bool]:
    """Return (possibly-truncated text, was_truncated)."""
    limit = max_chars if max_chars is not None else settings.span_body_max_chars
    if text is None or len(text) <= limit:
        return text, False
    half = max(1, (limit - 40) // 2)
    head, tail = text[:half], text[-half:]
    elided = len(text) - len(head) - len(tail)
    return f"{head}\n… [{elided} chars truncated] …\n{tail}", True


# --- Structure-aware truncation for the reflect stage -----------------------

# What a cut leaves behind. Small enough to free real room, large enough that
# both ends of a tool result are still readable.
DEFAULT_MIN_KEEP = 400

# Stage order. A cut is only made if the stages before it were not enough, and
# cutting stops the moment the payload is under budget.
STAGE_TOOL_RESULT = 1
STAGE_CONVERSATION = 2
STAGE_ASSISTANT = 3


def trace_chars(trace: Trace) -> int:
    """How much of the analyst's budget this trace occupies."""
    return sum(len(span.input or "") + len(span.output or "") for span in trace.spans)


def allocate_budget(sizes: list[int], total: int) -> list[int]:
    """Split one analyst call's budget across the traces in its minibatch.

    An equal split is only the starting point. Traces vary enormously — one
    fifteen-step trace beside seven three-step ones is the normal case — and a
    strict equal share would mangle the big one down to a stub while the small
    ones left most of their allowance unused. So anything a trace does not need
    is handed back and shared out again, repeatedly, until either every trace fits
    or the remaining ones are all over their share.
    """
    if not sizes:
        return []

    shares = [0] * len(sizes)
    remaining = list(range(len(sizes)))
    pot = total

    while remaining:
        share = pot // len(remaining)
        # Traces that fit inside an equal share take what they need and release
        # the rest. If none do, everyone is over and the split is final.
        settled = [i for i in remaining if sizes[i] <= share]
        if not settled:
            for i in remaining:
                shares[i] = share
            break
        for i in settled:
            shares[i] = sizes[i]
            pot -= sizes[i]
        remaining = [i for i in remaining if i not in set(settled)]

    return shares


def _messages_of(payload: Any) -> list[dict] | None:
    if isinstance(payload, dict) and isinstance(payload.get("messages"), list):
        return payload["messages"]
    return None


def _is_tool_result(message: Any) -> bool:
    return isinstance(message, dict) and message.get("role") == "tool"


def _has_tool_calls(message: Any) -> bool:
    return isinstance(message, dict) and bool(message.get("tool_calls"))


def _slots(trace: Trace, *, cut_final_answer: bool = False) -> list[dict]:
    """Every piece of text that may be cut, with the stage it belongs to.

    What is deliberately absent is the point of the whole module:

      * tool-call names and arguments — the agent's decisions,
      * the **first** system message — it carries the skill being optimised, and
        the content-match activation detector reads the same payload, so cutting
        it would also read as "the agent never loaded the skill",
      * the last span's output — the answer the judge's verdict is about,
      * the span skeleton itself; every span stays in the list (§6.7).

    `cut_final_answer` releases exactly one of those, and only one caller sets
    it. Reflection protects the final answer because it has a better last resort:
    when a minibatch will not fit, it drops a whole item and says so in the
    ledger. Diagnosis has no such move — there is one trace and it has to fit —
    so for it a mangled final answer beats a request that overflows the context
    window and fails. Naming the single genuine difference and defaulting it to
    the safer side is the alternative to keeping two cascades that drift apart.
    """
    slots: list[dict] = []
    last_index = -1 if cut_final_answer else len(trace.spans) - 1

    for position, span in enumerate(trace.spans):
        messages = _messages_of(span.input_json)
        if messages is None:
            # No structure to be aware of. The whole input body is one slot, at
            # the conversation stage: it may hold a tool result, but nothing here
            # can tell which part, so it is not cut before real tool results are.
            if span.input:
                slots.append({
                    "span": position, "field": "input", "stage": STAGE_CONVERSATION,
                    "size": len(span.input),
                })
        else:
            seen_system = False
            for i, message in enumerate(messages):
                if not isinstance(message, dict):
                    continue
                role = message.get("role")
                content = message.get("content")
                if role == "system" and not seen_system:
                    seen_system = True
                    continue  # the skill lives here
                if not isinstance(content, str) or not content:
                    continue
                if _is_tool_result(message):
                    stage = STAGE_TOOL_RESULT
                elif role == "assistant":
                    stage = STAGE_ASSISTANT
                else:
                    stage = STAGE_CONVERSATION
                slots.append({
                    "span": position, "field": f"input.messages[{i}].content",
                    "stage": stage, "size": len(content), "message": i,
                })

        # The output. A tool call is never cut; prose from any span but the last
        # is an intermediate assistant turn.
        if position == last_index or _has_tool_calls(span.output_json):
            continue
        output_content = None
        if isinstance(span.output_json, dict):
            output_content = span.output_json.get("content")
        if isinstance(output_content, str) and output_content:
            slots.append({
                "span": position, "field": "output.content",
                "stage": STAGE_ASSISTANT, "size": len(output_content),
            })
        elif span.output_json is None and span.output:
            slots.append({
                "span": position, "field": "output",
                "stage": STAGE_ASSISTANT, "size": len(span.output),
            })

    return slots


def _dump(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False)


def _apply(spans: list[Span], slot: dict, min_keep: int) -> int:
    """Cut one slot in place. Returns the number of characters saved."""
    position = slot["span"]
    span = spans[position]
    before_total = len(span.input or "") + len(span.output or "")

    if slot["field"] == "input":
        text, _ = truncate_body(span.input, min_keep)
        spans[position] = replace(span, input=text)
    elif slot["field"] == "output":
        text, _ = truncate_body(span.output, min_keep)
        spans[position] = replace(span, output=text)
    elif slot["field"].startswith("input.messages"):
        payload = json.loads(_dump(span.input_json))  # a copy; never mutate the caller's
        message = payload["messages"][slot["message"]]
        message["content"], _ = truncate_body(message["content"], min_keep)
        spans[position] = replace(span, input_json=payload, input=_dump(payload))
    elif slot["field"] == "output.content":
        payload = json.loads(_dump(span.output_json))
        payload["content"], _ = truncate_body(payload["content"], min_keep)
        spans[position] = replace(span, output_json=payload, output=_dump(payload))

    after = spans[position]
    return before_total - (len(after.input or "") + len(after.output or ""))


def truncate_trace(
    trace: Trace, budget_chars: int, *, min_keep: int = DEFAULT_MIN_KEEP,
    cut_final_answer: bool = False,
) -> tuple[Trace, list[dict]]:
    """Fit one trace into `budget_chars`, cutting as little as possible.

    Measure first: a trace that already fits is returned character for
    character. Most do, and a cascade that ran unconditionally would put elision
    markers into every reflect prompt for no reason — leaving the analyst
    reasoning about mutilated evidence and a developer unable to tell a genuine
    truncation from routine formatting.

    When it does not fit, cuts are made one slot at a time, cheapest stage first
    and largest slot first within a stage, re-measuring after each. The first cut
    that brings the payload under budget is the last one made.

    Returns the new trace and a ledger of what was cut — which Part 1 renders,
    because "why did the optimizer propose that?" is otherwise unanswerable when
    the prompt on screen looks complete.
    """
    if trace_chars(trace) <= budget_chars:
        return trace, []

    spans = list(trace.spans)
    ledger: list[dict] = []
    # Stage ascending, then biggest first: cutting the largest slot frees the
    # most room per cut, so fewer slots are touched overall.
    pending = sorted(
        _slots(trace, cut_final_answer=cut_final_answer),
        key=lambda s: (s["stage"], -s["size"]),
    )

    for slot in pending:
        if trace_chars(Trace(trace.correlation_id, spans)) <= budget_chars:
            break
        if slot["size"] <= min_keep:
            continue  # already smaller than what a cut would leave
        before = slot["size"]
        saved = _apply(spans, slot, min_keep)
        if saved <= 0:
            continue
        ledger.append({
            "span_index": spans[slot["span"]].index,
            "field": slot["field"],
            "stage": slot["stage"],
            "before": before,
            "after": before - saved,
        })

    return Trace(trace.correlation_id, spans), ledger
