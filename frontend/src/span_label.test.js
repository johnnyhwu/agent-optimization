// Run with: pnpm test  (node --test, no test framework dependency — this module
// is plain ESM with no JSX, which is most of why the parsing lives here rather
// than inside the component that renders it.)
//
// These cases are the specification. There was no real Langfuse trace to check
// against while this was written, so the shapes below are the OpenAI and
// Anthropic documented ones, and the last group pins down what happens to
// everything else: it falls back, it does not guess.
import { test } from "node:test";
import assert from "node:assert/strict";
import { spanLabel, isGenericName, showRawName, toolCallsOf } from "./span_label.js";

const GENERIC = "OpenAI Completion";

test("OpenAI: a response asking for tools is named after them", () => {
  const span = {
    tool_name: GENERIC,
    output: {
      choices: [
        {
          message: {
            role: "assistant",
            content: null,
            tool_calls: [
              { id: "c1", function: { name: "read_file", arguments: '{"path":"a.md"}' } },
              { id: "c2", function: { name: "execute_sql", arguments: '{"q":"SELECT 1"}' } },
            ],
          },
        },
      ],
    },
  };
  const got = spanLabel(span);
  assert.equal(got.kind, "tool_call");
  assert.equal(got.label, "read_file, execute_sql");
});

test("OpenAI: a bare assistant message is the output shape too", () => {
  const span = {
    tool_name: GENERIC,
    output: { role: "assistant", content: null, tool_calls: [{ function: { name: "search" } }] },
  };
  assert.equal(spanLabel(span).label, "search");
});

test("OpenAI: the pre-tool_calls function_call shape still resolves", () => {
  const span = {
    tool_name: GENERIC,
    output: { role: "assistant", function_call: { name: "get_weather", arguments: "{}" } },
  };
  const got = spanLabel(span);
  assert.equal(got.kind, "tool_call");
  assert.equal(got.label, "get_weather");
});

test("Anthropic: tool_use inside the content array", () => {
  const span = {
    tool_name: "anthropic.messages.create",
    output: {
      role: "assistant",
      content: [
        { type: "text", text: "Let me look that up." },
        { type: "tool_use", id: "tu_1", name: "query_invoices", input: { account: 88213 } },
      ],
    },
  };
  const got = spanLabel(span);
  assert.equal(got.kind, "tool_call");
  assert.equal(got.label, "query_invoices");
});

test("a tool result coming back in is labelled as one", () => {
  const span = {
    tool_name: GENERIC,
    input: {
      messages: [
        { role: "assistant", content: null, tool_calls: [{ function: { name: "read_file" } }] },
        { role: "tool", tool_call_id: "c1", content: "file contents" },
      ],
    },
    output: { role: "assistant", content: "The file says…" },
  };
  // The output has no calls and the input carries a result, so this step is
  // where a tool's answer came back.
  assert.equal(spanLabel(span).kind, "tool_result");
});

test("Anthropic tool_result parts count as a tool result", () => {
  const span = {
    tool_name: GENERIC,
    input: [{ role: "user", content: [{ type: "tool_result", tool_use_id: "tu_1", content: "42" }] }],
    output: { role: "assistant", content: "The answer is 42." },
  };
  assert.equal(spanLabel(span).kind, "tool_result");
});

test("a plain answer with no tools is the assistant's response", () => {
  const span = {
    tool_name: GENERIC,
    input: { messages: [{ role: "user", content: "What is the balance?" }] },
    output: { choices: [{ message: { role: "assistant", content: "It is $2,845.50." } }] },
  };
  const got = spanLabel(span);
  assert.equal(got.kind, "assistant");
  assert.equal(got.label, "Assistant response");
});

test("a step that produced tools wins over one that consumed them", () => {
  // Both true at once: a result came in, and two more calls went out. What the
  // step produced is what it was for.
  const span = {
    tool_name: GENERIC,
    input: { messages: [{ role: "tool", content: "rows" }] },
    output: { role: "assistant", tool_calls: [{ function: { name: "summarise" } }] },
  };
  assert.equal(spanLabel(span).kind, "tool_call");
});

test("an unrecognised payload falls back to the raw name", () => {
  for (const body of ["some plain text", 42, null, { unexpected: { nested: true } }, []]) {
    const got = spanLabel({ tool_name: "vector_search", input: body, output: body });
    assert.equal(got.kind, "raw", `body ${JSON.stringify(body)}`);
    assert.equal(got.label, "vector_search");
  }
});

test("a span with nothing at all still produces a label", () => {
  assert.equal(spanLabel({}).label, "step");
  assert.equal(spanLabel(undefined).label, "step");
});

test("generic instrumentation names are recognised as generic", () => {
  for (const n of [
    "OpenAI Completion", "openai-chat-completion", "ChatCompletion", "generation",
    "llm call", "Anthropic Completion", "azure.chat.completions.create", "",
  ]) {
    assert.equal(isGenericName(n), true, n);
  }
  for (const n of ["read_file", "execute_sql", "billing_lookup", "select_skill"]) {
    assert.equal(isGenericName(n), false, n);
  }
});

test("the raw name is only worth repeating when it says something", () => {
  const calls = { role: "assistant", tool_calls: [{ function: { name: "read_file" } }] };
  const generic = { tool_name: GENERIC, output: calls };
  const named = { tool_name: "billing.agent.step", output: calls };
  assert.equal(showRawName(generic, spanLabel(generic)), false);
  assert.equal(showRawName(named, spanLabel(named)), true);
  // Nothing was derived, so the raw name is already the label — don't say it twice.
  const fell = { tool_name: "vector_search", output: "text" };
  assert.equal(showRawName(fell, spanLabel(fell)), false);
});

test("toolCallsOf handles the three dialects and ignores everything else", () => {
  assert.equal(toolCallsOf({ tool_calls: [{ function: { name: "a" } }] })[0].name, "a");
  assert.equal(toolCallsOf({ function_call: { name: "b" } })[0].name, "b");
  assert.equal(toolCallsOf({ content: [{ type: "tool_use", name: "c" }] })[0].name, "c");
  assert.deepEqual(toolCallsOf({ role: "user", content: "hello" }), []);
  assert.deepEqual(toolCallsOf(null), []);
});
