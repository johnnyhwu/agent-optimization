"""One trace, as one conversation — which is what the analyst has to read.

A Langfuse trace is a list of observations, and for an LLM agent every one of
them is a *whole* chat-completions request: the tool catalogue, the system
prompt, and every message so far. That is the right thing for a trace store to
hold — each span is a self-contained record of one call — and the worst possible
thing to concatenate. Rendered span by span, a fifteen-step trajectory repeats
the tool catalogue fifteen times, repeats the system prompt (which carries the
whole skill) fifteen times, and repeats message *k* fifteen minus *k* times. The
prompt is quadratic in the length of a trajectory and linear in the size of a
skill, and a minibatch multiplies the whole thing by eight.

The old truncation cascade could not save it, either: it deliberately refuses to
cut a span's first system message, because that is where the skill lives — so
with N spans there were N uncuttable copies, and the budget was arithmetically
unreachable. Every analyst call went out oversized, and the first thing anyone
saw was the model refusing it.

So the repetition is removed *before* anything is measured. Spans are folded
back into the single conversation they were snapshots of: the tools once, the
system prompt once, each message once, in the order they happened. That is also
exactly what the Evaluation page shows when a developer opens the last step of a
trace, which is the point — the analyst should read what a human reviewing the
same failure would read.

Only then is the budget applied, and now it can actually be met.

The message dialects (OpenAI `tool_calls`, the older `function_call`, Anthropic
content parts) are the ones `frontend/src/span_label.js` and
`frontend/src/components/SpanPayload.jsx` already parse for the trace viewer.
Same rules, same fallbacks: recognise, never require.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from typing import Any

from app.integrations.base import Trace
from app.services.truncation import truncate_body

# Stage order for cutting, cheapest evidence first. Mirrors
# `app/services/truncation.py` — a tool result is data and loses nothing by
# having its middle elided, a tool *call* is the agent's decision and is never
# cut at all.
STAGE_TOOL_RESULT = 1
STAGE_CONVERSATION = 2
STAGE_ASSISTANT = 3


@dataclass
class ToolCall:
    """One tool invocation, in whichever dialect the agent logged it."""

    name: str
    args: str = ""
    id: str | None = None


@dataclass
class Turn:
    """One message in the folded conversation.

    `span_index` is the span this turn was first seen in. It is not used to
    render anything — it is what lets the truncation ledger point at a step the
    developer can open in the trace viewer.
    """

    role: str
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    name: str | None = None
    span_index: int = 0

    @property
    def stage(self) -> int:
        if self.role == "tool":
            return STAGE_TOOL_RESULT
        if self.role == "assistant":
            return STAGE_ASSISTANT
        return STAGE_CONVERSATION


@dataclass
class Trajectory:
    """A whole agent run as one readable conversation."""

    tools: list[dict] = field(default_factory=list)
    system_prompt: str = ""
    turns: list[Turn] = field(default_factory=list)


# --- reading one message ----------------------------------------------------


def _is_obj(value: Any) -> bool:
    return isinstance(value, dict)


def _pretty(value: Any) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, indent=2)
    except (TypeError, ValueError):
        return str(value)


def _part_to_text(part: Any) -> str:
    """One content part as text. Tool-use parts render as "" — `tool_calls_of`
    already reports them, and rendering both would print each call twice."""
    if isinstance(part, str):
        return part
    if not _is_obj(part):
        return _pretty(part)
    kind = part.get("type")
    if kind == "tool_use":
        return ""
    if isinstance(part.get("text"), str):
        return part["text"]
    if kind == "image_url":
        url = part.get("image_url") or {}
        return f"[image] {url.get('url', '') if _is_obj(url) else ''}"
    if kind == "image":
        return "[image]"
    if kind == "tool_result":
        return f"[tool result {part.get('tool_use_id', '')}]\n{content_to_text(part.get('content'))}"
    return _pretty(part)


def content_to_text(content: Any) -> str:
    """A message body as text, in any of the dialects we see."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [_part_to_text(p) for p in content]
        return "\n\n".join(p for p in parts if p)
    return _pretty(content)


def tool_calls_of(message: Any) -> list[ToolCall]:
    """Tool calls in whichever dialect the agent logged them."""
    calls: list[ToolCall] = []
    if not _is_obj(message):
        return calls

    raw_calls = message.get("tool_calls")
    if isinstance(raw_calls, list):
        for call in raw_calls:
            if not _is_obj(call):
                continue
            fn = call.get("function") if _is_obj(call.get("function")) else {}
            calls.append(ToolCall(
                id=call.get("id"),
                name=fn.get("name") or call.get("name") or "tool",
                args=_arguments_text(fn.get("arguments", call.get("arguments", call.get("input")))),
            ))

    # The pre-`tool_calls` OpenAI shape. Still logged by older agent SDKs.
    legacy = message.get("function_call")
    if _is_obj(legacy):
        calls.append(ToolCall(
            name=legacy.get("name") or "tool",
            args=_arguments_text(legacy.get("arguments")),
        ))

    # Anthropic puts tool use inside the content array.
    content = message.get("content")
    if isinstance(content, list):
        for part in content:
            if _is_obj(part) and part.get("type") == "tool_use":
                calls.append(ToolCall(
                    id=part.get("id"),
                    name=part.get("name") or "tool",
                    args=_arguments_text(part.get("input")),
                ))
    return calls


def _arguments_text(value: Any) -> str:
    """Tool arguments as text. OpenAI serializes them to a JSON *string*, which
    is re-indented so the analyst reads arguments rather than one long line."""
    if value is None:
        return ""
    if isinstance(value, str):
        try:
            return json.dumps(json.loads(value), ensure_ascii=False, indent=2)
        except ValueError:
            return value  # not JSON after all — show what was actually logged
    return _pretty(value)


def _turn_from_message(message: Any, span_index: int) -> Turn | None:
    if not _is_obj(message) or not isinstance(message.get("role"), str):
        return None
    turn = Turn(
        role=message["role"].lower(),
        text=content_to_text(message.get("content")),
        tool_calls=tool_calls_of(message),
        name=message.get("name") or message.get("tool_call_id"),
        span_index=span_index,
    )
    if not turn.text and not turn.tool_calls:
        return None
    return turn


def _identity(turn: Turn) -> tuple:
    """What makes two turns the same turn seen in two spans.

    `span_index` is deliberately absent: the *point* is to recognise a message
    that span 5 is repeating from span 1.
    """
    return (
        turn.role,
        turn.name,
        turn.text,
        tuple((c.name, c.args) for c in turn.tool_calls),
    )


# --- folding a trace into one conversation ----------------------------------


def _messages_of(payload: Any) -> list | None:
    if _is_obj(payload) and isinstance(payload.get("messages"), list):
        return payload["messages"]
    if isinstance(payload, list):
        return payload
    return None


def _output_messages(payload: Any) -> list:
    """The assistant message(s) a span produced, in any shape we've seen."""
    if _is_obj(payload):
        choices = payload.get("choices")
        if isinstance(choices, list):
            return [c["message"] for c in choices if _is_obj(c) and _is_obj(c.get("message"))]
        if isinstance(payload.get("role"), str) or "content" in payload or "tool_calls" in payload:
            return [payload if isinstance(payload.get("role"), str) else {"role": "assistant", **payload}]
    return []


def build_trajectory(trace: Trace | None) -> Trajectory:
    """Fold a trace's spans back into the one conversation they snapshot.

    Each span carries the whole history up to its own call, so walking them in
    order and keeping only what has not been seen reconstructs the run exactly
    once. Turns are matched by content rather than by position: an agent that
    re-sends a summarised history, or runs two sessions inside one trace, must
    not have its later messages dropped just because the list got shorter.
    """
    traj = Trajectory()
    if trace is None:
        return traj

    seen: set[tuple] = set()

    def add(turn: Turn | None) -> None:
        if turn is None:
            return
        key = _identity(turn)
        if key in seen:
            return
        seen.add(key)
        traj.turns.append(turn)

    for span in trace.spans:
        payload = span.input_json
        if _is_obj(payload) and isinstance(payload.get("tools"), list) and not traj.tools:
            traj.tools = [t for t in payload["tools"] if _is_obj(t)]

        messages = _messages_of(payload)
        if messages is None:
            # No structure to fold. The span is still evidence — a tool span
            # logged as prose is exactly the kind of step that explains a
            # failure — so it is kept as one turn of its own.
            if span.input or span.output:
                body = "\n".join(part for part in (span.input, span.output) if part)
                add(Turn(role="span", text=body, name=span.tool_name, span_index=span.index))
            continue

        for message in messages:
            if _is_obj(message) and str(message.get("role", "")).lower() == "system":
                # Kept once. This is where the skill lives, and repeating it per
                # span is the whole reason this module exists.
                if not traj.system_prompt:
                    traj.system_prompt = content_to_text(message.get("content"))
                continue
            add(_turn_from_message(message, span.index))

        for message in _output_messages(span.output_json):
            add(_turn_from_message(message, span.index))
        if span.output_json is None and span.output:
            add(Turn(role="assistant", text=span.output, span_index=span.index))

    return traj


# --- rendering --------------------------------------------------------------


def _render_tools(tools: list[dict]) -> str:
    lines = [f"#### Tools Available ({len(tools)})"]
    for i, tool in enumerate(tools):
        fn = tool.get("function") if _is_obj(tool.get("function")) else tool
        name = fn.get("name") or f"tool {i}"
        description = str(fn.get("description") or "").strip()
        schema = fn.get("parameters", fn.get("input_schema"))
        lines.append(f"- **{name}** — {description}" if description else f"- **{name}**")
        if schema is not None:
            lines.append(f"  schema: {json.dumps(schema, ensure_ascii=False)}")
    return "\n".join(lines)


def _render_turn(turn: Turn, ordinal: int) -> str:
    label = turn.role if not turn.name else f"{turn.role} {turn.name}"
    out = [f"[{ordinal} · {label}]"]
    if turn.text:
        out.append(turn.text)
    for call in turn.tool_calls:
        out.append(f"    → tool_call {call.name}({call.args})")
    return "\n".join(out)


def render_preamble(traj: Trajectory) -> str:
    """The setup every turn happened under: the tool catalogue and the system
    prompt. Separable from the conversation because a whole minibatch usually
    shares one — see `shared_preamble`."""
    blocks: list[str] = []
    if traj.tools:
        blocks.append(_render_tools(traj.tools))
    if traj.system_prompt:
        blocks.append(f"#### System Prompt\n{traj.system_prompt}")
    return "\n\n".join(blocks)


def render_conversation(traj: Trajectory) -> str:
    if not traj.turns:
        return ""
    turns = "\n\n".join(_render_turn(turn, i) for i, turn in enumerate(traj.turns, 1))
    return f"#### Conversation ({len(traj.turns)} turns)\n{turns}"


def render_trajectory(traj: Trajectory, *, include_preamble: bool = True) -> str:
    """The run as the analyst reads it: each thing said exactly once.

    `include_preamble=False` is for the case where the batch shares one and it
    has already been printed above all of them.
    """
    blocks = []
    if include_preamble:
        blocks.append(render_preamble(traj))
    blocks.append(render_conversation(traj))
    return "\n\n".join(b for b in blocks if b)


def preamble_chars(traj: Trajectory) -> int:
    """The size of the shared setup: system prompt plus tool catalogue."""
    total = len(traj.system_prompt)
    for tool in traj.tools:
        total += len(json.dumps(tool, ensure_ascii=False))
    return total


def conversation_chars(traj: Trajectory) -> int:
    """The size of what actually varies between one run and another."""
    total = 0
    for turn in traj.turns:
        total += len(turn.text) + len(turn.role)
        total += sum(len(call.name) + len(call.args) for call in turn.tool_calls)
    return total


def trajectory_chars(traj: Trajectory) -> int:
    """Everything this trajectory would occupy if it were rendered alone.

    Summed rather than rendered because the truncation loop asks after every
    cut, and re-serialising a whole trajectory each time to learn its length is
    work for nothing.
    """
    return preamble_chars(traj) + conversation_chars(traj)


def shared_preamble(trajectories: list[Trajectory]) -> Trajectory | None:
    """The setup, if every run in the batch had the same one — else `None`.

    They normally did. A minibatch is several questions answered by the same
    agent under the same candidate skill, so the tool catalogue is identical and
    the system prompt is identical — and the system prompt is where the skill
    lives, which for a real deployment is thousands of tokens. Printed per
    trajectory, an eight-question batch spends eight copies of it before a
    single tool call is shown; printed once, it costs what it is.

    The check is exact equality rather than an assumption, because "the agent
    was told something different on this one" is itself a finding, and hoisting
    would erase it. One trajectory is not a batch, so there is nothing to hoist.
    """
    if len(trajectories) < 2:
        return None
    first = trajectories[0]
    if not first.system_prompt and not first.tools:
        return None
    for other in trajectories[1:]:
        if other.system_prompt != first.system_prompt or other.tools != first.tools:
            return None
    return Trajectory(tools=first.tools, system_prompt=first.system_prompt)


# --- truncation -------------------------------------------------------------


def truncate_trajectory(
    traj: Trajectory, budget_chars: int, *, min_keep: int = 400,
) -> tuple[Trajectory, list[dict]]:
    """Fit one trajectory's **conversation** into `budget_chars`, cutting as
    little as possible.

    The budget is over the conversation alone, because the preamble is not
    cuttable and counting it here would only mean cutting the conversation to
    make room for something no cut can reach. The caller reserves the preamble
    separately — once for the whole batch when they share one.

    Measure first: a conversation that already fits is returned exactly as it
    came, so an elision marker in a prompt always means something was genuinely
    cut.

    When it does not fit, turns are cut cheapest-stage-first and largest-first
    within a stage, re-measuring after each. Never cut:

      * the system prompt — it carries the skill being optimised, and the
        content-match activation detector reads the same text,
      * any tool call's name or arguments — "it queried the wrong table" is
        visible nowhere else,
      * the last turn — the answer the judge's verdict is about.

    Returns the trimmed trajectory and a ledger of what was cut, in the same
    shape `app/services/truncation.py` produces, so the UI reads one format.
    """
    if conversation_chars(traj) <= budget_chars:
        return traj, []

    turns = list(traj.turns)
    ledger: list[dict] = []
    last = len(turns) - 1
    candidates = sorted(
        (i for i in range(len(turns)) if i != last and turns[i].text),
        key=lambda i: (turns[i].stage, -len(turns[i].text)),
    )

    for index in candidates:
        if conversation_chars(replace(traj, turns=turns)) <= budget_chars:
            break
        turn = turns[index]
        before = len(turn.text)
        if before <= min_keep:
            continue  # already smaller than what a cut would leave
        text, _ = truncate_body(turn.text, min_keep)
        turns[index] = replace(turn, text=text)
        ledger.append({
            "span_index": turn.span_index,
            "field": f"turn[{index}].{turn.role}",
            "stage": turn.stage,
            "before": before,
            "after": len(text),
        })

    return replace(traj, turns=turns), ledger
