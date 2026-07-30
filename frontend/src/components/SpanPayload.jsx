import React, { useState } from "react";

// Rendering for a span's input/output body (right column).
//
// Langfuse stores whatever the agent's SDK handed it, so there is no schema to
// validate against — but for an LLM generation the shape is in practice the
// chat-completions request/response: `{tools, messages}` going in, an assistant
// message coming back. That is the thing a developer opens a span to read, and
// dumping it as JSON buried the answer to "what did the model actually see?".
//
// Two rules govern everything below:
//
//   1. **Recognise, never require.** Every branch falls back to pretty-printed
//      JSON. An unfamiliar payload must still render, not throw.
//   2. **Collapse, never cut.** The body arrives whole (the view path stopped
//      truncating; §6.7's cut now only guards the diagnosis LLM's context
//      window). Long content hides behind a disclosure and scrolls inside its
//      own box — the evidence is always one click away, never gone.
//
// The `Pretty | JSON` toggle is the safety net for rule 1: anything this
// renderer doesn't know about is still readable verbatim.

const CHAT_ROLES = ["system", "developer", "user", "assistant", "tool", "function"];
const PREVIEW_CHARS = 90;

const isObj = (v) => v !== null && typeof v === "object" && !Array.isArray(v);

function pretty(value) {
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

/** A JSON string (OpenAI serializes tool-call arguments) re-indented for reading. */
function prettyMaybeJson(value) {
  if (typeof value !== "string") return pretty(value);
  try {
    return JSON.stringify(JSON.parse(value), null, 2);
  } catch {
    return value; // not JSON after all — show what was actually logged
  }
}

// `content` is a plain string in the OpenAI shape and an array of parts in the
// Anthropic one (and in OpenAI's multimodal messages). Tool-use parts return ""
// here because `toolCallsOf` renders them separately — otherwise they'd appear
// twice.
function partToText(part) {
  if (typeof part === "string") return part;
  if (!isObj(part)) return pretty(part);
  if (part.type === "tool_use") return "";
  if (typeof part.text === "string") return part.text;
  if (part.type === "image_url") return `[image] ${part.image_url?.url ?? ""}`;
  if (part.type === "image") return "[image]";
  if (part.type === "tool_result")
    return `[tool result ${part.tool_use_id ?? ""}]\n${contentToText(part.content)}`;
  return pretty(part);
}

function contentToText(content) {
  if (content == null) return "";
  if (typeof content === "string") return content;
  if (Array.isArray(content)) return content.map(partToText).filter(Boolean).join("\n\n");
  return pretty(content);
}

/** Tool calls in whichever dialect the agent logged them. */
function toolCallsOf(msg) {
  const calls = [];
  if (Array.isArray(msg?.tool_calls)) {
    for (const c of msg.tool_calls) {
      calls.push({
        id: c?.id,
        name: c?.function?.name ?? c?.name ?? "tool",
        args: c?.function?.arguments ?? c?.arguments ?? c?.input,
      });
    }
  }
  if (isObj(msg?.function_call)) {
    calls.push({ name: msg.function_call.name ?? "tool", args: msg.function_call.arguments });
  }
  if (Array.isArray(msg?.content)) {
    for (const p of msg.content) {
      if (isObj(p) && p.type === "tool_use") calls.push({ id: p.id, name: p.name, args: p.input });
    }
  }
  return calls;
}

const looksLikeMessage = (v) => isObj(v) && typeof v.role === "string";

function firstLine(text, limit = PREVIEW_CHARS) {
  const flat = String(text).replace(/\s+/g, " ").trim();
  return flat.length > limit ? `${flat.slice(0, limit)}…` : flat;
}

// --- building blocks --------------------------------------------------------

// Native <details>: keyboard support, find-in-page and the browser's own
// disclosure semantics for free. `open` is a defaultOpen here — React only
// writes the attribute when the prop changes, so a section the developer opened
// by hand stays open across re-renders (the same rule the middle column follows
// for a hand-picked span).
export function Collapsible({ title, meta, open = false, className = "", children }) {
  return (
    <details className={`collapse ${className}`} open={open}>
      <summary>
        <span className="collapse-title">{title}</span>
        {meta != null && <span className="collapse-meta">{meta}</span>}
      </summary>
      <div className="collapse-body">{children}</div>
    </details>
  );
}

function ToolCall({ call }) {
  return (
    <div className="toolcall">
      <div className="toolcall-head">
        <span className="toolcall-name">{call.name}</span>
        {call.id && <span className="collapse-meta">{call.id}</span>}
      </div>
      <pre className="body">{prettyMaybeJson(call.args ?? "")}</pre>
    </div>
  );
}

function Message({ msg, open }) {
  const role = CHAT_ROLES.includes(String(msg?.role).toLowerCase())
    ? String(msg.role).toLowerCase()
    : "other";
  const calls = toolCallsOf(msg);
  const text = contentToText(msg?.content);
  // What this turn was *for*, in one line: the tools it asked for, or the start
  // of what it said.
  const preview = calls.length
    ? `→ ${calls.map((c) => `${c.name}()`).join(", ")}`
    : firstLine(text) || "(no content)";
  const meta = msg?.name || msg?.tool_call_id || (text ? `${text.length} chars` : null);

  return (
    <Collapsible
      className="msg"
      open={open}
      title={
        <>
          <span className={`role ${role}`}>{msg?.role ?? "?"}</span>
          <span className="msg-preview">{preview}</span>
        </>
      }
      meta={meta}
    >
      {text && <pre className="body">{text}</pre>}
      {calls.map((c, i) => (
        <ToolCall key={c.id ?? i} call={c} />
      ))}
      {!text && !calls.length && <div className="payload-empty">(no content)</div>}
    </Collapsible>
  );
}

// Per the agreed default: everything closed except the last turn — that is the
// one the span is actually about; the rest is context the developer opens when
// the last turn doesn't explain itself.
function MessageList({ messages }) {
  return (
    <div className="msglist">
      {messages.map((m, i) => (
        <Message key={i} msg={m} open={i === messages.length - 1} />
      ))}
    </div>
  );
}

function ToolsBlock({ tools }) {
  return (
    <Collapsible className="tools" title="Tools" meta={`${tools.length} available`}>
      {tools.map((t, i) => {
        const fn = isObj(t?.function) ? t.function : t; // OpenAI wraps in {type, function}
        const schema = fn?.parameters ?? fn?.input_schema;
        return (
          <Collapsible
            key={fn?.name ?? i}
            className="tool"
            title={<span className="toolname">{fn?.name ?? `tool ${i}`}</span>}
          >
            {fn?.description && <div className="tooldesc">{fn.description}</div>}
            <pre className="body">{pretty(schema ?? fn)}</pre>
          </Collapsible>
        );
      })}
    </Collapsible>
  );
}

/** `model`, `temperature`, … — the request's scalar settings, on one line. */
function requestMeta(value) {
  const parts = [];
  for (const [k, v] of Object.entries(value)) {
    if (k === "messages" || k === "tools") continue;
    if (v === null || typeof v === "object") continue;
    parts.push(`${k}: ${v}`);
  }
  return parts;
}

// --- shape dispatch ---------------------------------------------------------

function PrettyBody({ value }) {
  if (value == null || value === "") return <div className="payload-empty">(empty)</div>;
  if (typeof value !== "object") return <pre className="body">{String(value)}</pre>;

  if (Array.isArray(value)) {
    if (value.length && value.every(looksLikeMessage)) return <MessageList messages={value} />;
    return <pre className="body">{pretty(value)}</pre>;
  }

  // A chat-completions request: the tool catalogue, then the conversation.
  if (Array.isArray(value.messages)) {
    const meta = requestMeta(value);
    return (
      <>
        {meta.length > 0 && <div className="payload-meta">{meta.join(" · ")}</div>}
        {Array.isArray(value.tools) && value.tools.length > 0 && <ToolsBlock tools={value.tools} />}
        <MessageList messages={value.messages} />
      </>
    );
  }

  // A chat-completions response.
  if (Array.isArray(value.choices) && value.choices.some((c) => looksLikeMessage(c?.message))) {
    return (
      <div className="msglist">
        {value.choices.map((c, i) =>
          looksLikeMessage(c?.message) ? (
            <Message key={i} msg={c.message} open />
          ) : (
            <pre className="body" key={i}>{pretty(c)}</pre>
          )
        )}
      </div>
    );
  }

  // A bare message — the usual shape of a generation's output.
  if (looksLikeMessage(value) || Array.isArray(value.content)) {
    return (
      <div className="msglist">
        <Message msg={value} open />
      </div>
    );
  }

  return <pre className="body">{pretty(value)}</pre>;
}

// --- the block SpanDetail renders -------------------------------------------

export default function Payload({ label, value }) {
  const [mode, setMode] = useState("pretty");
  // Nothing to toggle when the body is plain text: both modes would show it
  // identically, and a dead control is worse than no control.
  const structured = value !== null && typeof value === "object";

  return (
    <div className="payload">
      <div className="payload-head">
        <span className="label">{label}</span>
        {structured && (
          <div className="segmented sm">
            <button
              className={mode === "pretty" ? "active" : ""}
              onClick={() => setMode("pretty")}
            >
              Pretty
            </button>
            <button className={mode === "json" ? "active" : ""} onClick={() => setMode("json")}>
              JSON
            </button>
          </div>
        )}
      </div>
      {structured && mode === "json" ? (
        <pre className="body payload-json">{pretty(value)}</pre>
      ) : (
        <PrettyBody value={value} />
      )}
    </div>
  );
}
