import test from "node:test";
import assert from "node:assert/strict";
import { AGENT_PREFILL_KEYS, prefillAgent } from "./agent_prefill.js";

const DEFAULTS = {
  agent_chat_url: "http://mine:8080/v1/chat/completions",
  agent_skills_url: "http://mine:8080/skills",
  agent_auth_header: "X-Api-Key",
  agent_timeout_s: 60,
  // Everything else a defaults response carries. None of it belongs in a form
  // that asks for an agent server.
  judge_model: "gpt-4o",
  concurrency: 4,
};

test("an untouched form opens on the developer's own agent", () => {
  const out = prefillAgent({}, DEFAULTS);
  assert.equal(out.agent_chat_url, DEFAULTS.agent_chat_url);
  assert.equal(out.agent_skills_url, DEFAULTS.agent_skills_url);
  assert.equal(out.agent_auth_header, "X-Api-Key");
  assert.equal(out.agent_timeout_s, 60);
});

test("nothing outside the agent keys travels", () => {
  const out = prefillAgent({}, DEFAULTS);
  assert.deepEqual(Object.keys(out).sort(), [...AGENT_PREFILL_KEYS].sort());
});

test("a credential is never prefilled, however the defaults arrive", () => {
  // The server does not send one back — this is the belt for the braces, since
  // a key rendered into a form is a key on screen and in a saved draft.
  const out = prefillAgent({}, { ...DEFAULTS, agent_api_key: "sk-live-123" });
  assert.equal(out.agent_api_key, undefined);
  assert.equal(JSON.stringify(out).includes("sk-live-123"), false);
});

test("what the developer typed survives — this fills gaps, it does not edit", () => {
  const typed = {
    agent_chat_url: "http://staging:9000/v1/chat/completions",
    agent_timeout_s: 5,
  };
  const out = prefillAgent(typed, DEFAULTS);
  assert.equal(out.agent_chat_url, "http://staging:9000/v1/chat/completions");
  assert.equal(out.agent_timeout_s, 5);
  // The blank one beside them is still filled.
  assert.equal(out.agent_skills_url, DEFAULTS.agent_skills_url);
});

test("a field cleared to blank counts as empty, whitespace included", () => {
  const out = prefillAgent({ agent_chat_url: "", agent_skills_url: "   " }, DEFAULTS);
  assert.equal(out.agent_chat_url, DEFAULTS.agent_chat_url);
  assert.equal(out.agent_skills_url, DEFAULTS.agent_skills_url);
});

test("a zero timeout is an answer, not an empty field", () => {
  // It is a nonsense value and the form rejects it. Replacing it with 60 would
  // be this function overruling somebody rather than helping them.
  const out = prefillAgent({ agent_timeout_s: 0 }, DEFAULTS);
  assert.equal(out.agent_timeout_s, 0);
});

test("a default the deployment never set is not written as a blank", () => {
  // `run_config.defaults()` returns "" for an unset AGENT_SKILLS_URL. Writing
  // that over an empty field changes nothing on screen and everything in a
  // draft, which would then look like a value somebody chose.
  const out = prefillAgent({}, { agent_chat_url: "http://a/v1", agent_skills_url: "", agent_auth_header: null });
  assert.equal(out.agent_chat_url, "http://a/v1");
  assert.equal("agent_skills_url" in out, false);
  assert.equal("agent_auth_header" in out, false);
});

test("no defaults yet is not an error — the form stays as it is", () => {
  assert.deepEqual(prefillAgent({ agent_chat_url: "http://a/v1" }, null), {
    agent_chat_url: "http://a/v1",
  });
  assert.deepEqual(prefillAgent(undefined, undefined), {});
});

test("the caller's object is not edited in place", () => {
  const config = {};
  const out = prefillAgent(config, DEFAULTS);
  assert.deepEqual(config, {});
  assert.notEqual(out, config);
});
