// What a trace step actually *did*, derived from its own payload.
//
// The problem this solves: every step in a real trace was labelled the same
// thing — typically "OpenAI Completion". That label is Langfuse's observation
// `name`, which the backend passes through verbatim
// (`integrations/real/langfuse.py`), and which comes from whatever the agent's
// instrumentation chose. The OpenAI SDK's auto-instrumentation names every
// generation identically, so the column that should read "read_file",
// "execute_sql", "final answer" reads as one repeated string.
//
// The information is not missing, only unused: the *body* of each span says
// plainly what happened. `trace_view.span_to_out` sends the structured
// input/output in preference to the flattened text, so the browser already has
// the chat-completions object. This module reads it.
//
// Two rules, the same ones SpanPayload renders by:
//
//   1. **Recognise, never require.** Every path falls back to the raw
//      `tool_name`. An unfamiliar payload gets the label it has today, not a
//      wrong one and not a crash.
//   2. **Never contradict the trace store.** The derived label is an addition;
//      the raw name stays on screen, because matching a step against Langfuse's
//      own UI is something people do.
//
// The dialect handling is shared with SpanPayload rather than copied — it is the
// same parse, and two copies of it would drift.

export const isObj = (v) => v !== null && typeof v === "object" && !Array.isArray(v);

export const looksLikeMessage = (v) => isObj(v) && typeof v.role === "string";

/** Tool calls in whichever dialect the agent logged them. */
export function toolCallsOf(msg) {
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
  // The pre-`tool_calls` OpenAI shape. Still logged by older agent SDKs.
  if (isObj(msg?.function_call)) {
    calls.push({ name: msg.function_call.name ?? "tool", args: msg.function_call.arguments });
  }
  // Anthropic puts tool use inside the content array.
  if (Array.isArray(msg?.content)) {
    for (const p of msg.content) {
      if (isObj(p) && p.type === "tool_use") calls.push({ id: p.id, name: p.name, args: p.input });
    }
  }
  return calls;
}

/** Every message in a payload, whichever container it arrived in. */
function messagesOf(value) {
  if (value == null) return [];
  if (looksLikeMessage(value)) return [value];
  if (Array.isArray(value)) return value.filter(looksLikeMessage);
  if (!isObj(value)) return [];
  // A chat-completions request.
  if (Array.isArray(value.messages)) return value.messages.filter(looksLikeMessage);
  // A chat-completions response.
  if (Array.isArray(value.choices)) {
    return value.choices.map((c) => c?.message).filter(looksLikeMessage);
  }
  // A bare Anthropic-style body: content without a role.
  if (Array.isArray(value.content)) return [value];
  return [];
}

/** Does this payload carry a tool *result* coming back in? */
function hasToolResult(value) {
  for (const msg of messagesOf(value)) {
    const role = String(msg.role || "").toLowerCase();
    if (role === "tool" || role === "function") return true;
    if (Array.isArray(msg.content)) {
      for (const part of msg.content) {
        if (isObj(part) && part.type === "tool_result") return true;
      }
    }
  }
  return false;
}

function textOf(msg) {
  const c = msg?.content;
  if (typeof c === "string") return c.trim();
  if (Array.isArray(c)) {
    return c
      .map((p) => (typeof p === "string" ? p : isObj(p) && typeof p.text === "string" ? p.text : ""))
      .join(" ")
      .trim();
  }
  return "";
}

// A Langfuse name worth showing on its own. The generic ones are exactly the
// problem — they are what the auto-instrumentation produces for *every* step, so
// falling back to one tells the reader nothing they didn't already know.
//
// Checked by splitting into words rather than by one pattern: real names arrive
// as `openai-chat-completion`, `azure.chat.completions.create`, `OpenAI
// Completion` — the same handful of words in any order, any separator, any
// number of them. A regex that matched all of those would be longer than this
// list and harder to add to.
const GENERIC_WORDS = new Set([
  "openai", "anthropic", "azure", "bedrock", "vertex", "gemini", "litellm", "llm",
  "chat", "completion", "completions", "generation", "generations",
  "create", "invoke", "call", "calls", "messages", "message", "response", "api",
]);

const allGeneric = (text) => {
  const words = text.toLowerCase().split(/[^a-z0-9]+/).filter(Boolean);
  return words.length > 0 && words.every((w) => GENERIC_WORDS.has(w));
};

export const isGenericName = (name) => {
  const text = String(name ?? "").trim();
  if (!text) return true; // no name at all is as unhelpful as a generic one
  // Two splittings, either of which may be the right one. `ChatCompletion` needs
  // the camelCase boundary to become two words; `OpenAI Completion` is ruined by
  // it, because `OpenAI` splits into `open` + `ai`. Rather than pick, try both.
  return allGeneric(text) || allGeneric(text.replace(/([a-z0-9])([A-Z])/g, "$1 $2"));
};

/**
 * `{ kind, label, detail }` for one span.
 *
 *   kind         'tool_call' | 'tool_result' | 'assistant' | 'raw'
 *   label        what to show as the step's name
 *   detail       the tool names behind a 'tool_call', for a title attribute
 *
 * Reading the output before the input is deliberate: what a step *produced* is
 * what it was for. A generation that both received a tool result and then asked
 * for two more tools is a tool call — the result it consumed is the previous
 * step's story.
 */
export function spanLabel(span) {
  const raw = span?.tool_name || "";

  const calls = messagesOf(span?.output).flatMap(toolCallsOf);
  if (calls.length) {
    const names = calls.map((c) => c.name).filter(Boolean);
    return {
      kind: "tool_call",
      label: names.length ? names.join(", ") : "Tool call",
      detail: names.join(", "),
    };
  }

  if (hasToolResult(span?.input)) {
    return { kind: "tool_result", label: "Tool result", detail: "" };
  }

  const out = messagesOf(span?.output);
  const assistant = out.find((m) => String(m.role || "").toLowerCase() === "assistant") || out[0];
  if (assistant && textOf(assistant)) {
    return { kind: "assistant", label: "Assistant response", detail: "" };
  }

  // Nothing recognised. The raw name is all there is — which is the situation
  // every span was in before this module existed.
  return { kind: "raw", label: raw || "step", detail: "" };
}

/**
 * Whether the raw Langfuse name is worth repeating next to the derived label.
 * "OpenAI Completion" beside "read_file, execute_sql" is noise; a name the agent
 * actually chose is context.
 */
export function showRawName(span, derived) {
  const raw = span?.tool_name || "";
  if (!raw || derived.kind === "raw") return false;
  return !isGenericName(raw);
}
